"""Reconcile surviving agent executors without prematurely releasing ownership."""
from __future__ import annotations
import time
from datetime import datetime, timezone
from typing import Any
from run.agents.contracts import AgentRunResult

def watch_detached_completion(scheduler, task, future: Any, continuation=None):
    """Reconcile a task after the caller timed out but its worker survived."""
    from run.agents.queue import _agent_task_error_detail

    add_done_callback = getattr(future, "add_done_callback", None)
    if not callable(add_done_callback):
        return

    def reconcile(done_future: Any) -> None:
        try:
            result = done_future.result()
        except BaseException as exc:
            with scheduler._lock:
                current = scheduler._tasks.get(task.id)
                if current is not task:
                    return
                previous = task.error if isinstance(task.error, dict) else {}
                user_cancel_requested = bool(
                    previous.get("user_cancel_requested")
                )
                if task.status == "cancelled" or user_cancel_requested:
                    task.status = "cancelled"
                elif task.status == "timed_out_running":
                    task.status = "timed_out"
                else:
                    task.status = "failed"
                detail = _agent_task_error_detail(exc)
                detail.update(
                    {
                        "cancel_requested": bool(
                            previous.get("cancel_requested")
                        ),
                        "process_terminated": True,
                    }
                )
                if user_cancel_requested:
                    detail["user_cancel_requested"] = True
                task.error = detail
                if continuation is not None and task.status == "timed_out":
                    task.result = continuation.partial(task.agent)
                    task.resumable = continuation.available
                task.finished_at = datetime.now(timezone.utc).isoformat()
                scheduler._last_activity = time.monotonic()
                scheduler._prune_terminal_tasks_locked()
                scheduler._emit(task, task.status, detail=detail)
            return

        with scheduler._lock:
            if scheduler._tasks.get(task.id) is not task:
                return
            if (task.error or {}).get("user_cancel_requested") or task.status == "cancelled":
                task.result = result
                task.resumable = False
                task.status = "cancelled"
                task.error = {**(task.error or {}), "process_terminated": True}
                task.finished_at = datetime.now(timezone.utc).isoformat()
                scheduler._emit(task, "cancelled")
                return
        if continuation is not None and continuation.enabled and continuation.timed_out.is_set():
            with scheduler._lock:
                if scheduler._tasks.get(task.id) is not task:
                    return
                task.result = continuation.partial(task.agent)
                task.resumable = continuation.available
                task.status = "cancelled" if (task.error or {}).get("user_cancel_requested") else "timed_out"
                task.error = {**(task.error or {}), "process_terminated": True}
                task.finished_at = datetime.now(timezone.utc).isoformat()
                scheduler._emit(task, task.status)
            return
        try:
            if task.result_handler is not None:
                task.result_handler(result)
        except BaseException as exc:
            with scheduler._lock:
                current = scheduler._tasks.get(task.id)
                if current is not task:
                    return
                previous = task.error if isinstance(task.error, dict) else {}
                detail = _agent_task_error_detail(exc)
                detail.update(
                    {
                        "cancel_requested": bool(
                            previous.get("cancel_requested")
                        ),
                        "process_terminated": True,
                    }
                )
                if previous.get("user_cancel_requested"):
                    detail["user_cancel_requested"] = True
                task.status = "failed"
                task.error = detail
                task.finished_at = datetime.now(timezone.utc).isoformat()
                scheduler._last_activity = time.monotonic()
                scheduler._prune_terminal_tasks_locked()
                scheduler._emit(task, "failed", detail=detail)
            return

        with scheduler._lock:
            current = scheduler._tasks.get(task.id)
            if current is not task:
                return
            was_timeout = task.status == "timed_out_running"
            was_cancel_requested = bool(
                isinstance(task.error, dict)
                and task.error.get("user_cancel_requested")
            )
            if isinstance(result, AgentRunResult):
                result.metadata = {
                    **result.metadata,
                    "completed_after_detach": True,
                    "cancel_requested": was_cancel_requested,
                }
            task.result = result
            task.completed_after_timeout = was_timeout
            task.status = "completed"
            task.error = None
            task.finished_at = datetime.now(timezone.utc).isoformat()
            scheduler._last_activity = time.monotonic()
            scheduler._prune_terminal_tasks_locked()
            scheduler._emit(
                task,
                "completed_after_detach",
                detail={
                    "completed_after_timeout": was_timeout,
                    "cancel_requested": was_cancel_requested,
                    "process_terminated": True,
                },
            )

    try:
        add_done_callback(reconcile)
    except (RuntimeError, TypeError, ValueError):
        # A future that is already shutting down may reject callback
        # registration; the original timeout state remains truthful.
        return

