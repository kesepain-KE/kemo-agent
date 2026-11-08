"""Shared bounded retry policy; successful side effects are never re-executed."""
from __future__ import annotations

from dataclasses import dataclass
import random
import sqlite3
import threading
import time
from typing import Callable, TypeVar

from provider.schema import ProviderError

T = TypeVar("T")


class RetryCancelled(RuntimeError):
    pass


def transient(error: Exception) -> bool:
    """An upstream 'retryable=true' cannot grant replay of deterministic failures."""
    if isinstance(error, sqlite3.OperationalError):
        return "locked" in str(error).casefold() or "busy" in str(error).casefold()
    if isinstance(error, ProviderError):
        if error.retry_budget_exhausted or getattr(error, "cancelled", False):
            return False
        if error.retryable_declared and not error.retryable:
            return False
        if error.category.casefold() in {
            "internal_error", "auth_error", "authentication_error", "authorization_error",
            "validation", "validation_error", "request_validation_error",
            "protocol_error", "gateway_protocol_error", "invalid_tool_arguments", "input_error",
        }:
            return False
        if error.status_code is not None:
            return error.status_code in {408, 429} or 500 <= error.status_code < 600
        return error.category in {
            "timeout", "transport_error", "connection_error", "upstream_error",
            "gateway_error", "network_error", "provider_unavailable", "provider_incomplete", "output_truncated",
        }
    return isinstance(error, (TimeoutError, ConnectionError))


@dataclass(frozen=True, slots=True)
class RetryManager:
    max_retries: int = 5
    base_delay: float = .25
    max_delay: float = 8.0

    def __post_init__(self):
        if type(self.max_retries) is not int or not 0 <= self.max_retries <= 5:
            raise ValueError("CardStream retries must be between 0 and 5 (excluding initial attempt)")
        if self.base_delay < 0 or self.max_delay < self.base_delay:
            raise ValueError("Invalid retry backoff")

    def call(
        self, operation: Callable[[], T], *, replay_safe: bool,
        cancel_event: threading.Event | None = None, deadline: float | None = None,
        on_retry: Callable[[int, Exception], None] | None = None,
        retry_if: Callable[[Exception], bool] = transient,
    ) -> T:
        for attempt in range(self.max_retries + 1):
            if cancel_event is not None and cancel_event.is_set():
                raise RetryCancelled("CardStream request cancelled")
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("CardStream request deadline exhausted")
            try:
                return operation()
            except Exception as error:
                if not replay_safe or attempt >= self.max_retries or not retry_if(error):
                    raise
                if on_retry is not None:
                    on_retry(attempt + 1, error)
                delay = self.delay(attempt + 1, retry_after_ms=getattr(error, "retry_after_ms", None))
                if deadline is not None:
                    delay = min(delay, max(0.0, deadline - time.monotonic()))
                if cancel_event is not None:
                    if cancel_event.wait(delay):
                        raise RetryCancelled("CardStream request cancelled") from error
                else:
                    time.sleep(delay)
        raise AssertionError("RetryManager exhausted without a result or exception")

    def delay(self, retry_number: int, *, retry_after_ms: int | None = None) -> float:
        suggested = (max(0, retry_after_ms) / 1000
                     if type(retry_after_ms) is int else 0.0)
        backoff = self.base_delay * 2**max(0, retry_number - 1) * random.uniform(.75, 1.25)
        return min(self.max_delay, max(suggested, backoff))
