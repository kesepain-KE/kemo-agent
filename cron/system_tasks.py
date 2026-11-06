"""Framework-owned system cron execution adapters."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from run.agents import AgentRunner


def execute_memory_promotion(
    root: Path,
    user: str,
    *,
    config: dict[str, Any],
    provider_factory: Callable[[dict[str, Any]], Any],
    cancel_event: threading.Event | None,
) -> dict[str, Any]:
    from cron.review_due import scan_and_promote

    result = scan_and_promote(
        root=root,
        user=user,
        config=config,
        provider_factory=provider_factory,
        cancel_event=cancel_event,
    )
    if isinstance(result, dict):
        result["data_changed"] = any(
            bool(result.get(key))
            for key in ("deleted", "promotions", "applied")
        )
    return result


def execute_memory_review(
    root: Path,
    user: str,
    *,
    trigger: str,
    config: dict[str, Any],
    provider_factory: Callable[[dict[str, Any]], Any],
    cancel_event: threading.Event | None,
    task_id: str,
) -> dict[str, Any]:
    if cancel_event is not None and cancel_event.is_set():
        return {"status": "cancelled", "action": trigger, "user": user}
    result = AgentRunner(
        root,
        user,
        config=config,
        provider_factory=provider_factory,
    ).run(
        "memory_temporary_important",
        {"trigger": trigger},
        cancel_event=cancel_event,
        task_id=task_id,
    )
    response = {
        "status": "completed",
        "action": trigger,
        "user": user,
        "data": result.data if isinstance(getattr(result, "data", None), dict) else {},
    }
    update = (
        result.metadata.get("important_memory_update")
        if isinstance(getattr(result, "metadata", None), dict)
        else None
    )
    if isinstance(update, dict):
        response["memory_update"] = update
        response["data_changed"] = any(
            bool(update.get(key))
            for key in (
                "featured",
                "reference_corrections",
                "reconciled",
                "created",
                "updated",
                "deleted",
                "changed",
                "promoted",
            )
        )
    else:
        response["data_changed"] = False
    return response


def execute_session_sweep(
    root: Path,
    user: str,
    *,
    config: dict[str, Any],
) -> dict[str, Any]:
    from run.scheduler import SESSION_IDLE_CLOSE_SECONDS, sweep_idle_sessions

    cron_config = (
        checked_293 if isinstance(checked_293 := config.get("cron"), dict) else {}
    )
    raw_idle = cron_config.get("session_idle_close_seconds")
    try:
        idle_seconds = (
            max(3600, int(raw_idle))
            if raw_idle is not None
            else SESSION_IDLE_CLOSE_SECONDS
        )
    except (TypeError, ValueError):
        idle_seconds = SESSION_IDLE_CLOSE_SECONDS
    result = sweep_idle_sessions(root, user, idle_seconds=idle_seconds)
    result["data_changed"] = any(
        int(result.get(key) or 0) > 0
        for key in ("closed", "requeued_closed", "queued_memory", "queued_summary")
    )
    return result


__all__ = [
    "execute_memory_promotion",
    "execute_memory_review",
    "execute_session_sweep",
]
