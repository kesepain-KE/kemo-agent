"""Safe uploaded-attachment resolution for provider requests and tools."""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import stat
import warnings
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageSequence, UnidentifiedImageError

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
_PIL_IMAGE_MIME = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "GIF": "image/gif",
    "WEBP": "image/webp",
    "BMP": "image/bmp",
}
_CHAT_IMAGE_MIME = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp"}
)
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


def _project_or_absolute_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _is_link_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(reparse_flag and attributes & reparse_flag)


def _same_existing_path(left: Path, right: Path) -> bool:
    """Compare filesystem identity, including Windows 8.3 path aliases."""
    try:
        return os.path.samefile(left, right)
    except OSError:
        return os.path.normcase(os.path.realpath(left)) == os.path.normcase(
            os.path.realpath(right)
        )


def _reject_linked_path(candidate: Path, base: Path) -> None:
    current = candidate
    while True:
        if _is_link_or_junction(current):
            raise AttachmentError("上传附件路径不得包含符号链接或目录联接")
        if _same_existing_path(current, base):
            return
        parent = current.parent
        if parent == current:
            raise AttachmentError("附件路径必须位于当前用户的 file_upload 目录")
        current = parent


def _safe_uploaded_path(root: Path, user: str, value: Any) -> tuple[Path, str]:
    if not isinstance(value, str) or not value.strip():
        raise AttachmentError("上传附件缺少有效 path")
    root_resolved = root.resolve()
    base = _upload_root(root_resolved, user)
    raw = Path(value.strip())
    candidate = raw if raw.is_absolute() else root_resolved / raw
    candidate = Path(os.path.abspath(candidate))
    _reject_linked_path(candidate, base)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(base)
    except FileNotFoundError:
        raise AttachmentError(f"上传附件不存在：{value}") from None
    except ValueError:
        raise AttachmentError("附件路径必须位于当前用户的 file_upload 目录") from None
    if not resolved.is_file():
        raise AttachmentError(f"上传附件不是文件：{value}")
    return resolved, resolved.relative_to(root_resolved).as_posix()


def _message_files_root(root: Path, user: str, source: Any) -> tuple[Path, Path, str]:
    source_name = str(source or "").strip()
    if not source_name or Path(source_name).name != source_name:
        raise AttachmentError("外部消息附件缺少有效来源模块")
    root_resolved = root.resolve()
    plugin_dir = (root_resolved / "message" / "out" / source_name).resolve()
    expected_parent = (root_resolved / "message" / "out").resolve()
    try:
        plugin_dir.relative_to(expected_parent)
    except ValueError:
        raise AttachmentError("外部消息附件来源越出 message/out") from None
    config_path = plugin_dir / "message.json"
    try:
        config = json.loads(config_path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AttachmentError(f"无法验证外部消息附件来源：{source_name}（{exc}）") from exc
    if not isinstance(config, dict) or str(config.get("bound_user") or "") != user:
        raise AttachmentError("外部消息附件来源与当前用户不匹配")
    files_dir = str(config.get("files_dir") or "").strip()
    relative_root = Path(files_dir)
    if not files_dir or relative_root.is_absolute() or ".." in relative_root.parts:
        raise AttachmentError("外部消息模块 files_dir 无效")
    files_root = (plugin_dir / relative_root).resolve()
    try:
        files_root.relative_to(plugin_dir)
    except ValueError:
        raise AttachmentError("外部消息模块 files_dir 越出模块目录") from None
    return plugin_dir, files_root, source_name


def _safe_message_path(
    root: Path,
    user: str,
    source: Any,
    value: Any,
) -> tuple[Path, str, str]:
    if not isinstance(value, str) or not value.strip():
        raise AttachmentError("外部消息附件缺少有效 path")
    root_resolved = root.resolve()
    plugin_dir, files_root, source_name = _message_files_root(root_resolved, user, source)
    raw = Path(value.strip())
    if raw.is_absolute():
        candidate = raw
    else:
        root_candidate = Path(os.path.abspath(root_resolved / raw))
        try:
            root_candidate.relative_to(plugin_dir)
            candidate = root_candidate
        except ValueError:
            candidate = Path(os.path.abspath(plugin_dir / raw))
    _reject_linked_path(candidate, files_root)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(files_root)
    except FileNotFoundError:
        raise AttachmentError(f"外部消息附件不存在：{value}") from None
    except ValueError:
        raise AttachmentError("外部消息附件必须位于对应模块的 files_dir") from None
    if not resolved.is_file():
        raise AttachmentError(f"外部消息附件不是文件：{value}")
    return resolved, resolved.relative_to(root_resolved).as_posix(), source_name


def _safe_local_path(root: Path, value: Any) -> tuple[Path, str]:
    if not isinstance(value, str) or not value.strip():
        raise AttachmentError("本地媒体缺少有效 path")
    root_resolved = root.resolve()
    raw = Path(value.strip()).expanduser()
    candidate = raw if raw.is_absolute() else root_resolved / raw
    candidate = Path(os.path.abspath(candidate))
    if _is_link_or_junction(candidate):
        raise AttachmentError("本地媒体路径不能是符号链接或目录联接")
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        raise AttachmentError(f"本地媒体不存在：{value}") from None
    if not resolved.is_file():
        raise AttachmentError(f"本地媒体不是文件：{value}")
    return resolved, _project_or_absolute_path(root_resolved, resolved)


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
        if not _signature_matches(path, media_kind, handle.read(64)):
            return False
    if media_kind != "image":
        return True
    expected_mime = _IMAGE_SUFFIX_MIME.get(path.suffix.casefold())
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                detected_mime = _PIL_IMAGE_MIME.get(
                    str(image.format or "").upper()
                )
                image.verify()
            with Image.open(path) as image:
                for frame in ImageSequence.Iterator(image):
                    frame.load()
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        SyntaxError,
        UnidentifiedImageError,
    ):
        return False
    return detected_mime == expected_mime


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
        "scope": "file_upload",
        "relative_path": relative,
        "mime_type": mime_type,
        "size": size,
        "checksum_sha256": checksum,
        "media_kind": media_kind,
        "is_image": media_kind == "image",
        "is_audio": media_kind == "audio",
        "is_video": media_kind == "video",
        "is_file": media_kind == "file",
        "origin": "web_upload",
        "cleanup_policy": "retain",
    }


def history_attachment_descriptors(values: Any) -> list[dict[str, Any]]:
    """Return durable, UI-safe metadata without inline data or absolute paths."""

    if not isinstance(values, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in values:
        if not isinstance(raw, dict):
            continue
        asset_id = str(raw.get("asset_id") or "").strip()
        name = Path(str(raw.get("name") or "attachment")).name[:255]
        media_kind = str(raw.get("media_kind") or "file").strip().lower()
        if media_kind not in {"image", "audio", "video", "file"}:
            media_kind = "file"
        mime_type = str(raw.get("mime_type") or "application/octet-stream").strip()
        scope = str(raw.get("scope") or "").strip()
        relative_path = str(raw.get("relative_path") or "").replace("\\", "/").strip("/")
        if not relative_path and str(raw.get("origin") or "") == "web_upload":
            project_path = str(raw.get("path") or "").replace("\\", "/")
            marker = "/file_upload/"
            if marker in f"/{project_path}":
                relative_path = f"/{project_path}".split(marker, 1)[1].strip("/")
            scope = scope or "file_upload"
        if relative_path and any(part in {"", ".", ".."} for part in relative_path.split("/")):
            relative_path = ""
        if scope != "file_upload":
            scope = "external"
            relative_path = ""
        key = asset_id or f"{scope}\0{relative_path}\0{name}"
        if key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "asset_id": asset_id,
                "name": name,
                "media_kind": media_kind,
                "mime_type": mime_type,
                "size": max(0, int(raw.get("size") or 0)),
                "checksum_sha256": str(raw.get("checksum_sha256") or "").strip(),
                "scope": scope,
                "relative_path": relative_path,
            }
        )
    return result


def describe_message_asset(
    root: Path,
    user: str,
    value: dict[str, Any],
    *,
    source: str | None = None,
) -> dict[str, Any]:
    """Validate one external-message attachment and issue a Run-scoped asset handle."""

    source_value = source if source is not None else str(value.get("source") or "")
    path, project_path, source_name = _safe_message_path(
        root, user, source_value, value.get("path")
    )
    size = path.stat().st_size
    checksum = _sha256(path)
    _, files_root, _ = _message_files_root(root, user, source_name)
    relative = path.relative_to(files_root).as_posix()
    asset_id = "asset_" + hashlib.sha256(
        f"message\0{user}\0{source_name}\0{relative}\0{checksum}".encode("utf-8")
    ).hexdigest()[:32]
    media_kind, mime_type = _media_kind_and_mime(path)
    return {
        "asset_id": asset_id,
        "name": str(value.get("name") or path.name),
        "path": project_path,
        "mime_type": mime_type,
        "size": size,
        "checksum_sha256": checksum,
        "media_kind": media_kind,
        "is_image": media_kind == "image",
        "is_audio": media_kind == "audio",
        "is_video": media_kind == "video",
        "is_file": media_kind == "file",
        "origin": "message_attachment",
        "source": source_name,
        "cleanup_policy": "transport_owned",
    }


def describe_local_asset(root: Path, user: str, value: dict[str, Any]) -> dict[str, Any]:
    """Validate an explicit local path for direct use by the multimodal tool."""

    path, display_path = _safe_local_path(root, value.get("path"))
    size = path.stat().st_size
    checksum = _sha256(path)
    asset_id = "asset_" + hashlib.sha256(
        f"local\0{user}\0{path}\0{checksum}".encode("utf-8")
    ).hexdigest()[:32]
    media_kind, mime_type = _media_kind_and_mime(path)
    return {
        "asset_id": asset_id,
        "name": str(value.get("name") or path.name),
        "path": display_path,
        "mime_type": mime_type,
        "size": size,
        "checksum_sha256": checksum,
        "media_kind": media_kind,
        "is_image": media_kind == "image",
        "is_audio": media_kind == "audio",
        "is_video": media_kind == "video",
        "is_file": media_kind == "file",
        "origin": "local_path",
        "cleanup_policy": "retain",
    }


def describe_uploaded_assets(
    root: Path,
    user: str,
    values: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [describe_uploaded_asset(root, user, value) for value in values]


class RunAssetResolver:
    """Resolve Web, external-message and explicit local assets for one Run/tool call."""

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
        origin = str(descriptor.get("origin") or "web_upload")
        if origin == "web_upload":
            verified = describe_uploaded_asset(self.root, self.user, descriptor)
            path, _ = _safe_uploaded_path(self.root, self.user, verified["path"])
        elif origin == "message_attachment":
            verified = describe_message_asset(self.root, self.user, descriptor)
            path, _, _ = _safe_message_path(
                self.root,
                self.user,
                verified.get("source"),
                verified["path"],
            )
        elif origin == "local_path":
            verified = describe_local_asset(self.root, self.user, descriptor)
            path, _ = _safe_local_path(self.root, verified["path"])
        else:
            raise AttachmentError(f"不支持的运行资产来源：{origin}")
        if verified["asset_id"] != asset_id:
            raise AttachmentError(f"附件内容已经变化，请重新登记：{asset_id}")
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
        if str(provider).casefold() == "chat" and mime_type not in _CHAT_IMAGE_MIME:
            raise AttachmentError(
                f"Chat 图片通道不支持 {path.suffix or mime_type} 格式；"
                "请转换为 JPEG、PNG、WEBP 或 GIF"
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


# 兼容既有导入；新代码应使用更准确的 RunAssetResolver。
UploadedAssetResolver = RunAssetResolver
