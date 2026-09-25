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

    @property
    def is_last_attempt(self) -> bool:
        return self.attempt_index >= self.max_attempts

    @property
    def defer_commit(self) -> bool:
        return not self.is_last_attempt


@dataclass(slots=True)
class RetryLedger:
    max_attempts: int = MAX_ATTEMPTS
    attempts: int = 0
    consecutive_failures: int = 0
    exhausted: bool = False

    def begin_attempt(self) -> RunAttemptContext:
        if self.attempts >= self.max_attempts:
            raise RuntimeError("retry attempt budget already exhausted")
        self.attempts += 1
        return RunAttemptContext(self.attempts, self.max_attempts)

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        self.exhausted = self.attempts >= self.max_attempts

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.exhausted = False

    def can_retry(self) -> bool:
        return self.attempts < self.max_attempts


def run_attempts(
    run_once: Callable[[RunAttemptContext], T],
    *,
    ledger: RetryLedger,
    should_retry_error: Callable[[BaseException], bool],
    on_retry: Callable[[RunAttemptContext, BaseException], None] | None = None,
    wait_before_retry: Callable[[RunAttemptContext, BaseException], None] | None = None,
) -> T:
    while ledger.attempts < ledger.max_attempts:
        attempt = ledger.begin_attempt()
        try:
            result = run_once(attempt)
        except (KeyboardInterrupt, GeneratorExit):
            raise
        except BaseException as exc:
            ledger.record_failure()
            if not should_retry_error(exc):
                raise
            if not ledger.can_retry():
                mark_retry_exhausted(
                    exc,
                    attempts=ledger.attempts,
                    max_attempts=ledger.max_attempts,
                )
                raise
            if on_retry is not None:
                on_retry(attempt, exc)
            if wait_before_retry is not None:
                wait_before_retry(attempt, exc)
            continue
        ledger.record_success()
        return result
    raise AssertionError("retry loop exited without a result or exception")
