"""仅通过显式输入独立执行子代理。"""

from __future__ import annotations

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
    ReasoningConfig,
    ToolCallItem,
    ToolDefinition,
    ToolResultItem,
    Usage,
    text_from_content,
)
from agents._runtime.resources import (
    AgentPromptBundle,
    build_agent_prompt_bundle,
    build_agent_tool_registry,
)
from run.agents import AgentDefinition, AgentRegistry, discover_agents
from run.config import load_config, provider_runtime_config, resolve_agent_model
from run.tools import (
    ConsecutiveIdenticalToolCallTracker,
    ConsecutiveToolFailureTracker,
    ToolRegistry,
    execute_tool,
)


_AGENT_TIMEOUT_CLEANUP_GRACE = 1.0


class AgentRunError(RuntimeError):
    pass


class AgentInputError(AgentRunError):
    pass


class AgentOutputError(AgentRunError):
    def __init__(self, message: str, *, raw_text: str = "") -> None:
        super().__init__(message)
        self.raw_text = str(raw_text or "")


class AgentTimeoutError(AgentRunError):
    def __init__(self, message: str, *, process_terminated: bool = False) -> None:
        super().__init__(message)
        self.process_terminated = bool(process_terminated)


class AgentCancelledError(AgentRunError):
    pass


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


def validate_json_schema(value: Any, schema: dict[str, Any], *, location: str = "$") -> None:
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
    elif isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            validate_json_schema(item, schema["items"], location=f"{location}[{index}]")


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise AgentOutputError("子代理响应中没有 JSON 对象") from None
        try:
            value = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise AgentOutputError(f"子代理 JSON 无效：{exc}") from exc
    if not isinstance(value, dict):
        raise AgentOutputError("子代理输出必须是 JSON 对象")
    return value


def resolve_agent_provider_config(
    config: dict[str, Any], definition: AgentDefinition, *, model_override: str | None = None
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

    def run_model(self, input_data: dict[str, Any]) -> AgentRunResult:
        return self.runner._run_model(self, input_data)


def _load_executor(definition: AgentDefinition) -> Callable[[AgentExecutionContext, dict[str, Any]], Any]:
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
        total["estimated"] = bool(total.get("estimated", False) or usage.get("estimated", False))

    @staticmethod
    def _tool_definitions(schemas: list[dict[str, Any]]) -> list[ToolDefinition]:
        definitions: list[ToolDefinition] = []
        for raw in schemas:
            function = raw.get("function") if isinstance(raw.get("function"), dict) else raw
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
    ) -> AgentRunResult:
        definition = context.definition
        runtime = resolve_agent_provider_config(
            self.config,
            definition,
            model_override=context.model_override,
        )
        provider = self.provider_factory(runtime)
        system = (
            context.prompt_bundle.text
            + "\n\n[output_schema]\n"
            + json.dumps(definition.output_schema, ensure_ascii=False, sort_keys=True)
        )
        items: list[Any] = [
            MessageItem.text(
                MessageRole.USER,
                json.dumps(input_data, ensure_ascii=False, sort_keys=True),
                item_id=f"msg_{uuid.uuid4().hex}",
            )
        ]
        total_usage: dict[str, Any] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "estimated": False,
        }
        tool_records: list[dict[str, Any]] = []
        final_text = ""
        final_model = runtime["model"]
        response_ids: list[str] = []
        parent_request_id: str | None = None
        max_iterations = definition.capabilities.max_tool_iterations
        tool_config = self.config.get("tools") or {}
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
            raise AgentRunError(
                "tools.consecutive_identical_call_limit 必须是正整数"
            )
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
        identical_calls = ConsecutiveIdenticalToolCallTracker(
            identical_call_limit
        )
        for iteration in range(1, max_iterations + 1):
            if context.cancel_event.is_set():
                raise AgentCancelledError(f"子代理 {definition.name} 已取消")
            request_id = f"req_{uuid.uuid4().hex}"
            tool_schemas = context.tool_registry.schemas(exclude=failures.unavailable)
            try:
                with provider_request_slot(
                    self.config,
                    cancel_event=context.cancel_event,
                ):
                    response = provider.create(
                        KemoRequest(
                            request_id=request_id,
                            parent_request_id=parent_request_id,
                            attempt=1,
                            model=runtime["model"],
                            stream=False,
                            system_prompt=system,
                            input=list(items),
                            tools=self._tool_definitions(tool_schemas),
                            generation={"max_output_tokens": context.max_tokens},
                            reasoning=ReasoningConfig(
                                enabled=True,
                                effort=runtime["reasoning_effort"],
                                return_mode="content",
                                context="auto",
                            ),
                            provider_options={
                                "reasoning_effort": runtime["reasoning_effort"]
                            },
                            metadata={
                                "capability": "conversation",
                                "user": self.user,
                                "source": "subagent",
                                "agent": definition.name,
                                "task_id": context.task_id,
                                "iteration": iteration,
                            },
                        )
                    )
            except ProviderCongestionError as exc:
                if context.cancel_event.is_set():
                    raise AgentCancelledError(f"子代理 {definition.name} 已取消") from exc
                raise
            if not isinstance(response, KemoResponse):
                raise AgentRunError("Provider create() 必须返回 KemoResponse")
            response_ids.append(response.id)
            parent_request_id = parent_request_id or request_id
            self._merge_usage(total_usage, self._usage_dict(response.usage))
            final_model = response.model or runtime["model"]
            if response.status not in {ResponseStatus.COMPLETED, ResponseStatus.REQUIRES_ACTION}:
                message = response.error.message if response.error is not None else str(response.status)
                raise AgentRunError(f"子代理 Provider 响应失败：{message}")
            calls = [item for item in response.output if isinstance(item, ToolCallItem)]
            messages = [item for item in response.output if isinstance(item, MessageItem)]
            items.extend(response.output)
            if not calls:
                final_text = "".join(text_from_content(item.content) for item in messages)
                break
            if iteration >= max_iterations:
                raise AgentRunError(f"子代理 {definition.name} 工具调用超过最大循环次数 {max_iterations}")
            for call in calls:
                identical_call_count = identical_calls.record(
                    call.name, call.arguments
                )
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
                                "agent_trigger": input_data.get("trigger"),
                                "tool_timeout": tool_timeout,
                                "agent_timeout": agent_timeout,
                                "knowledge_scopes": list(definition.capabilities.knowledge_scopes),
                            },
                            timeout=tool_timeout,
                            cancel_event=context.cancel_event,
                        )
                        payload = {"ok": True, "result": value}
                        status = "completed"
                    except Exception as exc:
                        payload = {
                            "ok": False,
                            "error": {
                                "message": str(exc),
                                "exception_type": type(exc).__name__,
                            },
                        }
                        status = "failed"
                    failure_count = failures.record(
                        call.name,
                        succeeded=bool(payload.get("ok")),
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
                        "result": payload,
                        "iteration": iteration,
                        "consecutive_identical_calls": identical_call_count,
                    }
                )
                items.append(
                    ToolResultItem(
                        id=f"result_{uuid.uuid4().hex}",
                        call_id=call.call_id,
                        name=call.name,
                        is_error=not bool(payload.get("ok")),
                        content=[JsonContent(data=payload)],
                    )
                )
        else:
            raise AgentRunError(f"子代理 {definition.name} 未生成最终输出")
        try:
            data = _parse_json_object(final_text)
            validate_json_schema(data, definition.output_schema)
        except AgentOutputError as exc:
            raise AgentOutputError(str(exc), raw_text=final_text) from exc
        except AgentInputError as exc:
            raise AgentOutputError(str(exc), raw_text=final_text) from exc
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
                "tool_calls": tool_records,
                "response_ids": response_ids,
            },
        )

    def run(
        self,
        name: str,
        input_data: dict[str, Any],
        *,
        cancel_event: threading.Event | None = None,
        timeout: float | None = None,
        model_override: str | None = None,
        event_callback: Callable[[RunEvent], None] | None = None,
        task_id: str = "",
        max_tokens: int | None = None,
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
        )
        function = _load_executor(definition)
        _event(event_callback, agent=name, status="started", task_id=task_id)
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"agent-{name}")
        future = executor.submit(function, context, input_data)
        deadline = time.monotonic() + effective_timeout
        try:
            while True:
                if caller_cancel_event is not None and caller_cancel_event.is_set():
                    stopped.set()
                    future.cancel()
                    raise AgentCancelledError(f"子代理 {name} 已取消")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    stopped.set()
                    future.cancel()
                    cleanup_deadline = (
                        time.monotonic() + _AGENT_TIMEOUT_CLEANUP_GRACE
                    )
                    while not future.done():
                        cleanup_remaining = cleanup_deadline - time.monotonic()
                        if cleanup_remaining <= 0:
                            break
                        time.sleep(min(0.05, cleanup_remaining))
                    process_terminated = future.done()
                    state = (
                        "执行线程已退出"
                        if process_terminated
                        else "执行线程未在清理宽限期内退出"
                    )
                    raise AgentTimeoutError(
                        f"子代理 {name} 执行超时（{effective_timeout:g}s）；"
                        f"已自动请求取消，{state}",
                        process_terminated=process_terminated,
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
                status = (
                    "timed_out"
                    if exc.process_terminated
                    else "timed_out_running"
                )
            else:
                status = "failed"
            detail = {"error": str(exc), "exception_type": type(exc).__name__}
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
            executor.shutdown(wait=False, cancel_futures=True)
