"""Provider tool-argument parsing shared by compatibility transports."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


MISSING = object()
_MAX_RAW_ARGUMENTS = 1_000_000
_MAX_ARGUMENT_DEPTH = 64
_MAX_ARGUMENT_NODES = 4096


class _ArgumentLimitError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ParsedToolArguments:
    arguments: dict[str, Any]
    arguments_raw: str | None
    parse_error: dict[str, Any] | None


def _estimate_json_size(
    value: Any,
    *,
    limit: int,
    depth: int = 0,
    state: list[int] | None = None,
    seen: set[int] | None = None,
) -> int:
    counters = state if state is not None else [0, 0]
    identities = seen if seen is not None else set()
    if depth > _MAX_ARGUMENT_DEPTH:
        raise _ArgumentLimitError("工具参数嵌套层级超过上限")
    counters[0] += 1
    if counters[0] > _MAX_ARGUMENT_NODES:
        raise _ArgumentLimitError("工具参数节点数量超过上限")
    if isinstance(value, (dict, list, tuple)):
        identity = id(value)
        if identity in identities:
            raise _ArgumentLimitError("工具参数包含循环引用")
        identities.add(identity)
    if isinstance(value, dict):
        total = 2
        for key, item in value.items():
            total += len(str(key)) + 4
            total += _estimate_json_size(
                item,
                limit=limit,
                depth=depth + 1,
                state=counters,
                seen=identities,
            )
            if total > limit:
                raise _ArgumentLimitError("工具参数序列化长度超过上限")
        return total
    if isinstance(value, (list, tuple)):
        total = 2
        for item in value:
            total += 1 + _estimate_json_size(
                item,
                limit=limit,
                depth=depth + 1,
                state=counters,
                seen=identities,
            )
            if total > limit:
                raise _ArgumentLimitError("工具参数序列化长度超过上限")
        return total
    if isinstance(value, str):
        return len(value) + 2
    if value is None or isinstance(value, bool):
        return 4 if value is None else 4
    if isinstance(value, (int, float)):
        return len(str(value))
    return len(str(value)) + 2


def bounded_json_length(value: Any, *, limit: int = _MAX_RAW_ARGUMENTS) -> int:
    """Return an exact small JSON length or ``limit + 1`` without unbounded work."""

    try:
        estimated = _estimate_json_size(value, limit=limit)
    except (RecursionError, _ArgumentLimitError):
        return limit + 1
    if estimated > limit:
        return limit + 1
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError, RecursionError):
        return limit + 1
    return len(encoded) if len(encoded) <= limit else limit + 1


def _limit_error(kind: str, message: str) -> dict[str, Any]:
    return {"kind": kind, "message": message}


def parse_tool_arguments(value: Any = MISSING) -> ParsedToolArguments:
    if value is MISSING:
        return ParsedToolArguments(
            {},
            None,
            {"kind": "missing_arguments", "message": "工具参数字段缺失"},
        )
    if isinstance(value, Mapping):
        arguments = dict(value)
        if bounded_json_length(arguments) > _MAX_RAW_ARGUMENTS:
            return ParsedToolArguments(
                {},
                None,
                _limit_error("arguments_too_large", "工具参数对象超过大小或复杂度上限"),
            )
        try:
            arguments_raw = json.dumps(
                arguments,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
        except (TypeError, ValueError, RecursionError):
            return ParsedToolArguments(
                {},
                None,
                _limit_error("arguments_too_large", "工具参数对象无法在安全边界内序列化"),
            )
        return ParsedToolArguments(
            arguments,
            arguments_raw,
            None,
        )
    if isinstance(value, str):
        if len(value) > _MAX_RAW_ARGUMENTS:
            return ParsedToolArguments(
                {},
                None,
                _limit_error("arguments_too_large", "工具参数原始内容超过大小上限"),
            )
        raw = value
        if not raw.strip():
            return ParsedToolArguments(
                {}, raw, {"kind": "empty_arguments", "message": "工具参数字段为空"}
            )
    elif value is None:
        return ParsedToolArguments(
            {}, None, {"kind": "invalid_arguments_type", "message": "工具参数字段类型无效"}
        )
    else:
        raw = str(value)
        if len(raw) > _MAX_RAW_ARGUMENTS:
            return ParsedToolArguments(
                {},
                None,
                _limit_error("arguments_too_large", "工具参数原始内容超过大小上限"),
            )
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, RecursionError) as exc:
        if isinstance(exc, RecursionError):
            return ParsedToolArguments(
                {},
                raw,
                _limit_error("invalid_json", "工具参数 JSON 嵌套层级超过解析上限"),
            )
        return ParsedToolArguments(
            {},
            raw,
            {
                "kind": "invalid_json",
                "message": "工具参数 JSON 解析失败",
                "line": exc.lineno,
                "column": exc.colno,
                "position": exc.pos,
            },
        )
    if not isinstance(parsed, dict):
        return ParsedToolArguments(
            {}, raw, {"kind": "non_object", "message": "工具参数 JSON 根节点必须是对象"}
        )
    if bounded_json_length(parsed) > _MAX_RAW_ARGUMENTS:
        return ParsedToolArguments(
            {},
            raw,
            _limit_error("arguments_too_large", "工具参数对象超过大小或复杂度上限"),
        )
    return ParsedToolArguments(parsed, raw, None)


__all__ = [
    "MISSING",
    "ParsedToolArguments",
    "bounded_json_length",
    "parse_tool_arguments",
]
