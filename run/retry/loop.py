"""Generic attempt ledger and synchronous retry loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from run.retry.policy import MAX_ATTEMPTS, mark_retry_exhausted


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class RunAttemptContext:
    attempt_index: int
    max_attempts: int


@dataclass(slots=True)
class RetryLedger:
    max_attempts: int = MAX_ATTEMPTS
    attempts: int = 0
    consecutive_failures: int = 0
    exhausted: bool = False

    @property
    def max_retries(self) -> int:
        return max(0, self.max_attempts - 1)

    def begin_attempt(self) -> RunAttemptContext:
        if self.exhausted:
            raise RuntimeError("retry attempt budget already exhausted")
        self.attempts += 1
        return RunAttemptContext(self.attempts, self.max_attempts)

    def next_consecutive_failures(self, progress: bool) -> int:
        return 1 if progress else self.consecutive_failures + 1

    def would_allow_retry(self, progress: bool) -> bool:
        return self.next_consecutive_failures(progress) <= self.max_retries

    def record_failure(self, progress: bool = False) -> int:
        self.consecutive_failures = self.next_consecutive_failures(progress)
        self.exhausted = self.consecutive_failures > self.max_retries
        return self.consecutive_failures

    def can_retry(self) -> bool:
        return not self.exhausted


def run_attempts(
    run_once: Callable[[RunAttemptContext], T],
    *,
    ledger: RetryLedger,
    should_retry_error: Callable[[BaseException], bool],
    on_retry: Callable[[RunAttemptContext, BaseException], None] | None = None,
    wait_before_retry: Callable[[RunAttemptContext, BaseException], None] | None = None,
) -> T:
    while not ledger.exhausted:
        attempt = ledger.begin_attempt()
        try:
            result = run_once(attempt)
        except (KeyboardInterrupt, GeneratorExit):
            raise
        except BaseException as exc:
            progress = bool(getattr(exc, "retry_progress", False))
            failures = ledger.record_failure(progress)
            if not should_retry_error(exc):
                raise
            if not ledger.can_retry():
                mark_retry_exhausted(
                    exc,
                    attempts=failures,
                    max_attempts=ledger.max_attempts,
                )
                raise
            if on_retry is not None:
                on_retry(attempt, exc)
            if wait_before_retry is not None:
                wait_before_retry(attempt, exc)
            continue
        return result
    raise AssertionError("retry loop exited without a result or exception")
