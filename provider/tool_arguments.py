"""Provider tool-argument parsing shared by compatibility transports."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


MISSING = object()
_MAX_RAW_ARGUMENTS = 1024 * 1024
_MAX_ARGUMENT_DEPTH = 64
_MAX_ARGUMENT_NODES = 4096


class _ArgumentLimitError(ValueError):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class _DuplicateKeyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ParsedToolArguments:
    arguments: dict[str, Any]
    arguments_raw: str | None
    parse_error: dict[str, Any] | None


def _validate_argument_value(
    value: Any,
    *,
    depth: int = 0,
    state: list[int] | None = None,
    active: set[int] | None = None,
) -> None:
    counters = state if state is not None else [0]
    active_ids = active if active is not None else set()
    if depth > _MAX_ARGUMENT_DEPTH:
        raise _ArgumentLimitError("arguments_too_complex", "工具参数嵌套层级超过上限")
    counters[0] += 1
    if counters[0] > _MAX_ARGUMENT_NODES:
        raise _ArgumentLimitError("arguments_too_complex", "工具参数节点数量超过上限")
    if isinstance(value, (dict, list)):
        identity = id(value)
        if identity in active_ids:
            raise _ArgumentLimitError("invalid_arguments_type", "工具参数包含循环引用")
        active_ids.add(identity)
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise _ArgumentLimitError(
                    "invalid_arguments_type", "工具参数对象的键必须是字符串"
                )
            _validate_argument_value(
                item,
                depth=depth + 1,
                state=counters,
                active=active_ids,
            )
        active_ids.remove(id(value))
        return
    if isinstance(value, list):
        for item in value:
            _validate_argument_value(
                item,
                depth=depth + 1,
                state=counters,
                active=active_ids,
            )
        active_ids.remove(id(value))
        return
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise _ArgumentLimitError("non_finite_number", "工具参数不能包含 NaN/Infinity")
    raise _ArgumentLimitError("invalid_arguments_type", "工具参数包含非 JSON 类型")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate key: {key}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> Any:
    raise _ArgumentLimitError("non_finite_number", f"工具参数包含非有限数值: {value}")


def bounded_json_length(value: Any, *, limit: int = _MAX_RAW_ARGUMENTS) -> int:
    """Return exact UTF-8 JSON bytes or ``limit + 1`` within complexity limits."""

    try:
        _validate_argument_value(value)
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        return limit + 1
    return len(encoded) if len(encoded) <= limit else limit + 1


def _limit_error(kind: str, message: str) -> dict[str, Any]:
    return {"kind": kind, "message": message}


def _raw_json_nesting_exceeds(raw: str, *, limit: int) -> bool:
    """Detect excessive JSON container nesting without invoking the JSON parser."""

    depth = 0
    in_string = False
    escaped = False
    for char in raw:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > limit:
                return True
        elif char in "]}":
            depth = max(0, depth - 1)
    return False


def parse_tool_arguments(value: Any = MISSING) -> ParsedToolArguments:
    if value is MISSING:
        return ParsedToolArguments(
            {},
            None,
            {"kind": "missing_arguments", "message": "工具参数字段缺失"},
        )
    if isinstance(value, Mapping):
        arguments = dict(value)
        try:
            _validate_argument_value(arguments)
            arguments_raw = json.dumps(
                arguments,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
        except _ArgumentLimitError as exc:
            return ParsedToolArguments(
                {},
                None,
                _limit_error(exc.kind, str(exc)),
            )
        except (TypeError, ValueError, RecursionError):
            return ParsedToolArguments(
                {},
                None,
                _limit_error("invalid_arguments_type", "工具参数对象无法安全序列化"),
            )
        if len(arguments_raw.encode("utf-8")) > _MAX_RAW_ARGUMENTS:
            return ParsedToolArguments(
                {}, None, _limit_error("arguments_too_large", "工具参数原始内容超过大小上限")
            )
        return ParsedToolArguments(
            arguments,
            arguments_raw,
            None,
        )
    if isinstance(value, str):
        if len(value.encode("utf-8")) > _MAX_RAW_ARGUMENTS:
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
        return ParsedToolArguments(
            {}, None, _limit_error("invalid_arguments_type", "工具参数字段类型无效")
        )
    if _raw_json_nesting_exceeds(raw, limit=_MAX_ARGUMENT_DEPTH):
        return ParsedToolArguments(
            {},
            raw,
            _limit_error("invalid_json", "工具参数 JSON 嵌套层级超过解析上限"),
        )
    try:
        parsed = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except _DuplicateKeyError:
        return ParsedToolArguments(
            {}, raw, _limit_error("duplicate_key", "工具参数 JSON 包含重复键")
        )
    except _ArgumentLimitError as exc:
        return ParsedToolArguments({}, raw, _limit_error(exc.kind, str(exc)))
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
    try:
        _validate_argument_value(parsed)
    except _ArgumentLimitError as exc:
        return ParsedToolArguments(
            {},
            raw,
            _limit_error(exc.kind, str(exc)),
        )
    return ParsedToolArguments(parsed, raw, None)


__all__ = [
    "MISSING",
    "ParsedToolArguments",
    "bounded_json_length",
    "parse_tool_arguments",
]
