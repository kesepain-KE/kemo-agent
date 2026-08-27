"""Safe diagnostic contracts for untrusted Provider payloads.

Raw tool arguments are execution-time data.  They must never cross into
runtime errors, SSE metadata, logs, history, or other durable diagnostics.
"""

from __future__ import annotations

import json
import re
from typing import Any

from provider.tool_arguments import bounded_json_length


_MAX_INVALID_TOOL_CALLS = 32
_MAX_IDENTIFIER_CHARS = 160
_MAX_PROVIDER_MESSAGE_CHARS = 320
_MAX_PROVIDER_BODY_CHARS = 8192
_MAX_DIAGNOSTIC_STRING_CHARS = 2048
_MAX_DIAGNOSTIC_DEPTH = 32
_MAX_DIAGNOSTIC_NODES = 2048
_MAX_DIAGNOSTIC_COLLECTION_ITEMS = 512
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.:/-]+$")
_SAFE_FINISH_REASON_RE = re.compile(r"^[a-z0-9_.:-]{1,64}$")
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "authorization_token",
        "auth_token",
        "cookie",
        "credential",
        "credentials",
        "client_secret",
        "id_token",
        "password",
        "passwd",
        "private_key",
        "refresh_token",
        "secret",
        "secret_key",
        "session_token",
        "access_token",
        "token",
    }
)
_SENSITIVE_SUFFIXES = (
    "_api_key",
    "_authorization",
    "_authorization_token",
    "_auth_token",
    "_cookie",
    "_credential",
    "_credentials",
    "_password",
    "_private_key",
    "_refresh_token",
    "_secret",
    "_secret_key",
    "_session_token",
    "_access_token",
    "_id_token",
    "_client_secret",
)
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?(?:-----END [^-\r\n]*PRIVATE KEY-----|$)",
    re.IGNORECASE | re.DOTALL,
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_URL_CREDENTIAL_RE = re.compile(r"(?i)(https?://)([^\s/@:]+):([^\s/@]+)@")
_ASSIGNMENT_RE = re.compile(
    r"(?i)(['\"]?)(api[_-]?key|authorization(?:[_-]?token)?|auth[_-]?token|"
    r"cookie|credential|password|passwd|private[_-]?key|refresh[_-]?token|"
    r"secret(?:[_-]?key)?|session[_-]?token|access[_-]?token|id[_-]?token|"
    r"client[_-]?secret|token)\1"
    r"(\s*[:=]\s*)([^\s,;}&]+)"
)


def _normalize_key(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return text.casefold().replace("-", "_").replace(" ", "_")


def redact_diagnostic_text(value: Any) -> str:
    """Redact credential-shaped material from non-executable diagnostics."""

    text = str(value or "")
    text = _PRIVATE_KEY_RE.sub("[provider-secret-redacted]", text)
    text = _BEARER_RE.sub("Bearer ***", text)
    text = _URL_CREDENTIAL_RE.sub(r"\1***:***@", text)
    return _ASSIGNMENT_RE.sub(r"\1\2\1\3***", text)


def _has_embedded_json(value: str) -> bool:
    decoder = json.JSONDecoder()
    compact = value.lstrip()
    for index, marker in enumerate(compact):
        if marker not in "[{":
            continue
        try:
            parsed, _ = decoder.raw_decode(compact[index:])
        except RecursionError:
            return True
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(parsed, (dict, list)):
            return True
    return False


def _bounded_diagnostic_text(
    value: Any,
    *,
    state: dict[str, int],
    limit: int,
) -> tuple[str, bool]:
    text = str(value or "")
    remaining = max(0, _MAX_PROVIDER_BODY_CHARS - state.get("chars", 0))
    allowed = min(len(text), max(0, int(limit)), remaining)
    state["chars"] = state.get("chars", 0) + allowed
    return text[:allowed], len(text) > allowed


def safe_provider_message(
    value: Any,
    fallback: str,
    *,
    _state: dict[str, int] | None = None,
) -> str:
    """Keep a useful Provider message without echoing a raw payload."""

    # Structured Provider values are payloads, not human-readable messages.
    # Treat them as opaque so their repr cannot bypass the string redaction
    # rules below (for example, ``{"password": "..."}``).
    if isinstance(value, (dict, list, tuple, set)):
        return fallback
    state = _state if _state is not None else {"chars": 0}
    bounded_raw_text, truncated = _bounded_diagnostic_text(
        value,
        state=state,
        limit=_MAX_PROVIDER_BODY_CHARS,
    )
    bounded_raw_text = bounded_raw_text.strip()
    if bounded_raw_text and _has_embedded_json(bounded_raw_text):
        return fallback
    text = redact_diagnostic_text(bounded_raw_text).strip()
    if not text:
        return fallback
    compact = text.lstrip()
    if _has_embedded_json(compact):
        return fallback
    lowered = compact.casefold()
    if (
        '"arguments"' in lowered
        or "'arguments'" in lowered
        or '"tool_calls"' in lowered
        or re.search(r"\barguments\s*[:=]", lowered)
    ):
        return fallback
    if truncated and len(text) < _MAX_PROVIDER_MESSAGE_CHARS:
        text += "…(诊断内容已截断)"
    return text[:_MAX_PROVIDER_MESSAGE_CHARS]


def _safe_identifier(value: Any) -> str:
    text = str(value or "").strip()[:_MAX_IDENTIFIER_CHARS]
    if not text or not _SAFE_IDENTIFIER_RE.fullmatch(text):
        return ""
    return text


def safe_parse_error(value: Any) -> dict[str, Any]:
    """Return a fixed-message, position-only JSON parse diagnostic."""

    kind_messages = {
        "missing_arguments": "工具参数字段缺失",
        "empty_arguments": "工具参数字段为空",
        "invalid_arguments_type": "工具参数字段类型无效",
        "non_object": "工具参数 JSON 根节点必须是对象",
        "arguments_too_large": "工具参数超过安全大小或复杂度上限",
        "invalid_json": "工具参数 JSON 解析失败",
    }
    result: dict[str, Any] = {
        "kind": "invalid_json",
        "message": kind_messages["invalid_json"],
    }
    if not isinstance(value, dict):
        return result
    kind = str(value.get("kind") or "").strip().casefold()
    if kind in kind_messages:
        result["kind"] = kind
        result["message"] = kind_messages[kind]
    aliases = (
        (("line", "lineno"), "line"),
        (("column", "colno"), "column"),
        (("position", "pos"), "position"),
    )
    for sources, target in aliases:
        for source in sources:
            raw = value.get(source)
            if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
                result[target] = raw
                break
    return result


def tool_arguments_diagnostic(
    raw_arguments: Any = None,
    *,
    diagnostic: Any = None,
) -> dict[str, Any]:
    """Describe omitted arguments without exposing any argument value."""

    if isinstance(raw_arguments, str):
        available = bool(raw_arguments)
        length = len(raw_arguments)
    else:
        source = diagnostic if isinstance(diagnostic, dict) else {}
        available = bool(source.get("available"))
        raw_length = source.get("length")
        length = (
            raw_length
            if isinstance(raw_length, int)
            and not isinstance(raw_length, bool)
            and raw_length >= 0
            else 0
        )
    return {
        "available": available,
        "length": length,
        "content_omitted": True,
        "json_root_expected": "object",
    }


def invalid_tool_call_diagnostic(
    *,
    call_id: Any,
    name: Any,
    raw_arguments: Any = None,
    parse_error: Any = None,
    arguments_diagnostic: Any = None,
) -> dict[str, Any]:
    """Build the allowlisted diagnostic for one malformed tool call."""

    result: dict[str, Any] = {
        "parse_error": safe_parse_error(parse_error),
        "arguments_diagnostic": tool_arguments_diagnostic(
            raw_arguments,
            diagnostic=arguments_diagnostic,
        ),
    }
    safe_call_id = _safe_identifier(call_id)
    safe_name = _safe_identifier(name)
    if safe_call_id:
        result["call_id"] = safe_call_id
    if safe_name:
        result["name"] = safe_name
    return result


def safe_invalid_tool_details(value: Any) -> dict[str, Any]:
    """Allowlist an untrusted invalid-tool-arguments detail object."""

    source = value if isinstance(value, dict) else {}
    result: dict[str, Any] = {"reason": "invalid_tool_arguments"}
    finish_reason = str(source.get("finish_reason") or "").strip().casefold()
    if _SAFE_FINISH_REASON_RE.fullmatch(finish_reason):
        result["finish_reason"] = finish_reason
    invalid_calls = source.get("invalid_tool_calls")
    safe_calls: list[dict[str, Any]] = []
    if isinstance(invalid_calls, list):
        for item in invalid_calls[:_MAX_INVALID_TOOL_CALLS]:
            if not isinstance(item, dict):
                continue
            safe_calls.append(
                invalid_tool_call_diagnostic(
                    call_id=item.get("call_id"),
                    name=item.get("name") or item.get("tool_name"),
                    raw_arguments=item.get("arguments_raw")
                    if isinstance(item.get("arguments_raw"), str)
                    else item.get("raw_arguments"),
                    parse_error=item.get("parse_error"),
                    arguments_diagnostic=item.get("arguments_diagnostic"),
                )
            )
    if safe_calls:
        result["invalid_tool_calls"] = safe_calls
    return result


def _is_sensitive_key(value: Any) -> bool:
    key = _normalize_key(value)
    return key in _SENSITIVE_KEYS or any(key.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES)


def sanitize_provider_diagnostic(
    value: Any,
    *,
    key: str = "",
    _depth: int = 0,
    _state: dict[str, int] | None = None,
    _seen: set[int] | None = None,
) -> Any:
    """Remove execution data and credentials from a Provider diagnostic tree."""

    state = _state if _state is not None else {"nodes": 0, "chars": 0}
    seen = _seen if _seen is not None else set()
    normalized_key = _normalize_key(key)
    if _is_sensitive_key(normalized_key):
        return "***"
    state["nodes"] = state.get("nodes", 0) + 1
    if _depth > _MAX_DIAGNOSTIC_DEPTH or state["nodes"] > _MAX_DIAGNOSTIC_NODES:
        return {
            "content_omitted": True,
            "reason": "diagnostic_limit",
        }
    if isinstance(value, (dict, list, tuple)):
        identity = id(value)
        if identity in seen:
            return {
                "content_omitted": True,
                "reason": "diagnostic_cycle",
            }
        seen.add(identity)
    if normalized_key == "parse_error":
        return safe_parse_error(value)
    if normalized_key == "incomplete_details" and isinstance(value, dict):
        if str(value.get("reason") or "").strip().casefold() == "invalid_tool_arguments":
            return safe_invalid_tool_details(value)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (item_key, item_value) in enumerate(value.items()):
            if index >= _MAX_DIAGNOSTIC_COLLECTION_ITEMS:
                result["_omitted_items"] = {
                    "reason": "diagnostic_limit",
                    "max_items": _MAX_DIAGNOSTIC_COLLECTION_ITEMS,
                }
                break
            item_name = str(item_key)[:_MAX_IDENTIFIER_CHARS]
            normalized_item = _normalize_key(item_name)
            if normalized_item == "arguments_diagnostic":
                if "arguments_diagnostic" not in result:
                    result["arguments_diagnostic"] = tool_arguments_diagnostic(
                        diagnostic=item_value,
                    )
                continue
            if normalized_item in {"message", "detail"} and isinstance(item_value, str):
                result[item_name] = safe_provider_message(
                    item_value,
                    "Provider 错误信息已省略",
                    _state=state,
                )
                continue
            if normalized_item in {"arguments", "arguments_raw", "raw_arguments"}:
                existing = result.get("arguments_diagnostic")
                raw = item_value if isinstance(item_value, str) else None
                if raw is None:
                    estimated_length = bounded_json_length(item_value)
                    existing = {
                        **(existing if isinstance(existing, dict) else {}),
                        "available": True,
                        "length": estimated_length,
                    }
                result["arguments_diagnostic"] = tool_arguments_diagnostic(
                    raw,
                    diagnostic=existing,
                )
                continue
            result[item_name] = sanitize_provider_diagnostic(
                item_value,
                key=item_name,
                _depth=_depth + 1,
                _state=state,
                _seen=seen,
            )
        return result
    if isinstance(value, list):
        result = []
        for index, item in enumerate(value):
            if index >= _MAX_DIAGNOSTIC_COLLECTION_ITEMS:
                result.append(
                    {
                        "_omitted_items": {
                            "reason": "diagnostic_limit",
                            "max_items": _MAX_DIAGNOSTIC_COLLECTION_ITEMS,
                        }
                    }
                )
                break
            result.append(
                sanitize_provider_diagnostic(
                    item,
                    _depth=_depth + 1,
                    _state=state,
                    _seen=seen,
                )
            )
        return result
    if isinstance(value, tuple):
        return sanitize_provider_diagnostic(
            list(value)[:_MAX_DIAGNOSTIC_COLLECTION_ITEMS],
            _depth=_depth,
            _state=state,
            _seen=seen,
        )
    if isinstance(value, str):
        bounded, truncated = _bounded_diagnostic_text(
            value,
            state=state,
            limit=_MAX_DIAGNOSTIC_STRING_CHARS,
        )
        redacted = redact_diagnostic_text(bounded)
        return (
            redacted + "…(诊断内容已截断)"
            if truncated
            else redacted
        )
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return {
        "content_omitted": True,
        "reason": "unsupported_diagnostic_type",
        "payload_type": type(value).__name__,
    }


def safe_provider_body(value: Any) -> Any:
    """Return a bounded Provider error body without retaining raw payload text.

    Provider responses are untrusted and may contain tool arguments, cookies, or
    other credentials.  Keep structured error fields when they are small enough,
    but replace raw bytes/text with length-only metadata and summarize oversized
    structures.  This is intended for ``ProviderError.body`` diagnostics, not for
    the request/response data needed to execute a valid tool call.
    """

    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {
            "content_omitted": True,
            "payload_type": "bytes",
            "payload_length": len(value),
        }
    if isinstance(value, str):
        return {
            "content_omitted": True,
            "payload_type": "text",
            "payload_length": len(value),
        }

    try:
        sanitized = sanitize_provider_diagnostic(value)
    except (TypeError, ValueError, RecursionError):
        return {
            "content_omitted": True,
            "payload_type": type(value).__name__,
            "reason": "diagnostic_limit",
        }
    try:
        encoded = json.dumps(
            sanitized,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError, RecursionError):
        return {
            "content_omitted": True,
            "payload_type": type(value).__name__,
        }
    if len(encoded) > _MAX_PROVIDER_BODY_CHARS:
        payload_type = "object" if isinstance(value, dict) else (
            "array" if isinstance(value, list) else type(value).__name__
        )
        return {
            "content_omitted": True,
            "payload_type": payload_type,
            "payload_length": len(encoded),
        }
    return sanitized


__all__ = [
    "invalid_tool_call_diagnostic",
    "redact_diagnostic_text",
    "safe_invalid_tool_details",
    "safe_provider_message",
    "safe_provider_body",
    "safe_parse_error",
    "sanitize_provider_diagnostic",
    "tool_arguments_diagnostic",
]
