"""Transport-specific image validation without changing the Kemo wire schema."""

from __future__ import annotations

from typing import Any, ClassVar

from provider.protocol.models import ImageContent, KemoRequest, MessageItem, ToolResultItem
from provider.protocol.validation import validate_tool_linkage


CHAT_IMAGE_MAX_BYTES = 20 * 1024 * 1024


class ChatImageContent(ImageContent):
    """Internal Chat image; never a relaxed native Kemo wire model."""

    _max_inline_image_bytes: ClassVar[int] = CHAT_IMAGE_MAX_BYTES


def validate_chat_request(request: KemoRequest) -> None:
    """Adapter-local contract; never a bypass switch on the native protocol."""
    validate_tool_linkage(request.input)
    for item in request.input:
        if isinstance(item, (MessageItem, ToolResultItem)):
            for block in item.content:
                if isinstance(block, ImageContent):
                    ChatImageContent.model_validate(block.model_dump(mode="json"))


def image_content_type(transport: str) -> type[ImageContent]:
    return ChatImageContent if transport.casefold() == "chat" else ImageContent


def prepare_image_blocks(content: Any, *, transport: str) -> Any:
    """Preserve non-image blocks, applying the selected image transport limit."""
    if not isinstance(content, list):
        return content
    image_type = image_content_type(transport)
    return [
        image_type.model_validate(block)
        if isinstance(block, dict) and block.get("type") == "image"
        else block
        for block in content
    ]
