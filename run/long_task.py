"""Conversation-scoped long-task authorization and runtime state.

The state deliberately lives in the existing per-user history registry.  A
long task is therefore isolated by ``(user, source, session_id)`` and does not
become a global or user configuration switch.
"""

from __future__ import annotations

from datetime import datetime, timezone
import copy
from pathlib import Path
import uuid
from typing import Any

from run.history_index import (
    index_lock,
    read_registry_record,
    upsert_registry_record,
)
from run.usage import merge_usage, usage_from_dict


LONG_TASK_TERMINAL_STATUSES = frozenset(
    {"disabled", "completed", "failed", "cancelled", "interrupted", "paused"}
)
LONG_TASK_ACTIVE_STATUSES = frozenset({"running", "pausing", "cancelling"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _empty_state(*, enabled: bool = False) -> dict[str, Any]:
    return {
        "enabled": bool(enabled),
        "status": "enabled" if enabled else "disabled",
        "task_id": "",
        "original_prompt": "",
        "started_at": "",
        "updated_at": _now(),
        "finished_at": "",
        "run_count": 0,
        "continuation_count": 0,
        "total_tool_calls": 0,
        "total_provider_requests": 0,
        "active_elapsed_ms": 0,
        "usage": {},
        "current_run_id": "",
        "last_stop_reason": "",
        "cancel_requested": False,
        "last_error": None,
    }


def _state(record: dict[str, Any] | None) -> dict[str, Any]:
    value = record.get("long_task") if isinstance(record, dict) else None
    result = _empty_state()
    if isinstance(value, dict):
        result.update(copy.deepcopy(value))
    result["enabled"] = bool(result.get("enabled", False))
    result["status"] = str(result.get("status") or ("enabled" if result["enabled"] else "disabled"))
    result["updated_at"] = str(result.get("updated_at") or _now())
    return result


def _public_state(value: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(value)
    started = _parse_time(result.get("started_at"))
    if started is not None and result.get("status") in LONG_TASK_ACTIVE_STATUSES:
        result["active_elapsed_ms"] = max(
            0, round((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        )
    else:
        result["active_elapsed_ms"] = max(0, int(result.get("active_elapsed_ms") or 0))
    return result


def _final_elapsed_ms(current: dict[str, Any]) -> int:
    accumulated = max(0, int(current.get("active_elapsed_ms") or 0))
    started = _parse_time(current.get("started_at"))
    if started is None:
        return accumulated
    wall_clock = max(
        0,
        round((datetime.now(timezone.utc) - started).total_seconds() * 1000),
    )
    return max(accumulated, wall_clock)


def get_long_task_state(
    root: Path, user: str, source: str, session_id: str
) -> dict[str, Any]:
    record = read_registry_record(root, user, source, session_id)
    return _public_state(_state(record))


def _mutate_state(
    root: Path,
    user: str,
    source: str,
    session_id: str,
    mutator: Any,
) -> dict[str, Any]:
    with index_lock(root, user):
        record = read_registry_record(root, user, source, session_id)
        if not isinstance(record, dict):
            # Session creation normally reserves this row first.  Returning a
            # conflict here prevents an accidental user-wide/global state.
            raise KeyError(f"会话不存在：{session_id}")
        state = _state(record)
        rendered_state = mutator(state)
        if not isinstance(rendered_state, dict):
            raise TypeError("长任务状态 mutator 必须返回对象")
        state = rendered_state
        record["long_task"] = copy.deepcopy(state)
        record["updated_at"] = _now()
        rendered = upsert_registry_record(
            root, user, record, updated_at=str(record["updated_at"])
        )
        return _public_state(_state(rendered))


def set_long_task_enabled(
    root: Path, user: str, source: str, session_id: str, enabled: bool
) -> dict[str, Any]:
    def mutate(current: dict[str, Any]) -> dict[str, Any]:
        was_terminal = current.get("status") in LONG_TASK_TERMINAL_STATUSES
        current_status = str(current.get("status") or "")
        was_enabled = bool(current.get("enabled"))
        current["enabled"] = bool(enabled)
        current["updated_at"] = _now()
        if not enabled:
            if current_status == "cancelling":
                current["status"] = "cancelling"
            elif current_status in {"running", "pausing"}:
                current["status"] = "pausing"
            else:
                current["status"] = "disabled"
        elif was_terminal and not was_enabled:
            current = _empty_state(enabled=True)
        elif was_terminal:
            # A repeated PUT(enabled=true) is idempotent and keeps the latest
            # task statistics visible.  activate_long_task() resets them only
            # when the next user request actually needs cross-Run execution.
            current["status"] = current_status
        elif current_status == "running":
            # PUT is idempotent. Repeating the enabled preference while a
            # logical task is active must not demote it back to the idle
            # authorization state.
            current["status"] = "running"
            current["cancel_requested"] = False
        elif current.get("status") == "pausing":
            # The current Run is still alive.  Re-enabling it removes a
            # pending pause/cancel request without resetting its statistics.
            current["status"] = "running"
            current["cancel_requested"] = False
        elif current.get("status") == "cancelling":
            # Cancellation already set the Run's threading.Event and cannot
            # be undone by flipping the authorization switch back on.
            current["status"] = "cancelling"
        else:
            current["status"] = "enabled"
            current["cancel_requested"] = False
        return current

    return _mutate_state(root, user, source, session_id, mutate)


def activate_long_task(
    root: Path,
    user: str,
    source: str,
    session_id: str,
    *,
    original_prompt: str,
) -> dict[str, Any] | None:
    activated: dict[str, Any] | None = None

    def mutate(current: dict[str, Any]) -> dict[str, Any]:
        nonlocal activated
        if not current.get("enabled"):
            activated = None
            return current
        if current.get("status") in LONG_TASK_ACTIVE_STATUSES:
            activated = current
            return current
        if current.get("status") in LONG_TASK_TERMINAL_STATUSES:
            # A new user request after a finished/paused task starts a fresh
            # logical task while preserving the explicit user authorization.
            current = _empty_state(enabled=True)
        now = _now()
        current.update(
            {
                "status": "running",
                "task_id": f"long_task_{uuid.uuid4().hex}",
                "original_prompt": str(original_prompt or ""),
                "started_at": now,
                "updated_at": now,
                "finished_at": "",
                "current_run_id": "",
                "last_stop_reason": "",
                "cancel_requested": False,
                "last_error": None,
            }
        )
        activated = current
        return current

    result = _mutate_state(root, user, source, session_id, mutate)
    return result if activated is not None else None


def record_long_task_run(
    root: Path,
    user: str,
    source: str,
    session_id: str,
    *,
    run_id: str,
    elapsed_ms: int = 0,
    tool_calls: int = 0,
    provider_requests: int = 0,
    usage: dict[str, Any] | None = None,
    stop_reason: str = "",
    continuation: bool = False,
) -> dict[str, Any]:
    def mutate(current: dict[str, Any]) -> dict[str, Any]:
        current["current_run_id"] = str(run_id or "")
        current["updated_at"] = _now()
        current["last_stop_reason"] = str(stop_reason or "")
        current["run_count"] = max(0, int(current.get("run_count") or 0)) + 1
        current["continuation_count"] = max(
            0,
            int(current.get("continuation_count") or 0)
            + (1 if continuation else 0),
        )
        current["total_tool_calls"] = max(0, int(current.get("total_tool_calls") or 0)) + max(0, int(tool_calls or 0))
        current["total_provider_requests"] = max(0, int(current.get("total_provider_requests") or 0)) + max(0, int(provider_requests or 0))
        current["active_elapsed_ms"] = max(0, int(current.get("active_elapsed_ms") or 0)) + max(0, int(elapsed_ms or 0))
        merged = dict(current.get("usage") or {})
        merge_usage(merged, usage_from_dict(usage))
        current["usage"] = merged
        return current

    return _mutate_state(root, user, source, session_id, mutate)


def set_long_task_current_run(
    root: Path,
    user: str,
    source: str,
    session_id: str,
    run_id: str,
) -> dict[str, Any]:
    """Point session controls at the currently active internal Run."""

    def mutate(current: dict[str, Any]) -> dict[str, Any]:
        if current.get("status") in LONG_TASK_ACTIVE_STATUSES:
            current["current_run_id"] = str(run_id or "")
            current["updated_at"] = _now()
        return current

    return _mutate_state(root, user, source, session_id, mutate)


def finish_long_task(
    root: Path,
    user: str,
    source: str,
    session_id: str,
    *,
    status: str,
    stop_reason: str = "",
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if status not in {"completed", "paused", "failed", "cancelled", "interrupted"}:
        raise ValueError(f"不支持的长任务终态：{status}")

    def mutate(current: dict[str, Any]) -> dict[str, Any]:
        current["status"] = status
        current["updated_at"] = _now()
        current["finished_at"] = _now()
        current["active_elapsed_ms"] = _final_elapsed_ms(current)
        current["current_run_id"] = ""
        current["last_stop_reason"] = str(stop_reason or current.get("last_stop_reason") or "")
        current["cancel_requested"] = status == "cancelled"
        current["last_error"] = copy.deepcopy(error) if error else None
        return current

    return _mutate_state(root, user, source, session_id, mutate)


def request_long_task_cancel(
    root: Path, user: str, source: str, session_id: str
) -> dict[str, Any]:
    def mutate(current: dict[str, Any]) -> dict[str, Any]:
        if current.get("status") in LONG_TASK_ACTIVE_STATUSES:
            current["status"] = "cancelling"
            current["cancel_requested"] = True
            current["updated_at"] = _now()
        elif current.get("status") == "paused" and current.get("task_id"):
            current["status"] = "cancelled"
            current["cancel_requested"] = True
            current["finished_at"] = _now()
            current["updated_at"] = _now()
        return current

    return _mutate_state(root, user, source, session_id, mutate)


__all__ = [
    "LONG_TASK_ACTIVE_STATUSES",
    "get_long_task_state",
    "set_long_task_enabled",
    "activate_long_task",
    "record_long_task_run",
    "set_long_task_current_run",
    "finish_long_task",
    "request_long_task_cancel",
]
