"""仅通过显式输入独立执行子代理。"""

from __future__ import annotations

import copy
import json
import importlib.util
import math
import re
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from events import RunEvent
from provider.factory import (
    ProviderCongestionError,
    create_provider,
    provider_request_slot,
)
from provider.protocol.enums import MessageRole, ResponseStatus
from provider.protocol.models import (
    JsonContent,
    KemoRequest,
    KemoResponse,
    MessageItem,
    ReasoningItem,
    ReasoningConfig,
    ToolCallItem,
    ToolDefinition,
    ToolResultItem,
    Usage,
    text_from_content,
)
from provider.protocol.diagnostics import safe_provider_message
from provider.schema import ProviderError
from agents._runtime.resources import (
    AgentPromptBundle,
    build_agent_prompt_bundle,
    build_agent_tool_registry,
)
from run.agents import AgentDefinition, AgentRegistry, discover_agents
from run.config import load_config, provider_runtime_config, resolve_agent_model
from run.tools import (
    ExecutionCapacityError,
    abandon_execution,
    attach_execution,
    release_execution,
    reserve_execution,
)
from run.extensions import resolve_reasoning_selection
from run.tools import (
    invalid_tool_name,
    response_invalid_tool_arguments_error,
    system_prompt_with_tool_argument_repair,
    validate_tool_call_batch,
)
from run.tools import (
    ConsecutiveIdenticalToolCallTracker,
    ConsecutiveToolFailureTracker,
    ToolResultTooLargeError,
    ToolRegistry,
    execute_tool,
    tool_call_signature,
)


_AGENT_TIMEOUT_CLEANUP_GRACE = 1.0
_AGENT_CANCEL_CLEANUP_GRACE = 0.1
_STRUCTURED_OUTPUT_TOOL_NAME = "submit_structured_output"
_MAX_AGENT_RETRY_ATTEMPTS = 5
_MAX_AGENT_RETRY_RECOVERY_CALLS = 32
_MAX_AGENT_RETRY_RECOVERY_CHARS = 120_000
_AGENT_RETRYABLE_CATEGORIES = frozenset(
    {
        "connection_error",
        "gateway_error",
        "provider_error",
        "timeout",
        "upstream_error",
    }
)
_AGENT_NON_RETRYABLE_CATEGORIES = frozenset(
    {
        "auth_error",
        "authorization_error",
        "asset_error",
        "asset_integrity_error",
        "capability_error",
        "context_length_exceeded",
        "gateway_protocol_error",
        "idempotency_conflict",
        "invalid_request",
        "protocol_error",
        "request_too_large",
        "request_validation_error",
        "result_too_large",
        "execution_capacity",
        "validation_error",
    }
)
_AGENT_NON_RETRYABLE_STATUSES = frozenset({400, 401, 403, 404, 409, 422})
_SERIAL_EXECUTION_LOCKS_GUARD = threading.RLock()
_SERIAL_EXECUTION_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_SERIAL_EXECUTION_LOCAL = threading.local()


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

    if name == "expand_call":
        command = str(arguments.get("command") or "").strip().casefold()
        return command not in {"configuration_status", "query", "refresh", "status"}
    if name == "file":
        action = str(arguments.get("action") or "").strip().casefold()
        return action not in {
            "exists",
            "hash",
            "list_dir",
            "read",
            "read_range",
            "search",
            "stat",
            "tree_dir",
        }
    return True


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
    error_type = type(error).__name__
    if error_type in {"AgentCancelledError", "ToolCancelledError"}:
        return False
    if error_type == "AgentInputError":
        return False
    declared = getattr(error, "retryable_declared", None)
    # Structured/JSON output failures are commonly caused by transient
    # truncation or provider formatting drift; let the bounded retry loop
    # repair them just like other provider response failures.
    if error_type == "AgentOutputError":
        return bool(getattr(error, "retryable", True)) if declared is True else True
    if error_type == "AgentTimeoutError":
        return bool(getattr(error, "process_terminated", False))
    if declared is True:
        return bool(getattr(error, "retryable", False))
    if declared is not False:
        explicit_retryable = getattr(error, "retryable", None)
        if isinstance(explicit_retryable, bool):
            return explicit_retryable
    if bool(getattr(error, "still_running", False)):
        return False
    category = str(
        getattr(error, "category", "")
        or getattr(error, "code", "")
        or ""
    ).casefold()
    if category in _AGENT_NON_RETRYABLE_CATEGORIES:
        return False
    for raw_status in (
        getattr(error, "status_code", None),
        getattr(error, "provider_status", None),
    ):
        try:
            if int(raw_status) in _AGENT_NON_RETRYABLE_STATUSES:
                return False
        except (TypeError, ValueError):
            continue
    if category in _AGENT_RETRYABLE_CATEGORIES:
        return True
    if error_type in {
        "AgentProviderError",
        "ProviderError",
        "ProviderCongestionError",
    }:
        return True
    return False


def _agent_retry_delay_seconds(error: BaseException, failed_attempt: int) -> float:
    raw = getattr(error, "retry_after_ms", None)
    try:
        milliseconds = int(raw)
    except (TypeError, ValueError):
        milliseconds = -1
    if milliseconds >= 0:
        return min(120.0, max(0.25, milliseconds / 1000.0))
    return min(2.0, 0.25 * (2 ** max(0, failed_attempt - 1)))


def _agent_retry_reason(error: BaseException) -> str:
    raw = str(
        getattr(error, "category", "")
        or getattr(error, "code", "")
        or type(error).__name__
    ).strip()
    safe = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in raw
    )
    return safe[:80] or "agent_error"


def _mark_agent_retry_exhausted(
    error: BaseException,
    *,
    attempts: int,
    max_attempts: int,
) -> None:
    setattr(error, "retry_exhausted", True)
    setattr(error, "retry_attempts", attempts)
    setattr(error, "retry_max_attempts", max_attempts)
    setattr(error, "retryable_declared", True)
    setattr(error, "retryable", False)


def _run_agent_with_retries(
    function: Callable[[Any, dict[str, Any]], "AgentRunResult"],
    context: Any,
    input_data: dict[str, Any],
    *,
    max_attempts: int,
) -> "AgentRunResult":
    state = _AgentRetryState()
    for attempt in range(1, max_attempts + 1):
        context.attempt = attempt
        context.max_attempts = max_attempts
        context.retry_state = state
        try:
            result = function(context, input_data)
            if not isinstance(result, AgentRunResult):
                raise AgentRunError(
                    f"子代理 {context.definition.name} executor 必须返回 AgentRunResult"
                )
            result.metadata = {
                **result.metadata,
                "retry_attempts": attempt,
                "retry_max_attempts": max_attempts,
            }
            return result
        except (KeyboardInterrupt, GeneratorExit):
            raise
        except BaseException as exc:
            if isinstance(exc, AgentCancelledError):
                raise
            if context.cancel_event.is_set():
                raise AgentCancelledError(
                    f"子代理 {context.definition.name} 已取消"
                ) from exc
            if not _agent_error_is_retryable(
                exc,
                cancel_event=context.cancel_event,
            ):
                raise
            if attempt >= max_attempts:
                _mark_agent_retry_exhausted(
                    exc,
                    attempts=attempt,
                    max_attempts=max_attempts,
                )
                raise
            _event(
                context.event_callback,
                agent=context.definition.name,
                status="retrying",
                task_id=context.task_id,
                detail={
                    "failed_attempt": attempt,
                    "next_attempt": attempt + 1,
                    "max_attempts": max_attempts,
                    "exception_type": type(exc).__name__,
                    "reason": _agent_retry_reason(exc),
                },
            )
            if context.cancel_event.wait(_agent_retry_delay_seconds(exc, attempt)):
                raise AgentCancelledError(
                    f"子代理 {context.definition.name} 重试等待期间已取消"
                ) from exc
    raise AssertionError("子代理重试循环异常退出")


def _response_items_for_next_request(output: list[Any]) -> list[Any]:
    """Copy Provider output into local history with request-unique IDs.

    Provider item and tool-call IDs are only guaranteed to be unique inside a
    single response.  A subagent request carries several response iterations,
    so every local item and tool call needs a fresh identity.  Tool results
    created by the runner use the normalized call IDs and remain linked.
    """

    call_id_map: dict[str, str] = {}
    normalized_call_ids: dict[int, str] = {}
    for item in output:
        if not isinstance(item, ToolCallItem):
            continue
        call_id = f"call_{uuid.uuid4().hex}"
        normalized_call_ids[id(item)] = call_id
        call_id_map.setdefault(item.call_id, call_id)

    normalized: list[Any] = []
    for item in output:
        prefix = (
            "rs"
            if isinstance(item, ReasoningItem)
            else (
                "call"
                if isinstance(item, ToolCallItem)
                else (
                    "result"
                    if isinstance(item, ToolResultItem)
                    else "msg" if isinstance(item, MessageItem) else "item"
                )
            )
        )
        updates = {"id": f"{prefix}_{uuid.uuid4().hex}"}
        if isinstance(item, ToolCallItem):
            updates["call_id"] = normalized_call_ids[id(item)]
        elif isinstance(item, ToolResultItem) and item.call_id in call_id_map:
            updates["call_id"] = call_id_map[item.call_id]
        item = item.model_copy(update=updates)
        normalized.append(item)
    return normalized


def _serial_execution_key(root: Path, user: str) -> tuple[str, str]:
    return str(root.resolve()).casefold(), user


def _serial_execution_lock(key: tuple[str, str]) -> threading.Lock:
    """Return the process-local write lock shared by every runner for one user."""

    with _SERIAL_EXECUTION_LOCKS_GUARD:
        return _SERIAL_EXECUTION_LOCKS.setdefault(key, threading.Lock())


def _owned_serial_execution_keys() -> set[tuple[str, str]]:
    keys = getattr(_SERIAL_EXECUTION_LOCAL, "keys", None)
    if keys is None:
        keys = set()
        _SERIAL_EXECUTION_LOCAL.keys = keys
    return keys


def _execute_agent(
    function: Callable[[Any, dict[str, Any]], "AgentRunResult"],
    context: Any,
    input_data: dict[str, Any],
    *,
    serial_lock: threading.Lock | None,
    serial_key: tuple[str, str] | None,
    max_attempts: int,
) -> "AgentRunResult":
    def execute() -> AgentRunResult:
        owned = _owned_serial_execution_keys()
        if serial_key is not None:
            owned.add(serial_key)
        try:
            return _run_agent_with_retries(
                function,
                context,
                input_data,
                max_attempts=max_attempts,
            )
        finally:
            if serial_key is not None:
                owned.discard(serial_key)

    if serial_lock is not None:
        with serial_lock:
            if context.cancel_event.is_set():
                raise AgentCancelledError(
                    f"子代理 {context.definition.name} 在等待用户级串行执行时已取消"
                )
            return execute()
    if serial_key is not None:
        if context.cancel_event.is_set():
            raise AgentCancelledError(
                f"子代理 {context.definition.name} 在等待用户级串行执行时已取消"
            )
        return execute()
    return _run_agent_with_retries(
        function,
        context,
        input_data,
        max_attempts=max_attempts,
    )


class AgentRunError(RuntimeError):
    pass


class AgentInputError(AgentRunError):
    pass


class AgentOutputError(AgentRunError):
    def __init__(self, message: str, *, raw_text: str = "") -> None:
        super().__init__(message)
        self.raw_text = str(raw_text or "")


class AgentProviderError(AgentRunError):
    """A Provider response failure with safe retry classification metadata."""

    def __init__(
        self,
        message: str,
        *,
        category: str = "provider_error",
        code: str = "",
        status_code: int | None = None,
        retryable: bool | None = None,
        retry_after_ms: int | None = None,
    ) -> None:
        super().__init__(message)
        self.category = str(category or "provider_error")[:160]
        self.code = str(code or "")[:160]
        self.status_code = status_code
        self.retryable_declared = isinstance(retryable, bool)
        self.retryable = bool(retryable) if self.retryable_declared else False
        self.retry_after_ms = retry_after_ms


class AgentToolRetryError(AgentRunError):
    """A transient tool failure that should restart the subagent attempt."""

    def __init__(
        self,
        tool_name: str,
        *,
        status_code: int | None = None,
        retry_after_ms: int | None = None,
    ) -> None:
        super().__init__("子代理工具调用出现可恢复错误，正在准备自动重试")
        self.tool_name = str(tool_name or "unknown_tool")[:160]
        self.category = "tool_error"
        self.status_code = status_code
        self.retryable_declared = True
        self.retryable = True
        self.retry_after_ms = retry_after_ms


class AgentTimeoutError(AgentRunError):
    def __init__(
        self,
        message: str,
        *,
        process_terminated: bool = False,
        completion_future: Any = None,
    ) -> None:
        super().__init__(message)
        self.process_terminated = bool(process_terminated)
        self.completion_future = completion_future


class AgentCancelledError(AgentRunError):
    def __init__(
        self,
        message: str,
        *,
        process_terminated: bool = True,
        completion_future: Any = None,
    ) -> None:
        super().__init__(message)
        self.process_terminated = bool(process_terminated)
        self.completion_future = completion_future


@dataclass(slots=True)
class AgentRunResult:
    agent: str
    data: dict[str, Any]
    raw_text: str
    usage: dict[str, Any]
    model: str
    metadata: dict[str, Any] = field(default_factory=dict)


def _event(
    callback: Callable[[RunEvent], None] | None,
    *,
    agent: str,
    status: str,
    task_id: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    if callback is None:
        return
    metadata = {"phase": "subagent", "agent": agent, "status": status}
    if task_id:
        metadata["task_id"] = task_id
    if detail:
        metadata.update(detail)
    callback(RunEvent(type="reasoning_delta", metadata=metadata))


def _type_matches(value: Any, expected: Any) -> bool:
    if isinstance(expected, list):
        return any(_type_matches(value, item) for item in expected)
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, True)


def validate_json_schema(
    value: Any, schema: dict[str, Any], *, location: str = "$"
) -> None:
    one_of = schema.get("oneOf")
    if isinstance(one_of, list):
        matches = 0
        for candidate in one_of:
            if not isinstance(candidate, dict):
                continue
            try:
                validate_json_schema(value, candidate, location=location)
            except AgentInputError:
                continue
            matches += 1
        if matches != 1:
            raise AgentInputError(f"{location} 必须且只能匹配 oneOf 中的一个结构")
        return
    if "const" in schema and value != schema["const"]:
        raise AgentInputError(f"{location} 必须等于 {schema['const']!r}")
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        raise AgentInputError(f"{location} 不在允许值中")
    expected = schema.get("type")
    if expected is not None and not _type_matches(value, expected):
        raise AgentInputError(f"{location} 类型不符合 {expected}")
    if isinstance(value, str):
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if isinstance(minimum, int) and len(value) < minimum:
            raise AgentInputError(f"{location} 长度不能小于 {minimum}")
        if isinstance(maximum, int) and len(value) > maximum:
            raise AgentInputError(f"{location} 长度不能大于 {maximum}")
    elif isinstance(value, dict):
        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        missing = [name for name in required if name not in value]
        if missing:
            raise AgentInputError(f"{location} 缺少字段：{', '.join(missing)}")
        if schema.get("additionalProperties") is False:
            extras = [name for name in value if name not in properties]
            if extras:
                raise AgentInputError(f"{location} 包含未知字段：{', '.join(extras)}")
        for name, item in value.items():
            child = properties.get(name)
            if isinstance(child, dict):
                validate_json_schema(item, child, location=f"{location}.{name}")
    elif isinstance(value, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(value) < minimum:
            raise AgentInputError(f"{location} 元素数量不能小于 {minimum}")
        if isinstance(maximum, int) and len(value) > maximum:
            raise AgentInputError(f"{location} 元素数量不能大于 {maximum}")
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                validate_json_schema(
                    item,
                    schema["items"],
                    location=f"{location}[{index}]",
                )


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as original_error:
        decoder = json.JSONDecoder()
        candidate_starts = [match.start() for match in re.finditer(r"\{", cleaned)]
        candidate_errors: list[json.JSONDecodeError] = []
        for start in candidate_starts:
            try:
                candidate, _ = decoder.raw_decode(cleaned, start)
            except json.JSONDecodeError as candidate_error:
                candidate_errors.append(candidate_error)
                continue
            if isinstance(candidate, dict):
                return candidate
        if not candidate_starts:
            raise AgentOutputError("子代理响应中没有 JSON 对象") from None
        diagnostic_error = (
            max(candidate_errors, key=lambda error: error.pos)
            if candidate_errors
            else original_error
        )
        candidate = cleaned[candidate_starts[0] :].rstrip()
        likely_truncated = (
            not candidate.endswith("}")
            or "unterminated string" in diagnostic_error.msg.casefold()
            or diagnostic_error.pos >= max(0, len(candidate) - 2)
        )
        if likely_truncated:
            raise AgentOutputError(
                f"子代理 JSON 疑似被截断：{diagnostic_error}"
            ) from diagnostic_error
        raise AgentOutputError(
            f"子代理 JSON 无效：{diagnostic_error}"
        ) from diagnostic_error
    if not isinstance(value, dict):
        raise AgentOutputError("子代理输出必须是 JSON 对象")
    return value


def resolve_agent_provider_config(
    config: dict[str, Any],
    definition: AgentDefinition,
    *,
    model_override: str | None = None,
) -> dict[str, Any]:
    runtime = provider_runtime_config(config)
    runtime["model"] = resolve_agent_model(
        config,
        definition.model_profile,
        model_override=model_override,
    )
    runtime["model_profile"] = definition.model_profile
    return runtime


@dataclass(slots=True)
class AgentExecutionContext:
    runner: "AgentRunner"
    definition: AgentDefinition
    prompt_bundle: AgentPromptBundle
    tool_registry: ToolRegistry
    cancel_event: threading.Event
    model_override: str | None
    max_tokens: int | None
    task_id: str
    structured_output_tool: bool
    event_callback: Callable[[RunEvent], None] | None = field(default=None, repr=False)
    attempt: int = 1
    max_attempts: int = 1
    retry_state: _AgentRetryState | None = field(default=None, repr=False)

    def run_model(self, input_data: dict[str, Any]) -> AgentRunResult:
        return self.runner._run_model(
            self,
            input_data,
            retry_state=self.retry_state,
            attempt=self.attempt,
            max_attempts=self.max_attempts,
        )


def _load_executor(
    definition: AgentDefinition,
) -> Callable[[AgentExecutionContext, dict[str, Any]], Any]:
    if definition.executor == "builtin:llm":
        return lambda context, input_data: context.run_model(input_data)
    file_name, _, function_name = definition.executor.partition(":")
    path = definition.directory / file_name
    module_name = f"kemo_agent_executor_{definition.name}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise AgentRunError(f"无法加载子代理执行模块：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    function = getattr(module, function_name, None)
    if not callable(function):
        raise AgentRunError(f"子代理执行入口不可调用：{definition.executor}")
    return function


class AgentRunner:
    def __init__(
        self,
        root: Path,
        user: str,
        *,
        config: dict[str, Any] | None = None,
        registry: AgentRegistry | None = None,
        provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
    ) -> None:
        self.root = root.resolve()
        self.user = user
        self.config = config or load_config(user, self.root)
        self._fixed_registry = registry
        self.registry = registry or discover_agents(self.root, self.user)
        self.provider_factory = provider_factory

    def refresh_registry(self) -> AgentRegistry:
        if self._fixed_registry is None:
            self.registry = discover_agents(self.root, self.user)
        return self.registry

    @staticmethod
    def _usage_dict(usage: Usage) -> dict[str, Any]:
        return {
            "prompt_tokens": int(usage.input_tokens or 0),
            "completion_tokens": int(usage.output_tokens or 0),
            "total_tokens": int(
                usage.total_tokens
                if usage.total_tokens is not None
                else (usage.input_tokens or 0) + (usage.output_tokens or 0)
            ),
            "estimated": not usage.measurement.exact,
            "source": str(usage.measurement.mode),
            "cached_tokens": int(usage.cached_input_tokens or 0),
            "reasoning_tokens": int(usage.reasoning_tokens or 0),
        }

    @staticmethod
    def _merge_usage(total: dict[str, Any], usage: dict[str, Any]) -> None:
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            total[key] = int(total.get(key, 0)) + int(usage.get(key, 0))
        total["estimated"] = bool(
            total.get("estimated", False) or usage.get("estimated", False)
        )

    @staticmethod
    def _tool_definitions(schemas: list[dict[str, Any]]) -> list[ToolDefinition]:
        definitions: list[ToolDefinition] = []
        for raw in schemas:
            function = (
                raw.get("function") if isinstance(raw.get("function"), dict) else raw
            )
            definitions.append(
                ToolDefinition(
                    name=str(function.get("name") or ""),
                    description=str(function.get("description") or ""),
                    parameters=dict(
                        function.get("parameters")
                        or function.get("input_schema")
                        or {"type": "object"}
                    ),
                    strict=bool(function.get("strict", True)),
                )
            )
        return definitions

    def _run_model(
        self,
        context: AgentExecutionContext,
        input_data: dict[str, Any],
        *,
        retry_state: _AgentRetryState | None = None,
        attempt: int = 1,
        max_attempts: int = 1,
    ) -> AgentRunResult:
        retry_state = retry_state or _AgentRetryState()
        definition = context.definition
        runtime = resolve_agent_provider_config(
            self.config,
            definition,
            model_override=context.model_override,
        )
        provider = self.provider_factory(runtime)
        reasoning_selection = resolve_reasoning_selection(
            self.config,
            runtime,
            provider,
            model=runtime["model"],
            cancel_event=context.cancel_event,
        )
        system = (
            context.prompt_bundle.text
            + "\n\n[output_schema]\n"
            + json.dumps(definition.output_schema, ensure_ascii=False, sort_keys=True)
        )
        if context.structured_output_tool:
            system += (
                "\n\n[structured_output_transport]\n"
                f"必须调用 {_STRUCTURED_OUTPUT_TOOL_NAME} 一次提交最终结果；"
                "工具参数必须符合 output_schema。不要把最终结果作为普通文本输出。"
            )
        items: list[Any] = [
            MessageItem.text(
                MessageRole.USER,
                json.dumps(input_data, ensure_ascii=False, sort_keys=True),
                item_id=f"msg_{uuid.uuid4().hex}",
            )
        ]
        items.extend(_agent_recovery_items(retry_state.recovery))
        total_usage: dict[str, Any] = copy.deepcopy(retry_state.usage)
        tool_records: list[dict[str, Any]] = []
        final_text = ""
        final_data: dict[str, Any] | None = None
        final_model = runtime["model"]
        response_ids: list[str] = list(retry_state.response_ids)
        parent_request_id: str | None = None
        tool_config = self.config.get("tools") or {}
        raw_global_tool_calls = tool_config.get("max_iterations", 80)
        if (
            isinstance(raw_global_tool_calls, bool)
            or not isinstance(raw_global_tool_calls, int)
            or raw_global_tool_calls < 1
        ):
            raise AgentRunError("tools.max_iterations 必须是正整数")
        max_tool_calls = min(
            raw_global_tool_calls,
            definition.capabilities.max_tool_iterations,
        )
        raw_invalid_tool_arguments_retries = tool_config.get(
            "invalid_tool_arguments_retries", 2
        )
        if (
            isinstance(raw_invalid_tool_arguments_retries, bool)
            or not isinstance(raw_invalid_tool_arguments_retries, int)
            or raw_invalid_tool_arguments_retries < 0
        ):
            raise AgentRunError(
                "tools.invalid_tool_arguments_retries 必须是大于等于 0 的整数"
            )
        invalid_tool_arguments_retry_limit = raw_invalid_tool_arguments_retries
        max_provider_iterations = max_tool_calls + 1
        processed_tool_calls = 0
        tool_timeout = float(tool_config.get("timeout", 240))
        agent_timeout = (self.config.get("agent_runtime") or {}).get(
            "default_timeout", 600
        )
        raw_identical_call_limit = tool_config.get(
            "consecutive_identical_call_limit", 8
        )
        if (
            isinstance(raw_identical_call_limit, bool)
            or not isinstance(raw_identical_call_limit, int)
            or raw_identical_call_limit < 1
        ):
            raise AgentRunError("tools.consecutive_identical_call_limit 必须是正整数")
        identical_call_limit = raw_identical_call_limit
        raw_failure_limit = (self.config.get("history") or {}).get(
            "consecutive_tool_fail_limit", 5
        )
        if (
            isinstance(raw_failure_limit, bool)
            or not isinstance(raw_failure_limit, int)
            or raw_failure_limit < 1
        ):
            raise AgentRunError("history.consecutive_tool_fail_limit 必须是正整数")
        failure_limit = raw_failure_limit
        failures = ConsecutiveToolFailureTracker(failure_limit)
        identical_calls = ConsecutiveIdenticalToolCallTracker(identical_call_limit)
        seen_calls: dict[str, dict[str, Any]] = {
            signature: copy.deepcopy(value["result"])
            for signature, value in retry_state.recovery.items()
            if value.get("replay_policy") == "reuse"
            and isinstance(value.get("result"), dict)
        }
        blocked_recovery: dict[str, dict[str, Any]] = {
            signature: value
            for signature, value in retry_state.recovery.items()
            if value.get("replay_policy") == "blocked"
        }
        tool_argument_retry_count = 0
        for iteration in range(1, max_provider_iterations + 1):
            if context.cancel_event.is_set():
                raise AgentCancelledError(f"子代理 {definition.name} 已取消")
            tool_schemas = context.tool_registry.schemas(exclude=failures.unavailable)
            tool_definitions = self._tool_definitions(tool_schemas)
            if context.structured_output_tool:
                if any(
                    tool.name == _STRUCTURED_OUTPUT_TOOL_NAME
                    for tool in tool_definitions
                ):
                    raise AgentRunError(
                        f"子代理工具名与内部结构化输出工具冲突："
                        f"{_STRUCTURED_OUTPUT_TOOL_NAME}"
                    )
                tool_definitions.append(
                    ToolDefinition(
                        name=_STRUCTURED_OUTPUT_TOOL_NAME,
                        description="提交符合输出 Schema 的最终结构化结果。",
                        parameters=definition.output_schema,
                        strict=True,
                    )
                )
            invalid_tool_arguments_retries = 0
            repair_tool_name = ""
            while True:
                request_id = f"req_{uuid.uuid4().hex}"
                request_system = (
                    system_prompt_with_tool_argument_repair(
                        system,
                        tool_name=repair_tool_name,
                        retry_number=invalid_tool_arguments_retries,
                    )
                    if invalid_tool_arguments_retries
                    else system
                )
                try:
                    with provider_request_slot(
                        self.config,
                        cancel_event=context.cancel_event,
                    ):
                        response = provider.create(
                            KemoRequest(
                                request_id=request_id,
                                parent_request_id=parent_request_id,
                                attempt=attempt + invalid_tool_arguments_retries,
                                model=runtime["model"],
                                stream=False,
                                system_prompt=request_system,
                                input=list(items),
                                tools=tool_definitions,
                                generation={"max_output_tokens": context.max_tokens},
                                reasoning=(
                                    ReasoningConfig(
                                        enabled=True,
                                        effort=reasoning_selection.effort,
                                        return_mode="content",
                                        context="auto",
                                    )
                                    if reasoning_selection.enabled
                                    and reasoning_selection.effort
                                    else None
                                ),
                                provider_options=(
                                    {"reasoning_effort": reasoning_selection.effort}
                                    if reasoning_selection.enabled
                                    and reasoning_selection.effort
                                    else {}
                                ),
                                metadata={
                                    "capability": "conversation",
                                    "user": self.user,
                                    "source": "subagent",
                                    "agent": definition.name,
                                    "task_id": context.task_id,
                                    "iteration": iteration,
                                    "tool_argument_retry": (
                                        invalid_tool_arguments_retries
                                    ),
                                    "retry_attempt": attempt,
                                },
                            )
                        )
                except ProviderCongestionError as exc:
                    if context.cancel_event.is_set():
                        raise AgentCancelledError(
                            f"子代理 {definition.name} 已取消"
                        ) from exc
                    raise
                if not isinstance(response, KemoResponse):
                    raise AgentRunError("Provider create() 必须返回 KemoResponse")
                response_ids.append(response.id)
                retry_state.response_ids = list(response_ids)
                parent_request_id = parent_request_id or request_id
                self._merge_usage(total_usage, self._usage_dict(response.usage))
                retry_state.usage = copy.deepcopy(total_usage)
                final_model = response.model or runtime["model"]
                invalid_error = response_invalid_tool_arguments_error(response)
                if invalid_error is None:
                    declared_tool_schemas: dict[str, dict[str, Any]] = {}
                    for raw_schema in context.tool_registry.schemas():
                        function = (
                            raw_schema.get("function")
                            if isinstance(raw_schema.get("function"), dict)
                            else raw_schema
                        )
                        name = str(function.get("name") or "").strip()
                        parameters = function.get("parameters") or function.get(
                            "input_schema"
                        )
                        if name and isinstance(parameters, dict):
                            declared_tool_schemas[name] = parameters
                    for tool in tool_definitions:
                        if isinstance(tool.parameters, dict):
                            declared_tool_schemas[tool.name] = tool.parameters
                    invalid_error = validate_tool_call_batch(
                        [
                            item
                            for item in response.output
                            if isinstance(item, ToolCallItem)
                        ],
                        declared_tool_schemas,
                    )
                if invalid_error is None:
                    break
                if (
                    invalid_tool_arguments_retries
                    >= invalid_tool_arguments_retry_limit
                ):
                    raise AgentRunError(
                        f"{invalid_error['message']}；已重试 "
                        f"{invalid_tool_arguments_retries}/"
                        f"{invalid_tool_arguments_retry_limit} 次"
                    )
                invalid_tool_arguments_retries += 1
                tool_argument_retry_count += 1
                repair_tool_name = invalid_tool_name(invalid_error)
            if response.status not in {
                ResponseStatus.COMPLETED,
                ResponseStatus.REQUIRES_ACTION,
            }:
                if response.error is not None:
                    provider_error = response.error
                    message = safe_provider_message(
                        provider_error.message,
                        "Provider 响应失败",
                    )
                    retryable: bool | None = None
                    fields_set = getattr(provider_error, "model_fields_set", set())
                    if "retryable" in fields_set:
                        retryable = provider_error.retryable
                    elif isinstance(provider_error.details, dict):
                        declared = provider_error.details.get("retryable")
                        if isinstance(declared, bool):
                            retryable = declared
                    raise AgentProviderError(
                        f"子代理 Provider 响应失败：{message}",
                        category=provider_error.type or "provider_error",
                        code=provider_error.code,
                        status_code=provider_error.provider_status,
                        retryable=retryable,
                        retry_after_ms=provider_error.retry_after_ms,
                    )
                raise AgentProviderError(
                    f"子代理 Provider 响应失败：{response.status}",
                    category=(
                        "provider_incomplete"
                        if response.status == ResponseStatus.INCOMPLETE
                        else "provider_error"
                    ),
                    retryable=response.status != ResponseStatus.CANCELLED,
                )
            normalized_output = _response_items_for_next_request(response.output)
            calls = [
                item for item in normalized_output if isinstance(item, ToolCallItem)
            ]
            messages = [
                item for item in normalized_output if isinstance(item, MessageItem)
            ]
            items.extend(normalized_output)
            structured_calls = [
                call for call in calls if call.name == _STRUCTURED_OUTPUT_TOOL_NAME
            ]
            if structured_calls:
                raw_structured = json.dumps(
                    structured_calls[0].arguments,
                    ensure_ascii=False,
                    sort_keys=True,
                )
                if len(structured_calls) != 1 or len(calls) != 1:
                    raise AgentOutputError(
                        "子代理结构化输出必须且只能调用一次提交工具",
                        raw_text=raw_structured,
                    )
                try:
                    validate_json_schema(
                        structured_calls[0].arguments,
                        definition.output_schema,
                    )
                except AgentInputError as exc:
                    raise AgentOutputError(
                        str(exc),
                        raw_text=raw_structured,
                    ) from exc
                final_data = dict(structured_calls[0].arguments)
                final_text = raw_structured
                tool_records.append(
                    {
                        "id": structured_calls[0].call_id,
                        "name": _STRUCTURED_OUTPUT_TOOL_NAME,
                        "arguments": structured_calls[0].arguments,
                        "status": "structured_output",
                        "iteration": iteration,
                    }
                )
                break
            if not calls:
                final_text = "".join(
                    text_from_content(item.content) for item in messages
                )
                break
            retryable_tool_failure: AgentToolRetryError | None = None
            for call in calls:
                if processed_tool_calls >= max_tool_calls:
                    raise AgentRunError(
                        f"子代理 {definition.name} 已达到最大工具调用次数 {max_tool_calls}"
                    )
                processed_tool_calls += 1
                signature = tool_call_signature(call.name, call.arguments)
                reuse_allowed = _agent_tool_result_reuse_allowed(
                    call.name,
                    call.arguments,
                )
                duplicate = False
                identical_call_count = identical_calls.record(call.name, call.arguments)
                if identical_calls.is_blocked(identical_call_count):
                    payload = {
                        "ok": False,
                        "error": {
                            "message": (
                                f"工具 {call.name} 使用完全相同参数连续调用已达到"
                                f"上限 {identical_call_limit} 次"
                            ),
                            "exception_type": (
                                "ConsecutiveIdenticalToolCallLimitExceeded"
                            ),
                            "limit": identical_call_limit,
                            "consecutive_identical_calls": identical_call_count,
                            "instruction": (
                                "请修改参数、改用其他工具或根据已有结果继续任务"
                            ),
                        },
                    }
                    status = "identical_call_blocked"
                elif failures.is_unavailable(call.name):
                    payload = {
                        "ok": False,
                        "error": {
                            "message": (
                                f"工具 {call.name} 已连续失败 {failure_limit} 次，"
                                "本轮暂时不可用；请更换工具或调整方案"
                            ),
                            "exception_type": "ToolTemporarilyUnavailable",
                            "consecutive_failures": failure_limit,
                            "temporarily_unavailable": True,
                        },
                    }
                    status = "temporarily_unavailable"
                else:
                    blocked_result = retry_state.recovery.get(signature)
                    if (
                        not isinstance(blocked_result, dict)
                        or blocked_result.get("replay_policy") != "blocked"
                    ):
                        blocked_result = None
                    duplicate = reuse_allowed and signature in seen_calls
                    if blocked_result is not None:
                        payload = copy.deepcopy(blocked_result.get("result") or {})
                        status = "retry_reuse_blocked"
                        duplicate = True
                    elif duplicate:
                        payload = copy.deepcopy(seen_calls[signature])
                        status = "duplicate_reused"
                    else:
                        try:
                            tool = context.tool_registry.get(call.name)
                            value = execute_tool(
                                tool,
                                call.arguments,
                                context={
                                    "root": str(self.root),
                                    "user": self.user,
                                    "caller": "subagent",
                                    "agent": definition.name,
                                    "task_id": context.task_id,
                                    "agent_trigger": input_data.get("trigger"),
                                    "tool_timeout": tool_timeout,
                                    "agent_timeout": agent_timeout,
                                    "knowledge_scopes": list(
                                        definition.capabilities.knowledge_scopes
                                    ),
                                },
                                timeout=tool_timeout,
                                cancel_event=context.cancel_event,
                            )
                            payload = {"ok": True, "result": value}
                            status = "completed"
                        except ToolResultTooLargeError as exc:
                            payload = {"ok": False, "error": exc.error_payload()}
                            status = "result_too_large"
                        except Exception as exc:
                            error = {
                                "message": safe_provider_message(
                                    str(exc),
                                    "工具调用失败",
                                ),
                                "exception_type": str(
                                    getattr(exc, "remote_exception_type", "")
                                    or type(exc).__name__
                                ),
                            }
                            for field in ("category", "retry_after_ms", "still_running"):
                                value = getattr(exc, field, None)
                                if isinstance(value, (bool, int, float)):
                                    error[field] = value
                                elif isinstance(value, str) and value.strip():
                                    error[field] = value.strip()[:160]
                            retryable = getattr(exc, "retryable", None)
                            if isinstance(retryable, bool) and getattr(
                                exc,
                                "retryable_declared",
                                True,
                            ):
                                error["retryable"] = retryable
                            payload = {
                                "ok": False,
                                "error": error,
                            }
                            if bool(getattr(exc, "still_running", False)):
                                failures.unavailable.add(call.name)
                                status = "timed_out_running"
                            else:
                                status = "failed"
                        if payload.get("ok") is True:
                            if reuse_allowed:
                                seen_calls[signature] = copy.deepcopy(payload)
                        else:
                            seen_calls.pop(signature, None)
                        failure_count = failures.record(
                            call.name,
                            succeeded=(
                                bool(payload.get("ok")) or status == "result_too_large"
                            ),
                        )
                        if failure_count >= failure_limit:
                            payload["error"].update(
                                {
                                    "consecutive_failures": failure_count,
                                    "temporarily_unavailable": True,
                                    "instruction": "请更换工具或调整方案，不要继续重试该工具",
                                }
                            )
                tool_records.append(
                    {
                        "id": call.call_id,
                        "name": call.name,
                        "arguments": call.arguments,
                        "status": status,
                        "duplicate": duplicate,
                        "result": payload,
                        "iteration": iteration,
                        "consecutive_identical_calls": identical_call_count,
                    }
                )
                _record_agent_recovery(retry_state, call, payload)
                items.append(
                    ToolResultItem(
                        id=f"result_{uuid.uuid4().hex}",
                        call_id=call.call_id,
                        name=call.name,
                        is_error=not bool(payload.get("ok")),
                        content=[JsonContent(data=payload)],
                    )
                )
                if (
                    attempt < max_attempts
                    and retryable_tool_failure is None
                    and _agent_tool_failure_is_retryable(payload, status)
                ):
                    error = payload.get("error")
                    if not isinstance(error, dict):
                        error = {}
                    retryable_tool_failure = AgentToolRetryError(
                        call.name,
                        status_code=_safe_int(error.get("status_code")),
                        retry_after_ms=_safe_int(error.get("retry_after_ms")),
                    )
            if retryable_tool_failure is not None:
                raise retryable_tool_failure
        else:
            raise AgentRunError(f"子代理 {definition.name} 未生成最终输出")
        if final_data is None:
            try:
                data = _parse_json_object(final_text)
                validate_json_schema(data, definition.output_schema)
            except AgentOutputError as exc:
                raise AgentOutputError(str(exc), raw_text=final_text) from exc
            except AgentInputError as exc:
                raise AgentOutputError(str(exc), raw_text=final_text) from exc
        else:
            data = final_data
        current_tool_ids = {
            str(record.get("id") or "")
            for record in tool_records
            if isinstance(record, dict)
        }
        committed_tool_records = [
            *_agent_recovery_records(
                retry_state.recovery,
                exclude_ids=current_tool_ids,
            ),
            *tool_records,
        ]
        return AgentRunResult(
            agent=definition.name,
            data=data,
            raw_text=final_text,
            usage=total_usage,
            model=final_model,
            metadata={
                "model_profile": definition.model_profile,
                "execution": definition.execution,
                "write_policy": definition.write_policy,
                "source": definition.source,
                "task_id": context.task_id,
                "prompt": context.prompt_bundle.diagnostics,
                "tool_calls": committed_tool_records,
                "response_ids": response_ids,
                "tool_argument_retries": tool_argument_retry_count,
                "structured_output_transport": (
                    "tool" if final_data is not None else "text"
                ),
            },
        )

    def run(
        self,
        name: str,
        input_data: dict[str, Any],
        *,
        cancel_event: threading.Event | None = None,
        timeout: float | None = None,
        timeout_survival_seconds: float | None = None,
        model_override: str | None = None,
        event_callback: Callable[[RunEvent], None] | None = None,
        task_id: str = "",
        max_tokens: int | None = None,
        structured_output_tool: bool = False,
    ) -> AgentRunResult:
        definition = self.refresh_registry().get(name)
        if not isinstance(input_data, dict):
            raise AgentInputError("子代理输入必须是 JSON 对象")
        validate_json_schema(input_data, definition.input_schema)
        caller_cancel_event = cancel_event
        stopped = threading.Event()
        if caller_cancel_event is not None and caller_cancel_event.is_set():
            raise AgentCancelledError(f"子代理 {name} 已取消")
        try:
            effective_timeout = float(
                timeout if timeout is not None else definition.timeout
            )
        except (TypeError, ValueError) as exc:
            raise AgentRunError("子代理 timeout 必须是正数") from exc
        if not math.isfinite(effective_timeout) or effective_timeout <= 0:
            raise AgentRunError("子代理 timeout 必须是正数")
        raw_survival = timeout_survival_seconds
        if raw_survival is None:
            raw_survival = (self.config.get("agent_runtime") or {}).get(
                "timeout_survival_seconds", 0.0
            )
        try:
            survival_seconds = float(raw_survival)
        except (TypeError, ValueError) as exc:
            raise AgentRunError("子代理 timeout_survival_seconds 必须是非负数") from exc
        if not math.isfinite(survival_seconds) or survival_seconds < 0:
            raise AgentRunError("子代理 timeout_survival_seconds 必须是非负数")
        prompt_bundle = build_agent_prompt_bundle(
            self.root,
            self.user,
            definition,
            self.config,
        )
        tool_registry = build_agent_tool_registry(
            self.root,
            self.user,
            definition,
            self.config,
        )
        context = AgentExecutionContext(
            runner=self,
            definition=definition,
            prompt_bundle=prompt_bundle,
            tool_registry=tool_registry,
            cancel_event=stopped,
            model_override=model_override,
            max_tokens=max_tokens,
            task_id=task_id,
            structured_output_tool=bool(structured_output_tool),
            event_callback=event_callback,
        )
        function = _load_executor(definition)
        _event(event_callback, agent=name, status="started", task_id=task_id)
        try:
            execution_id = reserve_execution(
                f"agent:{self.root}:{self.user}:{name}"
            )
        except ExecutionCapacityError as exc:
            raise AgentRunError(str(exc)) from exc
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"agent-{name}")
        serial_key = (
            _serial_execution_key(self.root, self.user)
            if definition.execution == "background_serial"
            else None
        )
        serial_lock = (
            None
            if serial_key is None or serial_key in _owned_serial_execution_keys()
            else _serial_execution_lock(serial_key)
        )
        try:
            future = executor.submit(
                _execute_agent,
                function,
                context,
                input_data,
                serial_lock=serial_lock,
                serial_key=serial_key,
                max_attempts=_MAX_AGENT_RETRY_ATTEMPTS,
            )
            attach_execution(execution_id, future, executor)
        except BaseException:
            release_execution(execution_id)
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        deadline = time.monotonic() + effective_timeout
        try:
            while True:
                if caller_cancel_event is not None and caller_cancel_event.is_set():
                    stopped.set()
                    future.cancel()
                    cleanup_deadline = time.monotonic() + _AGENT_CANCEL_CLEANUP_GRACE
                    while not future.done() and time.monotonic() < cleanup_deadline:
                        time.sleep(0.05)
                    process_terminated = not abandon_execution(execution_id)
                    raise AgentCancelledError(
                        f"子代理 {name} 已取消",
                        process_terminated=process_terminated,
                        completion_future=(None if process_terminated else future),
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if survival_seconds > 0:
                        survival_deadline = time.monotonic() + survival_seconds
                        while not future.done():
                            if (
                                caller_cancel_event is not None
                                and caller_cancel_event.is_set()
                            ):
                                stopped.set()
                                future.cancel()
                                cleanup_deadline = (
                                    time.monotonic() + _AGENT_CANCEL_CLEANUP_GRACE
                                )
                                while (
                                    not future.done()
                                    and time.monotonic() < cleanup_deadline
                                ):
                                    time.sleep(0.05)
                                process_terminated = not abandon_execution(execution_id)
                                raise AgentCancelledError(
                                    f"子代理 {name} 已取消",
                                    process_terminated=process_terminated,
                                    completion_future=(None if process_terminated else future),
                                )
                            survival_remaining = survival_deadline - time.monotonic()
                            if survival_remaining <= 0:
                                break
                            time.sleep(min(0.05, survival_remaining))
                        if future.done():
                            result = future.result()
                            if not isinstance(result, AgentRunResult):
                                raise AgentRunError(
                                    f"子代理 {name} executor 必须返回 AgentRunResult"
                                )
                            result.metadata = {
                                **result.metadata,
                                "completed_after_timeout": True,
                                "timeout_seconds": effective_timeout,
                                "timeout_survival_seconds": survival_seconds,
                            }
                            _event(
                                event_callback,
                                agent=name,
                                status="completed_after_timeout",
                                task_id=task_id,
                                detail={
                                    "usage": result.usage,
                                    "model": result.model,
                                    "timeout_seconds": effective_timeout,
                                    "timeout_survival_seconds": survival_seconds,
                                },
                            )
                            return result
                    stopped.set()
                    future.cancel()
                    cleanup_deadline = time.monotonic() + _AGENT_TIMEOUT_CLEANUP_GRACE
                    while not future.done():
                        cleanup_remaining = cleanup_deadline - time.monotonic()
                        if cleanup_remaining <= 0:
                            break
                        time.sleep(min(0.05, cleanup_remaining))
                    process_terminated = not abandon_execution(execution_id)
                    state = (
                        "执行线程已退出"
                        if process_terminated
                        else "执行线程未在清理宽限期内退出"
                    )
                    raise AgentTimeoutError(
                        f"子代理 {name} 执行超时（{effective_timeout:g}s）；"
                        f"存活期 {survival_seconds:g}s 内未完成；"
                        f"已自动请求取消，{state}",
                        process_terminated=process_terminated,
                        completion_future=(None if process_terminated else future),
                    )
                if future.done():
                    result = future.result()
                    break
                stopped.wait(min(0.05, remaining))
            if not isinstance(result, AgentRunResult):
                raise AgentRunError(f"子代理 {name} executor 必须返回 AgentRunResult")
            _event(
                event_callback,
                agent=name,
                status="completed",
                task_id=task_id,
                detail={"usage": result.usage, "model": result.model},
            )
            return result
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, GeneratorExit)):
                raise
            if isinstance(exc, AgentCancelledError):
                status = "cancelled"
            elif isinstance(exc, AgentTimeoutError):
                status = "timed_out" if exc.process_terminated else "timed_out_running"
            else:
                status = "failed"
            detail = {
                "error": safe_provider_message(str(exc), "子代理运行失败"),
                "exception_type": type(exc).__name__,
            }
            for field_name in (
                "category",
                "code",
                "status_code",
                "retryable",
                "retry_after_ms",
                "retry_attempts",
                "retry_max_attempts",
                "retry_exhausted",
            ):
                value = getattr(exc, field_name, None)
                if isinstance(value, (bool, int, float)):
                    detail[field_name] = value
                elif isinstance(value, str) and value.strip():
                    detail[field_name] = value.strip()[:160]
            if isinstance(exc, AgentTimeoutError):
                detail.update(
                    {
                        "cancel_requested": True,
                        "process_terminated": exc.process_terminated,
                    }
                )
                if not exc.process_terminated:
                    detail["action_required"] = "inspect_runtime_logs"
            _event(
                event_callback,
                agent=name,
                status=status,
                task_id=task_id,
                detail=detail,
            )
            raise
        finally:
            if future.done():
                release_execution(execution_id)
