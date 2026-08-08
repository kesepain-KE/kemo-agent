"""用户文件空间、临时文件空间与头像领域服务。"""

from __future__ import annotations

import io
import mimetypes
import os
import re
import warnings
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from run.attachments import AttachmentError, describe_uploaded_asset
from web.constants import (
    AVATAR_MAX_BYTES,
    FILE_UPLOAD_MAX_BYTES,
    TEXT_DOCUMENT_MAX_CHARS,
    _AVATAR_FORMATS,
    _AVATAR_SEARCH_ORDER,
    _EDITABLE_TEXT_SUFFIXES,
    _FILE_SCOPES,
    _MEDIA_PREVIEW_TYPES,
)
from web.errors import (
    ConflictError,
    InvalidRequestError,
    NotFoundError,
    WebServiceError,
)
from web.services._io import (
    atomic_write as _atomic_write,
    validated_text as _validated_text,
)
from web.services._paths import (
    _directory_listing,
    _flat_files,
    _image_media_type,
    _reject_link_path,
    _safe_relative_target,
    _visible_children,
)
from web.services.artifact_resolver import DownloadArtifactResolver


class FileServiceMixin:
    _ATTACHMENT_THUMBNAIL_SIZE = (320, 240)
    _ATTACHMENT_THUMBNAIL_RE = re.compile(r"^[a-f0-9]{64}$")
    _DOWNLOAD_ARTIFACT_RE = re.compile(r"^[a-f0-9]{64}$")
    _ATTACHMENT_IMAGE_SUFFIXES = frozenset(
        {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
    )

    def _project_path(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def _file_scope_root(self, user: Any, scope: Any) -> tuple[str, str, Path]:
        name = self.require_user(user)
        if not isinstance(scope, str) or scope not in _FILE_SCOPES:
            raise InvalidRequestError(
                "scope 只允许 file_upload 或 download"
            )
        return name, scope, self.root / "users" / name / scope

    def files(
        self,
        user: Any,
        scope: Any,
        *,
        path: str = "",
        search: str = "",
        page: int = 1,
        page_size: int = 6,
    ) -> dict[str, Any]:
        name, normalized_scope, directory = self._file_scope_root(user, scope)
        return {
            "user": name,
            "scope": normalized_scope,
            "root": self._project_path(directory),
            **_directory_listing(
                directory,
                path=path,
                search=search,
                page=page,
                page_size=page_size,
            ),
        }

    def _write_area_file(
        self,
        directory: Path,
        path: Any,
        data: bytes,
        *,
        avoid_overwrite: bool = False,
    ) -> dict[str, Any]:
        if len(data) > FILE_UPLOAD_MAX_BYTES:
            raise InvalidRequestError(
                f"文件超过最大限制 {FILE_UPLOAD_MAX_BYTES // (1024 * 1024)} MB"
            )
        relative, target = _safe_relative_target(directory, path)
        _reject_link_path(directory.resolve(), target)
        if target.exists() and target.is_dir():
            raise ConflictError(f"目标是目录：{relative}")
        if avoid_overwrite and target.exists():
            stem = target.stem
            suffix = target.suffix
            index = 2
            while True:
                candidate = target.with_name(f"{stem} ({index}){suffix}")
                _reject_link_path(directory.resolve(), candidate)
                if not candidate.exists():
                    target = candidate
                    relative = target.relative_to(directory.resolve()).as_posix()
                    break
                index += 1
        _atomic_write(target, data)
        return {
            "path": relative,
            "size": len(data),
            "updated": True,
            "renamed": str(relative) != str(path).replace("\\", "/"),
        }

    def save_file(self, user: Any, scope: Any, path: Any, data: bytes) -> dict[str, Any]:
        name, normalized_scope, directory = self._file_scope_root(user, scope)
        with self._file_upload_lock:
            result = self._write_area_file(
                directory,
                path,
                data,
                avoid_overwrite=True,
            )
            thumbnail_metadata: dict[str, Any] = {}
            if (
                normalized_scope == "file_upload"
                and Path(str(result["path"])).suffix.casefold()
                in self._ATTACHMENT_IMAGE_SUFFIXES
            ):
                _, target = _safe_relative_target(directory, result["path"])
                try:
                    descriptor = describe_uploaded_asset(
                        self.root,
                        name,
                        {"path": self._project_path(target)},
                    )
                except (AttachmentError, OSError):
                    descriptor = None
                if descriptor is not None:
                    checksum = str(descriptor.get("checksum_sha256") or "")
                    thumbnail_metadata = {
                        "checksum_sha256": checksum,
                        "media_kind": str(descriptor.get("media_kind") or "file"),
                        "mime_type": str(
                            descriptor.get("mime_type")
                            or "application/octet-stream"
                        ),
                        "thumbnail_available": bool(
                            self._ensure_attachment_thumbnail(
                                name,
                                target,
                                descriptor,
                            )
                        ),
                    }
        return {
            "user": name,
            "scope": normalized_scope,
            **result,
            **thumbnail_metadata,
        }

    def _attachment_thumbnail_path(self, user: str, checksum: str) -> Path:
        normalized = str(checksum or "").strip().lower()
        if not self._ATTACHMENT_THUMBNAIL_RE.fullmatch(normalized):
            raise InvalidRequestError("附件缩略图标识无效")
        return (
            self.root
            / "users"
            / user
            / "history"
            / "thumbnails"
            / f"{normalized}.webp"
        )

    def _ensure_attachment_thumbnail(
        self,
        user: str,
        source: Path,
        descriptor: dict[str, Any],
    ) -> str:
        """Create one small durable preview without retaining the source file."""

        if str(descriptor.get("media_kind") or "") != "image":
            return ""
        checksum = str(descriptor.get("checksum_sha256") or "").strip().lower()
        try:
            target = self._attachment_thumbnail_path(user, checksum)
        except InvalidRequestError:
            return ""
        if target.is_file() and not target.is_symlink():
            return checksum
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(source) as opened:
                    opened.seek(0)
                    image = ImageOps.exif_transpose(opened)
                    image.thumbnail(
                        self._ATTACHMENT_THUMBNAIL_SIZE,
                        Image.Resampling.LANCZOS,
                    )
                    if image.mode not in {"RGB", "RGBA"}:
                        image = image.convert(
                            "RGBA" if "transparency" in image.info else "RGB"
                        )
                    buffer = io.BytesIO()
                    image.save(buffer, format="WEBP", quality=78, method=4)
            _atomic_write(target, buffer.getvalue())
        except (
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            OSError,
            SyntaxError,
            UnidentifiedImageError,
            ValueError,
        ):
            return ""
        return checksum

    def attachment_thumbnail(
        self,
        user: Any,
        checksum: Any,
        *,
        path: Any = "",
    ) -> Path:
        """Return a cached preview, lazily backfilling legacy attachments."""

        name = self.require_user(user)
        normalized = str(checksum or "").strip().lower()
        target = self._attachment_thumbnail_path(name, normalized)
        if target.is_file() and not target.is_symlink():
            return target

        if isinstance(path, str) and path.strip():
            upload_root = (self.root / "users" / name / "file_upload").resolve()
            _, source = _safe_relative_target(upload_root, path)
            _reject_link_path(upload_root, source)
            if (
                source.suffix.casefold() in self._ATTACHMENT_IMAGE_SUFFIXES
                and source.is_file()
                and not source.is_symlink()
            ):
                try:
                    descriptor = describe_uploaded_asset(
                        self.root,
                        name,
                        {"path": self._project_path(source)},
                    )
                except (AttachmentError, OSError):
                    descriptor = None
                if (
                    descriptor is not None
                    and str(descriptor.get("checksum_sha256") or "").lower()
                    == normalized
                ):
                    with self._file_upload_lock:
                        self._ensure_attachment_thumbnail(name, source, descriptor)
        if target.is_file() and not target.is_symlink():
            return target
        raise NotFoundError("附件缩略图不存在")

    def require_uploaded_files(self, user: str, uploaded_files: Any) -> list[dict[str, Any]]:
        if uploaded_files in (None, []):
            return []
        if not isinstance(uploaded_files, list) or len(uploaded_files) > 20:
            raise InvalidRequestError("uploaded_files 必须是不超过 20 项的文件路径数组")
        directory = (self.root / "users" / user / "file_upload").resolve()
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for value in uploaded_files:
            if not isinstance(value, str) or not value.strip():
                raise InvalidRequestError("uploaded_files 只能包含非空文件路径")
            relative, target = _safe_relative_target(directory, value)
            _reject_link_path(directory, target)
            relative_path = relative.as_posix() if isinstance(relative, Path) else str(relative).replace("\\", "/")
            if relative_path in seen:
                continue
            if not target.is_file():
                raise NotFoundError(f"上传文件不存在：{relative_path}")
            seen.add(relative_path)
            try:
                descriptor = describe_uploaded_asset(
                    self.root,
                    user,
                    {
                    "name": target.name,
                    "path": self._project_path(target),
                    "size": target.stat().st_size,
                    },
                )
                self._ensure_attachment_thumbnail(user, target, descriptor)
                normalized.append(descriptor)
            except AttachmentError as exc:
                raise InvalidRequestError(str(exc)) from None
        return normalized

    def write_file_text(
        self,
        user: Any,
        scope: Any,
        path: Any,
        content: Any,
    ) -> dict[str, Any]:
        text = _validated_text(content)
        name, normalized_scope, directory = self._file_scope_root(user, scope)
        relative, target = _safe_relative_target(directory, path)
        if target.suffix.lower() not in _EDITABLE_TEXT_SUFFIXES:
            raise InvalidRequestError("该文件类型不允许通过文本编辑接口修改")
        result = self._write_area_file(directory, relative, text.encode("utf-8"))
        return {"user": name, "scope": normalized_scope, **result}

    def read_file_text(self, user: Any, scope: Any, path: Any) -> dict[str, Any]:
        name, normalized_scope, directory = self._file_scope_root(user, scope)
        relative, target = _safe_relative_target(directory, path)
        _reject_link_path(directory.resolve(), target)
        if target.suffix.lower() not in _EDITABLE_TEXT_SUFFIXES:
            raise InvalidRequestError("该文件类型不允许通过文本读取接口打开")
        if not target.is_file():
            raise NotFoundError(f"文件不存在：{relative}")
        try:
            data = target.read_bytes()
            if len(data) > TEXT_DOCUMENT_MAX_CHARS * 4:
                raise InvalidRequestError("文本文件超过在线编辑大小限制")
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            raise InvalidRequestError("文件不是有效 UTF-8 文本") from None
        return {"user": name, "scope": normalized_scope, "path": relative, "content": content, "size": len(data)}

    def _make_area_directory(self, directory: Path, path: Any) -> dict[str, Any]:
        relative, target = _safe_relative_target(directory, path)
        _reject_link_path(directory.resolve(), target)
        if target.exists():
            raise ConflictError(f"目标已存在：{relative}")
        target.mkdir(parents=True)
        return {"path": relative, "created": True}

    def make_directory(self, user: Any, scope: Any, path: Any) -> dict[str, Any]:
        name, normalized_scope, directory = self._file_scope_root(user, scope)
        return {
            "user": name,
            "scope": normalized_scope,
            **self._make_area_directory(directory, path),
        }

    def _move_area_path(self, directory: Path, path: Any, new_path: Any) -> dict[str, Any]:
        relative, source = _safe_relative_target(directory, path)
        target_relative, target = _safe_relative_target(directory, new_path)
        _reject_link_path(directory.resolve(), source)
        _reject_link_path(directory.resolve(), target)
        if not source.exists():
            raise NotFoundError(f"文件或目录不存在：{relative}")
        if target.exists():
            raise ConflictError(f"目标已存在：{target_relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)
        return {"path": relative, "new_path": target_relative, "moved": True}

    def move_file(self, user: Any, scope: Any, path: Any, new_path: Any) -> dict[str, Any]:
        name, normalized_scope, directory = self._file_scope_root(user, scope)
        return {
            "user": name,
            "scope": normalized_scope,
            **self._move_area_path(directory, path, new_path),
        }

    def file_download(self, user: Any, scope: Any, path: Any) -> Path:
        _, _, directory = self._file_scope_root(user, scope)
        _, target = _safe_relative_target(directory, path)
        if target.is_symlink() or not target.is_file():
            raise NotFoundError(f"文件不存在：{path}")
        return target

    def download_artifact(
        self,
        user: Any,
        checksum: Any,
        *,
        path: Any,
        size: Any,
    ) -> tuple[Path, str]:
        """Resolve a generated artifact after safe moves or nested-path changes."""

        name = self.require_user(user)
        normalized = str(checksum or "").strip().lower()
        if not self._DOWNLOAD_ARTIFACT_RE.fullmatch(normalized):
            raise InvalidRequestError("产物校验和无效")
        try:
            expected_size = int(size)
        except (TypeError, ValueError):
            raise InvalidRequestError("产物大小无效") from None
        if expected_size <= 0:
            raise InvalidRequestError("产物大小必须大于 0")

        directory = self.root / "users" / name / "download"
        resolver = getattr(self, "_download_artifact_resolver", None)
        if not isinstance(resolver, DownloadArtifactResolver):
            resolver = DownloadArtifactResolver()
            setattr(self, "_download_artifact_resolver", resolver)
        candidate = resolver.resolve(
            directory,
            normalized,
            path=path,
            expected_size=expected_size,
        )
        return (
            candidate,
            mimetypes.guess_type(candidate.name)[0] or "application/octet-stream",
        )

    def _media_preview(self, directory: Path, path: Any) -> tuple[Path, str, str]:
        relative, target = _safe_relative_target(directory, path)
        _reject_link_path(directory.resolve(), target)
        if target.is_symlink() or not target.is_file():
            raise NotFoundError(f"文件不存在：{relative}")
        preview_type = _MEDIA_PREVIEW_TYPES.get(target.suffix.lower())
        if preview_type is None:
            raise InvalidRequestError("该文件类型不支持网页预览")
        kind, media_type, max_bytes, limit_label = preview_type
        try:
            size = target.stat().st_size
        except OSError as exc:
            raise WebServiceError(f"无法读取文件信息：{relative}") from exc
        if size > max_bytes:
            raise InvalidRequestError(f"文件超过{limit_label}，请下载后查看")
        return target, media_type, kind

    def file_preview(self, user: Any, scope: Any, path: Any) -> tuple[Path, str, str]:
        _, _, directory = self._file_scope_root(user, scope)
        return self._media_preview(directory, path)

    def tmp_file_preview(self, path: Any) -> tuple[Path, str, str]:
        return self._media_preview(self.root / "tmp", path)

    def delete_file(self, user: Any, scope: Any, path: Any) -> dict[str, Any]:
        name, normalized_scope, directory = self._file_scope_root(user, scope)
        relative, target = _safe_relative_target(directory, path)
        if target.is_symlink() or not target.is_file():
            raise NotFoundError(f"文件不存在：{relative}")
        try:
            target.unlink()
        except OSError as exc:
            raise WebServiceError(f"文件删除失败：{relative}") from exc
        return {
            "user": name,
            "scope": normalized_scope,
            "path": relative,
            "deleted": True,
        }

    def delete_files(self, user: Any, scope: Any, paths: Any) -> dict[str, Any]:
        name, normalized_scope, directory = self._file_scope_root(user, scope)
        return {
            "user": name,
            "scope": normalized_scope,
            **self._delete_area_files(directory, paths, item_label="文件"),
        }

    def delete_all_files(self, user: Any, scope: Any) -> dict[str, Any]:
        name, normalized_scope, directory = self._file_scope_root(user, scope)
        directory.mkdir(parents=True, exist_ok=True)
        paths = [item["relative_path"] for item in _flat_files(directory)]
        if not paths:
            self._prune_empty_directories(directory)
            result = {"deleted_paths": [], "deleted_count": 0}
        else:
            result = self._delete_area_files(directory, paths, item_label="文件")
        return {"user": name, "scope": normalized_scope, **result}

    def tmp_files(
        self,
        *,
        path: str = "",
        search: str = "",
        page: int = 1,
        page_size: int = 6,
    ) -> dict[str, Any]:
        directory = self.root / "tmp"
        return {
            "root": "tmp",
            **_directory_listing(
                directory,
                path=path,
                search=search,
                page=page,
                page_size=page_size,
            ),
        }

    def save_tmp_file(self, path: Any, data: bytes) -> dict[str, Any]:
        return {"root": "tmp", **self._write_area_file(self.root / "tmp", path, data)}

    def write_tmp_text(self, path: Any, content: Any) -> dict[str, Any]:
        text = _validated_text(content)
        directory = self.root / "tmp"
        relative, target = _safe_relative_target(directory, path)
        if target.suffix.lower() not in _EDITABLE_TEXT_SUFFIXES:
            raise InvalidRequestError("该文件类型不允许通过文本编辑接口修改")
        return {"root": "tmp", **self._write_area_file(directory, relative, text.encode())}

    def read_tmp_text(self, path: Any) -> dict[str, Any]:
        directory = self.root / "tmp"
        relative, target = _safe_relative_target(directory, path)
        _reject_link_path(directory.resolve(), target)
        if target.suffix.lower() not in _EDITABLE_TEXT_SUFFIXES:
            raise InvalidRequestError("该文件类型不允许通过文本读取接口打开")
        if not target.is_file():
            raise NotFoundError(f"文件不存在：{relative}")
        data = target.read_bytes()
        if len(data) > TEXT_DOCUMENT_MAX_CHARS * 4:
            raise InvalidRequestError("文本文件超过在线编辑大小限制")
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            raise InvalidRequestError("文件不是有效 UTF-8 文本") from None
        return {"root": "tmp", "path": relative, "content": content, "size": len(data)}

    def make_tmp_directory(self, path: Any) -> dict[str, Any]:
        return {"root": "tmp", **self._make_area_directory(self.root / "tmp", path)}

    def move_tmp_file(self, path: Any, new_path: Any) -> dict[str, Any]:
        return {
            "root": "tmp",
            **self._move_area_path(self.root / "tmp", path, new_path),
        }

    def delete_tmp_file(self, path: Any) -> dict[str, Any]:
        result = self.delete_tmp_files([path])
        return {"path": result["deleted_paths"][0], "deleted": True}

    def delete_tmp_files(self, paths: Any) -> dict[str, Any]:
        return self._delete_area_files(self.root / "tmp", paths, item_label="临时文件")

    def _delete_area_files(
        self,
        directory: Path,
        paths: Any,
        *,
        item_label: str,
    ) -> dict[str, Any]:
        if not isinstance(paths, list) or not paths:
            raise InvalidRequestError("paths 必须是非空数组")
        if len(paths) > 10_000:
            raise InvalidRequestError(f"单次最多删除 10000 个{item_label}")

        root = directory.resolve()
        validated: list[tuple[str, Path]] = []
        seen: set[str] = set()
        for value in paths:
            relative, target = _safe_relative_target(directory, value)
            if relative in seen:
                continue
            _reject_link_path(root, target)
            if target.is_symlink() or not target.is_file():
                raise NotFoundError(f"{item_label}不存在：{relative}")
            seen.add(relative)
            validated.append((relative, target))

        deleted_paths: list[str] = []
        for relative, target in validated:
            try:
                target.unlink()
            except OSError as exc:
                raise WebServiceError(f"{item_label}删除失败：{relative}") from exc
            deleted_paths.append(relative)

        self._prune_empty_directories(directory)
        return {
            "deleted_paths": deleted_paths,
            "deleted_count": len(deleted_paths),
        }

    def delete_all_tmp_files(self) -> dict[str, Any]:
        directory = self.root / "tmp"
        directory.mkdir(parents=True, exist_ok=True)
        paths = [item["relative_path"] for item in _flat_files(directory)]
        if not paths:
            self._prune_empty_directories(directory)
            return {"deleted_paths": [], "deleted_count": 0}
        return self.delete_tmp_files(paths)

    def _prune_empty_directories(self, directory: Path) -> None:
        if not directory.is_dir():
            return
        for current, directories, _ in os.walk(directory, topdown=False, followlinks=False):
            for name in directories:
                candidate = Path(current) / name
                if candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)():
                    continue
                try:
                    candidate.rmdir()
                except OSError:
                    continue

    def _avatar_path(self, user: str) -> Path | None:
        directory = self.root / "users" / user / "avatar"
        if not directory.is_dir():
            return None
        candidates = {
            item.suffix.casefold(): item
            for item in _visible_children(directory)
            if item.is_file() and item.stem.casefold() == "avatar"
        }
        for suffix in _AVATAR_SEARCH_ORDER:
            path = candidates.get(suffix)
            if path is not None:
                return path
        return None

    def avatar(self, user: Any) -> Path | None:
        name = self.require_user(user)
        return self._avatar_path(name)

    def save_avatar(
        self,
        user: Any,
        data: Any,
        content_type: Any,
    ) -> dict[str, Any]:
        name = self.require_user(user)
        if not isinstance(data, bytes) or not data:
            raise InvalidRequestError("头像文件不能为空")
        if len(data) > AVATAR_MAX_BYTES:
            raise InvalidRequestError("头像文件不能超过 5 MB")
        declared = str(content_type or "").strip().casefold()
        if declared == "image/jpg":
            declared = "image/jpeg"
        if declared not in _AVATAR_FORMATS:
            raise InvalidRequestError("头像只支持 PNG、JPEG、GIF 或 WebP")
        detected = _image_media_type(data)
        if detected is None or detected != declared:
            raise InvalidRequestError("头像文件内容与声明的图片格式不一致")
        directory = self.root / "users" / name / "avatar"
        target = directory / f"avatar{_AVATAR_FORMATS[detected]}"
        _atomic_write(target, data)
        for candidate in _visible_children(directory):
            if (
                candidate != target
                and candidate.is_file()
                and candidate.stem.casefold() == "avatar"
                and candidate.suffix.casefold() in _AVATAR_SEARCH_ORDER
            ):
                try:
                    candidate.unlink()
                except OSError as exc:
                    try:
                        target.unlink()
                    except OSError:
                        pass
                    raise WebServiceError("旧头像清理失败") from exc
        return {
            "user": name,
            "avatar_path": self._project_path(target),
            "size": len(data),
            "format": detected,
        }
