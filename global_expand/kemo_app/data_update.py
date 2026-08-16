"""Collect a secret-safe bridge summary for Prompt injection."""

from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from lifecycle import load_ready_config
import start_expand


BASE_DIR = Path(__file__).resolve().parent
INPUT_PATH = BASE_DIR / "input_data.md"
MANIFEST_PATH = BASE_DIR / "expand.json"
CONNECTIONS_PATH = BASE_DIR / "_connections.json"
ACTIVATION_PATH = BASE_DIR / "_activated.json"
MIN_LAUNCH_INTERVAL_SECONDS = 60
AUTO_LAUNCH_HEALTH_TIMEOUT_SECONDS = 12
PERSISTENCE_CHECKPOINT_SECONDS = 300
FAILURE_BACKOFF_SECONDS = (60, 300, 900, 1800)
NON_COUNTING_LAUNCH_ERRORS = {
    "bridge_port_in_use",
    "bridge_process_unmanaged",
    "bridge_start_pending",
    "bridge_process_unverified",
    "bridge_not_initialized",
    "cooldown",
    "backoff",
    "not_activated",
}
_FAILURE_DESCRIPTIONS = {
    "not_activated": "尚未记录启用意愿，不会自动启动。",
    "cooldown": "距离上一次启动尝试过近，正在等待短暂冷却。",
    "backoff": "连续启动失败后进入临时退避，稍后会自动重试。",
    "bridge_not_initialized": "桥接配置或凭据尚未初始化完成。",
    "bridge_port_in_use": "桥接端口被其他服务占用；为避免误伤，未自动关闭该服务。",
    "bridge_process_unmanaged": "检测到不属于当前实例的 kemo_app；为避免误杀，拒绝接管。",
    "bridge_start_pending": "上一启动器可能仍在向后台子进程交接，暂不重复启动。",
    "bridge_start_crashed": "桥接启动进程提前退出。",
    "bridge_health_timeout": "桥接进程未在等待期限内达到健康状态。",
    "bridge_process_unverified": "PID 存活但健康端点不可验证，未自动终止。",
    "bridge_stop_failed": "受管桥接进程未能正常停止。",
    "bridge_stop_timeout": "受管桥接进程停止超时。",
    "health_recheck_failed": "自动启动后再次检查健康状态失败。",
    "launch_failed": "桥接自动启动失败。",
}


def _atomic_text(path: Path, content: str) -> bool:
    try:
        if path.read_text(encoding="utf-8") == content:
            return False
    except (OSError, UnicodeError):
        pass
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)
    return True


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _set_manifest(*, active: bool, healthy: bool, update_time: str = "") -> None:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("expand.json 顶层必须是 JSON 对象")
    previous_active = bool(payload.get("open_input"))
    previous_health = str(payload.get("input_health") or "")
    next_health = "正常" if healthy else "异常"
    payload["open_input"] = active
    payload["input_health"] = next_health
    if active and healthy and update_time:
        previous_update = _parse_time(payload.get("recent_update"))
        current_update = _parse_time(update_time)
        checkpoint_due = bool(
            previous_update is None
            or current_update is None
            or (current_update - previous_update).total_seconds()
            >= PERSISTENCE_CHECKPOINT_SECONDS
        )
        if (
            previous_active != active
            or previous_health != next_health
            or checkpoint_due
        ):
            payload["recent_update"] = update_time
    elif not active:
        payload.pop("recent_update", None)
    _atomic_json(MANIFEST_PATH, payload)


def _persistence_time(value: datetime) -> str:
    checkpoint = int(value.timestamp()) // PERSISTENCE_CHECKPOINT_SECONDS
    rendered = datetime.fromtimestamp(
        checkpoint * PERSISTENCE_CHECKPOINT_SECONDS,
        tz=value.tzinfo,
    )
    return rendered.strftime("%Y-%m-%d %H:%M:%S")


def _load_connections() -> dict[str, Any]:
    try:
        data = json.loads(CONNECTIONS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def _read_activation() -> dict[str, Any] | None:
    try:
        value = json.loads(ACTIVATION_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _update_activation(payload: dict[str, Any]) -> None:
    # Use the same cross-process lifecycle lock as start/stop so framework
    # refreshes cannot overwrite each other's backoff state.
    with start_expand._lifecycle_lock():
        current = _read_activation()
        if current is None:
            return
        current.update(payload)
        _atomic_json(ACTIVATION_PATH, current)


def _parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo is not None else parsed.astimezone()
    except (TypeError, ValueError):
        return None


def _failure_description(code: str) -> str:
    return _FAILURE_DESCRIPTIONS.get(code, f"桥接启动失败（{code}）。")


def _should_auto_launch(now: datetime | None = None) -> tuple[bool, str]:
    activation = _read_activation()
    if activation is None:
        return False, "not_activated"
    current_time = now or datetime.now().astimezone()
    blocked_until = _parse_time(activation.get("blocked_until"))
    if blocked_until and current_time < blocked_until:
        return False, "backoff"
    last_attempt = activation.get("last_launch_attempt")
    if last_attempt:
        last_time = _parse_time(last_attempt)
        if last_time:
            if (current_time - last_time).total_seconds() < MIN_LAUNCH_INTERVAL_SECONDS:
                return False, "cooldown"
    return True, "ok"


def _auto_launch() -> tuple[bool, str]:
    allowed, reason = _should_auto_launch()
    if not allowed:
        return False, reason
    attempted_at = datetime.now().astimezone().isoformat(timespec="seconds")
    _update_activation({"last_launch_attempt": attempted_at})
    result = start_expand.execute("start")
    if bool(result.get("ok")) and bool(result.get("active") or result.get("running")):
        # start() resets the persisted failure state after the port is ready.
        return True, "started"
    error = str(result.get("error") or result.get("message") or "launch_failed")
    activation = _read_activation() or {}
    try:
        failures = max(0, int(activation.get("consecutive_failures") or 0))
    except (TypeError, ValueError):
        failures = 0
    counted = error not in NON_COUNTING_LAUNCH_ERRORS
    new_failures = failures + 1 if counted else failures
    backoff_index = min(max(new_failures, 1) - 1, len(FAILURE_BACKOFF_SECONDS) - 1)
    delay = FAILURE_BACKOFF_SECONDS[backoff_index] if counted else MIN_LAUNCH_INTERVAL_SECONDS
    now = datetime.now().astimezone()
    blocked_until = datetime.fromtimestamp(now.timestamp() + delay, tz=now.tzinfo)
    _update_activation(
        {
            "last_launch_attempt": attempted_at,
            "consecutive_failures": new_failures,
            "last_error": error,
            "last_error_at": attempted_at,
            "blocked_until": blocked_until.isoformat(timespec="seconds"),
        }
    )
    return False, error


def _safe_label(value: object) -> str:
    return str(value or "unknown").replace("`", "").replace("\n", " ").strip() or "unknown"


def _inactive_output(state: dict[str, Any]) -> None:
    initialized = "已创建本地配置" if state["initialized"] else "未完成"
    missing = "、".join(str(item) for item in state.get("missing", [])) or "本地凭据"
    _atomic_text(
        INPUT_PATH,
        "# kemo app 桥接服务\n\n"
        "- 状态: **未激活**\n"
        f"- 初始化: **{initialized}**\n"
        f"- 待配置: {missing}\n\n"
        "未完成本地初始化与凭据配置，不会连接上游、监听端口或向 Prompt 注入运行数据。\n",
    )
    # "inactive" is a valid, successfully collected state.  Keep Prompt
    # injection disabled while allowing the framework health contract to pass.
    _set_manifest(active=False, healthy=True)


def _probe_health(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=5) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict) or data.get("service") != "kemo_app" or data.get("status") != "ok":
        raise RuntimeError("unexpected_bridge_health_response")
    return data


def _write_online_output(
    data: dict[str, Any],
    *,
    host: str,
    port: int,
    update_time: str,
) -> None:
    connections = _load_connections()
    devices = connections.get("devices") if isinstance(connections.get("devices"), list) else []
    lines = [
        "# kemo app 桥接服务",
        "",
        "- 状态: **在线**",
        f"- 服务: {data.get('display_name') or data.get('service')} v{data.get('version')}",
        f"- 端口: {host}:{port}",
        f"- 上游: {data.get('upstream', 'unknown')}",
        f"- WebSocket 连接: {int(connections.get('websocket_connections') or data.get('websocket_connections') or 0)}",
        f"- 在线设备: {int(connections.get('connected_devices') or data.get('connected_devices') or 0)}",
    ]
    if devices:
        lines.extend(["", "## 在线设备"])
        for device in devices:
            if not isinstance(device, dict):
                continue
            lines.append(
                f"- 用户: `{_safe_label(device.get('user'))}`；设备 ID: `{_safe_label(device.get('device_id'))}`；连接数: {int(device.get('connections') or 1)}"
            )
    _atomic_text(INPUT_PATH, "\n".join(lines) + "\n")
    _set_manifest(active=True, healthy=True, update_time=update_time)


def _wait_for_health(url: str, timeout: float = AUTO_LAUNCH_HEALTH_TIMEOUT_SECONDS) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    latest: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return _probe_health(url)
        except Exception as exc:
            latest = exc
            time.sleep(0.4)
    if latest is not None:
        raise latest
    raise TimeoutError("bridge_health_not_ready")


def _launch_diagnostics(reason: str) -> dict[str, Any]:
    activation = _read_activation() or {}
    underlying = str(activation.get("last_error") or reason)
    try:
        failures = max(0, int(activation.get("consecutive_failures") or 0))
    except (TypeError, ValueError):
        failures = 0
    return {
        "reason": reason,
        "error_code": underlying,
        "description": _failure_description(underlying if reason in {"backoff", "cooldown"} else reason),
        "consecutive_failures": failures,
        "blocked_until": str(activation.get("blocked_until") or ""),
        "last_error_at": str(activation.get("last_error_at") or ""),
    }


def update() -> dict[str, Any]:
    config, state = load_ready_config(BASE_DIR)
    if config is None:
        _inactive_output(state)
        return {
            "ok": True,
            "status": "inactive",
            "active": False,
            "initialized": state["initialized"],
            "configured": False,
            "missing": state.get("missing", []),
            "resources": [],
        }

    host = str(config.get("host") or "127.0.0.1")
    port = int(config.get("port", 8742))
    probe_host = "127.0.0.1" if host in {"0.0.0.0", "::", "[::]"} else host
    url = f"http://{probe_host}:{port}/v1/health"
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    update_time = now.strftime("%Y-%m-%d %H:%M:%S")
    try:
        runtime_status = start_expand.status()
        if not runtime_status.get("active"):
            if runtime_status.get("unmanaged_process"):
                raise RuntimeError("bridge_process_unmanaged")
            raise RuntimeError("bridge_not_running")
        data = _probe_health(url)
        _write_online_output(data, host=host, port=port, update_time=update_time)
        return {"ok": True, "status": "online", "active": True, "resources": []}
    except Exception as exc:
        launched, launch_reason = _auto_launch()
        if launched:
            try:
                data = _wait_for_health(url)
                _write_online_output(data, host=host, port=port, update_time=update_time)
                return {
                    "ok": True,
                    "status": "online_auto_launched",
                    "active": True,
                    "auto_launch": "started",
                    "resources": [],
                }
            except Exception as reprobe_exc:
                exc = reprobe_exc
                launch_reason = "health_recheck_failed"
                activation = _read_activation() or {}
                try:
                    failures = max(0, int(activation.get("consecutive_failures") or 0)) + 1
                except (TypeError, ValueError):
                    failures = 1
                delay = FAILURE_BACKOFF_SECONDS[min(failures - 1, len(FAILURE_BACKOFF_SECONDS) - 1)]
                now = datetime.now().astimezone()
                _update_activation(
                    {
                        "last_error": launch_reason,
                        "last_error_at": now.isoformat(timespec="seconds"),
                        "consecutive_failures": failures,
                        "blocked_until": datetime.fromtimestamp(
                            now.timestamp() + delay,
                            tz=now.tzinfo,
                        ).isoformat(timespec="seconds"),
                    }
                )
        diagnosis = _launch_diagnostics(launch_reason)
        retry_line = (
            f"- 下次允许重试: {diagnosis['blocked_until']}\n"
            if diagnosis["blocked_until"]
            else ""
        )
        _atomic_text(
            INPUT_PATH,
            "# kemo app 桥接服务\n\n"
            "- 状态: **已配置但未运行**\n"
            f"- 端口: {host}:{port}\n"
            f"- 自动拉起: {launch_reason}\n"
            f"- 错误代码: {diagnosis['error_code']}\n"
            f"- 原因: {diagnosis['description']}\n"
            f"- 连续启动失败: {diagnosis['consecutive_failures']}\n"
            f"{retry_line}"
            f"- 最近检查: {_persistence_time(now)}\n",
        )
        _set_manifest(active=False, healthy=False)
        return {
            "ok": False,
            "status": "offline",
            "active": False,
            "error": type(exc).__name__,
            "auto_launch": launch_reason,
            "diagnosis": diagnosis,
            "resources": [],
        }


def main() -> None:
    outcome = update()
    print(json.dumps(outcome, ensure_ascii=False), flush=True)
    raise SystemExit(0 if outcome.get("ok") else 1)


if __name__ == "__main__":
    main()
