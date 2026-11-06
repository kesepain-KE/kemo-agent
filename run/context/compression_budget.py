"""Run-local compression deadline and actual model-attempt ledger."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import copy_context
import math
import threading
import time
from typing import Any, Callable

from run.tools import abandon_execution, attach_execution, release_execution, reserve_execution


class CompressionBudgetExhausted(RuntimeError):
    category = "compression_budget_exhausted"
    retryable = False

    def __init__(self, reason: str) -> None:
        super().__init__("上下文压缩预算耗尽；原 RAW、VIEW 与有效 Token 保持不变")
        self.reason = reason
        self.still_running = False
        self.completion_future: Future[Any] | None = None


class CompressionCancelledError(RuntimeError):
    still_running: bool
    completion_future: Future[Any] | None


class CompressionBudget:
    """One lazy absolute deadline; no reset on fragments, recovery or retry."""

    def __init__(self, timeout_seconds: float = 180.0, max_attempts: int = 4):
        if (
            isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0
        ):
            raise ValueError("agents.compression_timeout_seconds 必须是有限正数")
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("压缩总尝试次数必须是正整数")
        self.timeout_seconds = float(timeout_seconds)
        self.max_attempts = max_attempts
        self.attempts = 0
        self.deadline: float | None = None
        self.reason = ""
        self._event_reported = False
        self._lock = threading.RLock()
        self._pending: list[Any] = []

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "CompressionBudget":
        agents = config.get("agents") or {}
        return cls(
            timeout_seconds=agents.get("compression_timeout_seconds", 180.0),
            max_attempts=(config.get("provider_runtime") or {}).get("max_retries", 5) + 1,
        )

    def check(self) -> float:
        with self._lock:
            now = time.monotonic()
            if self.deadline is None:
                self.deadline = now + self.timeout_seconds
            if now >= self.deadline and not self.reason:
                self.reason = "deadline"
            if self.reason:
                raise CompressionBudgetExhausted(self.reason)
            return self.deadline - now

    def claim_attempt(self) -> None:
        with self._lock:
            self.check()
            if self.attempts >= self.max_attempts:
                self.reason = "attempts"
                raise CompressionBudgetExhausted(self.reason)
            self.attempts += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "timeout_seconds": self.timeout_seconds, "max_attempts": self.max_attempts,
                "attempts": self.attempts, "exhausted": bool(self.reason),
                "reason": self.reason,
                "still_running": any(not future.done() for future in self._pending),
                "elapsed_seconds": (
                    max(0.0, time.monotonic() - self.deadline + self.timeout_seconds)
                    if self.deadline is not None else 0.0
                ),
            }

    def take_exhaustion_event(self) -> bool:
        with self._lock:
            if not self.reason or self._event_reported:
                return False
            self._event_reported = True
            return True

    def call(
        self, function: Callable[[threading.Event], Any], *,
        label: str, cancel_event: Any = None,
    ) -> Any:
        """Bound even non-cooperative I/O; never pretend its thread has exited.

        Reuse the existing watchdog/slot registry. Workers only prepare budgets
        or return provisional model results; cache publication stays on caller.
        """
        self.check()
        if cancel_event is not None and cancel_event.is_set():
            raise CompressionCancelledError("cancelled")
        stopped = threading.Event()
        execution_id = reserve_execution(label)
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="compression-budget")
        try:
            future = executor.submit(copy_context().run, function, stopped)
            with self._lock:
                self._pending = [item for item in self._pending if not item.done()]
                self._pending.append(future)
            attach_execution(execution_id, future, executor)
        except BaseException:
            release_execution(execution_id)
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        try:
            while True:
                remaining = self.check()
                if future.done():
                    value = future.result()
                    self.check()  # A late result cannot publish coverage.
                    return value
                if cancel_event is not None and cancel_event.is_set():
                    raise CompressionCancelledError("cancelled")
                time.sleep(min(.01, remaining))
        except BaseException as exc:
            stopped.set()
            future.cancel()
            still_running = abandon_execution(execution_id)
            if isinstance(exc, (CompressionBudgetExhausted, CompressionCancelledError)):
                exc.still_running = still_running
                exc.completion_future = future if still_running else None
            raise
        finally:
            if future.done():
                release_execution(execution_id)
