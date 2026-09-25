"""Public execution errors, result contracts, and schema validation for agents."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from events import RunEvent
from run.agents import AgentDefinition
from run.config import provider_runtime_config, resolve_agent_model

class AgentRunError(RuntimeError):
    pass


class AgentToolLimitError(AgentRunError):
    """Normal bounded stop after the subagent reaches its tool-call ceiling."""


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
        cancelled: bool = False,
    ) -> None:
        super().__init__(message)
        self.category = str(category or "provider_error")[:160]
        self.code = str(code or "")[:160]
        self.status_code = status_code
        self.retryable_declared = isinstance(retryable, bool)
        self.retryable = bool(retryable) if self.retryable_declared else False
        self.retry_after_ms = retry_after_ms
        self.cancelled = bool(cancelled)


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
    source: str = "",
    session_id: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    if callback is None:
        return
    metadata = {"phase": "subagent", "agent": agent, "status": status}
    if task_id:
        metadata["task_id"] = task_id
    if detail:
        metadata.update(detail)
    # Scope is authoritative and must not be spoofed by diagnostic detail.
    if source:
        metadata["source"] = source
    if session_id:
        metadata["session_id"] = session_id
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
