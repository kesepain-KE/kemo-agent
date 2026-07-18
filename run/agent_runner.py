"""仅通过显式输入独立执行子代理。"""

from __future__ import annotations

import json
import importlib.util
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
from provider.factory import create_provider
from provider.schema import ChatRequest
from agents._runtime.resources import (
    AgentPromptBundle,
    build_agent_prompt_bundle,
    build_agent_tool_registry,
)
from run.agents import AgentDefinition, AgentRegistry, discover_agents
from run.config import deep_merge, load_config, provider_runtime_config
from run.tools import ToolRegistry, execute_tool


class AgentRunError(RuntimeError):
    pass


class AgentInputError(AgentRunError):
    pass


class AgentOutputError(AgentRunError):
    pass


class AgentTimeoutError(AgentRunError):
    pass


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
    expected = schema.get("type")
    if expected is not None and not _type_matches(value, expected):
        raise AgentInputError(f"{location} 类型不符合 {expected}")
    if isinstance(value, dict):
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
    profiles = config.get("agent_models") or {}
    profile = profiles.get(definition.model_profile, {})
    if not isinstance(profile, dict):
        raise AgentRunError(f"子代理模型档位必须是对象：{definition.model_profile}")
    overrides = profile.get("provider", profile)
    if not isinstance(overrides, dict):
        raise AgentRunError(f"子代理模型档位 provider 必须是对象：{definition.model_profile}")
    merged = dict(config)
    merged["provider"] = deep_merge(config.get("provider") or {}, overrides)
    if model_override:
        merged["provider"]["model"] = model_override
    return provider_runtime_config(merged)


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
    def _merge_usage(total: dict[str, Any], usage: dict[str, Any]) -> None:
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            total[key] = int(total.get(key, 0)) + int(usage.get(key, 0))
        total["estimated"] = bool(total.get("estimated", False) or usage.get("estimated", False))

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
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": json.dumps(input_data, ensure_ascii=False, sort_keys=True),
            },
        ]
        schemas = context.tool_registry.schemas() or None
        total_usage: dict[str, Any] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "estimated": False,
        }
        tool_records: list[dict[str, Any]] = []
        final_text = ""
        final_model = runtime["model"]
        max_iterations = definition.capabilities.max_tool_iterations
        tool_timeout = float((self.config.get("tools") or {}).get("timeout", 60))
        for iteration in range(1, max_iterations + 1):
            if context.cancel_event.is_set():
                raise AgentCancelledError(f"子代理 {definition.name} 已取消")
            response = provider.chat(
                ChatRequest(
                    model=runtime["model"],
                    messages=messages,
                    stream=False,
                    tools=schemas,
                    max_tokens=context.max_tokens,
                )
            )
            self._merge_usage(total_usage, response.usage.to_dict())
            final_model = response.model or runtime["model"]
            if not response.tool_calls:
                final_text = response.text
                break
            if iteration >= max_iterations:
                raise AgentRunError(f"子代理 {definition.name} 工具调用超过最大循环次数 {max_iterations}")
            messages.append(
                {
                    "role": "assistant",
                    "content": response.text or None,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments, ensure_ascii=False),
                            },
                        }
                        for call in response.tool_calls
                    ],
                }
            )
            for call in response.tool_calls:
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
                tool_records.append(
                    {
                        "id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                        "status": status,
                        "result": payload,
                        "iteration": iteration,
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": json.dumps(payload, ensure_ascii=False, default=str),
                    }
                )
        else:
            raise AgentRunError(f"子代理 {definition.name} 未生成最终输出")
        data = _parse_json_object(final_text)
        try:
            validate_json_schema(data, definition.output_schema)
        except AgentInputError as exc:
            raise AgentOutputError(str(exc)) from exc
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
        stopped = cancel_event or threading.Event()
        if stopped.is_set():
            raise AgentCancelledError(f"子代理 {name} 已取消")
        effective_timeout = float(timeout if timeout is not None else definition.timeout)
        if effective_timeout <= 0:
            raise AgentRunError("子代理 timeout 必须是正数")
        prompt_bundle = build_agent_prompt_bundle(
            self.root,
            self.user,
            definition,
            self.config,
        )
        tool_registry = build_agent_tool_registry(self.root, self.user, definition)
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
                if stopped.is_set():
                    future.cancel()
                    raise AgentCancelledError(f"子代理 {name} 已取消")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    future.cancel()
                    raise AgentTimeoutError(f"子代理 {name} 执行超时（{effective_timeout:g}s）")
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
            status = "cancelled" if isinstance(exc, AgentCancelledError) else "failed"
            _event(
                event_callback,
                agent=name,
                status=status,
                task_id=task_id,
                detail={"error": str(exc), "exception_type": type(exc).__name__},
            )
            raise
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
