"""Cross-platform subprocess options for background work."""

from __future__ import annotations

import subprocess
import sys
import os
import signal
import time
from typing import Any


def hidden_subprocess_kwargs() -> dict[str, Any]:
    """Return options that prevent background children from opening a Windows console.

    Console applications launched by a detached/Web Python process otherwise receive a
    short-lived ``conhost.exe`` window.  Other platforms need no special option.
    """

    if sys.platform != "win32":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        "startupinfo": startupinfo,
    }


def cancellable_subprocess_kwargs() -> dict[str, Any]:
    """Start a child in its own process group without showing a Windows console."""

    if sys.platform == "win32":
        options = hidden_subprocess_kwargs()
        options["creationflags"] = int(options.get("creationflags", 0)) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
        )
        return options
    return {"start_new_session": True}


def terminate_process_tree(process: subprocess.Popen[Any], *, grace_seconds: float = 0.5) -> None:
    """Best-effort cross-platform termination of a process and its descendants."""

    if process.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            **hidden_subprocess_kwargs(),
        )
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        process.terminate()
    deadline = time.monotonic() + max(0.0, grace_seconds)
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
