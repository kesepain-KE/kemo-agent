"""Per-user completion and failure sound storage and fallback playback."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from web.constants import (
    COMPLETION_SOUND_MAX_BYTES,
    FAILURE_SOUND_MAX_BYTES,
    _COMPLETION_SOUND_FORMATS,
    _COMPLETION_SOUND_SEARCH_ORDER,
    _FAILURE_SOUND_FORMATS,
    _FAILURE_SOUND_SEARCH_ORDER,
)
from web.errors import InvalidRequestError, WebServiceError
from web.services._io import atomic_write as _atomic_write
from web.services._paths import _reject_link_path


class SoundServiceMixin:
    def _completion_sound_user_root(self, user: str) -> Path:
        directory = self.root / "users" / user
        if directory.is_symlink() or getattr(directory, "is_junction", lambda: False)():
            raise InvalidRequestError("结束音效不允许保存在符号链接或目录联接用户目录中")
        return directory

    def _completion_sound_path(self, user: str) -> Path | None:
        directory = self._completion_sound_user_root(user)
        root = directory.resolve()
        for suffix in _COMPLETION_SOUND_SEARCH_ORDER:
            candidate = directory / f"completion_sound{suffix}"
            _reject_link_path(root, candidate)
            if not candidate.exists():
                continue
            if not candidate.is_file():
                raise InvalidRequestError("结束音效固定路径不是普通文件")
            try:
                if candidate.stat().st_size > COMPLETION_SOUND_MAX_BYTES:
                    continue
                with candidate.open("rb") as stream:
                    header = stream.read(16)
            except OSError:
                continue
            expected = next(
                (
                    media_type
                    for media_type, extension in _COMPLETION_SOUND_FORMATS.items()
                    if extension == suffix
                ),
                None,
            )
            if header and self._completion_sound_media_type(header) == expected:
                return candidate
        return None

    def completion_sound_path(self, user: Any) -> Path | None:
        name = self.require_user(user)
        return self._completion_sound_path(name)

    def load_completion_sound(self, user: Any) -> bytes | None:
        name = self.require_user(user)
        target = self._completion_sound_path(name)
        if target is None:
            return None
        try:
            data = target.read_bytes()
        except OSError as exc:
            raise WebServiceError("结束音效读取失败") from exc
        expected = next(
            (
                media_type
                for media_type, extension in _COMPLETION_SOUND_FORMATS.items()
                if extension == target.suffix.casefold()
            ),
            None,
        )
        if not data or len(data) > COMPLETION_SOUND_MAX_BYTES:
            return None
        if self._completion_sound_media_type(data) != expected:
            return None
        return data

    def completion_sound_status(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        target = self._completion_sound_path(name)
        if target is None:
            return {
                "user": name,
                "enabled": False,
                "available": False,
                "filename": "",
                "mime_type": "",
                "size": 0,
                "updated_at": "",
            }
        try:
            stat = target.stat()
        except OSError as exc:
            raise WebServiceError("结束音效状态读取失败") from exc
        media_type = next(
            (
                value
                for value, extension in _COMPLETION_SOUND_FORMATS.items()
                if extension == target.suffix.casefold()
            ),
            "application/octet-stream",
        )
        return {
            "user": name,
            "enabled": True,
            "available": True,
            "filename": target.name,
            "mime_type": media_type,
            "size": stat.st_size,
            "updated_at": datetime.fromtimestamp(
                stat.st_mtime,
                timezone.utc,
            ).isoformat(),
            "terminal_fallback_supported": (
                self._sound_platform_system().casefold() == "windows"
                and target.suffix.casefold() in {".wav", ".mp3"}
            ),
        }

    def save_completion_sound(
        self,
        user: Any,
        data: Any,
        content_type: Any,
    ) -> dict[str, Any]:
        name = self.require_user(user)
        if not isinstance(data, bytes) or not data:
            raise InvalidRequestError("结束音效文件不能为空")
        if len(data) > COMPLETION_SOUND_MAX_BYTES:
            raise InvalidRequestError("结束音效文件不能超过 5 MB")
        declared = str(content_type or "").strip().casefold()
        if declared == "audio/mp3":
            declared = "audio/mpeg"
        if declared not in _COMPLETION_SOUND_FORMATS:
            raise InvalidRequestError("结束音效只支持 MP3、WAV、Ogg 或 WebM 音频")
        detected = self._completion_sound_media_type(data)
        if detected is None or detected != declared:
            raise InvalidRequestError("结束音效内容与声明的音频格式不一致")
        directory = self._completion_sound_user_root(name)
        root = directory.resolve()
        target = directory / f"completion_sound{_COMPLETION_SOUND_FORMATS[detected]}"
        _reject_link_path(root, target)
        _atomic_write(target, data)
        for suffix in _COMPLETION_SOUND_SEARCH_ORDER:
            candidate = directory / f"completion_sound{suffix}"
            if candidate == target or not candidate.exists():
                continue
            _reject_link_path(root, candidate)
            if not candidate.is_file():
                try:
                    target.unlink()
                except OSError:
                    pass
                raise InvalidRequestError("旧结束音效固定路径不是普通文件")
            try:
                candidate.unlink()
            except OSError as exc:
                try:
                    target.unlink()
                except OSError:
                    pass
                raise WebServiceError("旧结束音效清理失败") from exc
        return self.completion_sound_status(name)

    def delete_completion_sound(self, user: Any) -> bool:
        name = self.require_user(user)
        directory = self._completion_sound_user_root(name)
        root = directory.resolve()
        deleted = False
        for suffix in _COMPLETION_SOUND_SEARCH_ORDER:
            candidate = directory / f"completion_sound{suffix}"
            _reject_link_path(root, candidate)
            if not candidate.exists():
                continue
            if not candidate.is_file():
                raise InvalidRequestError("结束音效固定路径不是普通文件")
            try:
                candidate.unlink()
            except OSError as exc:
                raise WebServiceError("结束音效删除失败") from exc
            deleted = True
        return deleted

    def play_completion_sound_fallback(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        target = self._completion_sound_path(name)
        if target is None:
            return {
                "user": name,
                "played": False,
                "mode": "",
                "reason": "not_configured",
            }
        try:
            mode = self._play_windows_completion_sound(target)
        except Exception:
            return {
                "user": name,
                "played": False,
                "mode": "",
                "reason": "playback_failed",
            }
        if not mode:
            unsupported_format = target.suffix.casefold() not in {".wav", ".mp3"}
            return {
                "user": name,
                "played": False,
                "mode": "",
                "reason": "unsupported_format" if unsupported_format else "unsupported_host",
            }
        return {
            "user": name,
            "played": True,
            "mode": mode,
            "reason": "browser_fallback",
        }

    def _failure_sound_user_root(self, user: str) -> Path:
        directory = self.root / "users" / user
        if directory.is_symlink() or getattr(directory, "is_junction", lambda: False)():
            raise InvalidRequestError("失败音效不允许保存在符号链接或目录联接用户目录中")
        return directory

    @staticmethod
    def _reject_failure_sound_path(root: Path, target: Path) -> None:
        if target.is_symlink() or getattr(target, "is_junction", lambda: False)():
            raise InvalidRequestError("失败音效不允许使用符号链接或目录联接路径")
        _reject_link_path(root, target)

    def _failure_sound_path(self, user: str) -> Path | None:
        directory = self._failure_sound_user_root(user)
        root = directory.resolve()
        for suffix in _FAILURE_SOUND_SEARCH_ORDER:
            candidate = directory / f"failure_sound{suffix}"
            self._reject_failure_sound_path(root, candidate)
            if not candidate.exists():
                continue
            if not candidate.is_file():
                raise InvalidRequestError("失败音效固定路径不是普通文件")
            try:
                if candidate.stat().st_size > FAILURE_SOUND_MAX_BYTES:
                    continue
                with candidate.open("rb") as stream:
                    header = stream.read(16)
            except OSError:
                continue
            expected = next(
                (
                    media_type
                    for media_type, extension in _FAILURE_SOUND_FORMATS.items()
                    if extension == suffix
                ),
                None,
            )
            if header and self._completion_sound_media_type(header) == expected:
                return candidate
        return None

    def failure_sound_path(self, user: Any) -> Path | None:
        name = self.require_user(user)
        return self._failure_sound_path(name)

    def load_failure_sound(self, user: Any) -> bytes | None:
        name = self.require_user(user)
        target = self._failure_sound_path(name)
        if target is None:
            return None
        try:
            data = target.read_bytes()
        except OSError as exc:
            raise WebServiceError("失败音效读取失败") from exc
        expected = next(
            (
                media_type
                for media_type, extension in _FAILURE_SOUND_FORMATS.items()
                if extension == target.suffix.casefold()
            ),
            None,
        )
        if not data or len(data) > FAILURE_SOUND_MAX_BYTES:
            return None
        if self._completion_sound_media_type(data) != expected:
            return None
        return data

    def failure_sound_status(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        target = self._failure_sound_path(name)
        if target is None:
            return {
                "user": name,
                "enabled": False,
                "available": False,
                "filename": "",
                "mime_type": "",
                "size": 0,
                "updated_at": "",
            }
        try:
            stat = target.stat()
        except OSError as exc:
            raise WebServiceError("失败音效状态读取失败") from exc
        media_type = next(
            (
                value
                for value, extension in _FAILURE_SOUND_FORMATS.items()
                if extension == target.suffix.casefold()
            ),
            "application/octet-stream",
        )
        return {
            "user": name,
            "enabled": True,
            "available": True,
            "filename": target.name,
            "mime_type": media_type,
            "size": stat.st_size,
            "updated_at": datetime.fromtimestamp(
                stat.st_mtime,
                timezone.utc,
            ).isoformat(),
            "terminal_fallback_supported": (
                self._sound_platform_system().casefold() == "windows"
                and target.suffix.casefold() in {".wav", ".mp3"}
            ),
        }

    def save_failure_sound(
        self,
        user: Any,
        data: Any,
        content_type: Any,
    ) -> dict[str, Any]:
        name = self.require_user(user)
        if not isinstance(data, bytes) or not data:
            raise InvalidRequestError("失败音效文件不能为空")
        if len(data) > FAILURE_SOUND_MAX_BYTES:
            raise InvalidRequestError("失败音效文件不能超过 5 MB")
        declared = str(content_type or "").strip().casefold()
        if declared == "audio/mp3":
            declared = "audio/mpeg"
        if declared not in _FAILURE_SOUND_FORMATS:
            raise InvalidRequestError("失败音效只支持 MP3、WAV、Ogg 或 WebM 音频")
        detected = self._completion_sound_media_type(data)
        if detected is None or detected != declared:
            raise InvalidRequestError("失败音效内容与声明的音频格式不一致")
        directory = self._failure_sound_user_root(name)
        root = directory.resolve()
        target = directory / f"failure_sound{_FAILURE_SOUND_FORMATS[detected]}"
        self._reject_failure_sound_path(root, target)
        for suffix in _FAILURE_SOUND_SEARCH_ORDER:
            candidate = directory / f"failure_sound{suffix}"
            if candidate == target:
                continue
            self._reject_failure_sound_path(root, candidate)
            if candidate.exists() and not candidate.is_file():
                raise InvalidRequestError("旧失败音效固定路径不是普通文件")
        _atomic_write(target, data)
        for suffix in _FAILURE_SOUND_SEARCH_ORDER:
            candidate = directory / f"failure_sound{suffix}"
            if candidate == target or not candidate.exists():
                continue
            self._reject_failure_sound_path(root, candidate)
            if not candidate.is_file():
                try:
                    target.unlink()
                except OSError:
                    pass
                raise InvalidRequestError("旧失败音效固定路径不是普通文件")
            try:
                candidate.unlink()
            except OSError as exc:
                try:
                    target.unlink()
                except OSError:
                    pass
                raise WebServiceError("旧失败音效清理失败") from exc
        return self.failure_sound_status(name)

    def delete_failure_sound(self, user: Any) -> bool:
        name = self.require_user(user)
        directory = self._failure_sound_user_root(name)
        root = directory.resolve()
        deleted = False
        for suffix in _FAILURE_SOUND_SEARCH_ORDER:
            candidate = directory / f"failure_sound{suffix}"
            self._reject_failure_sound_path(root, candidate)
            if not candidate.exists():
                continue
            if not candidate.is_file():
                raise InvalidRequestError("失败音效固定路径不是普通文件")
            try:
                candidate.unlink()
            except OSError as exc:
                raise WebServiceError("失败音效删除失败") from exc
            deleted = True
        return deleted

    def play_failure_sound_fallback(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        target = self._failure_sound_path(name)
        if target is None:
            return {
                "user": name,
                "played": False,
                "mode": "",
                "reason": "not_configured",
            }
        try:
            mode = self._play_windows_failure_sound(target)
        except Exception:
            return {
                "user": name,
                "played": False,
                "mode": "",
                "reason": "playback_failed",
            }
        if not mode:
            unsupported_format = target.suffix.casefold() not in {".wav", ".mp3"}
            return {
                "user": name,
                "played": False,
                "mode": "",
                "reason": "unsupported_format" if unsupported_format else "unsupported_host",
            }
        return {
            "user": name,
            "played": True,
            "mode": mode,
            "reason": "browser_fallback",
        }
