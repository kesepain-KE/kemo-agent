"""Validation and display helpers for one conversation request."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from provider.protocol.models import ContentBlock, TextContent
from run.infra import EngineError


_CONTENT_LIST_ADAPTER = TypeAdapter(list[ContentBlock])


def required_text(request: dict[str, Any], name: str) -> str:
    value = request.get(name)
    if not isinstance(value, str) or not value.strip():
        raise EngineError(f"请求字段 {name!r} 必须是非空字符串")
    return value.strip()


def request_content_blocks(request: dict[str, Any]) -> list[ContentBlock]:
    prompt_value = request.get("prompt", "")
    prompt = prompt_value.strip() if isinstance(prompt_value, str) else ""
    raw_content = request.get("content")
    if raw_content is None:
        raw_content = []
    if not isinstance(raw_content, list):
        raise EngineError("请求字段 'content' 必须是 Content Block 数组")
    combined: list[Any] = []
    if prompt:
        combined.append({"type": "text", "text": prompt})
    combined.extend(raw_content)
    if not combined:
        return []
    try:
        return _CONTENT_LIST_ADAPTER.validate_python(combined)
    except ValidationError as exc:
        raise EngineError(
            f"请求字段 'content' 无效：{exc.errors(include_url=False)}"
        ) from exc


def content_for_message(
    blocks: list[ContentBlock],
) -> str | list[dict[str, Any]]:
    if all(isinstance(block, TextContent) for block in blocks):
        return "".join(
            block.text for block in blocks if isinstance(block, TextContent)
        )
    return [block.model_dump(mode="json", exclude_none=True) for block in blocks]


def uploaded_file_context(
    request: dict[str, Any],
    *,
    vision_route: str | None = None,
    direct_asset_ids: set[str] | None = None,
) -> str:
    raw_files = request.get("uploaded_files") or []
    if not isinstance(raw_files, list):
        raise EngineError("请求字段 'uploaded_files' 必须是文件描述数组")
    lines: list[str] = []
    for item in raw_files:
        if not isinstance(item, dict):
            raise EngineError("请求字段 'uploaded_files' 包含无效文件描述")
        path = str(item.get("path") or "").strip()
        name = str(item.get("name") or "").strip()
        if not path:
            raise EngineError("上传文件描述缺少 path")
        size = max(0, int(item.get("size") or 0))
        asset_id = str(item.get("asset_id") or "").strip()
        mime_type = str(
            item.get("mime_type") or "application/octet-stream"
        ).strip()
        suffix = ""
        direct = asset_id in (direct_asset_ids or set())
        media_kind = str(
            item.get("media_kind") or ("image" if item.get("is_image") else "file")
        )
        if media_kind == "image" and asset_id:
            suffix = (
                f"；图片资产 {asset_id} 已直接提供给主模型"
                if direct or vision_route == "main"
                else f"；图片资产 {asset_id}，需要查看时调用 multimodal 工具"
            )
        elif media_kind == "audio" and asset_id:
            suffix = (
                f"；音频资产 {asset_id} 已直接提供给主模型"
                if direct
                else f"；音频资产 {asset_id}，需要处理时调用 multimodal 工具"
            )
        elif media_kind == "video" and asset_id:
            suffix = (
                f"；视频资产 {asset_id} 已直接提供给主模型"
                if direct
                else f"；视频资产 {asset_id}，需要分析时调用 multimodal 工具"
            )
        elif media_kind == "file" and asset_id and direct:
            suffix = f"；文件资产 {asset_id} 已直接提供给主模型"
        lines.append(
            f"- {name or Path(path).name}：{path}（{mime_type}，{size} bytes{suffix}）"
        )
    if not lines:
        return ""
    return (
        "\n\n[本轮输入资产]\n"
        "以下文件已经由 Web、外部消息或运行工具登记；普通文件可按需使用 file 工具读取，"
        "并请严格按每项资产说明选择主模型或 multimodal 工具：\n"
        + "\n".join(lines)
    )


def content_display(blocks: list[ContentBlock]) -> str:
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, TextContent):
            parts.append(block.text)
        else:
            asset_id = getattr(block, "asset_id", None)
            parts.append(f"[{block.type}:{asset_id or 'inline'}]")
    return "\n".join(part for part in parts if part)
