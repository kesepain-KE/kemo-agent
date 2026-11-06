"""In-memory aggregation for high-frequency cron executions."""

from __future__ import annotations

import copy
from pathlib import Path
import threading
import time
from typing import Any

from run.infra import LogStore


class CronLogAggregator:
    def __init__(self, root: Path, *, flush_seconds: float = 3600.0) -> None:
        self.root = root.resolve()
        self.flush_seconds = max(1.0, float(flush_seconds))
        self._lock = threading.RLock()
        self._windows: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self._active: dict[tuple[str, str], tuple[str, str, str, str]] = {}

    @staticmethod
    def _key(record: dict[str, Any]) -> tuple[str, str, str, str]:
        user = str(record.get("user") or "")
        task_id = str(record.get("task_id") or "")
        # One metrics window per task, independent of status/data changes.
        return user, task_id, "hourly", ""

    def record_success(self, record: dict[str, Any]) -> None:
        self.record_aggregated(
            record, immediate_on_change=(record.get("result") or {}).get("data_changed") is not False,
        )

    def record_repeated(self, record: dict[str, Any]) -> None:
        """Every partial result remains immediately durable."""

        self.record_aggregated(record, immediate_on_change=True)

    def record_aggregated(self, record: dict[str, Any], *, immediate_on_change: bool = False) -> None:
        key = self._key(record)
        owner = key[:2]
        now = time.monotonic()
        error: Exception | None = None
        if immediate_on_change:
            try:
                LogStore(self.root).append_cron(record)
            except Exception as exc:
                error = exc
        with self._lock:
            previous = self._active.get(owner)
            if previous is not None and previous != key:
                self._flush_key_locked(previous)
            self._active[owner] = key
            # A run at the boundary belongs to the next window.
            window = self._windows.get(key)
            if window is not None and now - float(window["opened_monotonic"]) >= self.flush_seconds:
                try:
                    self._flush_key_locked(key)
                except Exception as exc:
                    error = error or exc
            self._append_locked(key, record, now)
        if error is not None:
            raise error

    def _append_locked(
        self,
        key: tuple[str, str, str, str],
        record: dict[str, Any],
        now: float,
    ) -> None:
        window = self._windows.get(key)
        if window is None:
            window = {
                "first_executed_at": str(record.get("executed_at") or ""),
                "last_executed_at": str(record.get("executed_at") or ""),
                "runs": 0,
                "total_duration_ms": 0,
                "max_duration_ms": 0,
                "last_result": {},
                "last_error": None,
                "opened_monotonic": now,
                "status_counts": {},
            }
            self._windows[key] = window
        duration = max(0, int(record.get("duration_ms") or 0))
        window["last_executed_at"] = str(record.get("executed_at") or "")
        window["runs"] = int(window.get("runs") or 0) + 1
        window["total_duration_ms"] = int(window.get("total_duration_ms") or 0) + duration
        window["max_duration_ms"] = max(int(window.get("max_duration_ms") or 0), duration)
        window["last_result"] = copy.deepcopy(record.get("result") or {})
        window["last_error"] = copy.deepcopy(record.get("error"))
        status = str(record.get("status") or "unknown")
        counts = window["status_counts"]
        counts[status] = counts.get(status, 0) + 1

    def record_immediate(self, record: dict[str, Any]) -> None:
        self.record_aggregated(record, immediate_on_change=True)

    def flush_due(self, *, now: float | None = None) -> int:
        current = time.monotonic() if now is None else float(now)
        with self._lock:
            keys = [
                key
                for key, window in self._windows.items()
                if current - float(window["opened_monotonic"]) >= self.flush_seconds
            ]
            return self._flush_keys_locked(keys)

    def flush(self) -> int:
        with self._lock:
            return self._flush_keys_locked(list(self._windows))

    def _flush_keys_locked(self, keys: list[tuple[str, str, str, str]]) -> int:
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

    def _flush_key_locked(self, key: tuple[str, str, str, str]) -> None:
        window = self._windows.get(key)
        if not isinstance(window, dict):
            return
        runs = max(1, int(window.get("runs") or 0))
        total = max(0, int(window.get("total_duration_ms") or 0))
        counts = window["status_counts"]
        successes = counts.get("success", 0)
        status = "success" if successes == runs else "partial" if successes else "failed"
        record = {
            "schema_version": 1,
            "executed_at": str(window.get("last_executed_at") or ""),
            "user": key[0],
            "task_id": key[1],
            "status": status,
            "duration_ms": round(total / runs),
            "result": {
                "aggregated": True,
                "window_started_at": str(window.get("first_executed_at") or ""),
                "window_finished_at": str(window.get("last_executed_at") or ""),
                "runs": runs,
                "status_counts": dict(counts),
                "successes": successes,
                "failures": runs - successes,
                "success_rate": successes / runs,
                "average_duration_ms": round(total / runs),
                "max_duration_ms": max(0, int(window.get("max_duration_ms") or 0)),
                "last_result": copy.deepcopy(window.get("last_result") or {}),
            },
            "error": copy.deepcopy(window.get("last_error")),
        }
        LogStore(self.root).append_cron(record)
        self._windows.pop(key, None)
        owner = key[:2]
        if self._active.get(owner) == key:
            self._active.pop(owner, None)


__all__ = ["CronLogAggregator"]
