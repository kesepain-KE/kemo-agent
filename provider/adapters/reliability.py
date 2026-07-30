"""Bounded network retry and cancellation helpers for Provider adapters."""

from __future__ import annotations

import email.utils
import http.client
import random
import socket
import threading
import time
import urllib.error
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from provider.schema import ProviderError, ProviderTimeoutError


NETWORK_READ_ERRORS = (OSError, http.client.HTTPException)
CANCEL_REQUEST_TIMEOUT_SECONDS = 2.0
_CANCEL_POLL_SECONDS = 0.1
_T = TypeVar("_T")


def parse_retry_after_ms(
    exc: urllib.error.HTTPError,
    body: Any,
) -> int | None:
    """Read a Kemo retry hint from the body or standard HTTP header."""

    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            value = error.get("retry_after_ms")
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
    raw = exc.headers.get("Retry-After") if exc.headers is not None else None
    if not raw:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        try:
            target = email.utils.parsedate_to_datetime(str(raw))
            seconds = target.timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0, round(seconds * 1000))


def transport_error(exc: BaseException, *, action: str) -> ProviderError:
    """Normalize errors raised after the HTTP response has been opened."""

    reason = getattr(exc, "reason", exc)
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return ProviderTimeoutError(f"Kemo gateway {action}超时：{reason}")
    return ProviderError(
        f"Kemo gateway {action}连接中断：{reason}",
        category="connection_error",
        retryable=True,
    )


@dataclass(slots=True)
class KemoNetworkRetryPolicy:
    """Retry only explicitly transient transport failures with bounded waits."""

    max_attempts: int = 3
    base_seconds: float = 0.5
    cap_seconds: float = 10.0

    @staticmethod
    def cancelled_error(*, attempt_count: int | None = None) -> ProviderError:
        return ProviderError(
            "Kemo gateway 请求已取消",
            category="cancelled",
            retryable=False,
            attempt_count=attempt_count,
        )

    def wait_before_retry(
        self,
        error: ProviderError,
        *,
        failed_attempt: int,
        cancel_event: threading.Event | None,
    ) -> None:
        retry_after_ms = getattr(error, "retry_after_ms", None)
        if isinstance(retry_after_ms, int) and retry_after_ms >= 0:
            delay = min(self.cap_seconds, retry_after_ms / 1000.0)
        else:
            base = min(
                self.cap_seconds,
                self.base_seconds * (2 ** max(0, failed_attempt - 1)),
            )
            delay = min(
                self.cap_seconds,
                base + random.uniform(0.0, base * 0.25),
            )
        if cancel_event is not None:
            if cancel_event.wait(delay):
                raise self.cancelled_error(attempt_count=failed_attempt)
        elif delay > 0:
            time.sleep(delay)

    def retry_or_raise(
        self,
        error: ProviderError,
        *,
        failed_attempt: int,
        cancel_event: threading.Event | None,
    ) -> None:
        error.attempt_count = failed_attempt
        if not error.retryable or failed_attempt >= self.max_attempts:
            raise error
        self.wait_before_retry(
            error,
            failed_attempt=failed_attempt,
            cancel_event=cancel_event,
        )

    def run(
        self,
        operation: Callable[[], _T],
        *,
        cancel_event: threading.Event | None,
    ) -> _T:
        for attempt in range(1, self.max_attempts + 1):
            if cancel_event is not None and cancel_event.is_set():
                raise self.cancelled_error(attempt_count=attempt - 1)
            try:
                return operation()
            except ProviderError as exc:
                self.retry_or_raise(
                    exc,
                    failed_attempt=attempt,
                    cancel_event=cancel_event,
                )
        raise AssertionError("Provider 网络重试循环异常退出")


@dataclass(slots=True)
class ResponseCancellationWatcher:
    """Close a blocking response as soon as its Run is cancelled."""

    stopped: threading.Event
    thread: threading.Thread

    def close(self) -> None:
        self.stopped.set()
        if self.thread is not threading.current_thread():
            self.thread.join(timeout=_CANCEL_POLL_SECONDS * 2)


def start_cancel_watcher(
    response: Any,
    cancel_event: threading.Event | None,
) -> ResponseCancellationWatcher | None:
    if cancel_event is None:
        return None
    stopped = threading.Event()

    def watch() -> None:
        while not stopped.wait(_CANCEL_POLL_SECONDS):
            if not cancel_event.is_set():
                continue
            try:
                response.close()
            except Exception:
                pass
            return

    thread = threading.Thread(
        target=watch,
        name="kemo-sse-cancel",
        daemon=True,
    )
    thread.start()
    return ResponseCancellationWatcher(stopped=stopped, thread=thread)
