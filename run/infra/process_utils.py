"""Cross-platform subprocess options for background work."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
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


def visible_subprocess_kwargs() -> dict[str, Any]:
    """Return options for an explicitly requested visible Windows console."""

    if sys.platform != "win32":
        return {}
    return {
        "creationflags": getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010),
    }


def cancellable_subprocess_kwargs(*, show_terminal: bool = False) -> dict[str, Any]:
    """Start a child in its own process group.

    Windows children stay hidden by default.  A caller must explicitly pass
    ``show_terminal=True`` to request a new visible console; the visible mode
    intentionally does not combine ``CREATE_NEW_CONSOLE`` with the hidden
    startup flags.
    """

    if sys.platform == "win32":
        if show_terminal:
            options = visible_subprocess_kwargs()
            options["creationflags"] = int(options.get("creationflags", 0)) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
            )
            return options
        options = hidden_subprocess_kwargs()
        options["creationflags"] = int(options.get("creationflags", 0)) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
        )
        return options
    return {"start_new_session": True}


def detached_subprocess_kwargs(*, show_terminal: bool = False) -> dict[str, Any]:
    """Start a long-lived child detached from the launching tool process.

    The management process remains hidden by default.  Explicit visible mode
    creates a new console for the managed command and omits
    ``DETACHED_PROCESS`` because that flag would suppress the console.
    """

    if sys.platform == "win32":
        if show_terminal:
            options = visible_subprocess_kwargs()
            options["creationflags"] = int(options.get("creationflags", 0)) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
            )
            return options
        options = hidden_subprocess_kwargs()
        options["creationflags"] = (
            int(options.get("creationflags", 0))
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
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
        try:
            process.wait(timeout=max(0.1, grace_seconds))
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=max(0.1, grace_seconds))
            except subprocess.TimeoutExpired:
                pass
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


def terminate_pid_tree(
    pid: int,
    *,
    grace_seconds: float = 0.5,
    expected_process_started_at: str = "",
    expected_process_name: str = "",
) -> bool:
    """Best-effort termination for a persisted process-tree root PID.

    A persisted PID is only actionable when its expected creation identity is
    supplied.  Re-check that identity inside the destructive helper and before
    every signal fallback to narrow PID-reuse races; refuse to act when identity
    information is unavailable.
    """

    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    expected_started = str(expected_process_started_at or "").strip()
    if not expected_started:
        # A persisted PID without a stable creation identity is unsafe to
        # terminate: the operating system may already have reused it.
        return False
    from .process_identity import process_identity_matches, process_snapshot

    def identity_state() -> str:
        try:
            snapshot = process_snapshot(pid)
        except (OSError, ValueError):
            return "unsafe"
        if not snapshot.get("exists"):
            return "gone"
        return (
            "match"
            if process_identity_matches(
                snapshot,
                process_started_at=expected_started,
                process_name=expected_process_name,
            )
            is True
            else "unsafe"
        )

    if identity_state() != "match":
        return False
    if sys.platform == "win32":
        if identity_state() != "match":
            return False
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            **hidden_subprocess_kwargs(),
        )
        if completed.returncode != 0:
            state = identity_state()
            if state == "gone":
                return True
            if state != "match":
                return False
            try:
                os.kill(pid, signal.SIGTERM)
                return True
            except (OSError, ValueError):
                return False
        return True
    if identity_state() != "match":
        return False
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        state = identity_state()
        if state == "gone":
            return True
        if state != "match":
            return False
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return False
    deadline = time.monotonic() + max(0.0, grace_seconds)
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            break
        time.sleep(0.02)
    state = identity_state()
    if state == "gone":
        return True
    if state != "match":
        return False
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        state = identity_state()
        if state == "gone":
            return True
        if state != "match":
            return False
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    return True
