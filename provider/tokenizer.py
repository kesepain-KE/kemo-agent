"""Framework-wide local token counting using the pinned DeepSeek V4 tokenizer."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import io
import json
from pathlib import Path
from zipfile import ZipFile

from tokenizers import Tokenizer


_ARCHIVE = Path(__file__).with_name("assets") / "deepseek_v4_tokenizer.zip"
_ARCHIVE_SHA256 = "e7310d1dafe0a86d8a5629fe78a7c763760f651db9b8682718a1781dcd6fe495"
TOKENIZER_ID = "deepseek-v4-89085f12ef79460ac5f66d1119325ddf"
_TOKENIZER_MEMBER = "deepseek_v4_tokenizer/tokenizer.json"


@lru_cache(maxsize=1)
def _tokenizer() -> Tokenizer:
    try:
        archive_data = _ARCHIVE.read_bytes()
    except OSError as error:
        raise RuntimeError(f"DeepSeek tokenizer asset is unavailable: {_ARCHIVE}") from error
    actual_hash = hashlib.sha256(archive_data).hexdigest()
    if actual_hash != _ARCHIVE_SHA256:
        raise RuntimeError("DeepSeek tokenizer asset checksum mismatch")
    try:
        with ZipFile(io.BytesIO(archive_data)) as archive:
            tokenizer_data = archive.read(_TOKENIZER_MEMBER)
        return Tokenizer.from_buffer(tokenizer_data)
    except Exception as error:
        raise RuntimeError("DeepSeek tokenizer asset could not be loaded") from error


def count_text(text: str) -> int:
    if not isinstance(text, str):
        raise TypeError("Token counting expects text")
    return len(_tokenizer().encode(text, add_special_tokens=False).ids)


def count_value(value: object) -> int:
    text = value if isinstance(value, str) else json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return count_text(text)


def input_projection(items) -> tuple[list[dict], int]:
    """Count native media with a reserve; tool/JSON strings remain plain text."""
    projected, media_tokens = [], 0
    for item in items:
        value = item.model_dump(mode="json", exclude_none=True) if hasattr(item, "model_dump") else item
        if value.get("type") == "message" and isinstance(value.get("content"), list):
            content = []
            for block in value["content"]:
                if block.get("type") in {"image", "audio", "video", "file"}:
                    block = dict(block)
                    source = block.get("source")
                    if isinstance(source, dict):
                        block["source"] = {
                            key: field for key, field in source.items()
                            if key not in {"data", "uri"}
                        }
                    media_tokens += 1024
                content.append(block)
            value = {**value, "content": content}
        projected.append(value)
    return projected, media_tokens


def count_items(items) -> int:
    projected, media_tokens = input_projection(items)
    return (count_value(projected) if projected else 0) + media_tokens


def count_request(system_prompt: str, items, tools) -> int:
    projected, media_tokens = input_projection(items)
    schemas = [
        tool.model_dump(mode="json", exclude_none=True)
        if hasattr(tool, "model_dump") else tool for tool in tools
    ]
    return count_value({
        "system_prompt": system_prompt, "input": projected, "tools": schemas,
    }) + media_tokens


def count_chat_request(messages, tools) -> int:
    projected, media_tokens = [], 0
    for message in messages:
        value = dict(message)
        if isinstance(value.get("content"), list):
            content = []
            for block in value["content"]:
                if isinstance(block, dict) and block.get("type") in {
                    "image_url", "input_image", "input_audio", "audio", "file", "video_url",
                }:
                    block = {"type": block["type"]}
                    media_tokens += 1024
                content.append(block)
            value["content"] = content
        projected.append(value)
    return count_value({"messages": projected, "tools": tools or []}) + media_tokens
