"""Fixed-name, user-owned background media. Never used as agent attachments."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING
import logging
import uuid

from run.config import read_json_object, _contains_reparse_point, web_background
from run.infra import BoundedReadCache
from web.errors import InvalidRequestError, NotFoundError
from web.services._config_lock import config_lock
from web.services._io import atomic_write
from web.services.background_media import BACKGROUND_FORMATS, BACKGROUND_MAX_BYTES, validate_background

if TYPE_CHECKING:
    from web.service import WebRunService

_VALIDATED = BoundedReadCache(max_entries=64, max_bytes=64 * 1024, per_owner=4)
_LOGGER = logging.getLogger(__name__)


def _replace_backgrounds(directory: Path, candidates: list[Path], target: Path | None, data: bytes = b"") -> None:
    """Stage old assets locally so a locked file or disk failure can roll back."""
    staged: list[tuple[Path, Path]] = []
    installed = False
    try:
        for candidate in candidates:
            backup = directory / f".background-{uuid.uuid4().hex}{candidate.suffix}"
            candidate.replace(backup)
            staged.append((candidate, backup))
        if target is not None:
            installed = True
            atomic_write(target, data)
    except OSError:
        if installed and target is not None:
            target.unlink(missing_ok=True)
        for candidate, backup in reversed(staged):
            backup.replace(candidate)
        raise
    for _, backup in staged:
        try:
            backup.unlink()
        except OSError:
            # An old streaming reader may still hold the renamed file on Windows.
            # The new background is committed; don't misreport it as a failed upload.
            _LOGGER.warning("背景已更新，但暂存旧资源未能清理：%s", backup.name)


class BackgroundServiceMixin:
    if TYPE_CHECKING:
        root: Path
        require_user = WebRunService.require_user

    def _background_root(self, user: Any) -> tuple[str, Path]:
        name = self.require_user(user)
        directory = self.root / "users" / name
        if _contains_reparse_point(directory, self.root):
            raise InvalidRequestError("背景不允许符号链接或目录联接用户路径")
        config = directory / "user_config.json"
        if _contains_reparse_point(config, self.root):
            raise InvalidRequestError("背景配置不允许符号链接或目录联接")
        return name, directory

    def _background_candidates(self, directory: Path) -> list[Path]:
        candidates = []
        for path in directory.iterdir():
            if path.stem.casefold() != "background" or path.suffix.casefold() not in BACKGROUND_FORMATS:
                continue
            if _contains_reparse_point(path, self.root) or not path.is_file() or path.stat().st_nlink > 1:
                raise InvalidRequestError("背景固定路径必须是非链接的普通文件")
            candidates.append(path)
        return sorted(candidates, key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)

    def _background_metadata(self, directory: Path) -> tuple[Path | None, dict[str, Any]]:
        error = ""
        for target in self._background_candidates(directory):
            info = target.stat()
            if not 0 < info.st_size <= BACKGROUND_MAX_BYTES:
                error = "背景文件无效或超出限制"
                continue
            key = (str(target), info.st_mtime_ns, info.st_ctime_ns, info.st_size, info.st_ino)

            def validate() -> tuple[str, str]:
                with target.open("rb") as stream:
                    # A small wallpaper must not reserve the full 400 MB limit.
                    data = stream.read(info.st_size + 1)
                if len(data) != info.st_size:
                    raise InvalidRequestError("读取期间背景文件发生变化，请重新读取")
                return validate_background(data)

            try:
                suffix, media_type = _VALIDATED.get_or_load(
                    key, validate, owner=str(directory), ttl=60.0,
                )[0]
            except InvalidRequestError:
                error = "背景文件无效或超出限制"
                continue
            if BACKGROUND_FORMATS[target.suffix.casefold()] != media_type:
                error = "背景文件格式与后缀不匹配"
                continue
            return target, {
                "available": True, "filename": target.name,
                "media_kind": "video" if suffix in {".mp4", ".webm"} else "image",
                "mime_type": media_type, "size": info.st_size,
                "revision": f"{info.st_mtime_ns}-{info.st_ctime_ns}-{info.st_size}",
                "error": "",
            }
        return None, {
            "available": False, "filename": "", "media_kind": "", "mime_type": "",
            "size": 0, "revision": "", "error": error,
        }

    def background_status(self, user: Any) -> dict[str, Any]:
        name, directory = self._background_root(user)
        with config_lock(directory / "user_config.json"):
            _, metadata = self._background_metadata(directory)
            config = read_json_object(directory / "user_config.json", allow_empty=True)
            return {"user": name, **metadata, "settings": web_background(config)}

    def background_media(self, user: Any) -> tuple[Path, str]:
        _, directory = self._background_root(user)
        with config_lock(directory / "user_config.json"):
            target, metadata = self._background_metadata(directory)
            if target is None:
                raise NotFoundError("未设置可用的网页背景")
            return target, metadata["mime_type"]

    def save_background(self, user: Any, data: bytes) -> dict[str, Any]:
        _, directory = self._background_root(user)
        suffix, _ = validate_background(data)
        with config_lock(directory / "user_config.json"):
            candidates = self._background_candidates(directory)
            target = directory / f"background{suffix}"
            # Validate everything before writing; failed uploads keep the old asset.
            _replace_backgrounds(directory, candidates, target, data)
            return self.background_status(user)

    def delete_background(self, user: Any) -> dict[str, Any]:
        _, directory = self._background_root(user)
        with config_lock(directory / "user_config.json"):
            candidates = self._background_candidates(directory)
            _replace_backgrounds(directory, candidates, None)
            return {"deleted": bool(candidates), **self.background_status(user)}
