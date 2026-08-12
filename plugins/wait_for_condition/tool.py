"""可取消、可提前唤醒的长时等待工具。"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import socket
import time
from typing import Any


MAX_WAIT_SECONDS = 7200.0
DEFAULT_CHECK_INTERVAL = 5.0
MIN_CHECK_INTERVAL = 0.1
MAX_CHECK_INTERVAL = 60.0
CONDITIONS = frozenset(
    {
        "duration",
        "process_exit",
        "path_exists",
        "path_missing",
        "path_changed",
        "tcp_open",
        "tcp_closed",
    }
)


def _resolve_path(value: str, root: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("该等待条件需要非空 path")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.resolve(strict=False)


def _path_snapshot(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {"exists": False}
    except OSError as exc:
        return {"exists": False, "error": str(exc)}
    return {
        "exists": True,
        "is_dir": path.is_dir(),
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        raise ValueError("process_exit 需要 pid 为正整数")
    if os.name == "nt":
        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid,
        )
        if handle:
            exit_code = wintypes.DWORD()
            try:
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return True
                return exit_code.value == still_active
            finally:
                kernel32.CloseHandle(handle)
        error = ctypes.get_last_error()
        if error == 5:  # access denied still proves that the process exists
            return True
        if error in {0, 87, 1168}:  # invalid parameter / not found
            return False
        return False
    proc_stat = Path(f"/proc/{pid}/stat")
    try:
        fields = proc_stat.read_text("utf-8").split()
        if len(fields) >= 3 and fields[2] == "Z":
            return False
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _tcp_open(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except socket.gaierror as exc:
        raise ValueError(f"无法解析 host: {host}（{exc}）") from exc
    except (ConnectionError, OSError, TimeoutError):
        return False


def _validate(
    condition: str,
    timeout: float,
    check_interval: float,
    pid: int,
    path: str,
    host: str,
    port: int,
    root: Path,
) -> tuple[float, float, Path | None, str]:
    if condition not in CONDITIONS:
        raise ValueError(
            f"未知 condition: {condition}，可选: {', '.join(sorted(CONDITIONS))}"
        )
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout 必须是 1..7200 秒的数字")
    wait_seconds = float(timeout)
    if not 1 <= wait_seconds <= MAX_WAIT_SECONDS:
        raise ValueError("timeout 必须在 1..7200 秒之间")
    if isinstance(check_interval, bool) or not isinstance(check_interval, (int, float)):
        raise ValueError("check_interval 必须是数字")
    interval = float(check_interval)
    if not MIN_CHECK_INTERVAL <= interval <= MAX_CHECK_INTERVAL:
        raise ValueError("check_interval 必须在 0.1..60 秒之间")
    resolved_path = None
    normalized_host = ""
    if condition == "process_exit" and (
        isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0
    ):
        raise ValueError("process_exit 需要 pid 为正整数")
    if condition.startswith("path_"):
        resolved_path = _resolve_path(path, root)
    if condition.startswith("tcp_"):
        if not isinstance(host, str) or not host.strip():
            raise ValueError(f"{condition} 需要非空 host")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError(f"{condition} 需要 1..65535 的 port")
        normalized_host = host.strip()
    return wait_seconds, interval, resolved_path, normalized_host


def run(
    condition: str,
    timeout: float,
    check_interval: float = DEFAULT_CHECK_INTERVAL,
    pid: int = 0,
    path: str = "",
    host: str = "",
    port: int = 0,
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(context, dict) or not context.get("root"):
        raise ValueError("工具上下文缺少 root")
    root = Path(str(context["root"])).resolve()
    cancel_event = context.get("cancel_event")
    if cancel_event is None or not hasattr(cancel_event, "wait"):
        raise ValueError("工具上下文缺少 cancel_event")
    wait_seconds, interval, resolved_path, normalized_host = _validate(
        condition,
        timeout,
        check_interval,
        pid,
        path,
        host,
        port,
        root,
    )

    started = time.monotonic()
    deadline = started + wait_seconds
    initial_path = _path_snapshot(resolved_path) if resolved_path is not None else None
    checks = 0
    last_observation: dict[str, Any] = {}

    while True:
        if cancel_event.is_set():
            return {
                "ok": False,
                "status": "cancelled",
                "condition": condition,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "checks": checks,
            }

        checks += 1
        triggered = False
        trigger = ""
        if condition == "duration":
            triggered = time.monotonic() >= deadline
            trigger = "duration_elapsed"
        elif condition == "process_exit":
            exists = _process_exists(pid)
            last_observation = {"pid": pid, "process_exists": exists}
            triggered = not exists
            trigger = "process_exit"
        elif resolved_path is not None:
            current_path = _path_snapshot(resolved_path)
            last_observation = {"path": str(resolved_path), "snapshot": current_path}
            if condition == "path_exists":
                triggered = bool(current_path.get("exists"))
            elif condition == "path_missing":
                triggered = not bool(current_path.get("exists"))
            else:
                triggered = current_path != initial_path
                last_observation["initial_snapshot"] = initial_path
            trigger = condition
        else:
            probe_timeout = max(
                0.01,
                min(1.0, interval / 2, max(0.01, deadline - time.monotonic())),
            )
            is_open = _tcp_open(normalized_host, port, probe_timeout)
            last_observation = {
                "host": normalized_host,
                "port": port,
                "tcp_open": is_open,
            }
            triggered = is_open if condition == "tcp_open" else not is_open
            trigger = condition

        elapsed = time.monotonic() - started
        if triggered:
            return {
                "ok": True,
                "status": "triggered",
                "condition": condition,
                "trigger": trigger,
                "elapsed_seconds": round(elapsed, 3),
                "checks": checks,
                "observation": last_observation,
            }
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {
                "ok": True,
                "status": "timeout",
                "condition": condition,
                "triggered": False,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "checks": checks,
                "observation": last_observation,
            }
        cancel_event.wait(min(interval, remaining))
