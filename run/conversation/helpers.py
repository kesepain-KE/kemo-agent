"""Pure helpers shared by the conversation runtime stages.

The module deliberately contains no session, provider, tool, or filesystem
side effects.  The public conversation runtime re-exports these private
helpers so existing internal imports keep working while the orchestration
file remains focused on sequencing.
"""

from __future__ import annotations

import copy
import json
import queue
import threading
import uuid
from pathlib import Path
from typing import Any

from events import RunEvent
from provider.schema import ToolCall
from run.config import PromptBundle
from run.context import estimate_messages_tokens
from run.conversation.guidance import normalize_guidance
from run.conversation.provider_events import (
    metric_provider_response_payload as _metric_provider_response_payload,
)
from run.infra import EngineError
from run.tools import ToolResultTooLargeError, tool_call_signature


_EXPAND_CALL_LIVE_READ_COMMANDS = frozenset(
    {"configuration_status", "query", "refresh", "status"}
)
_FILE_LIVE_READ_ACTIONS = frozenset(
    {"exists", "hash", "list_dir", "read", "read_range", "search", "stat", "tree_dir"}
)
_MAX_AUTO_RETRY_ATTEMPTS = 5
_MAX_RETRY_RECOVERY_CALLS = 32
_MAX_RETRY_RECOVERY_CHARS = 120_000
_MAX_RETRY_GUIDANCE_ITEMS = 32
_MAX_RETRY_GUIDANCE_CHARS = 120_000
_NON_RETRYABLE_ERROR_CATEGORIES = frozenset(
    {
        "auth_error",
        "authorization_error",
        "capability_error",
        "context_length_exceeded",
        "gateway_protocol_error",
        "idempotency_conflict",
        "invalid_request",
        "protocol_error",
        "request_validation_error",
        "validation_error",
    }
)
_NON_RETRYABLE_ERROR_STATUSES = frozenset({400, 401, 403, 404, 409, 422})


def _metric_provider_responses(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        projected
        for value in values
        if (projected := _metric_provider_response_payload(value)) is not None
    ]

def _event_provider_response(
    event: RunEvent,
    *,
    durable: bool = False,
) -> dict[str, Any] | None:
    container = event.internal if durable else event.metadata
    value = container.get("provider_response") if isinstance(container, dict) else None
    return value if isinstance(value, dict) else None

def _tool_schema_map(schemas: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in schemas or []:
        if not isinstance(raw, dict):
            continue
        function = raw.get("function")
        function = function if isinstance(function, dict) else raw
        name = str(function.get("name") or "").strip()
        parameters = function.get("parameters") or function.get("input_schema")
        if name and isinstance(parameters, dict):
            result[name] = parameters
    return result

def _tool_result_reuse_allowed(name: str, arguments: dict[str, Any]) -> bool:
    """Return whether a successful result is stable for the rest of this run.

    Expand status and query results reflect external state that may change after an
    activate/sync/ingest call, or simply while an asynchronous server operation is
    still progressing. File reads likewise become stale after another tool or process
    mutates the filesystem. Replaying either kind of live read hides the current state.
    """

    if name == "expand_call":
        command = str(arguments.get("command") or "").strip().casefold()
        return command not in _EXPAND_CALL_LIVE_READ_COMMANDS
    if name == "file":
        action = str(arguments.get("action") or "").strip().casefold()
        return action not in _FILE_LIVE_READ_ACTIONS
    return True

def _drain_guidance(channel: Any) -> list[Any]:
    drain = getattr(channel, "drain", None)
    if callable(drain):
        return list(drain())
    if channel is None or not callable(getattr(channel, "get_nowait", None)):
        return []
    values: list[Any] = []
    while True:
        try:
            value = channel.get_nowait()
        except queue.Empty:
            break
        if normalize_guidance(value) is not None:
            values.append(value.strip() if isinstance(value, str) else value)
    return values

def _drain_or_close_guidance(channel: Any) -> list[Any]:
    drain_or_close = getattr(channel, "drain_or_close", None)
    if callable(drain_or_close):
        return list(drain_or_close())
    return _drain_guidance(channel)

def _close_guidance(channel: Any) -> None:
    close = getattr(channel, "close", None)
    if callable(close):
        close()

def _history_messages(window: dict[str, Any]) -> list[dict[str, Any]]:
    messages = window["text"].get("messages", [])
    return [
        dict(message)
        for message in messages
        if isinstance(message, dict)
        and message.get("role") in {"user", "assistant"}
        and isinstance(message.get("content"), str)
    ]

def _assistant_tool_message(
    text: str,
    calls: list[ToolCall],
    *,
    reasoning: str = "",
    native_reasoning: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": "assistant",
        "content": text or None,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in calls
        ],
    }
    if reasoning:
        message["reasoning_content"] = reasoning
    if native_reasoning:
        native_copy = copy.deepcopy(native_reasoning)
        # Provider 的 response item id 只在原响应内唯一；多轮工具循环中
        # 厂商可能重复使用 rs_1。进入新 KemoRequest 时换成本地唯一 id，
        # provider_state 本体仍保持不变。
        native_copy["id"] = f"rs_{uuid.uuid4().hex}"
        message["_kemo_reasoning"] = native_copy
    return message

def _response_reasoning_item(
    response: Any,
    *,
    streamed_content: str = "",
) -> dict[str, Any] | None:
    """取回本轮原生 reasoning item，供工具续轮完整回放。"""
    if not isinstance(response, dict):
        return None
    output = response.get("output")
    if not isinstance(output, list):
        return None
    for item in reversed(output):
        if not isinstance(item, dict) or item.get("type") != "reasoning":
            continue
        reasoning = copy.deepcopy(item)
        if streamed_content and not reasoning.get("content"):
            reasoning["content"] = streamed_content
        return reasoning
    return None

def _json_result(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)

def _guidance_detail_key(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    identifier = str(value.get("id") or "").strip()
    if identifier:
        return f"id:{identifier}"
    return _json_result(
        {
            "text": str(value.get("text") or ""),
            "uploaded_files": value.get("uploaded_files")
            if isinstance(value.get("uploaded_files"), list)
            else [],
        }
    )

def _remember_retry_guidance(state: Any, values: list[Any]) -> None:
    """Keep bounded guidance details available if this attempt must be retried."""

    if not isinstance(state, dict):
        return
    entries = state.setdefault("guidance", [])
    if not isinstance(entries, list):
        entries = []
        state["guidance"] = entries
    existing = {
        key
        for key in (_guidance_detail_key(item) for item in entries)
        if key
    }
    total_chars = sum(len(_json_result(item)) for item in entries)
    for raw in values:
        normalized = normalize_guidance(raw)
        if normalized is None:
            continue
        detail = normalized.history_detail()
        key = _guidance_detail_key(detail)
        if not key or key in existing:
            continue
        encoded = len(_json_result(detail))
        if len(entries) >= _MAX_RETRY_GUIDANCE_ITEMS:
            break
        if total_chars + encoded > _MAX_RETRY_GUIDANCE_CHARS:
            break
        entries.append(copy.deepcopy(detail))
        existing.add(key)
        total_chars += encoded

def _auto_retry_attempt_limit(request: dict[str, Any]) -> int:
    """Return the bounded run retry limit without exposing a user setting."""

    raw = request.get("_auto_retry_max_attempts", _MAX_AUTO_RETRY_ATTEMPTS)
    if isinstance(raw, bool):
        return _MAX_AUTO_RETRY_ATTEMPTS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _MAX_AUTO_RETRY_ATTEMPTS
    return max(1, min(_MAX_AUTO_RETRY_ATTEMPTS, value))

def _retry_error_is_eligible(
    event: RunEvent,
    *,
    cancel_event: threading.Event | None,
) -> bool:
    """Identify runtime failures that may be retried by the outer coordinator."""

    if event.type != "error":
        return False
    if cancel_event is not None and cancel_event.is_set():
        return False
    metadata = event.metadata or {}
    error = event.error if isinstance(event.error, dict) else {}
    if metadata.get("cancelled") is True or error.get("cancelled") is True:
        return False
    phase = str(error.get("phase") or metadata.get("phase") or "run").casefold()
    # Input validation is deterministic and must be reported immediately.
    if phase == "request":
        return False
    # Provider/adapter declarations are authoritative.  Do not let the
    # provisional-commit marker turn an explicit retryable=false into true.
    containers: list[dict[str, Any]] = [error, metadata]
    nested_details = error.get("details")
    if isinstance(nested_details, dict):
        containers.append(nested_details)
    for container in containers:
        declared = container.get("retryable")
        if isinstance(declared, bool):
            return declared
    category = str(
        error.get("category")
        or error.get("type")
        or (nested_details.get("category") if isinstance(nested_details, dict) else "")
        or ""
    ).casefold()
    if category in _NON_RETRYABLE_ERROR_CATEGORIES:
        return False
    for container in containers:
        try:
            status = int(container.get("status_code"))
        except (TypeError, ValueError):
            try:
                status = int(container.get("provider_status"))
            except (TypeError, ValueError):
                status = None
        if status in _NON_RETRYABLE_ERROR_STATUSES:
            return False
    return True

def _failure_requires_immediate_commit(error: Any) -> bool:
    """Do not leave a deterministic/non-retryable failure provisional."""

    if isinstance(error, BaseException):
        declared = getattr(error, "retryable_declared", None)
        if declared is True:
            return getattr(error, "retryable", None) is False
        category = str(
            getattr(error, "category", "")
            or getattr(error, "code", "")
            or ""
        ).casefold()
        if category in _NON_RETRYABLE_ERROR_CATEGORIES:
            return True
        for raw_status in (
            getattr(error, "status_code", None),
            getattr(error, "provider_status", None),
        ):
            try:
                if int(raw_status) in _NON_RETRYABLE_ERROR_STATUSES:
                    return True
            except (TypeError, ValueError):
                continue
        return False
    if not isinstance(error, dict):
        return False
    containers: list[dict[str, Any]] = [error]
    details = error.get("details")
    if isinstance(details, dict):
        containers.append(details)
    for container in containers:
        if isinstance(container.get("retryable"), bool):
            return container["retryable"] is False
    category = str(
        error.get("category")
        or error.get("type")
        or (details.get("category") if isinstance(details, dict) else "")
        or ""
    ).casefold()
    if category in _NON_RETRYABLE_ERROR_CATEGORIES:
        return True
    for container in containers:
        try:
            status = int(container.get("status_code"))
        except (TypeError, ValueError):
            try:
                status = int(container.get("provider_status"))
            except (TypeError, ValueError):
                status = None
        if status in _NON_RETRYABLE_ERROR_STATUSES:
            return True
    return str(error.get("phase") or "").casefold() == "request"

def _retry_reason(event: RunEvent) -> str:
    error = event.error if isinstance(event.error, dict) else {}
    raw = str(
        (event.metadata or {}).get("stop_reason")
        or error.get("code")
        or error.get("exception_type")
        or "run_error"
    ).strip()
    safe = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in raw
    )
    return safe[:80] or "run_error"

def _retrying_event(
    event: RunEvent,
    *,
    run_id: str,
    failed_attempt: int,
    next_attempt: int,
    max_attempts: int,
) -> RunEvent:
    error = event.error if isinstance(event.error, dict) else {}
    exception_type = str(error.get("exception_type") or "RuntimeError").strip()
    return RunEvent(
        type="retrying",
        content=f"运行出现问题，正在自动重试（第 {next_attempt}/{max_attempts} 次）",
        metadata={
            "run_id": run_id,
            "failed_attempt": failed_attempt,
            "next_attempt": next_attempt,
            "max_attempts": max_attempts,
            "exception_type": exception_type[:80],
            "reason": _retry_reason(event),
        },
    )

def _collect_retry_recovery(
    recovery: dict[str, dict[str, Any]],
    event: RunEvent,
) -> None:
    """Remember bounded successful tool results for a later run attempt."""

    if event.type != "tool_call_result":
        return
    result = event.result
    arguments = event.arguments
    name = str(event.tool_name or "").strip()
    if not name or not isinstance(arguments, dict) or not isinstance(result, dict):
        return
    ok = result.get("ok") is True
    if ok and not _tool_result_reuse_allowed(name, arguments):
        return
    signature = tool_call_signature(name, arguments)
    if signature in recovery:
        return
    candidate = {
        "id": str(event.tool_call_id or f"recovered_{len(recovery) + 1}"),
        "name": name,
        "arguments": copy.deepcopy(arguments),
        "result": copy.deepcopy(result),
        "replay_policy": "reuse" if ok else "blocked",
    }
    if len(recovery) >= _MAX_RETRY_RECOVERY_CALLS:
        return
    projected = sum(len(_json_result(value)) for value in recovery.values())
    if projected + len(_json_result(candidate)) > _MAX_RETRY_RECOVERY_CHARS:
        return
    recovery[signature] = candidate

def _retry_recovery_messages(
    recovery: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for item in recovery.values():
        name = str(item.get("name") or "unknown_tool")
        call_id = str(item.get("id") or f"recovered_{len(messages) + 1}")
        arguments = item.get("arguments")
        result = item.get("result")
        if not isinstance(arguments, dict) or not isinstance(result, dict):
            continue
        messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": _json_result(arguments),
                        },
                    }
                ],
            }
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "name": name,
                "content": _json_result(result),
            }
        )
    return messages

def _retry_recovery_provider_responses(
    recovery: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Represent recovered tool calls/results as bounded synthetic history output."""

    responses: list[dict[str, Any]] = []
    for index, item in enumerate(recovery.values(), start=1):
        name = str(item.get("name") or "unknown_tool")
        call_id = str(item.get("id") or f"recovered_{index}")
        arguments = item.get("arguments")
        result = item.get("result")
        if not isinstance(arguments, dict) or not isinstance(result, dict):
            continue
        responses.append(
            {
                "id": f"retry_recovery_response_{index}",
                "_iteration": 0,
                "output": [
                    {
                        "id": f"retry_recovery_call_{index}",
                        "type": "tool_call",
                        "call_id": call_id,
                        "name": name,
                        "arguments": copy.deepcopy(arguments),
                    },
                    {
                        "id": f"retry_recovery_result_{index}",
                        "type": "tool_result",
                        "call_id": call_id,
                        "name": name,
                        "is_error": result.get("ok") is False,
                        "content": [{"type": "json", "data": copy.deepcopy(result)}],
                    },
                ],
            }
        )
    return responses

def _retry_recovery_tool_records(
    recovery: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in recovery.values():
        name = str(item.get("name") or "unknown_tool")
        call_id = str(item.get("id") or "")
        arguments = item.get("arguments")
        result = item.get("result")
        if not call_id or not isinstance(arguments, dict) or not isinstance(result, dict):
            continue
        records.append(
            {
                "id": call_id,
                "name": name,
                "arguments": copy.deepcopy(arguments),
                "status": "recovered" if result.get("ok") is True else "recovery_blocked",
                "duplicate": False,
                "consecutive_identical_calls": 0,
                "result": copy.deepcopy(result),
                "iteration": 0,
                "elapsed_ms": 0,
                "recovered": True,
            }
        )
    return records

def _tool_context_diagnostics(
    tool_records: list[dict[str, Any]],
    *,
    iteration: int,
) -> list[dict[str, Any]]:
    """Describe tool payload sizes without copying result bodies into errors."""

    diagnostics: list[dict[str, Any]] = []
    for record in tool_records:
        if not isinstance(record, dict) or record.get("iteration") != iteration:
            continue
        arguments = record.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        item: dict[str, Any] = {
            "name": str(record.get("name") or ""),
            "argument_chars": len(_json_result(arguments)),
            "result_chars": len(_json_result(record.get("result"))),
            "status": str(record.get("status") or ""),
        }
        action = arguments.get("action")
        if action is not None:
            item["action"] = str(action)
        path = arguments.get("path")
        if path is not None:
            item["path"] = str(path)
        diagnostics.append(item)
    return diagnostics

def _tool_error_payload(exc: BaseException) -> dict[str, Any]:
    """Preserve safe retry classification for the next model iteration."""

    if isinstance(exc, ToolResultTooLargeError):
        return exc.error_payload()
    detail: dict[str, Any] = {
        "message": str(exc),
        "exception_type": str(
            getattr(exc, "remote_exception_type", "") or type(exc).__name__
        ),
    }
    for field in (
        "category",
        "status_code",
        "retry_after_ms",
        "attempt_count",
        "still_running",
    ):
        value = getattr(exc, field, None)
        if isinstance(value, bool) or isinstance(value, (int, float)):
            detail[field] = value
        elif isinstance(value, str) and value.strip():
            detail[field] = value.strip()[:160]
    retryable = getattr(exc, "retryable", None)
    if (
        isinstance(retryable, bool)
        and getattr(exc, "retryable_declared", True)
    ):
        detail["retryable"] = retryable
    return detail

def _tool_failure_is_retryable(
    result_payload: dict[str, Any],
    status: str,
) -> bool:
    """Allow outer retries only for explicitly/transiently retryable tool errors."""

    if status in {
        "cancelled",
        "duplicate_reused",
        "identical_call_blocked",
        "not_executed",
        "recovery_blocked",
        "result_too_large",
        "retry_reuse_blocked",
        "temporarily_unavailable",
    }:
        return False
    error = result_payload.get("error")
    if not isinstance(error, dict) or error.get("cancelled") is True:
        return False
    declared = error.get("retryable")
    if isinstance(declared, bool):
        return declared
    if error.get("still_running") is True:
        return True
    category = str(error.get("category") or error.get("type") or "").casefold()
    if category in {"connection_error", "gateway_error", "timeout", "upstream_error"}:
        return True
    for key in ("status_code", "provider_status"):
        try:
            if int(error.get(key)) in {408, 425, 429, 500, 502, 503, 504}:
                return True
        except (TypeError, ValueError):
            continue
    return False

def _ensure_fixed_content_fits(
    selection: Any,
    *,
    system_message: dict[str, Any] | None,
) -> None:
    if not selection.fixed_content_over_budget:
        if not selection.recent_content_over_budget:
            return
        raise EngineError(
            "最近完整历史超过输入预算；请调大 agents.token_limit、调低 "
            "history.recent_full_rounds，或缩小 Prompt 注入内容"
        )
    system_tokens = estimate_messages_tokens([system_message]) if system_message else 0
    raise EngineError(
        "固定提示词和工具定义超过输入预算："
        f"system_prompt≈{system_tokens} tokens，"
        f"tool_schema≈{selection.tool_schema_tokens} tokens，"
        f"input_budget={selection.input_budget} tokens；"
        "请调小 memory.temporary_injection_limits 或 prompt.char_limits"
    )

def _memory_injected_chars(bundle: PromptBundle) -> int:
    return sum(
        section.injected_chars
        for section in bundle.sections
        if section.name in {"permanent_memory", "important_memory"}
        or section.name.startswith("temporary_memory:")
    )

def _replace_primary_system_message(
    messages: list[dict[str, Any]],
    system_message: dict[str, Any] | None,
) -> None:
    if messages and messages[0].get("role") == "system":
        if system_message is None:
            messages.pop(0)
        else:
            messages[0] = system_message
        return
    if system_message is not None:
        messages.insert(0, system_message)

def _committed_failure_event(
    event: RunEvent,
    terminal_event: RunEvent,
) -> RunEvent:
    """Expose failure state without replacing the public error terminal."""

    committed = bool(terminal_event.metadata.get("committed", True))
    failure = copy.deepcopy(terminal_event.metadata.get("failure") or {})
    declared_retryable = failure.get("retryable")
    if not isinstance(declared_retryable, bool):
        declared_retryable = terminal_event.metadata.get("retryable")
    if not isinstance(declared_retryable, bool):
        declared_retryable = not committed
    event.metadata = {
        **event.metadata,
        "committed": committed,
        "retryable": declared_retryable,
        "status": "failed",
        "stop_reason": terminal_event.metadata.get(
            "stop_reason", "provider_error"
        ),
        "failure": failure,
    }
    if event.usage is None and terminal_event.usage is not None:
        event.usage = dict(terminal_event.usage)
    if not committed and failure:
        # Provisional attempts are not shown as final failures and must not
        # carry an upstream response body into the retry event stream.
        event.error = failure
    return event
