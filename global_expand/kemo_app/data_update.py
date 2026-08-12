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
MAX_CONSECUTIVE_FAILURES = 3
MIN_LAUNCH_INTERVAL_SECONDS = 60
AUTO_LAUNCH_HEALTH_TIMEOUT_SECONDS = 12


def _atomic_text(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _set_manifest(*, active: bool, healthy: bool, update_time: str = "") -> None:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("expand.json 顶层必须是 JSON 对象")
    payload["open_input"] = active
    payload["input_health"] = "正常" if healthy else "异常"
    if active and healthy and update_time:
        payload["recent_update"] = update_time
    elif not active:
        payload.pop("recent_update", None)
    _atomic_json(MANIFEST_PATH, payload)


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
    current = _read_activation()
    if current is None:
        return
    current.update(payload)
    _atomic_json(ACTIVATION_PATH, current)


def _should_auto_launch(now: datetime | None = None) -> tuple[bool, str]:
    activation = _read_activation()
    if activation is None:
        return False, "not_activated"
    try:
        failures = max(0, int(activation.get("consecutive_failures") or 0))
    except (TypeError, ValueError):
        failures = 0
    if failures >= MAX_CONSECUTIVE_FAILURES:
        return False, "too_many_failures"
    last_attempt = activation.get("last_launch_attempt")
    if last_attempt:
        try:
            last_time = datetime.fromisoformat(str(last_attempt))
            if last_time.tzinfo is None:
                last_time = last_time.astimezone()
            current_time = now or datetime.now().astimezone()
            if (current_time - last_time).total_seconds() < MIN_LAUNCH_INTERVAL_SECONDS:
                return False, "cooldown"
        except (TypeError, ValueError):
            pass
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
    activation = _read_activation() or {}
    try:
        failures = max(0, int(activation.get("consecutive_failures") or 0))
    except (TypeError, ValueError):
        failures = 0
    _update_activation(
        {
            "last_launch_attempt": attempted_at,
            "consecutive_failures": failures + 1,
        }
    )
    return False, str(result.get("error") or result.get("message") or "launch_failed")


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
    update_time = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
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
        _atomic_text(
            INPUT_PATH,
            "# kemo app 桥接服务\n\n"
            "- 状态: **已配置但未运行**\n"
            f"- 端口: {host}:{port}\n"
            f"- 自动拉起: {launch_reason}\n"
            f"- 最近检查: {update_time}\n",
        )
        _set_manifest(active=False, healthy=False)
        return {
            "ok": False,
            "status": "offline",
            "active": False,
            "error": type(exc).__name__,
            "auto_launch": launch_reason,
            "resources": [],
        }


def main() -> None:
    outcome = update()
    print(json.dumps(outcome, ensure_ascii=False), flush=True)
    raise SystemExit(0 if outcome.get("ok") else 1)


if __name__ == "__main__":
    main()
