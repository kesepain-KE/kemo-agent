"""Shell job lookup, bounded waiting and operation replies."""

from __future__ import annotations

from pathlib import Path
import threading
import time
from typing import Any, Callable

from run.tools import (
    FOREGROUND_HANDOFF,
    JOB_TERMINAL_STATUSES,
    ToolCancelledError,
    assert_background_job_access,
    public_background_job,
    read_background_job,
    shell_status_wait_seconds,
)


def run_job_operation(
    action: str,
    *,
    root: Path,
    user: str,
    job_id: str,
    context: dict[str, Any],
    wait: bool,
    timeout: int | None,
    operate: Callable[[Path, str, str], dict[str, Any]],
) -> dict[str, Any]:
    deadline = (
        time.monotonic() + shell_status_wait_seconds(timeout)
        if action == "status" and wait else None
    )
    cancel_event = context.get("cancel_event") if deadline is not None else None
    source = str(context.get("source") or context.get("caller") or "")
    scope = str(context.get("session_id") or context.get("task_id") or "")
    wait_outcome = None
    while True:
        current = read_background_job(root, user, job_id)
        assert_background_job_access(current, source=source, session_id=scope)
        current = operate(root, user, job_id)
        if deadline is None:
            break
        if current.get("status") in JOB_TERMINAL_STATUSES:
            wait_outcome = "finished"
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            wait_outcome = "timeout"
            break
        interval = min(5.0, remaining)
        if isinstance(cancel_event, threading.Event):
            if cancel_event.wait(interval):
                raise ToolCancelledError("工具调用因用户紧急停止而取消")
        else:
            time.sleep(interval)
    operation_ok = (
        True
        if action == "status"
        else current.get("status") in {"cancelling", "cancelled"}
        or bool(current.get("cancel_requested"))
    )
    return {
        "ok": operation_ok,
        "operation_ok": operation_ok,
        "job_succeeded": current.get("status") == "completed",
        "operation": action,
        **({"output": f"后台作业状态：{current.get('status')}"}
           if current.get("owner_kind") != FOREGROUND_HANDOFF else {}),
        **public_background_job(current, root=root),
        **({"wait_outcome": wait_outcome} if wait_outcome is not None else {}),
    }
