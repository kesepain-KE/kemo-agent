"""Transport-specific image validation without changing the Kemo wire schema."""

from __future__ import annotations

import base64
import binascii
import re
from typing import Annotated, Any, Union

from pydantic import Field, TypeAdapter, field_validator, model_validator

from provider.protocol.models import (
    AudioContent, ContentBlock, FileContent, ImageContent, JsonContent, KemoRequest, MessageItem,
    ReferenceContent, RefusalContent, TextContent, ToolResultItem, VideoContent,
)
from provider.protocol.validation import validate_tool_linkage


CHAT_IMAGE_MAX_BYTES = 20 * 1024 * 1024


class ChatImageContent(ImageContent):
    """Internal Chat image; never a relaxed native Kemo wire model."""

    @model_validator(mode="after")
    def validate_reference(self) -> "ChatImageContent":
        # Local validation overrides the inherited validator by name. Native
        # models retain their fixed 1 MiB rule and expose no relaxation hook.
        if not self.asset_id and self.source is None:
            raise ValueError("媒体内容至少需要 asset_id 或 source")
        if self.checksum_sha256 is not None:
            checksum = self.checksum_sha256.strip().casefold()
            if re.fullmatch(r"[0-9a-f]{64}", checksum) is None:
                raise ValueError("checksum_sha256 必须是 64 位十六进制 SHA-256")
            self.checksum_sha256 = checksum
        if self.source is not None and self.source.kind in {"inline_base64", "data_url"}:
            payload = self.source.data
            if self.source.kind == "data_url":
                match = re.fullmatch(
                    r"data:([^;,]+);base64,(.+)", self.source.uri or "", re.DOTALL,
                )
                if match is None:
                    raise ValueError("data_url 必须是 Base64 Data URL")
                if self.mime_type and self.mime_type.casefold() != match.group(1).casefold():
                    raise ValueError("data_url MIME 与 mime_type 不一致")
                payload = match.group(2)
            try:
                decoded = base64.b64decode(payload or "", validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError("内联图片必须是有效 Base64") from exc
            if not decoded or len(decoded) > CHAT_IMAGE_MAX_BYTES:
                raise ValueError(f"内联图片解码后必须为 1..{CHAT_IMAGE_MAX_BYTES} 字节")
        return self


ChatContentBlock = Annotated[
    Union[TextContent, ChatImageContent, AudioContent, VideoContent, FileContent,
          JsonContent, ReferenceContent, RefusalContent],
    Field(discriminator="type"),
]

_CHAT_CONTENT_ADAPTER = TypeAdapter(list[ChatContentBlock])


class ChatMessageItem(MessageItem):
    """Validate Chat images with the adapter's schema before native wrapping."""

    @field_validator(
        "content", mode="plain", json_schema_input_type=list[ChatContentBlock],
    )
    @classmethod
    def validate_chat_content(cls, value: Any) -> list[ContentBlock]:
        # Keep the inherited mutable list type. Only this adapter's ingress
        # uses the Chat union; native MessageItem validation stays unchanged.
        return [block for block in _CHAT_CONTENT_ADAPTER.validate_python(value)]


def message_item_type(transport: str) -> type[MessageItem]:
    return ChatMessageItem if transport.casefold() == "chat" else MessageItem


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
