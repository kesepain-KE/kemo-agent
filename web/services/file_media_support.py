"""Media detection and Windows playback helpers for file services."""

from __future__ import annotations

import os
import platform
import threading
import time
import uuid
from pathlib import Path


_ACTIVE_COMPLETION_SOUND_PLAYBACKS: set[str] = set()
_ACTIVE_COMPLETION_SOUND_PLAYBACKS_LOCK = threading.Lock()


def _completion_sound_media_type(data: bytes) -> str | None:
    if data.startswith(b"ID3"):
        return "audio/mpeg"
    if len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0:
        return "audio/mpeg"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "audio/wav"
    if data.startswith(b"OggS"):
        return "audio/ogg"
    if data.startswith(b"\x1aE\xdf\xa3"):
        return "audio/webm"
    return None


def _play_windows_sound(target: Path, *, alias_prefix: str) -> str:
    if platform.system().casefold() != "windows":
        return ""
    suffix = target.suffix.casefold()
    if suffix == ".wav":
        import winsound

        winsound.PlaySound(
            str(target),
            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
        )
        return "user_wav"
    if suffix != ".mp3":
        return ""

    import ctypes

    playback_key = os.path.normcase(str(target.resolve()))
    with _ACTIVE_COMPLETION_SOUND_PLAYBACKS_LOCK:
        if playback_key in _ACTIVE_COMPLETION_SOUND_PLAYBACKS:
            return "user_mp3_mci_active"
        _ACTIVE_COMPLETION_SOUND_PLAYBACKS.add(playback_key)
    alias = f"kemo_{alias_prefix}_{uuid.uuid4().hex}"

    def command(value: str, *, result_chars: int = 0) -> str:
        buffer = ctypes.create_unicode_buffer(result_chars) if result_chars else None
        code = ctypes.windll.winmm.mciSendStringW(
            value,
            buffer,
            result_chars,
            None,
        )
        if code:
            raise OSError(f"Windows MCI 播放失败：{code}")
        return buffer.value if buffer is not None else ""

    try:
        command(f'open "{target}" type mpegvideo alias {alias}')
        duration_text = command(f"status {alias} length", result_chars=64)
        duration_seconds = min(600.0, max(2.0, float(duration_text) / 1000.0 + 2.0))
        command(f"play {alias}")
    except Exception:
        try:
            command(f"close {alias}")
        except OSError:
            pass
        with _ACTIVE_COMPLETION_SOUND_PLAYBACKS_LOCK:
            _ACTIVE_COMPLETION_SOUND_PLAYBACKS.discard(playback_key)
        raise

    def close_later() -> None:
        time.sleep(duration_seconds)
        try:
            command(f"close {alias}")
        except OSError:
            pass
        finally:
            with _ACTIVE_COMPLETION_SOUND_PLAYBACKS_LOCK:
                _ACTIVE_COMPLETION_SOUND_PLAYBACKS.discard(playback_key)

    threading.Thread(
        target=close_later,
        name=f"{alias_prefix}-sound-mci-cleanup",
        daemon=True,
    ).start()
    return "user_mp3_mci"


def _play_windows_completion_sound(target: Path) -> str:
    """Compatibility wrapper for the existing completion-sound tests/API."""

    return _play_windows_sound(target, alias_prefix="completion")


def _play_windows_failure_sound(target: Path) -> str:
    return _play_windows_sound(target, alias_prefix="failure")

