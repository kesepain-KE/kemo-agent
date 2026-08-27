"""Shared recovery helpers for malformed Provider tool arguments."""

from __future__ import annotations

import copy
import re
from typing import Any

from provider.protocol.enums import ResponseStatus
from provider.protocol.diagnostics import (
    invalid_tool_call_diagnostic,
    safe_invalid_tool_details,
    safe_parse_error,
)
from provider.protocol.models import KemoResponse, ToolCallItem
from provider.tool_arguments import bounded_json_length


INVALID_TOOL_ARGUMENTS_STOP_REASON = "invalid_tool_arguments"
PROVIDER_TOOL_ARGUMENTS_ERROR = "ProviderToolArgumentsError"
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.:/-]+$")


def _safe_identifier(value: Any) -> str:
    text = str(value or "").strip()[:160]
    return text if text and _SAFE_IDENTIFIER_RE.fullmatch(text) else ""


def validate_tool_call_batch(
    calls: list[Any],
    tool_schemas: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Validate a complete Provider tool-call batch before any execution.

    The returned payload contains only names, field names, and argument lengths;
    it never includes raw argument values.  A single invalid call invalidates the
    whole batch so parallel calls cannot partially create side effects.
    """

    from run.tools import ToolValidationError, validate_arguments

    invalid_calls: list[dict[str, Any]] = []
    for call in calls:
        name = str(getattr(call, "name", "") or "").strip()
        call_id = str(getattr(call, "id", "") or getattr(call, "call_id", ""))
        arguments = getattr(call, "arguments", None)
        raw_arguments = getattr(call, "arguments_raw", None)
        parse_error = getattr(call, "parse_error", None)
        parse_kind = (
            str(parse_error.get("kind") or "").strip().casefold()
            if isinstance(parse_error, dict)
            else ""
        )
        arguments_available = (
            raw_arguments is not None
            if parse_error is not None
            else raw_arguments is not None or isinstance(arguments, dict)
        )
        diagnostic: dict[str, Any] = {
            **(
                {"call_id": _safe_identifier(call_id)}
                if _safe_identifier(call_id)
                else {}
            ),
            **(
                {"name": _safe_identifier(name)}
                if _safe_identifier(name)
                else {}
            ),
            "arguments_diagnostic": {
                "available": arguments_available,
                "length": (
                    len(raw_arguments)
                    if isinstance(raw_arguments, str)
                    else (
                        bounded_json_length(arguments)
                        if isinstance(arguments, dict) and not parse_kind
                        else 0
                    )
                ),
                "content_omitted": True,
                "json_root_expected": "object",
            },
        }
        if isinstance(parse_error, dict):
            diagnostic["parse_error"] = safe_parse_error(parse_error)
        schema = tool_schemas.get(name)
        if "parse_error" in diagnostic:
            pass
        elif schema is None and tool_schemas:
            diagnostic["validation_error"] = {"kind": "unknown_tool"}
        elif schema is None:
            # An empty advertised schema means this runtime has no executable
            # tools. Keep the legacy pending-call path so cancellation can still
            # pair the call with a terminal result; execution will reject it.
            continue
        elif not isinstance(arguments, dict):
            diagnostic["validation_error"] = {"kind": "arguments_not_object"}
        else:
            try:
                validate_arguments(schema, arguments)
            except ToolValidationError:
                properties = schema.get("properties") or {}
                required = schema.get("required") or []
                details: dict[str, Any] = {"kind": "schema"}
                if isinstance(required, list):
                    missing = [
                        str(field)
                        for field in required
                        if isinstance(field, str) and field not in arguments
                    ]
                    if missing:
                        details["missing"] = missing[:64]
                if schema.get("additionalProperties") is False and isinstance(
                    properties, dict
                ):
                    extras = [
                        str(field) for field in arguments if field not in properties
                    ]
                    if extras:
                        details["extra"] = extras[:64]
                diagnostic["validation_error"] = details
        if "parse_error" in diagnostic or "validation_error" in diagnostic:
            invalid_calls.append(diagnostic)
    if not invalid_calls:
        return None
    first = invalid_calls[0]
    result: dict[str, Any] = {
        "message": "Provider 返回的工具参数未通过 Schema 校验",
        "exception_type": PROVIDER_TOOL_ARGUMENTS_ERROR,
        "phase": "provider",
        "stop_reason": INVALID_TOOL_ARGUMENTS_STOP_REASON,
        "invalid_tool_calls": invalid_calls,
    }
    if first.get("name"):
        result["tool_name"] = first["name"]
        result["message"] = f"Provider 返回的工具 {first['name']!r} 参数未通过 Schema 校验"
        validation_error = first.get("validation_error")
        if isinstance(validation_error, dict):
            missing = validation_error.get("missing")
            if isinstance(missing, list) and missing:
                result["message"] = (
                    f"Provider 返回的工具 {first['name']!r} 缺少必填参数："
                    + ", ".join(str(field) for field in missing[:64])
                )
    return result


def invalid_tool_call_error(item: ToolCallItem) -> dict[str, Any]:
    """Return the stable runtime error for one malformed tool call."""

    diagnostic = invalid_tool_call_diagnostic(
        call_id=item.call_id,
        name=item.name,
        raw_arguments=item.arguments_raw,
        parse_error=item.parse_error,
    )
    safe_call_id = str(diagnostic.get("call_id") or "")
    safe_tool_name = str(diagnostic.get("name") or "unknown_tool")
    return {
        "message": f"Provider 返回的工具 {safe_tool_name!r} 参数不是完整 JSON 对象",
        "exception_type": PROVIDER_TOOL_ARGUMENTS_ERROR,
        "phase": "provider",
        "stop_reason": INVALID_TOOL_ARGUMENTS_STOP_REASON,
        "call_id": safe_call_id,
        "tool_name": safe_tool_name,
        "parse_error": diagnostic["parse_error"],
        "arguments_diagnostic": diagnostic["arguments_diagnostic"],
    }


def is_invalid_tool_arguments_error(error: Any) -> bool:
    if not isinstance(error, dict):
        return False
    return (
        str(error.get("stop_reason") or "").strip().casefold()
        == INVALID_TOOL_ARGUMENTS_STOP_REASON
        or str(error.get("exception_type") or "").strip()
        == PROVIDER_TOOL_ARGUMENTS_ERROR
    )


def invalid_tool_name(error: dict[str, Any]) -> str:
    direct = str(error.get("tool_name") or "").strip()
    if direct:
        return direct
    details = error.get("incomplete_details")
    if not isinstance(details, dict):
        return ""
    invalid_calls = details.get("invalid_tool_calls")
    if not isinstance(invalid_calls, list):
        return ""
    for item in invalid_calls:
        if isinstance(item, dict) and str(item.get("name") or "").strip():
            return str(item["name"]).strip()
    return ""


def response_invalid_tool_arguments_error(
    response: KemoResponse,
) -> dict[str, Any] | None:
    """Normalize native and compatibility response failures before execution."""

    invalid_call = next(
        (
            item
            for item in response.output
            if isinstance(item, ToolCallItem) and item.parse_error is not None
        ),
        None,
    )
    if invalid_call is not None:
        return invalid_tool_call_error(invalid_call)
    details = response.incomplete_details
    if response.status != ResponseStatus.INCOMPLETE or not isinstance(details, dict):
        return None
    if (
        str(details.get("reason") or "").strip().casefold()
        != INVALID_TOOL_ARGUMENTS_STOP_REASON
    ):
        return None
    safe_details = safe_invalid_tool_details(details)
    error: dict[str, Any] = {
        "message": "Provider 返回的工具参数不是完整 JSON 对象",
        "exception_type": PROVIDER_TOOL_ARGUMENTS_ERROR,
        "phase": "provider",
        "stop_reason": INVALID_TOOL_ARGUMENTS_STOP_REASON,
        "incomplete_details": safe_details,
    }
    name = invalid_tool_name(error)
    if name:
        error["tool_name"] = name
        error["message"] = f"Provider 返回的工具 {name!r} 参数不是完整 JSON 对象"
    return error


def tool_argument_repair_instruction(*, tool_name: str, retry_number: int) -> str:
    target = f"工具 {tool_name!r}" if tool_name else "刚才的工具"
    return (
        "[provider_tool_argument_repair]\n"
        f"上一次生成{target}时，arguments 不是完整 JSON 对象。"
        f"这是第 {retry_number} 次恢复请求。请只重新生成原计划中的完整工具调用，"
        "确保 arguments 的根节点是完整 JSON object；不要重复已经输出的解释文字，"
        "也不要把 JSON 放进普通文本。\n"
        "[/provider_tool_argument_repair]"
    )


def messages_with_tool_argument_repair(
    messages: list[dict[str, Any]],
    *,
    tool_name: str,
    retry_number: int,
) -> list[dict[str, Any]]:
    """Add a transient correction without changing durable conversation messages."""

    repaired = copy.deepcopy(messages)
    correction = tool_argument_repair_instruction(
        tool_name=tool_name,
        retry_number=retry_number,
    )
    if repaired and repaired[0].get("role") == "system":
        content = repaired[0].get("content")
        if isinstance(content, str):
            repaired[0]["content"] = f"{content.rstrip()}\n\n{correction}"
            return repaired
    repaired.insert(0, {"role": "system", "content": correction})
    return repaired


def system_prompt_with_tool_argument_repair(
    system_prompt: str,
    *,
    tool_name: str,
    retry_number: int,
) -> str:
    correction = tool_argument_repair_instruction(
        tool_name=tool_name,
        retry_number=retry_number,
    )
    return f"{system_prompt.rstrip()}\n\n{correction}"
