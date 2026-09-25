"""Bounded retry policy and recovered-tool snapshots for subagents."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
import json
import threading
from typing import Any
import uuid

from provider.protocol.models import JsonContent, ToolCallItem, ToolResultItem
from run.retry.policy import (
    MAX_ATTEMPTS,
    backoff_seconds,
    mark_retry_exhausted,
    retry_reason,
    should_retry,
    view_from_exception,
)
from run.retry.recovery import MAX_RECOVERY_CALLS, MAX_RECOVERY_CHARS, reuse_allowed
from run.tools import tool_call_signature

_MAX_AGENT_RETRY_ATTEMPTS = MAX_ATTEMPTS
_MAX_AGENT_RETRY_RECOVERY_CALLS = MAX_RECOVERY_CALLS
_MAX_AGENT_RETRY_RECOVERY_CHARS = MAX_RECOVERY_CHARS
def _new_agent_usage() -> dict[str, Any]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "estimated": False,
    }


@dataclass(slots=True)
class _AgentRetryState:
    recovery: dict[str, dict[str, Any]] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=_new_agent_usage)
    response_ids: list[str] = field(default_factory=list)
    auxiliary: dict[str, Any] = field(default_factory=dict)


def _agent_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _agent_tool_result_reuse_allowed(
    name: str,
    arguments: dict[str, Any],
) -> bool:
    """Return whether replaying a successful tool result is safe in one run."""

    return reuse_allowed(name, arguments)


def _record_agent_recovery(
    state: _AgentRetryState,
    call: ToolCallItem,
    payload: dict[str, Any],
) -> None:
    if not isinstance(payload, dict):
        return
    succeeded = payload.get("ok") is True
    if succeeded and not _agent_tool_result_reuse_allowed(call.name, call.arguments):
        return
    signature = tool_call_signature(call.name, call.arguments)
    if signature in state.recovery:
        return
    candidate = {
        "id": str(call.call_id or f"recovered_{len(state.recovery) + 1}"),
        "name": str(call.name),
        "arguments": copy.deepcopy(call.arguments),
        "result": copy.deepcopy(payload),
        "replay_policy": "reuse" if succeeded else "blocked",
    }
    if len(state.recovery) >= _MAX_AGENT_RETRY_RECOVERY_CALLS:
        return
    projected = sum(len(_agent_json(value)) for value in state.recovery.values())
    if projected + len(_agent_json(candidate)) > _MAX_AGENT_RETRY_RECOVERY_CHARS:
        return
    state.recovery[signature] = candidate


def _agent_recovery_items(
    recovery: dict[str, dict[str, Any]],
) -> list[Any]:
    items: list[Any] = []
    used_call_ids: set[str] = set()
    for value in recovery.values():
        name = str(value.get("name") or "unknown_tool").strip()
        arguments = value.get("arguments")
        result = value.get("result")
        if not name or not isinstance(arguments, dict) or not isinstance(result, dict):
            continue
        call_id = str(value.get("id") or "").strip()
        if not call_id or call_id in used_call_ids:
            call_id = f"recovered_{uuid.uuid4().hex}"
        used_call_ids.add(call_id)
        items.extend(
            [
                ToolCallItem(
                    id=f"recovered_call_{uuid.uuid4().hex}",
                    call_id=call_id,
                    name=name,
                    arguments=copy.deepcopy(arguments),
                ),
                ToolResultItem(
                    id=f"recovered_result_{uuid.uuid4().hex}",
                    call_id=call_id,
                    name=name,
                    is_error=result.get("ok") is not True,
                    content=[JsonContent(data=copy.deepcopy(result))],
                ),
            ]
        )
    return items


def _agent_recovery_records(
    recovery: dict[str, dict[str, Any]],
    *,
    exclude_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    excluded = exclude_ids or set()
    records: list[dict[str, Any]] = []
    for value in recovery.values():
        call_id = str(value.get("id") or "")
        name = str(value.get("name") or "unknown_tool")
        arguments = value.get("arguments")
        result = value.get("result")
        if (
            not call_id
            or call_id in excluded
            or not isinstance(arguments, dict)
            or not isinstance(result, dict)
        ):
            continue
        records.append(
            {
                "id": call_id,
                "name": name,
                "arguments": copy.deepcopy(arguments),
                "status": (
                    "recovered" if result.get("ok") is True else "recovery_blocked"
                ),
                "duplicate": False,
                "consecutive_identical_calls": 0,
                "result": copy.deepcopy(result),
                "iteration": 0,
                "elapsed_ms": 0,
                "recovered": True,
            }
        )
    return records


def _agent_tool_failure_is_retryable(
    payload: dict[str, Any],
    status: str,
) -> bool:
    if status in {
        "cancelled",
        "duplicate_reused",
        "identical_call_blocked",
        "not_executed",
        "result_too_large",
        "retry_reuse_blocked",
        "temporarily_unavailable",
        "timed_out_running",
    }:
        return False
    error = payload.get("error")
    if not isinstance(error, dict) or error.get("cancelled") is True:
        return False
    declared = error.get("retryable")
    if isinstance(declared, bool):
        return declared
    if error.get("still_running") is True:
        return False
    category = str(
        error.get("category")
        or error.get("type")
        or error.get("exception_type")
        or ""
    ).casefold()
    if category in _AGENT_RETRYABLE_CATEGORIES:
        return True
    for key in ("status_code", "provider_status"):
        try:
            if int(error.get(key)) in {408, 425, 429, 500, 502, 503, 504}:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _agent_error_is_retryable(
    error: BaseException,
    *,
    cancel_event: threading.Event,
) -> bool:
    if cancel_event.is_set():
        return False
    if type(error).__name__ in {
        "AgentCancelledError",
        "ToolCancelledError",
        "AgentToolLimitError",
    }:
        return False
    return should_retry(view_from_exception(error))


def _agent_retry_delay_seconds(error: BaseException, failed_attempt: int) -> float:
    return backoff_seconds(failed_attempt, view_from_exception(error))


def _agent_retry_reason(error: BaseException) -> str:
    return retry_reason(view_from_exception(error))


def _mark_agent_retry_exhausted(
    error: BaseException,
    *,
    attempts: int,
    max_attempts: int,
) -> None:
    mark_retry_exhausted(error, attempts=attempts, max_attempts=max_attempts)
