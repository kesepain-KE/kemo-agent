"""Cross-platform process identity snapshots for durable wait contracts."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
from typing import Any


def _windows_snapshot(pid: int) -> dict[str, Any]:
    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == 5:
            return {
                "pid": pid,
                "exists": True,
                "query_status": "access_denied",
                "identity_available": False,
            }
        return {
            "pid": pid,
            "exists": False,
            "query_status": "not_found",
            "identity_available": False,
        }
    try:
        exit_code = wintypes.DWORD()
        if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            if exit_code.value != still_active:
                return {
                    "pid": pid,
                    "exists": False,
                    "query_status": "exited",
                    "identity_available": False,
                }

        created = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        started_at = ""
        if kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            started_at = str((int(created.dwHighDateTime) << 32) | created.dwLowDateTime)

        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        executable = ""
        if kernel32.QueryFullProcessImageNameW(
            handle,
            0,
            buffer,
            ctypes.byref(size),
        ):
            executable = buffer.value
        return {
            "pid": pid,
            "exists": True,
            "query_status": "ok",
            "identity_available": bool(started_at),
            "process_started_at": started_at,
            "process_name": Path(executable).name if executable else "",
            "executable": executable,
        }
    finally:
        kernel32.CloseHandle(handle)


def _posix_snapshot(pid: int) -> dict[str, Any]:
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        stat_text = stat_path.read_text("utf-8")
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        stat_text = ""
    if stat_text:
        closing = stat_text.rfind(")")
        opening = stat_text.find("(")
        fields = stat_text[closing + 2 :].split() if closing >= 0 else []
        if len(fields) >= 20:
            if fields[0] == "Z":
                return {
                    "pid": pid,
                    "exists": False,
                    "query_status": "zombie",
                    "identity_available": False,
                }
            process_name = (
                stat_text[opening + 1 : closing]
                if opening >= 0 and closing > opening
                else ""
            )
            try:
                executable = str(Path(f"/proc/{pid}/exe").resolve(strict=True))
            except (FileNotFoundError, OSError):
                executable = ""
            return {
                "pid": pid,
                "exists": True,
                "query_status": "ok",
                "identity_available": True,
                "process_started_at": str(fields[19]),
                "process_name": Path(executable).name if executable else process_name,
                "executable": executable,
            }
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return {
            "pid": pid,
            "exists": False,
            "query_status": "not_found",
            "identity_available": False,
        }
    except PermissionError:
        return {
            "pid": pid,
            "exists": True,
            "query_status": "access_denied",
            "identity_available": False,
        }
    return {
        "pid": pid,
        "exists": True,
        "query_status": "identity_unavailable",
        "identity_available": False,
    }


def process_snapshot(pid: int) -> dict[str, Any]:
    """Return existence and stable identity fields for one operating-system PID."""

    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ValueError("pid 必须为正整数")
    return _windows_snapshot(pid) if os.name == "nt" else _posix_snapshot(pid)


def process_identity_matches(
    snapshot: dict[str, Any],
    *,
    process_started_at: str = "",
    process_name: str = "",
) -> bool | None:
    """Compare optional expected identity fields against a live snapshot."""

    if not snapshot.get("exists"):
        return False
    expected_started = str(process_started_at or "").strip()
    expected_name = Path(str(process_name or "").strip()).name.casefold()
    if expected_started:
        actual_started = str(snapshot.get("process_started_at") or "").strip()
        if not actual_started:
            return None
        if actual_started != expected_started:
            return False
    if expected_name:
        actual_name = Path(str(snapshot.get("process_name") or "")).name.casefold()
        if not actual_name:
            return None
        if actual_name != expected_name:
            return False
    return True


__all__ = ["process_identity_matches", "process_snapshot"]
