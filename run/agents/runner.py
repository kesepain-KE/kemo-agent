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
from provider.protocol.diagnostics import (
    incomplete_retry_metadata,
    safe_provider_message,
)
from provider.schema import ProviderError
from agents._runtime.resources import (
    AgentPromptBundle,
    build_agent_prompt_bundle,
    build_agent_tool_registry,
    effective_knowledge_scopes,
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
from run.agents.model_loop import run_model as _run_model_loop
from run.retry.loop import RetryLedger, RunAttemptContext, run_attempts
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
_SERIAL_EXECUTION_LOCKS_GUARD = threading.RLock()
_SERIAL_EXECUTION_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_SERIAL_EXECUTION_LOCAL = threading.local()


from run.agents.retry_policy import (
    _MAX_AGENT_RETRY_ATTEMPTS,
    _AgentRetryState,
    _agent_error_is_retryable,
    _agent_recovery_items,
    _agent_recovery_records,
    _agent_retry_delay_seconds,
    _agent_retry_reason,
    _agent_tool_result_reuse_allowed,
    _agent_tool_failure_is_retryable,
    _mark_agent_retry_exhausted,
    _record_agent_recovery,
    _safe_int,
)

def _run_agent_with_retries(
    function: Callable[[Any, dict[str, Any]], "AgentRunResult"],
    context: Any,
    input_data: dict[str, Any],
    *,
    max_attempts: int,
) -> "AgentRunResult":
    state = _AgentRetryState()
    ledger = RetryLedger(max_attempts=max(1, int(max_attempts)))

    def run_once(attempt: RunAttemptContext) -> "AgentRunResult":
        context.attempt = attempt.attempt_index
        context.max_attempts = max_attempts
        context.retry_state = state
        result = function(context, input_data)
        if not isinstance(result, AgentRunResult):
            raise AgentRunError(
                f"子代理 {context.definition.name} executor 必须返回 AgentRunResult"
            )
        result.metadata = {
            **result.metadata,
            "retry_attempts": attempt.attempt_index,
            "retry_max_attempts": attempt.max_attempts,
        }
        return result

    def retryable(exc: BaseException) -> bool:
        if isinstance(exc, AgentCancelledError):
            return False
        if context.cancel_event.is_set():
            raise AgentCancelledError(
                f"子代理 {context.definition.name} 已取消"
            ) from exc
        return _agent_error_is_retryable(exc, cancel_event=context.cancel_event)

    def on_retry(attempt: RunAttemptContext, exc: BaseException) -> None:
        _event(
            context.event_callback,
            agent=context.definition.name,
            status="retrying",
            task_id=context.task_id,
            source=context.source,
            session_id=context.session_id,
            detail={
                "failed_attempt": attempt.attempt_index,
                "next_attempt": attempt.attempt_index + 1,
                "max_attempts": attempt.max_attempts,
                "max_retries": max(0, attempt.max_attempts - 1),
                "consecutive_failures": attempt.attempt_index,
                "exception_type": type(exc).__name__,
                "reason": _agent_retry_reason(exc),
            },
        )

    def wait_before_retry(attempt: RunAttemptContext, exc: BaseException) -> None:
        if context.cancel_event.wait(
            _agent_retry_delay_seconds(exc, attempt.attempt_index)
        ):
            raise AgentCancelledError(
                f"子代理 {context.definition.name} 重试等待期间已取消"
            ) from exc

    return run_attempts(
        run_once,
        ledger=ledger,
        should_retry_error=retryable,
        on_retry=on_retry,
        wait_before_retry=wait_before_retry,
    )


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


from run.agents.contracts import (
    AgentCancelledError,
    AgentInputError,
    AgentOutputError,
    AgentProviderError,
    AgentRunError,
    AgentRunResult,
    AgentTimeoutError,
    AgentToolLimitError,
    AgentToolRetryError,
    _event,
    _parse_json_object,
    resolve_agent_provider_config,
    validate_json_schema,
)



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
    source: str = ""
    session_id: str = ""
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
        """Compatibility entry point delegated to the model loop module."""

        return _run_model_loop(
            self,
            context,
            input_data,
            retry_state=retry_state,
            attempt=attempt,
            max_attempts=max_attempts,
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
        source: str = "",
        session_id: str = "",
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
            source=str(source or "").strip(),
            session_id=str(session_id or "").strip(),
            event_callback=event_callback,
        )
        function = _load_executor(definition)
        _event(
            event_callback,
            agent=name,
            status="started",
            task_id=task_id,
            source=context.source,
            session_id=context.session_id,
        )
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
                                source=context.source,
                                session_id=context.session_id,
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
                source=context.source,
                session_id=context.session_id,
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
                "retry_budget_exhausted",
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
                source=context.source,
                session_id=context.session_id,
                detail=detail,
            )
            raise
        finally:
            if future.done():
                release_execution(execution_id)
