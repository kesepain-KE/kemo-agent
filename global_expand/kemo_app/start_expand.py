"""kemo app 桥接服务 — 守护进程管理（start/stop/status/restart）。

常驻机制：本模块被框架按需调用（120s 超时内），通过 subprocess.Popen 拉起
detached 的 server.py 常驻子进程后立即返回；服务生命周期由本文件管理，
进程崩溃后由下一次 start/status 调用检测并自动拉起。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

from lifecycle import load_ready_config
from device_commands import DeviceCommandStore

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_PATH = Path(BASE_DIR)
SERVER_PATH = os.path.join(BASE_DIR, "daemon.py")
PID_PATH = os.path.join(BASE_DIR, "_server.pid")
LOG_PATH = os.path.join(BASE_DIR, "logs", "server.log")
ACTIVATION_PATH = os.path.join(BASE_DIR, "_activated.json")
DEVICE_COMMAND_PATH = os.path.join(BASE_DIR, "_device_commands.json")


def _read_activation() -> dict | None:
    try:
        with open(ACTIVATION_PATH, "r", encoding="utf-8") as file:
            value = json.load(file)
        return value if isinstance(value, dict) else None
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _write_activation() -> None:
    """Persist the operator's activation intent without exposing credentials."""

    now = datetime.now().astimezone().isoformat(timespec="seconds")
    current = _read_activation() or {}
    payload = {
        "activated_at": current.get("activated_at") or now,
        "last_launch_attempt": None,
        "consecutive_failures": 0,
    }
    temporary = ACTIVATION_PATH + ".tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")
    os.replace(temporary, ACTIVATION_PATH)


def _clear_activation() -> None:
    try:
        os.remove(ACTIVATION_PATH)
    except FileNotFoundError:
        pass


def _load_config() -> tuple[dict | None, dict]:
    return load_ready_config(BASE_PATH)


def _health(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/v1/health",
            timeout=0.8,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if isinstance(payload, dict) and payload.get("service") == "kemo_app":
            return payload
    except (OSError, UnicodeError, json.JSONDecodeError, urllib.error.URLError):
        pass
    return None


def _port_open(port: int) -> bool:
    return _health(port) is not None


def _read_pid_state() -> dict | None:
    try:
        with open(PID_PATH, "r", encoding="utf-8") as file:
            raw = file.read().strip()
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = {"pid": int(raw), "instance_id": ""}
        if not isinstance(value, dict):
            return None
        pid = int(value.get("pid") or 0)
        if pid <= 0:
            return None
        return {**value, "pid": pid, "instance_id": str(value.get("instance_id") or "")}
    except Exception:
        return None


def _read_pid():
    state = _read_pid_state()
    return int(state["pid"]) if state else None


def _write_pid_state(pid: int, instance_id: str) -> None:
    payload = {
        "pid": pid,
        "instance_id": instance_id,
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    temporary = PID_PATH + ".tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")
    os.replace(temporary, PID_PATH)


def _pid_alive(pid) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            return bool(ok) and exit_code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def status() -> dict:
    cfg, initialization = _load_config()
    pid_state = _read_pid_state()
    pid = int(pid_state["pid"]) if pid_state else None
    health = _health(int((cfg or {}).get("port", 8742))) if cfg is not None else None
    instance_id = str((pid_state or {}).get("instance_id") or "")
    health_pid = int((health or {}).get("process_pid") or 0)
    pid_matches = bool(
        pid
        and _pid_alive(pid)
        and health
        and health_pid == pid
        and instance_id
        and health.get("instance_id") == instance_id
    )
    legacy_matches = bool(
        pid and _pid_alive(pid) and health and health_pid == pid and not instance_id
    )
    owned = bool(pid_matches or legacy_matches)
    running = health is not None
    return {
        "ok": True,
        **initialization,
        "activated": _read_activation() is not None,
        "active": bool(initialization["configured"] and owned),
        "running": running,
        "pid": pid if (pid_matches or legacy_matches) else None,
        "instance_id": instance_id if pid_matches else "",
        "stale_pid": bool(pid_state and not (pid_matches or legacy_matches)),
        "unmanaged_process": bool(health and not (pid_matches or legacy_matches)),
        "orphaned_process": bool(running and not initialization["configured"]),
        "log": LOG_PATH,
    }


def start() -> dict:
    st = status()
    if not st["configured"]:
        return {
            **st,
            "ok": False,
            "active": False,
            "error": "bridge_not_initialized",
            "message": "桥接服务尚未完成初始化和凭据配置，拒绝启动。",
        }
    if st["running"]:
        if not st["active"]:
            return {
                **st,
                "ok": False,
                "error": "bridge_process_unmanaged",
                "message": "端口上存在未由当前模块实例管理的 kemo_app，拒绝接管或写入激活状态。",
            }
        _write_activation()
        return {**status(), "ok": True, "message": "already running"}
    cfg, _ = _load_config()
    assert cfg is not None
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    flags = 0
    for name in ("CREATE_NEW_PROCESS_GROUP", "DETACHED_PROCESS", "CREATE_NO_WINDOW"):
        flags |= getattr(subprocess, name, 0)
    instance_id = uuid.uuid4().hex
    environment = dict(os.environ)
    environment["KEMO_APP_INSTANCE_ID"] = instance_id
    with open(LOG_PATH, "ab") as lf:
        proc = subprocess.Popen(
            [sys.executable, SERVER_PATH],
            cwd=BASE_DIR,
            stdout=lf,
            stderr=lf,
            creationflags=flags,
            close_fds=True,
            env=environment,
        )
    _write_pid_state(proc.pid, instance_id)
    # 等待端口就绪（最多 6 秒）
    ready = False
    for _ in range(20):
        health = _health(int(cfg.get("port", 8742)))
        if health and health.get("instance_id") == instance_id:
            ready = True
            break
        time.sleep(0.3)
    if ready:
        _write_activation()
        return {**status(), "ok": True, "message": "started"}
    return {
        **status(),
        "ok": False,
        "active": False,
        "error": "bridge_port_not_ready",
        "message": "进程已创建，但桥接端口未就绪；请检查日志。",
        "pid": proc.pid,
    }


def _stop_process() -> dict:
    """Stop only the daemon process and keep activation intent unchanged."""

    state = _read_pid_state()
    pid = int(state["pid"]) if state else None
    cfg, _ = _load_config()
    health = _health(int((cfg or {}).get("port", 8742))) if cfg is not None else None
    instance_id = str((state or {}).get("instance_id") or "")
    health_pid = int((health or {}).get("process_pid") or 0)
    owned = bool(
        pid
        and _pid_alive(pid)
        and health
        and health_pid == pid
        and (not instance_id or health.get("instance_id") == instance_id)
    )
    if owned:
        if os.name == "nt":
            import ctypes

            PROCESS_TERMINATE = 0x0001
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
            if handle:
                kernel32.TerminateProcess(handle, 1)
                kernel32.CloseHandle(handle)
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        time.sleep(0.6)
    if os.path.exists(PID_PATH):
        try:
            os.remove(PID_PATH)
        except OSError:
            pass
    return status()


def stop() -> dict:
    after = _stop_process()
    return {**after, "ok": True, "message": "stopped" if not after["running"] else "still running"}


def deactivate() -> dict:
    _clear_activation()
    after = _stop_process()
    return {**after, "ok": True, "message": "deactivated" if not after["running"] else "still running"}


def restart() -> dict:
    _stop_process()
    time.sleep(0.5)
    return start()


_COMMANDS = {
    "activate": start,
    "start": start,
    "deactivate": deactivate,
    "stop": stop,
    "status": status,
    "configuration_status": status,
    "restart": restart,
}


def device_action(params: dict | None = None, *, context: dict | None = None) -> dict:
    arguments = dict(params or {})
    invocation = dict(context or {})
    username = str(invocation.get("user") or "").strip()
    action = str(arguments.get("action") or "").strip()
    device_id = str(arguments.get("device_id") or "").strip()
    if not username:
        return {"ok": False, "error": "missing_user", "message": "设备操作缺少用户上下文。"}
    connections = _read_connections()
    online = sorted({
        str(item.get("device_id") or "")
        for item in connections.get("devices", [])
        if isinstance(item, dict) and item.get("user") == username and str(item.get("device_id") or "")
    })
    if not device_id:
        if len(online) == 1:
            device_id = online[0]
        elif not online:
            return {"ok": False, "error": "no_online_device", "message": "当前没有在线 App 设备。"}
        else:
            return {
                "ok": False,
                "error": "device_required",
                "message": "当前有多台在线设备，请指定 device_id。",
                "devices": online,
            }
    ttl_seconds = arguments.get("ttl_seconds", 300)
    try:
        command = DeviceCommandStore(Path(DEVICE_COMMAND_PATH)).enqueue(
            username=username,
            device_id=device_id,
            action=action,
            arguments=arguments.get("arguments", {}),
            ttl_seconds=ttl_seconds,
        )
    except ValueError as exc:
        return {"ok": False, "error": "invalid_device_action", "message": str(exc)}
    delivered = device_id in online
    if delivered:
        delivered = _deliver_live(username, device_id, command)
    return {
        "ok": True,
        "queued": True,
        "delivered": delivered,
        "command_id": command["command_id"],
        "device_id": device_id,
        "action": command["action"],
        "status": command["status"],
        "expires_at": command["expires_at"],
        "message": "设备指令已入队；在线设备将通过 WebSocket 接收。" if delivered else "设备当前离线，指令将在有效期内等待设备连接。",
    }


def _deliver_live(username: str, device_id: str, command: dict) -> bool:
    config, _ = _load_config()
    if not config:
        return False
    request = urllib.request.Request(
        f"http://127.0.0.1:{int(config.get('port', 8742))}/internal/device-command",
        data=json.dumps({"user": username, "device_id": device_id, "command": command}, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Kemo-Internal": str(config.get("session_secret") or ""),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return bool(payload.get("delivered"))
    except (OSError, UnicodeError, json.JSONDecodeError, urllib.error.URLError):
        return False


def device_action_status(
    params: dict | None = None,
    *,
    context: dict | None = None,
) -> dict:
    command_id = str((params or {}).get("command_id") or "").strip()
    username = str((context or {}).get("user") or "").strip()
    if not command_id:
        return {"ok": False, "error": "missing_command_id"}
    if not username:
        return {"ok": False, "error": "missing_user"}
    command = DeviceCommandStore(Path(DEVICE_COMMAND_PATH)).get(command_id, username=username)
    if command is None:
        return {"ok": False, "command": None, "error": "command_not_found"}
    return {"ok": True, "command": command, "error": None}


def _read_connections() -> dict:
    try:
        value = json.loads(Path(BASE_DIR, "_connections.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def execute(command: str, params: dict | None = None, *, context: dict | None = None) -> dict:
    """框架操作层入口：execute(command, params)。"""
    cmd = str(command or "").strip().casefold()
    if cmd == "device_action":
        return device_action(params, context=context)
    if cmd == "device_action_status":
        return device_action_status(params, context=context)
    handler = _COMMANDS.get(cmd)
    if not handler:
        return {"ok": False, "error": f"unknown command: {command}"}
    try:
        return handler()
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _request() -> tuple[str, dict, dict]:
    if not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        if raw:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise TypeError("请求必须是 JSON 对象")
            command = payload.get("command") or payload.get("action")
            params = payload.get("params", {})
            context = payload.get("context", {})
            if not isinstance(command, str) or not command:
                raise ValueError("请求缺少 command")
            if not isinstance(params, dict):
                raise TypeError("params 必须是 JSON 对象")
            if not isinstance(context, dict):
                raise TypeError("context 必须是 JSON 对象")
            return command, params, context
    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    if not isinstance(params, dict):
        raise TypeError("params 必须是 JSON 对象")
    return command, params, {}


def main():
    cmd, params, context = _request()
    result = execute(cmd, params, context=context)
    print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
    raise SystemExit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
