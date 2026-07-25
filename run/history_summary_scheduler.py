"""Independent persistent worker for closed-session title and summary jobs."""

from __future__ import annotations

import copy
import threading
from typing import Any, Callable

from run.users import list_users


class HistorySummaryScheduler:
    """Claim a bounded number of durable summary jobs without blocking maintenance."""

    def __init__(
        self,
        root,
        *,
        processor: Callable[[str], dict[str, Any]],
        poll_interval: float = 5.0,
        max_jobs_per_cycle: int = 1,
        on_error: Callable[[str, Exception], None] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.processor = processor
        self.poll_interval = max(1.0, float(poll_interval))
        self.max_jobs_per_cycle = max(1, int(max_jobs_per_cycle))
        self.on_error = on_error
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._cursor = 0
        self._last_results: dict[str, Any] = {}

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._wake_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="history-summary-scheduler",
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, timeout: float = 10.0) -> None:
        self._stop_event.set()
        self._wake_event.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        with self._lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None

    def wake(self) -> None:
        self._wake_event.set()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self.running,
                "poll_interval": self.poll_interval,
                "max_jobs_per_cycle": self.max_jobs_per_cycle,
                "last_results": copy.deepcopy(self._last_results),
            }

    def scan_once(self) -> dict[str, Any]:
        users = list_users(self.root)
        if not users:
            with self._lock:
                self._last_results = {}
            return {}
        start = self._cursor % len(users)
        ordered = users[start:] + users[:start]
        results: dict[str, Any] = {}
        claimed = 0
        for offset, user in enumerate(ordered):
            if self._stop_event.is_set() or claimed >= self.max_jobs_per_cycle:
                break
            try:
                result = self.processor(user)
            except Exception as exc:
                if self.on_error is not None:
                    self.on_error(f"history_summary:{user}", exc)
                result = {
                    "claimed": 0,
                    "processed": [],
                    "failed": [{
                        "error": {
                            "message": str(exc),
                            "exception_type": type(exc).__name__,
                        }
                    }],
                }
            results[user] = result
            if int(result.get("claimed") or 0) > 0:
                claimed += 1
                self._cursor = (start + offset + 1) % len(users)
        with self._lock:
            self._last_results = copy.deepcopy(results)
        return results

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.scan_once()
            except Exception as exc:
                if self.on_error is not None:
                    self.on_error("history_summary", exc)
            self._wake_event.wait(self.poll_interval)
            self._wake_event.clear()

