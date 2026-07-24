"""Thread-safe handoff for guidance submitted while a run is active."""

from __future__ import annotations

import queue
import threading


class GuidanceMailbox:
    """Atomically assign guidance to the current run or the next turn."""

    def __init__(self, maxsize: int = 8) -> None:
        self._queue: queue.Queue[str] = queue.Queue(maxsize=max(1, int(maxsize)))
        self._lock = threading.Lock()
        self._open = True

    def offer(self, value: str) -> tuple[bool, int]:
        """Return ``(accepted_by_current_run, queue_size)``."""

        with self._lock:
            if not self._open:
                return False, 0
            self._queue.put_nowait(value)
            return True, self._queue.qsize()

    def drain(self) -> list[str]:
        """Drain at a normal tool boundary without closing input."""

        with self._lock:
            return self._drain_unlocked()

    def drain_or_close(self) -> list[str]:
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

    def get(self, block: bool = True, timeout: float | None = None) -> str:
        """Queue-compatible accessor for custom event sources and tests."""

        return self._queue.get(block=block, timeout=timeout)

    def get_nowait(self) -> str:
        return self._queue.get_nowait()

    def _drain_unlocked(self) -> list[str]:
        values: list[str] = []
        while True:
            try:
                value = self._queue.get_nowait()
            except queue.Empty:
                return values
            if isinstance(value, str) and value.strip():
                values.append(value.strip())
