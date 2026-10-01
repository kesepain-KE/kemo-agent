"""Per-user chat concurrency gate used by the Web service."""

from __future__ import annotations

import threading
import time


class _UserChatGate:
    """单用户聊天并发槽与有界等待区。"""

    def __init__(
        self,
        max_concurrent: int,
        max_pending: int,
        pending_timeout: float,
    ) -> None:
        self.max_concurrent = max(1, int(max_concurrent))
        self.max_pending = max(0, int(max_pending))
        self.pending_timeout = max(1.0, float(pending_timeout))
        self._semaphore = threading.BoundedSemaphore(self.max_concurrent)
        self._pending_count = 0
        self._active_count = 0
        self._lock = threading.Lock()

    def acquire(self, *, cancel_event: threading.Event | None = None) -> bool:
        if cancel_event is not None and cancel_event.is_set():
            return False
        if self._semaphore.acquire(blocking=False):
            if cancel_event is not None and cancel_event.is_set():
                self._semaphore.release()
                return False
            with self._lock:
                self._active_count += 1
            return True
        with self._lock:
            if self._pending_count >= self.max_pending:
                return False
            self._pending_count += 1
        acquired = False
        deadline = time.monotonic() + self.pending_timeout
        try:
            while not acquired:
                if cancel_event is not None and cancel_event.is_set():
                    return False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                acquired = self._semaphore.acquire(timeout=min(0.1, remaining))
            if cancel_event is not None and cancel_event.is_set():
                self._semaphore.release()
                acquired = False
                return False
            return acquired
        finally:
            with self._lock:
                self._pending_count = max(0, self._pending_count - 1)
                if acquired:
                    self._active_count += 1

    def release(self) -> None:
        with self._lock:
            if self._active_count < 1:
                raise RuntimeError("Web 聊天并发槽位重复释放")
            self._active_count -= 1
            self._semaphore.release()

    def status(self) -> dict[str, int]:
        with self._lock:
            return {
                "active_chats": self._active_count,
                "max_chats": self.max_concurrent,
                "pending_chats": self._pending_count,
                "max_pending": self.max_pending,
            }

    def matches(self, max_concurrent: int, max_pending: int, pending_timeout: float) -> bool:
        return (
            self.max_concurrent == max(1, int(max_concurrent))
            and self.max_pending == max(0, int(max_pending))
            and self.pending_timeout == max(1.0, float(pending_timeout))
        )

