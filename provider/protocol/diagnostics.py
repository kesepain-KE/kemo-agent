"""Safe diagnostic contracts for untrusted Provider payloads.

Raw tool arguments are execution-time data.  They must never cross into
runtime errors, SSE metadata, logs, history, or other durable diagnostics.
"""

from __future__ import annotations

import json
import re
from typing import Any


_MAX_INVALID_TOOL_CALLS = 32
_MAX_IDENTIFIER_CHARS = 160
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.:/-]+$")
_SAFE_FINISH_REASON_RE = re.compile(r"^[a-z0-9_.:-]{1,64}$")
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "credentials",
        "password",
        "passwd",
        "private_key",
        "refresh_token",
        "secret",
        "session_token",
        "access_token",
        "token",
    }
)
_SENSITIVE_SUFFIXES = (
    "_api_key",
    "_authorization",
    "_cookie",
    "_credential",
    "_credentials",
    "_password",
    "_private_key",
    "_refresh_token",
    "_secret",
    "_session_token",
    "_access_token",
)
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?(?:-----END [^-\r\n]*PRIVATE KEY-----|$)",
    re.IGNORECASE | re.DOTALL,
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_URL_CREDENTIAL_RE = re.compile(r"(?i)(https?://)([^\s/@:]+):([^\s/@]+)@")
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|authorization|cookie|credential|password|passwd|"
    r"private[_-]?key|refresh[_-]?token|secret|session[_-]?token|access[_-]?token|token)"
    r"(\s*[:=]\s*)([^\s,;}&]+)"
)


def redact_diagnostic_text(value: Any) -> str:
    """Redact credential-shaped material from non-executable diagnostics."""

    text = str(value or "")
    text = _PRIVATE_KEY_RE.sub("[provider-secret-redacted]", text)
    text = _BEARER_RE.sub("Bearer ***", text)
    text = _URL_CREDENTIAL_RE.sub(r"\1***:***@", text)
    return _ASSIGNMENT_RE.sub(r"\1\2***", text)


def _safe_identifier(value: Any) -> str:
    text = str(value or "").strip()[:_MAX_IDENTIFIER_CHARS]
    if not text or not _SAFE_IDENTIFIER_RE.fullmatch(text):
        return ""
    return text


def safe_parse_error(value: Any) -> dict[str, Any]:
    """Return a fixed-message, position-only JSON parse diagnostic."""

    result: dict[str, Any] = {
        "kind": "invalid_json",
        "message": "工具参数 JSON 解析失败",
    }
    if not isinstance(value, dict):
        return result
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
    key = str(value or "").strip().casefold().replace("-", "_")
    return key in _SENSITIVE_KEYS or any(key.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES)


def sanitize_provider_diagnostic(value: Any, *, key: str = "") -> Any:
    """Remove execution data and credentials from a Provider diagnostic tree."""

    normalized_key = key.strip().casefold().replace("-", "_")
    if _is_sensitive_key(normalized_key):
        return "***"
    if normalized_key == "parse_error":
        return safe_parse_error(value)
    if normalized_key == "incomplete_details" and isinstance(value, dict):
        if str(value.get("reason") or "").strip().casefold() == "invalid_tool_arguments":
            return safe_invalid_tool_details(value)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for item_key, item_value in value.items():
            item_name = str(item_key)
            normalized_item = item_name.strip().casefold().replace("-", "_")
            if normalized_item == "arguments_diagnostic":
                if "arguments_diagnostic" not in result:
                    result["arguments_diagnostic"] = tool_arguments_diagnostic(
                        diagnostic=item_value,
                    )
                continue
            if normalized_item in {"arguments", "arguments_raw", "raw_arguments"}:
                existing = result.get("arguments_diagnostic")
                raw = item_value if isinstance(item_value, str) else None
                if raw is None and normalized_item == "arguments":
                    try:
                        raw = json.dumps(item_value, ensure_ascii=False, default=str)
                    except (TypeError, ValueError):
                        raw = None
                result["arguments_diagnostic"] = tool_arguments_diagnostic(
                    raw,
                    diagnostic=existing,
                )
                continue
            result[item_name] = sanitize_provider_diagnostic(
                item_value,
                key=item_name,
            )
        return result
    if isinstance(value, list):
        return [sanitize_provider_diagnostic(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_provider_diagnostic(item) for item in value]
    if isinstance(value, str):
        return redact_diagnostic_text(value)
    return value


__all__ = [
    "invalid_tool_call_diagnostic",
    "redact_diagnostic_text",
    "safe_invalid_tool_details",
    "safe_parse_error",
    "sanitize_provider_diagnostic",
    "tool_arguments_diagnostic",
]
