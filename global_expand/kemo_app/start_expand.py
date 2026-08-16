"""kemo app 桥接服务 — 守护进程管理（start/stop/status/restart）。

常驻机制：本模块被框架按需调用（120s 超时内），通过 subprocess.Popen 拉起
detached 的 server.py 常驻子进程后立即返回；服务生命周期由本文件管理，
进程崩溃后由下一次 start/status 调用检测并自动拉起。
"""

from __future__ import annotations

import json
import os
import socket
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
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
LIFECYCLE_LOCK_PATH = os.path.join(BASE_DIR, "_lifecycle.lock")
STARTUP_TIMEOUT_SECONDS = 12.0
STOP_TIMEOUT_SECONDS = 8.0
STARTUP_RECOVERY_GRACE_SECONDS = 30.0

_LIFECYCLE_THREAD_LOCK = threading.RLock()
_LIFECYCLE_LOCAL = threading.local()


@contextmanager
def _lifecycle_lock(timeout: float = 15.0):
    """Serialize lifecycle mutations across threads and framework processes."""

    with _LIFECYCLE_THREAD_LOCK:
        depth = int(getattr(_LIFECYCLE_LOCAL, "depth", 0))
        if depth:
            _LIFECYCLE_LOCAL.depth = depth + 1
            try:
                yield
            finally:
                _LIFECYCLE_LOCAL.depth = depth
            return

        os.makedirs(os.path.dirname(LIFECYCLE_LOCK_PATH) or ".", exist_ok=True)
        lock_file = open(LIFECYCLE_LOCK_PATH, "a+b")
        deadline = time.monotonic() + timeout
        acquired = False
        try:
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0, os.SEEK_END)
                if lock_file.tell() == 0:
                    lock_file.write(b"0")
                    lock_file.flush()
                while not acquired:
                    try:
                        lock_file.seek(0)
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                        acquired = True
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("bridge_lifecycle_lock_timeout")
                        time.sleep(0.05)
            else:
                import fcntl

                while not acquired:
                    try:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        acquired = True
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("bridge_lifecycle_lock_timeout")
                        time.sleep(0.05)
            _LIFECYCLE_LOCAL.depth = 1
            yield
        finally:
            _LIFECYCLE_LOCAL.depth = 0
            if acquired:
                if os.name == "nt":
                    import msvcrt

                    lock_file.seek(0)
                    try:
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
                else:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()


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
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


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


def _remove_pid_state(*, instance_id: str | None = None) -> bool:
    """Remove the PID file only when it still belongs to the expected launch."""

    current = _read_pid_state()
    if instance_id is not None and str((current or {}).get("instance_id") or "") != instance_id:
        return False
    try:
        os.remove(PID_PATH)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


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


def _same_instance(pid_state: dict | None, health: dict | None) -> bool:
    instance_id = str((pid_state or {}).get("instance_id") or "")
    health_instance = str((health or {}).get("instance_id") or "")
    return bool(instance_id and health_instance and instance_id == health_instance)


def _status_unlocked() -> dict:
    cfg, initialization = _load_config()
    pid_state = _read_pid_state()
    pid = int(pid_state["pid"]) if pid_state else None
    health = _health(int((cfg or {}).get("port", 8742))) if cfg is not None else None
    instance_id = str((pid_state or {}).get("instance_id") or "")
    health_pid = int((health or {}).get("process_pid") or 0)
    reconciled_pid = False
    if pid_state and health_pid > 0 and _same_instance(pid_state, health) and health_pid != pid:
        # Windows launchers can hand off to a child process. The instance nonce is
        # the ownership proof; reconciling by PID alone would risk another process.
        _write_pid_state(health_pid, instance_id)
        pid_state = _read_pid_state()
        pid = health_pid
        reconciled_pid = True
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
        "reconciled_pid": reconciled_pid,
        "log": LOG_PATH,
    }


def status() -> dict:
    with _lifecycle_lock():
        return _status_unlocked()


def _log_offset() -> int:
    try:
        return os.path.getsize(LOG_PATH)
    except OSError:
        return 0


def _read_log_since(offset: int, limit: int = 16000) -> str:
    try:
        with open(LOG_PATH, "rb") as file:
            file.seek(max(0, offset))
            data = file.read(limit + 1)
        if len(data) > limit:
            data = data[-limit:]
        return data.decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def _python_executable() -> str:
    """Prefer pythonw on Windows so a persistent bridge never opens a console."""

    if os.name != "nt":
        return sys.executable
    current = Path(sys.executable)
    candidate = current.with_name("pythonw.exe")
    return str(candidate) if candidate.is_file() else sys.executable


def _terminate_pid(pid: int) -> bool:
    if not _pid_alive(pid):
        return True
    if os.name == "nt":
        import ctypes

        PROCESS_TERMINATE = 0x0001
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if not handle:
            return False
        try:
            return bool(kernel32.TerminateProcess(handle, 1))
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return not _pid_alive(pid)


def _failure(
    state: dict,
    error: str,
    message: str,
    *,
    diagnostics: str = "",
    pid: int | None = None,
) -> dict:
    result = {
        **state,
        "ok": False,
        "active": False,
        "error": error,
        "message": message,
    }
    if diagnostics:
        result["diagnostics"] = diagnostics
    if pid:
        result["pid"] = pid
    return result


def _state_age_seconds(state: dict | None) -> float | None:
    value = (state or {}).get("started_at")
    if not value:
        return None
    try:
        started = datetime.fromisoformat(str(value))
        if started.tzinfo is None:
            started = started.astimezone()
        return max(0.0, (datetime.now().astimezone() - started).total_seconds())
    except (TypeError, ValueError):
        return None


def start() -> dict:
    with _lifecycle_lock():
        st = status()
        if not st["configured"]:
            return _failure(
                st,
                "bridge_not_initialized",
                "桥接服务尚未完成初始化和凭据配置，拒绝启动。",
            )
        if st["running"]:
            if not st["active"]:
                return _failure(
                    st,
                    "bridge_process_unmanaged",
                    "端口上存在未由当前模块实例管理的 kemo_app，拒绝接管或覆盖。",
                )
            _write_activation()
            return {**status(), "ok": True, "message": "already running"}

        pending_state = _read_pid_state()
        if pending_state:
            pending_pid = int(pending_state.get("pid") or 0)
            pending_instance = str(pending_state.get("instance_id") or "")
            age = _state_age_seconds(pending_state)
            if pending_pid > 0 and _pid_alive(pending_pid):
                return _failure(
                    st,
                    "bridge_process_unverified",
                    "已有启动进程仍存活，但健康端点尚不可验证；未重复启动。",
                    pid=pending_pid,
                )
            if pending_instance and age is not None and age < STARTUP_RECOVERY_GRACE_SECONDS:
                return _failure(
                    st,
                    "bridge_start_pending",
                    "上一启动器已退出，仍在等待可能的子进程完成交接；未重复启动。",
                    pid=pending_pid,
                )
            _remove_pid_state(instance_id=pending_instance)

        cfg, _ = _load_config()
        assert cfg is not None
        port = int(cfg.get("port", 8742))
        if _port_open(port):
            return _failure(
                st,
                "bridge_port_in_use",
                f"端口 {port} 已被非 kemo_app 服务占用，未启动桥接进程。",
            )

        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        flags = 0
        for name in ("CREATE_NEW_PROCESS_GROUP", "DETACHED_PROCESS", "CREATE_NO_WINDOW"):
            flags |= getattr(subprocess, name, 0)
        instance_id = uuid.uuid4().hex
        environment = dict(os.environ)
        environment["KEMO_APP_INSTANCE_ID"] = instance_id
        log_offset = _log_offset()
        with open(LOG_PATH, "ab") as lf:
            proc = subprocess.Popen(
                [_python_executable(), SERVER_PATH],
                cwd=BASE_DIR,
                stdout=lf,
                stderr=lf,
                creationflags=flags,
                close_fds=True,
                env=environment,
            )
        _write_pid_state(proc.pid, instance_id)

        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        launcher_exited = False
        conflicting_health: dict | None = None
        while time.monotonic() < deadline:
            health = _health(port)
            if health and str(health.get("instance_id") or "") == instance_id:
                actual_pid = int(health.get("process_pid") or proc.pid)
                _write_pid_state(actual_pid, instance_id)
                _write_activation()
                return {**status(), "ok": True, "message": "started"}
            if health:
                conflicting_health = health
                break
            launcher_exited = proc.poll() is not None
            # A Windows venv redirector may exit before the actual child becomes
            # healthy, so an exited launcher is not by itself a startup failure.
            time.sleep(0.25)

        diagnostics = _read_log_since(log_offset)
        health = conflicting_health or _health(port)
        if health and str(health.get("instance_id") or "") != instance_id:
            error = "bridge_process_unmanaged"
            message = "启动期间发现另一 kemo_app 实例占用端口，未接管该实例。"
        elif _port_open(port):
            error = "bridge_port_in_use"
            message = f"启动期间端口 {port} 被其他服务占用。"
        elif launcher_exited or proc.poll() is not None:
            error = "bridge_start_crashed"
            message = "桥接启动进程提前退出，未达到健康状态。"
        else:
            error = "bridge_health_timeout"
            message = "桥接进程在等待期限内未达到健康状态。"

        # Only terminate the process handle created by this attempt. A health
        # process is terminated only when its instance nonce proves ownership.
        own_health = _health(port)
        cleanup_pid_state = False
        if own_health and str(own_health.get("instance_id") or "") == instance_id:
            _terminate_pid(int(own_health.get("process_pid") or 0))
            cleanup_pid_state = True
        elif proc.poll() is None:
            try:
                proc.terminate()
                cleanup_pid_state = True
            except (OSError, ProcessLookupError):
                pass
        # If a Windows launcher already exited, retain the instance marker. A
        # redirected child may still become healthy shortly afterwards, and the
        # next status() can then safely reconcile its real PID by the nonce.
        if cleanup_pid_state or error in {"bridge_port_in_use", "bridge_process_unmanaged"}:
            _remove_pid_state(instance_id=instance_id)
        return _failure(
            status(),
            error,
            message,
            diagnostics=diagnostics,
            pid=proc.pid,
        )


def _stop_process() -> dict:
    """Stop only the daemon process and keep activation intent unchanged."""
    with _lifecycle_lock():
        state = _read_pid_state()
        pid = int(state["pid"]) if state else None
        cfg, _ = _load_config()
        port = int((cfg or {}).get("port", 8742))
        health = _health(port) if cfg is not None else None
        instance_id = str((state or {}).get("instance_id") or "")
        health_pid = int((health or {}).get("process_pid") or 0)

        if health:
            same_instance = _same_instance(state, health)
            legacy_owned = bool(pid and health_pid == pid and not instance_id)
            if not (same_instance or legacy_owned):
                # A stale PID file is harmless but must never authorize killing
                # the currently healthy, differently identified process.
                _remove_pid_state(instance_id=instance_id)
                return _failure(
                    status(),
                    "bridge_process_unmanaged",
                    "检测到不同实例的 kemo_app，拒绝自动停止。",
                )
            target_pid = health_pid or int(pid or 0)
            if target_pid <= 0 or not _terminate_pid(target_pid):
                return _failure(
                    status(),
                    "bridge_stop_failed",
                    "无法终止当前受管桥接进程。",
                )
            deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                current = _health(port)
                if not current or str(current.get("instance_id") or "") != str(health.get("instance_id") or ""):
                    break
                time.sleep(0.2)
            current = _health(port)
            if current and str(current.get("instance_id") or "") == str(health.get("instance_id") or ""):
                return _failure(
                    status(),
                    "bridge_stop_timeout",
                    "桥接进程在等待期限内没有退出。",
                )
            _remove_pid_state(instance_id=instance_id)
            return status()

        if state and pid and _pid_alive(pid):
            return _failure(
                status(),
                "bridge_process_unverified",
                "PID 仍存活但健康端点不可验证；为避免误杀，未自动终止。",
            )
        _remove_pid_state(instance_id=instance_id if state else None)
        return status()


def stop() -> dict:
    with _lifecycle_lock():
        after = _stop_process()
        if not after.get("ok", True) or after.get("running"):
            return {
                **after,
                "ok": False,
                "error": after.get("error") or "bridge_still_running",
                "message": after.get("message") or "桥接进程仍在运行。",
            }
        return {**after, "ok": True, "message": "stopped"}


def deactivate() -> dict:
    with _lifecycle_lock():
        _clear_activation()
        after = _stop_process()
        if not after.get("ok", True) or after.get("running"):
            return {
                **after,
                "ok": False,
                "error": after.get("error") or "bridge_still_running",
                "message": after.get("message") or "已清除激活意愿，但桥接进程仍在运行。",
            }
        return {**after, "ok": True, "message": "deactivated"}


def restart() -> dict:
    with _lifecycle_lock():
        stopped = _stop_process()
        if not stopped.get("ok", True) or stopped.get("running"):
            return {
                **stopped,
                "ok": False,
                "error": stopped.get("error") or "bridge_restart_stop_failed",
                "message": "旧桥接实例未安全停止，已中止重启。",
            }
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
