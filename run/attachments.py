"""Safe uploaded-attachment resolution for provider requests and tools."""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import os
from pathlib import Path
from typing import Any, Iterable

from provider.protocol.assets import ResolvedAsset
from provider.protocol.models import (
    AudioContent,
    FileContent,
    ImageContent,
    MediaSource,
    VideoContent,
)


IMAGE_MAX_BYTES = 20 * 1024 * 1024
ACCEPTED_UPLOAD_MAX_BYTES = 80 * 1024 * 1024
AUDIO_MAX_BYTES = 100 * 1024 * 1024
VIDEO_MAX_BYTES = 1024 * 1024 * 1024
FILE_MAX_BYTES = 100 * 1024 * 1024
_IMAGE_SUFFIX_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}
_AUDIO_SUFFIX_MIME = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}
_VIDEO_SUFFIX_MIME = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
}
_MEDIA_LIMITS = {
    "image": IMAGE_MAX_BYTES,
    "audio": AUDIO_MAX_BYTES,
    "video": VIDEO_MAX_BYTES,
    "file": FILE_MAX_BYTES,
}


class AttachmentError(RuntimeError):
    """An uploaded attachment is missing, unsafe, or unsupported."""


def _upload_root(root: Path, user: str) -> Path:
    return (root / "users" / user / "file_upload").resolve()


def _safe_uploaded_path(root: Path, user: str, value: Any) -> tuple[Path, str]:
    if not isinstance(value, str) or not value.strip():
        raise AttachmentError("上传附件缺少有效 path")
    base = _upload_root(root.resolve(), user)
    raw = Path(value.strip())
    candidate = raw if raw.is_absolute() else root.resolve() / raw
    lexical = Path(os.path.abspath(candidate))
    try:
        lexical_relative = lexical.relative_to(base)
        current = base
        for part in lexical_relative.parts:
            current = current / part
            is_junction = getattr(current, "is_junction", lambda: False)
            if current.is_symlink() or is_junction():
                raise AttachmentError("上传附件路径不得包含符号链接或目录联接")
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(base)
    except FileNotFoundError:
        raise AttachmentError(f"上传附件不存在：{value}") from None
    except ValueError:
        raise AttachmentError("附件路径必须位于当前用户的 file_upload 目录") from None
    if not resolved.is_file():
        raise AttachmentError(f"上传附件不是文件：{value}")
    return resolved, resolved.relative_to(root.resolve()).as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _image_mime_from_signature(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    return None


def _media_kind_and_mime(path: Path) -> tuple[str, str]:
    suffix = path.suffix.casefold()
    if suffix in _IMAGE_SUFFIX_MIME:
        return "image", _IMAGE_SUFFIX_MIME[suffix]
    if suffix in _AUDIO_SUFFIX_MIME:
        return "audio", _AUDIO_SUFFIX_MIME[suffix]
    if suffix in _VIDEO_SUFFIX_MIME:
        return "video", _VIDEO_SUFFIX_MIME[suffix]
    return "file", mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _signature_matches(path: Path, media_kind: str, data: bytes) -> bool:
    """Reject renamed media while leaving ordinary documents to the file tool."""

    suffix = path.suffix.casefold()
    if media_kind == "image":
        return _image_mime_from_signature(data) == _IMAGE_SUFFIX_MIME.get(suffix)
    if media_kind == "audio":
        if suffix == ".wav":
            return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"
        if suffix == ".mp3":
            return data.startswith(b"ID3") or (
                len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0
            )
        if suffix == ".m4a":
            return len(data) >= 12 and data[4:8] == b"ftyp"
        if suffix == ".aac":
            return len(data) >= 2 and data[0] == 0xFF and data[1] & 0xF6 == 0xF0
        if suffix == ".ogg":
            return data.startswith(b"OggS")
        if suffix == ".flac":
            return data.startswith(b"fLaC")
    if media_kind == "video":
        if suffix in {".mp4", ".mov"}:
            return len(data) >= 12 and data[4:8] == b"ftyp"
        if suffix in {".webm", ".mkv"}:
            return data.startswith(b"\x1a\x45\xdf\xa3")
        if suffix == ".avi":
            return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"AVI "
    return media_kind == "file"


def validate_media_file(path: Path, media_kind: str) -> bool:
    with path.open("rb") as handle:
        return _signature_matches(path, media_kind, handle.read(64))


def describe_uploaded_asset(root: Path, user: str, value: dict[str, Any]) -> dict[str, Any]:
    """Validate one Web attachment and attach a stable, non-path asset handle."""

    path, project_path = _safe_uploaded_path(root, user, value.get("path"))
    size = path.stat().st_size
    checksum = _sha256(path)
    relative = path.relative_to(_upload_root(root, user)).as_posix()
    asset_id = "asset_" + hashlib.sha256(
        f"{user}\0{relative}\0{checksum}".encode("utf-8")
    ).hexdigest()[:32]
    media_kind, mime_type = _media_kind_and_mime(path)
    return {
        "asset_id": asset_id,
        "name": path.name,
        "path": project_path,
        "mime_type": mime_type,
        "size": size,
        "checksum_sha256": checksum,
        "media_kind": media_kind,
        "is_image": media_kind == "image",
        "is_audio": media_kind == "audio",
        "is_video": media_kind == "video",
        "is_file": media_kind == "file",
    }


def describe_uploaded_assets(
    root: Path,
    user: str,
    values: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [describe_uploaded_asset(root, user, value) for value in values]


class UploadedAssetResolver:
    """Resolve only the uploaded assets explicitly attached to the current Run."""

    def __init__(self, root: Path, user: str, descriptors: Iterable[dict[str, Any]]) -> None:
        self.root = root.resolve()
        self.user = user
        self._descriptors = {
            str(item.get("asset_id") or ""): dict(item)
            for item in descriptors
            if isinstance(item, dict) and str(item.get("asset_id") or "")
        }

    def _validated(
        self,
        asset_id: str,
        *,
        expected_kind: str | None = None,
    ) -> tuple[dict[str, Any], Path, str, int]:
        descriptor = self._descriptors.get(str(asset_id or ""))
        if descriptor is None:
            raise AttachmentError(f"附件不属于当前 Run 或不存在：{asset_id}")
        verified = describe_uploaded_asset(self.root, self.user, descriptor)
        if verified["asset_id"] != asset_id:
            raise AttachmentError(f"附件内容已经变化，请重新上传：{asset_id}")
        path, _ = _safe_uploaded_path(self.root, self.user, verified["path"])
        media_kind = str(verified.get("media_kind") or "file")
        if expected_kind is not None and media_kind != expected_kind:
            raise AttachmentError(
                f"附件类型不匹配：期望 {expected_kind}，实际为 {media_kind}"
            )
        size = path.stat().st_size
        limit = _MEDIA_LIMITS[media_kind]
        if size > limit:
            raise AttachmentError(
                f"{media_kind} 附件超过请求上限 {limit // (1024 * 1024)} MB"
            )
        if not validate_media_file(path, media_kind):
            if media_kind == "image":
                raise AttachmentError(f"文件不是受支持的真实图片：{path.name}")
            raise AttachmentError(f"文件内容与声明的 {media_kind} 类型不匹配：{path.name}")
        return verified, path, str(verified["mime_type"]), size

    def local_asset(
        self,
        asset_id: str,
        *,
        expected_kind: str | None = None,
    ) -> tuple[Path, dict[str, Any]]:
        descriptor, path, _, _ = self._validated(
            asset_id, expected_kind=expected_kind
        )
        return path, descriptor

    def resolve(self, asset_id: str, *, provider: str) -> ResolvedAsset:
        verified, path, mime_type, size = self._validated(
            asset_id, expected_kind="image"
        )
        data = path.read_bytes()
        return ResolvedAsset(
            asset_id=asset_id,
            mime_type=mime_type,
            size=size,
            source=MediaSource(
                kind="inline_base64",
                data=base64.b64encode(data).decode("ascii"),
            ),
            filename=path.name,
        )

    def image_content(self, asset_id: str, *, provider: str, detail: str = "auto") -> ImageContent:
        resolved = self.resolve(asset_id, provider=provider)
        return ImageContent(
            asset_id=resolved.asset_id,
            source=resolved.source,
            mime_type=resolved.mime_type,
            checksum_sha256=self._descriptors[asset_id].get("checksum_sha256"),
            detail=detail,
        )

    def remote_content(
        self,
        asset_id: str,
        *,
        remote_asset_id: str,
    ) -> ImageContent | AudioContent | VideoContent | FileContent:
        verified, _, mime_type, _ = self._validated(asset_id)
        common = {
            "asset_id": remote_asset_id,
            "mime_type": mime_type,
            "checksum_sha256": verified.get("checksum_sha256"),
        }
        media_kind = str(verified.get("media_kind") or "file")
        if media_kind == "image":
            return ImageContent(**common)
        if media_kind == "audio":
            return AudioContent(**common)
        if media_kind == "video":
            return VideoContent(**common)
        return FileContent(filename=str(verified.get("name") or ""), **common)
