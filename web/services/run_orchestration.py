"""One Web request owns one Run until the engine reports its real terminal."""
from __future__ import annotations

from typing import Any, Callable
from uuid import uuid4

from events import RunEvent
from run.conversation import session_lock
from web.constants import _WORKER_DONE


def run_source(
    service: Any, *, request: dict[str, Any], active: Any,
    normalized_run_id: str, normalized_source: str, normalized_session: str,
    name: str, cancel_event: Any, put: Callable[[Any], bool],
) -> None:
    request["_agent_root_task_id"] = f"main-task-{uuid4().hex}"
    iterator = None
    try:
        with session_lock(service.root, name, normalized_source, normalized_session):
            if cancel_event.is_set():
                put(RunEvent(type="done", metadata={
                    "status": "cancelled", "cancelled": True, "committed": False,
                    "stop_reason": "user_emergency_stop",
                }))
                return
            source_request = dict(request)
            source_request.update(user=name, source=normalized_source,
                                  session_id=normalized_session, run_id=normalized_run_id)
            iterator = iter(service.event_source(
                source_request, root=service.root, cancel_event=cancel_event,
            ))
            for event in iterator:
                provisional = (event.type == "error" and event.metadata.get("retryable") is True
                               and event.metadata.get("committed") is False)
                if provisional:
                    continue
                terminal = event.type in {"done", "error"}
                if cancel_event.is_set() and not terminal:
                    # Keep driving the engine through its cancellation cleanup;
                    # it owns tool/child liveness and the truthful terminal.
                    continue
                if not put(event) or terminal:
                    return
            if cancel_event.is_set():
                put(RunEvent(type="done", metadata={
                    "status": "cancelled", "cancelled": True, "committed": False,
                    "stop_reason": "user_emergency_stop",
                }))
            else:
                put(RunEvent(type="error", error={
                    "code": "RUN_MISSING_TERMINAL", "message": "运行未返回终态事件",
                    "phase": "run",
                }, metadata={"status": "failed", "committed": False, "retryable": False}))
    except BaseException as exc:
        put(exc)
    finally:
        if iterator is not None:
            close = getattr(iterator, "close", None)
            if callable(close):
                try:
                    close()
                except BaseException:
                    pass
        with service._active_runs_lock:
            active.guidance.close()
            service._active_runs.pop(normalized_run_id, None)
        put(_WORKER_DONE)
