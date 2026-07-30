"""Thread-safe handoff for guidance submitted while a run is active."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class GuidanceInput:
    """A structured message submitted while a run is active.

    ``uploaded_files`` contains only server-issued upload descriptors.  The
    web boundary validates those descriptors before they reach this mailbox;
    the runtime still revalidates them before exposing them to a provider or
    tool.  Keeping the text optional is intentional: an attachment-only
    guidance is a valid user message.
    """

    id: str = ""
    text: str = ""
    uploaded_files: list[dict[str, Any]] = field(default_factory=list)

    @property
    def display_text(self) -> str:
        text = self.text.strip()
        if text:
            return text
        names = [
            str(item.get("name") or "附件")
            for item in self.uploaded_files
            if isinstance(item, dict)
        ]
        return "附件引导：" + "、".join(names or ["新增输入资产"])

    def history_detail(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text.strip(),
            "display_text": self.display_text,
            "uploaded_files": [dict(item) for item in self.uploaded_files],
        }


def normalize_guidance(value: Any) -> GuidanceInput | None:
    """Normalize new envelopes while preserving the legacy string queue API."""

    if isinstance(value, GuidanceInput):
        if not value.text.strip() and not value.uploaded_files:
            return None
        return value
    if isinstance(value, str) and value.strip():
        return GuidanceInput(text=value.strip())
    if isinstance(value, dict):
        text = value.get("text", value.get("guidance", ""))
        files = value.get("uploaded_files", [])
        if not isinstance(text, str):
            text = str(text or "")
        if not isinstance(files, list):
            files = []
        normalized_files = [dict(item) for item in files if isinstance(item, dict)]
        if not text.strip() and not normalized_files:
            return None
        return GuidanceInput(
            id=str(value.get("id", value.get("guidance_id", "")) or "").strip(),
            text=text.strip(),
            uploaded_files=normalized_files,
        )
    return None


class GuidanceMailbox:
    """Atomically assign guidance to the current run or the next turn."""

    def __init__(self, maxsize: int = 8) -> None:
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=max(1, int(maxsize)))
        self._lock = threading.Lock()
        self._open = True

    def offer(self, value: str | GuidanceInput | dict[str, Any]) -> tuple[bool, int]:
        """Return ``(accepted_by_current_run, queue_size)``."""

        normalized = normalize_guidance(value)
        if normalized is None:
            raise ValueError("引导必须包含文本或至少一个附件")
        with self._lock:
            if not self._open:
                return False, 0
            # Keep bare strings as strings for compatibility with custom event
            # sources and third-party integrations that consume this mailbox.
            self._queue.put_nowait(value if isinstance(value, str) else normalized)
            return True, self._queue.qsize()

    def drain(self) -> list[Any]:
        """Drain at a normal tool boundary without closing input."""

        with self._lock:
            return self._drain_unlocked()

    def drain_or_close(self) -> list[Any]:
        """Drain pending values, or atomically close when none remain."""

        with self._lock:
            values = self._drain_unlocked()
            if not values:
                self._open = False
            return values

    def close(self) -> None:
        with self._lock:
            self._open = False

    @property
    def accepting(self) -> bool:
        with self._lock:
            return self._open

    def qsize(self) -> int:
        return self._queue.qsize()

    def get(self, block: bool = True, timeout: float | None = None) -> Any:
        """Queue-compatible accessor for custom event sources and tests."""

        return self._queue.get(block=block, timeout=timeout)

    def get_nowait(self) -> Any:
        return self._queue.get_nowait()

    def _drain_unlocked(self) -> list[Any]:
        values: list[Any] = []
        while True:
            try:
                value = self._queue.get_nowait()
            except queue.Empty:
                return values
            normalized = normalize_guidance(value)
            if normalized is not None:
                values.append(value.strip() if isinstance(value, str) else normalized)
