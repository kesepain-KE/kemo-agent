"""Shared capacity estimates and inspection, not a second context engine."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from run.context.service import context_status as context_status

from run.context.compression_budget import (
    CompressionBudget, CompressionBudgetExhausted, CompressionCancelledError,
)
from provider.tokenizer import count_text, count_value


def estimate_text_tokens(text: str) -> int:
    return count_text(text)


def _media_projection(value):
    if isinstance(value, list):
        parts = [_media_projection(item) for item in value]
        return [part[0] for part in parts], sum(part[1] for part in parts)
    if not isinstance(value, dict):
        return value, 0
    if (value.get("type") in {"image", "audio", "video", "file"}
            and value.get("asset_id") and not value.get("source")):
        return dict(value), 1024
    if value.get("kind") == "inline_base64" and isinstance(value.get("data"), str):
        return {**value, "data": "[inline media payload]"}, 1024
    if isinstance(value.get("uri"), str) and value["uri"].startswith("data:image/"):
        return {**value, "uri": "data:image/[inline media payload]"}, 1024
    parts = {key: _media_projection(item) for key, item in value.items()}
    return {key: part[0] for key, part in parts.items()}, sum(part[1] for part in parts.values())


def estimate_messages_tokens(messages) -> int:
    total = 0
    for message in messages:
        value, media = _media_projection({key: value for key, value in message.items()
                                          if key != "_kemo_tool_result"})
        total += 4 + count_value(value) + media
    return total


def estimate_tools_tokens(tools) -> int:
    return 8 + count_value(tools) if tools else 0


def __getattr__(name):
    if name == "context_status":
        from run.context.service import context_status
        return context_status
    raise AttributeError(name)
