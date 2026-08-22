"""In-memory aggregation for high-frequency successful cron executions."""

from __future__ import annotations

import copy
from pathlib import Path
import threading
import time
from typing import Any

from run.infra import LogStore


class CronLogAggregator:
    def __init__(self, root: Path, *, flush_seconds: float = 300.0) -> None:
        self.root = root.resolve()
        self.flush_seconds = max(1.0, float(flush_seconds))
        self._lock = threading.RLock()
        self._windows: dict[tuple[str, str], dict[str, Any]] = {}

    def record_success(self, record: dict[str, Any]) -> None:
        user = str(record.get("user") or "")
        task_id = str(record.get("task_id") or "")
        key = (user, task_id)
        now = time.monotonic()
        with self._lock:
            window = self._windows.get(key)
            if window is None:
                window = {
                    "first_executed_at": str(record.get("executed_at") or ""),
                    "last_executed_at": str(record.get("executed_at") or ""),
                    "runs": 0,
                    "total_duration_ms": 0,
                    "max_duration_ms": 0,
                    "last_result": {},
                    "opened_monotonic": now,
                }
                self._windows[key] = window
            duration = max(0, int(record.get("duration_ms") or 0))
            window["last_executed_at"] = str(record.get("executed_at") or "")
            window["runs"] = int(window.get("runs") or 0) + 1
            window["total_duration_ms"] = int(window.get("total_duration_ms") or 0) + duration
            window["max_duration_ms"] = max(
                int(window.get("max_duration_ms") or 0), duration
            )
            window["last_result"] = copy.deepcopy(record.get("result") or {})
            if now - float(window.get("opened_monotonic") or now) >= self.flush_seconds:
                self._flush_key_locked(key)

    def record_immediate(self, record: dict[str, Any]) -> None:
        key = (str(record.get("user") or ""), str(record.get("task_id") or ""))
        flush_error: Exception | None = None
        with self._lock:
            try:
                self._flush_key_locked(key)
            except Exception as exc:
                # Keep the success window for a later retry, but do not let it
                # suppress the current error/partial-result diagnostic.
                flush_error = exc
        immediate_error: Exception | None = None
        try:
            LogStore(self.root).append_cron(record)
        except Exception as exc:
            immediate_error = exc
        if immediate_error is not None:
            raise immediate_error
        if flush_error is not None:
            raise flush_error

    def flush_due(self, *, now: float | None = None) -> int:
        """Persist every aggregate whose real deadline has elapsed."""

        current = time.monotonic() if now is None else float(now)
        with self._lock:
            keys = [
                key
                for key, window in self._windows.items()
                if current - float(window.get("opened_monotonic") or current)
                >= self.flush_seconds
            ]
            flushed = 0
            first_error: Exception | None = None
            for key in keys:
                try:
                    self._flush_key_locked(key)
                    flushed += 1
                except Exception as exc:
                    first_error = first_error or exc
            if first_error is not None:
                raise first_error
            return flushed

    def flush(self) -> int:
        with self._lock:
            keys = list(self._windows)
            flushed = 0
            first_error: Exception | None = None
            for key in keys:
                try:
                    self._flush_key_locked(key)
                    flushed += 1
                except Exception as exc:
                    first_error = first_error or exc
            if first_error is not None:
                raise first_error
            return flushed

    def pending_windows(self) -> int:
        with self._lock:
            return len(self._windows)

    def _flush_key_locked(self, key: tuple[str, str]) -> None:
        window = self._windows.get(key)
        if not isinstance(window, dict):
            return
        runs = max(1, int(window.get("runs") or 0))
        total = max(0, int(window.get("total_duration_ms") or 0))
        record = {
            "schema_version": 1,
            "executed_at": str(window.get("last_executed_at") or ""),
            "user": key[0],
            "task_id": key[1],
            "status": "success",
            "duration_ms": round(total / runs),
            "result": {
                "aggregated": True,
                "window_started_at": str(window.get("first_executed_at") or ""),
                "window_finished_at": str(window.get("last_executed_at") or ""),
                "runs": runs,
                "successes": runs,
                "failures": 0,
                "average_duration_ms": round(total / runs),
                "max_duration_ms": max(0, int(window.get("max_duration_ms") or 0)),
                "last_result": copy.deepcopy(window.get("last_result") or {}),
            },
            "error": None,
        }
        LogStore(self.root).append_cron(record)
        self._windows.pop(key, None)


__all__ = ["CronLogAggregator"]
