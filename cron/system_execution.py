"""Bounded persistence summaries for framework-owned cron executions."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from run.infra import LogStore
from run.scheduler import CronStore

BEIJING = ZoneInfo("Asia/Shanghai")

_SUCCESS_SYSTEM_STATUSES = frozenset(
    {"success", "completed", "enabled", "ok", "active", "inactive", "skipped"}
)

def _system_result_summary(result: Any) -> dict[str, Any]:
    """Keep system-cron diagnostics useful without persisting prompt bodies."""

    if not isinstance(result, dict):
        return {}
    summary: dict[str, Any] = {}
    for key in (
        "status", "action", "category", "scope", "user", "model", "requested", "reason"
    ):
        value = result.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            summary[key] = value
    for key in (
        "created", "updated", "failed", "forgotten", "rejected", "deleted", "applied"
    ):
        value = result.get(key)
        if isinstance(value, list):
            summary[key] = [str(item) for item in value[:100]]
    errors = result.get("errors")
    if isinstance(errors, list):
        summary["errors"] = [
            {
                name: str(item.get(name))
                for name in ("module", "reason", "exception_type")
                if item.get(name) is not None
            }
            for item in errors[:100]
            if isinstance(item, dict)
        ]
    promotions = result.get("promotions")
    if isinstance(promotions, list):
        summary["promotions"] = [
            {
                name: item.get(name)
                for name in ("from_tier", "to_tier", "filename", "merged_with", "skill_created")
                if name in item
            }
            for item in promotions[:100]
            if isinstance(item, dict)
        ]
    nested = result.get("data")
    if isinstance(nested, dict):
        nested_summary = _system_result_summary(nested)
        if nested_summary:
            summary["data"] = nested_summary
    memory_update = result.get("memory_update")
    if isinstance(memory_update, dict):
        featured = memory_update.get("featured")
        reconciled = memory_update.get("reconciled")
        summary["memory_update"] = {
            "featured": (
                [str(item) for item in featured[:100]]
                if isinstance(featured, list)
                else []
            ),
            "reconciled": (
                [
                    {
                        key: str(item.get(key))
                        for key in ("action", "filename", "permanent_filename")
                        if item.get(key) is not None
                    }
                    for item in reconciled[:100]
                    if isinstance(item, dict)
                ]
                if isinstance(reconciled, list)
                else []
            ),
        }
    return summary


def _system_execution_record(
    *,
    user: str,
    task_id: str,
    executed_at: datetime,
    duration_ms: int,
    result: dict[str, Any] | None = None,
    error: BaseException | None = None,
) -> dict[str, Any]:
    """Build one bounded, user-scoped system-cron execution record."""

    status = "failed" if error is not None else str((result or {}).get("status") or "completed")
    if error is None and status in _SUCCESS_SYSTEM_STATUSES:
        status = "success"
    return {
        "schema_version": 1,
        "executed_at": executed_at.astimezone(BEIJING).isoformat(),
        "user": user,
        "task_id": task_id,
        "status": status,
        "duration_ms": max(0, int(duration_ms)),
        "result": _system_result_summary(result),
        "error": (
            {"type": type(error).__name__, "message": str(error)}
            if error is not None
            else None
        ),
    }


def _append_system_execution(
    root: Path,
    *,
    user: str,
    task_id: str,
    executed_at: datetime,
    duration_ms: int,
    result: dict[str, Any] | None = None,
    error: BaseException | None = None,
) -> None:
    """Append one bounded execution immediately.

    The scheduler uses an in-memory aggregator for high-frequency successes;
    this direct helper remains the durable path for errors, low-frequency
    tasks and callers outside a running scheduler.
    """

    try:
        LogStore(root).append_cron(
            _system_execution_record(
                user=user,
                task_id=task_id,
                executed_at=executed_at,
                duration_ms=duration_ms,
                result=result,
                error=error,
            )
        )
    except Exception:
        # Diagnostics persistence must never stop the scheduler itself.
        return
