"""Cross-object validation not naturally expressed by individual models."""

from __future__ import annotations

from collections.abc import Iterable

from provider.protocol.errors import ToolLinkageError
from provider.protocol.models import Item, KemoRequest, ToolCallItem, ToolResultItem


def validate_tool_linkage(items: Iterable[Item]) -> None:
    calls: dict[str, ToolCallItem] = {}
    results: set[str] = set()
    for index, item in enumerate(items):
        if isinstance(item, ToolCallItem):
            if item.call_id in calls:
                raise ToolLinkageError(
                    f"tool_call.call_id 重复：{item.call_id}",
                    path=f"items[{index}].call_id",
                )
            calls[item.call_id] = item
        elif isinstance(item, ToolResultItem):
            call = calls.get(item.call_id)
            if call is None:
                raise ToolLinkageError(
                    f"tool_result 无匹配 tool_call：{item.call_id}",
                    path=f"items[{index}].call_id",
                )
            if item.call_id in results:
                raise ToolLinkageError(
                    f"tool_result.call_id 重复：{item.call_id}",
                    path=f"items[{index}].call_id",
                )
            if item.name != call.name:
                raise ToolLinkageError(
                    f"tool_result.name 与 tool_call 不一致：{item.name} != {call.name}",
                    path=f"items[{index}].name",
                )
            results.add(item.call_id)


def validate_request(request: KemoRequest) -> KemoRequest:
    validate_tool_linkage(request.input)
    return request
