"""Persist Kemo media outputs as user-owned download artifacts."""

from __future__ import annotations

import mimetypes
import re
import threading
from pathlib import Path
from typing import Any

from provider.protocol.enums import MessageRole
from provider.protocol.models import (
    AudioContent,
    FileContent,
    ImageContent,
    KemoResponse,
    MessageItem,
    VideoContent,
)
from provider.schema import ProviderError
from run.attachments import validate_media_file


_OUTPUT_LOCK = threading.RLock()
_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MIME_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/mp4": ".m4a",
    "audio/ogg": ".ogg",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
}
_MEDIA_BLOCKS = (ImageContent, AudioContent, VideoContent, FileContent)
_OUTPUT_LIMITS = {
    "image": 20 * 1024 * 1024,
    "audio": 100 * 1024 * 1024,
    "video": 1024 * 1024 * 1024,
    "file": 100 * 1024 * 1024,
}


def _safe_filename(value: str, *, asset_id: str, mime_type: str) -> str:
    name = _UNSAFE_FILENAME.sub("_", Path(value or "").name).strip(" .")
    extension = _MIME_EXTENSIONS.get(mime_type) or mimetypes.guess_extension(mime_type)
    if not name:
        name = asset_id
    if extension and not Path(name).suffix:
        name += extension
    return name[:180]


def _unique_target(directory: Path, filename: str) -> tuple[Path, str]:
    target = directory / filename
    if not target.exists():
        return target, filename
    stem = target.stem
    suffix = target.suffix
    index = 2
    while True:
        candidate_name = f"{stem} ({index}){suffix}"
        candidate = directory / candidate_name
        if not candidate.exists():
            return candidate, candidate_name
        index += 1


def persist_response_media(
    provider: Any,
    response: KemoResponse,
    *,
    root: Path,
    user: str,
    cancel_event: threading.Event | None = None,
) -> list[dict[str, Any]]:
    """Download assistant media Assets, verify checksums, and return stable refs."""

    get_asset = getattr(provider, "get_asset", None)
    wait_asset_ready = getattr(provider, "wait_asset_ready", None)
    download_asset = getattr(provider, "download_asset", None)
    blocks = [
        block
        for item in response.output
        if isinstance(item, MessageItem) and item.role == MessageRole.ASSISTANT
        for block in item.content
        if isinstance(block, _MEDIA_BLOCKS)
    ]
    if not blocks:
        return []
    if not callable(get_asset) or not callable(wait_asset_ready) or not callable(download_asset):
        raise ProviderError(
            "Provider 返回了媒体内容，但没有实现 Kemo Asset 下载客户端",
            category="asset_error",
        )

    directory = (root / "users" / user / "download").resolve()
    directory.mkdir(parents=True, exist_ok=True)
    artifacts: list[dict[str, Any]] = []
    for block in blocks:
        asset_id = str(block.asset_id or "")
        if not asset_id:
            raise ProviderError(
                "Kemo 媒体输出缺少稳定 asset_id",
                category="gateway_protocol_error",
            )
        descriptor = wait_asset_ready(
            get_asset(asset_id),
            cancel_event=cancel_event,
        )
        mime_type = str(block.mime_type or descriptor.mime_type)
        if block.mime_type and descriptor.mime_type != block.mime_type:
            raise ProviderError(
                f"媒体输出 MIME 与 Asset 元数据不一致：{asset_id}",
                category="asset_integrity_error",
            )
        expected = str(block.checksum_sha256 or descriptor.checksum_sha256)
        if block.checksum_sha256 and descriptor.checksum_sha256 != block.checksum_sha256:
            raise ProviderError(
                f"媒体输出校验和与 Asset 元数据不一致：{asset_id}",
                category="asset_integrity_error",
            )
        requested_name = (
            block.filename
            if isinstance(block, FileContent) and block.filename
            else descriptor.filename
        )
        filename = _safe_filename(
            str(requested_name or ""),
            asset_id=asset_id,
            mime_type=mime_type,
        )
        media_type = str(getattr(block, "type", "file"))
        max_bytes = _OUTPUT_LIMITS.get(media_type, _OUTPUT_LIMITS["file"])
        if descriptor.size > max_bytes:
            raise ProviderError(
                f"Kemo 媒体输出超过本地 {media_type} 大小限制：{asset_id}",
                category="request_too_large",
            )
        with _OUTPUT_LOCK:
            target, filename = _unique_target(directory, filename)
            download_asset(
                asset_id,
                target,
                expected_sha256=expected,
                expected_size=descriptor.size,
                max_bytes=max_bytes,
                cancel_event=cancel_event,
            )
            if not validate_media_file(target, media_type):
                target.unlink(missing_ok=True)
                raise ProviderError(
                    f"Kemo 媒体输出内容与声明类型不匹配：{asset_id}",
                    category="asset_integrity_error",
                )
        artifacts.append(
            {
                "asset_id": asset_id,
                "type": media_type,
                "name": filename,
                "scope": "download",
                "path": filename,
                "project_path": target.relative_to(root.resolve()).as_posix(),
                "mime_type": mime_type,
                "size": target.stat().st_size,
                "checksum_sha256": expected,
                **(
                    {"duration_ms": block.duration_ms}
                    if isinstance(block, (AudioContent, VideoContent))
                    and block.duration_ms is not None
                    else {}
                ),
            }
        )
    return artifacts
