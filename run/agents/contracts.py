"""Public execution errors, result contracts, and schema validation for agents."""

from __future__ import annotations

from typing import TYPE_CHECKING

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from events import RunEvent
from run.agents import AgentDefinition
from run.config import provider_runtime_config, resolve_agent_model

class AgentRunError(RuntimeError):
    if TYPE_CHECKING:
        retry_budget_exhausted: bool
        retry_exhausted: bool
        retry_attempts: int
        retry_max_attempts: int


class AgentExecutionCapacityError(AgentRunError):
    """The watchdog temporarily cannot accept another agent execution."""

    category = "execution_capacity"
    retryable = True


class AgentToolLimitError(AgentRunError):
    """Normal bounded stop after the subagent reaches its tool-call ceiling."""

    def __init__(self, message, *, partial_result=None, continuation=None):
        super().__init__(message)
        self.partial_result = partial_result
        self.continuation = continuation
        self.process_terminated = True
        self.category = "tool_budget_exhausted"


class AgentInputError(AgentRunError):
    _REASONS = frozenset(
        {"type", "missing", "unknown", "length", "pattern", "enum", "one_of", "items"}
    )

    def __init__(
        self,
        message: str,
        *,
        schema_hint: dict[str, Any] | None = None,
        field_path: str | None = None,
        expected_type: str | None = None,
        reason: str = "type",
    ):
        self._field_path = field_path
        self._expected_type = expected_type
        self._reason = reason if reason in self._REASONS else "type"
        self.schema_hint = None
        if schema_hint is not None:
            # Schema diagnostics must not echo const/default/config/input values.
            message = "子代理 input 不符合契约，请按字段提示修正"
            self.schema_hint = bounded_schema_hint(schema_hint, limit=600 - len(message))
        super().__init__(message)

    @property
    def field_path(self) -> str | None:
        return self._field_path

    @property
    def expected_type(self) -> str | None:
        return self._expected_type

    @property
    def reason(self) -> str:
        return self._reason


_SAFE_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
_SAFE_SCHEMA_TYPES = frozenset(
    {"object", "array", "string", "integer", "number", "boolean", "null"}
)


def _safe_identifier(value: Any) -> bool:
    return isinstance(value, str) and _SAFE_IDENTIFIER.fullmatch(value) is not None


def _schema_type_label(expected: Any) -> str | None:
    if isinstance(expected, str):
        return expected if expected in _SAFE_SCHEMA_TYPES else None
    if isinstance(expected, list):
        values = [
            item for item in expected
            if isinstance(item, str) and item in _SAFE_SCHEMA_TYPES
        ]
        if values and len(values) == len(expected):
            return "|".join(values)
    return None


def _schema_node_for_path(schema: dict[str, Any], field_path: str | None) -> tuple[str, Any] | None:
    """Resolve only schema-backed ASCII property paths; never trust user path text."""
    if not isinstance(field_path, str) or not field_path.startswith("$"):
        return None
    position = 1
    node: Any = schema
    final_field: str | None = None
    token = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]{0,63})|\[(\d+)\]")
    while position < len(field_path):
        match = token.match(field_path, position)
        if match is None:
            return None
        field_name, index = match.groups()
        if field_name is not None:
            properties = node.get("properties") if isinstance(node, dict) else None
            if not isinstance(properties, dict) or field_name not in properties:
                return None
            node = properties[field_name]
            final_field = field_name
        else:
            items = node.get("items") if isinstance(node, dict) else None
            if not isinstance(items, dict):
                return None
            node = items
        position = match.end()
    if final_field is None:
        return None
    return final_field, node


def _type_mismatch_for(
    schema: dict[str, Any],
    *,
    field_path: str | None,
    expected_type: str | None,
) -> list[dict[str, str]]:
    resolved = _schema_node_for_path(schema, field_path)
    if resolved is None:
        return []
    field, node = resolved
    expected = _schema_type_label(node.get("type")) if isinstance(node, dict) else None
    if expected is None or expected_type is None:
        return []
    # The expected type must be schema-derived, not copied from an untrusted path.
    if expected_type != expected:
        return []
    return [{"field": field, "expected": expected}]


def agent_input_hint(
    schema: dict[str, Any],
    value: Any = None,
    *,
    field_path: str | None = None,
    expected_type: str | None = None,
    reason: str = "type",
) -> dict[str, Any]:
    """Field names only; validation remains exclusively validate_json_schema."""
    properties = schema.get("properties") or {}
    required = schema.get("required") or []
    values = value if isinstance(value, dict) else {}
    hint: dict[str, Any] = {
        "missing": [name for name in required if name not in values],
        "allowed": list(properties),
        "required": list(required),
        "reason": reason,
    }
    if schema.get("additionalProperties") is False:
        unknown = [name for name in values if name not in properties]
        safe_unknown = [name for name in unknown if _safe_identifier(name)]
        unsafe_count = len(unknown) - len(safe_unknown)
        hint["unknown"] = safe_unknown
        if unsafe_count:
            hint["unknown_unsafe"] = unsafe_count
            hint["truncated"] = True
    mismatch = _type_mismatch_for(
        schema,
        field_path=field_path,
        expected_type=expected_type,
    )
    if mismatch:
        hint["type_mismatch"] = mismatch
    return hint


def bounded_schema_hint(value: dict[str, Any], *, limit: int = 600) -> dict[str, Any]:
    """Whitelist bounded identifiers, never arbitrary schema or diagnostic text."""
    hint: dict[str, Any] = {}
    truncated = bool(value.get("truncated"))
    for key in ("missing", "unknown", "allowed", "required"):
        original = value.get(key) or []
        names = [
            name for name in original
            if _safe_identifier(name)
        ] if isinstance(original, list) else []
        hint[key] = names[:80]
        truncated = truncated or not isinstance(original, list) or len(names) != len(original) or len(names) > 80
    type_mismatch = value.get("type_mismatch")
    safe_mismatch: list[dict[str, str]] = []
    if isinstance(type_mismatch, list):
        for item in type_mismatch:
            if not isinstance(item, dict):
                truncated = True
                continue
            field = item.get("field")
            expected = item.get("expected")
            if (
                isinstance(field, str)
                and _safe_identifier(field)
                and isinstance(expected, str)
                and expected in _SAFE_SCHEMA_TYPES
            ):
                safe_mismatch.append({"field": field, "expected": expected})
            else:
                truncated = True
    elif type_mismatch is not None:
        truncated = True
    if safe_mismatch:
        hint["type_mismatch"] = safe_mismatch[:16]
        truncated = truncated or len(safe_mismatch) > 16

    unknown_unsafe = value.get("unknown_unsafe")
    if isinstance(unknown_unsafe, int) and not isinstance(unknown_unsafe, bool) and unknown_unsafe > 0:
        hint["unknown_unsafe"] = min(unknown_unsafe, 100000)
        truncated = truncated or unknown_unsafe > 100000
    elif unknown_unsafe not in (None, 0):
        truncated = True

    # Narrow the reason to a mapping key; any non-string value uses the generic note.
    raw_reason = value.get("reason")
    reason = raw_reason if isinstance(raw_reason, str) else ""
    if hint.get("type_mismatch"):
        note = "字段类型不符，按 allowed 中字段的 schema 类型修正"
    elif hint.get("unknown") or hint.get("unknown_unsafe"):
        note = "移除未在 allowed 中声明的字段"
    else:
        note = {
            "type": "字段类型不符，按 allowed 中字段的 schema 类型修正",
            "missing": "补齐 required 字段",
            "unknown": "移除未在 allowed 中声明的字段",
        }.get(reason, "补齐 required 并核对类型；未列字段须遵守契约。")
    hint["note"] = note
    if truncated:
        hint["truncated"] = True
    while len(json.dumps(hint, ensure_ascii=False, separators=(",", ":"))) > limit:
        hint["truncated"] = True
        # Retain the most actionable diagnostics first.  Remove lower-priority
        # fields before touching type_mismatch or missing.
        key = None
        for candidate in ("required", "allowed", "unknown_unsafe", "unknown", "missing", "type_mismatch"):
            current = hint.get(candidate)
            if not current:
                continue
            # Keep at least one required/missing/actionable type entry whenever
            # the budget allows any useful contract diagnostic at all.
            if isinstance(current, list) and candidate in {"required", "missing", "type_mismatch"}:
                if len(current) <= 1:
                    continue
            key = candidate
            break
        if key is not None:
            if isinstance(hint[key], list):
                hint[key].pop()
            else:
                hint.pop(key, None)
        elif hint["note"]:
            hint["note"] = hint["note"][:-1]
        else:
            break
    return hint


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
        partial_result: Any = None,
        continuation: Any = None,
    ) -> None:
        super().__init__(message)
        self.process_terminated = bool(process_terminated)
        self.completion_future = completion_future
        self.partial_result = partial_result
        self.continuation = continuation


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
            raise AgentInputError(
                f"{location} 必须且只能匹配 oneOf 中的一个结构",
                field_path=location,
                reason="one_of",
            )
        return
    if "const" in schema and value != schema["const"]:
        raise AgentInputError(
            f"{location} 必须等于 {schema['const']!r}",
            field_path=location,
            reason="enum",
        )
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        raise AgentInputError(
            f"{location} 不在允许值中",
            field_path=location,
            reason="enum",
        )
    expected = schema.get("type")
    if expected is not None and not _type_matches(value, expected):
        raise AgentInputError(
            f"{location} 类型不符合 {expected}",
            field_path=location,
            expected_type=_schema_type_label(expected),
            reason="type",
        )
    if isinstance(value, str):
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if isinstance(minimum, int) and len(value) < minimum:
            raise AgentInputError(
                f"{location} 长度不能小于 {minimum}",
                field_path=location,
                reason="length",
            )
        if isinstance(maximum, int) and len(value) > maximum:
            raise AgentInputError(
                f"{location} 长度不能大于 {maximum}",
                field_path=location,
                reason="length",
            )
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            try:
                matches = re.search(pattern, value)
            except re.error as exc:
                raise AgentInputError(
                    f"{location} Schema pattern 无效",
                    field_path=location,
                    reason="pattern",
                ) from exc
            if matches is None:
                raise AgentInputError(
                    f"{location} 不符合 pattern",
                    field_path=location,
                    reason="pattern",
                )
    elif isinstance(value, dict):
        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        missing = [name for name in required if name not in value]
        if missing:
            raise AgentInputError(
                f"{location} 缺少字段：{', '.join(missing)}",
                field_path=location,
                reason="missing",
            )
        if schema.get("additionalProperties") is False:
            extras = [name for name in value if name not in properties]
            if extras:
                raise AgentInputError(
                    f"{location} 包含未知字段：{', '.join(extras)}",
                    field_path=location,
                    reason="unknown",
                )
        for name, item in value.items():
            child = properties.get(name)
            if isinstance(child, dict):
                validate_json_schema(item, child, location=f"{location}.{name}")
    elif isinstance(value, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(value) < minimum:
            raise AgentInputError(f"{location} 元素数量不能小于 {minimum}", field_path=location, reason="items")
        if isinstance(maximum, int) and len(value) > maximum:
            raise AgentInputError(f"{location} 元素数量不能大于 {maximum}", field_path=location, reason="items")
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
