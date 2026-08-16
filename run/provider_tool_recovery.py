"""Shared recovery helpers for malformed Provider tool arguments."""

from __future__ import annotations

import copy
from typing import Any

from provider.protocol.enums import ResponseStatus
from provider.protocol.models import KemoResponse, ToolCallItem


INVALID_TOOL_ARGUMENTS_STOP_REASON = "invalid_tool_arguments"
PROVIDER_TOOL_ARGUMENTS_ERROR = "ProviderToolArgumentsError"


def invalid_tool_call_error(item: ToolCallItem) -> dict[str, Any]:
    """Return the stable runtime error for one malformed tool call."""

    return {
        "message": f"Provider 返回的工具 {item.name!r} 参数不是完整 JSON 对象",
        "exception_type": PROVIDER_TOOL_ARGUMENTS_ERROR,
        "phase": "provider",
        "stop_reason": INVALID_TOOL_ARGUMENTS_STOP_REASON,
        "call_id": item.call_id,
        "tool_name": item.name,
        "parse_error": copy.deepcopy(item.parse_error or {}),
        "arguments_raw": (item.arguments_raw or "")[:500],
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
    error: dict[str, Any] = {
        "message": "Provider 返回的工具参数不是完整 JSON 对象",
        "exception_type": PROVIDER_TOOL_ARGUMENTS_ERROR,
        "phase": "provider",
        "stop_reason": INVALID_TOOL_ARGUMENTS_STOP_REASON,
        "incomplete_details": copy.deepcopy(details),
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
