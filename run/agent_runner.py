"""仅通过显式输入独立执行子代理。"""

from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from events import RunEvent
from provider.factory import create_provider
from provider.schema import ChatRequest
from run.agents import AgentDefinition, AgentRegistry, discover_agents
from run.config import deep_merge, load_config, provider_runtime_config


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
        self.registry = registry or discover_agents(self.root)
        self.provider_factory = provider_factory

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
        definition = self.registry.get(name)
        if not isinstance(input_data, dict):
            raise AgentInputError("子代理输入必须是 JSON 对象")
        validate_json_schema(input_data, definition.input_schema)
        stopped = cancel_event or threading.Event()
        if stopped.is_set():
            raise AgentCancelledError(f"子代理 {name} 已取消")
        runtime = resolve_agent_provider_config(
            self.config, definition, model_override=model_override
        )
        provider = self.provider_factory(runtime)
        effective_timeout = float(timeout if timeout is not None else definition.timeout)
        if effective_timeout <= 0:
            raise AgentRunError("子代理 timeout 必须是正数")
        system = (
            definition.instruction
            + "\n\n只处理调用方显式传入的数据，不假设拥有主对话上下文。"
            + "\n必须只返回符合以下 JSON Schema 的 JSON 对象，不使用 Markdown：\n"
            + json.dumps(definition.output_schema, ensure_ascii=False, sort_keys=True)
        )
        request = ChatRequest(
            model=runtime["model"],
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(input_data, ensure_ascii=False, sort_keys=True),
                },
            ],
            stream=False,
            max_tokens=max_tokens,
        )
        _event(event_callback, agent=name, status="started", task_id=task_id)
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"agent-{name}")
        future = executor.submit(provider.chat, request)
        deadline = time.monotonic() + effective_timeout
        try:
            while True:
                if stopped.is_set():
                    future.cancel()
                    raise AgentCancelledError(f"子代理 {name} 已取消")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    future.cancel()
                    raise AgentTimeoutError(
                        f"子代理 {name} 执行超时（{effective_timeout:g}s）"
                    )
                if future.done():
                    response = future.result()
                    break
                stopped.wait(min(0.05, remaining))
            data = _parse_json_object(response.text)
            try:
                validate_json_schema(data, definition.output_schema)
            except AgentInputError as exc:
                raise AgentOutputError(str(exc)) from exc
            result = AgentRunResult(
                agent=name,
                data=data,
                raw_text=response.text,
                usage=response.usage.to_dict(),
                model=response.model or runtime["model"],
                metadata={
                    "model_profile": definition.model_profile,
                    "execution": definition.execution,
                    "write_policy": definition.write_policy,
                    "task_id": task_id,
                },
            )
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
