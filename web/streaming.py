"""SSE transport timing and ownership of in-flight synchronous reads."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable, Coroutine, Iterator
import logging
import math
from typing import Any

import anyio
from anyio.to_thread import run_sync as run_cleanup_sync
from starlette.responses import StreamingResponse

from web.services.event_stream import FinalizingEventStream, QueueEventStream


DEFAULT_SSE_HEARTBEAT_SECONDS = 15.0
HEARTBEAT_FRAME = b": kemo-heartbeat\n\n"
DISCONNECT_POLL_SECONDS = 0.1
_logger = logging.getLogger(__name__)


def heartbeat_seconds(value: Any = None) -> float:
    """Reject booleans/non-finite input and bound operational config to 1–30s."""
    if isinstance(value, bool):
        return DEFAULT_SSE_HEARTBEAT_SECONDS
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_SSE_HEARTBEAT_SECONDS
    if not math.isfinite(seconds):
        return DEFAULT_SSE_HEARTBEAT_SECONDS
    return max(1.0, min(30.0, seconds))


class ClosingStreamingResponse(StreamingResponse):
    """Also release resources when sending headers fails before iteration."""

    def __init__(
        self, *args: Any, on_close: Callable[[], Awaitable[None]], **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._on_close = on_close

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                try:
                    if isinstance(self.body_iterator, AsyncGenerator):
                        await self.body_iterator.aclose()
                finally:
                    await self._on_close()


class StreamCleanup:
    """Keep real reads alive until safe closure; cancelling a Task isn't a join.

    Built-in consumers support concurrent close and wake blocked next() within
    their queue poll interval. Embedded/custom generators are closed only after
    their in-flight next() really returns; their source must honor cancellation.
    """

    def __init__(self) -> None:
        self.tasks: set[asyncio.Task] = set()

    async def _run(self, work: Coroutine) -> None:
        task = asyncio.create_task(work)
        self.tasks.add(task)
        task.add_done_callback(self._finished)
        # Starlette can cancel the entire AnyIO scope on disconnect. Shield
        # cleanup from that scope, but never hold a request open indefinitely.
        with anyio.CancelScope(shield=True):
            await asyncio.wait({task}, timeout=1.0)

    def _finished(self, task: asyncio.Task) -> None:
        self.tasks.discard(task)
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                # Backend errors may contain credentials: log type only.
                _logger.warning("Web stream cleanup failed (%s)", type(error).__name__)

    async def close(
        self, iterator: Iterator, pending: asyncio.Task | None = None,
    ) -> None:
        await self._run(self._close(iterator, pending))

    async def _close(self, iterator: Iterator, pending: asyncio.Task | None) -> None:
        concurrent_close = isinstance(iterator, (QueueEventStream, FinalizingEventStream))
        close = getattr(iterator, "close", None)
        if isinstance(iterator, (QueueEventStream, FinalizingEventStream)):
            # Do not queue closure behind the very reads it must wake. SSE
            # next() uses asyncio's executor, which may be completely occupied.
            await run_cleanup_sync(iterator.close)
            if pending is not None:
                done, _ = await asyncio.wait({pending}, timeout=0.2)
                if not done:
                    # The consumer is already closed: running queue reads have
                    # their own bounded wakeup; queued reads need not start.
                    # This cancellation is never used to *cause* safe closure.
                    pending.cancel()
        if pending is not None:
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                # Never infer that a cancelled Task means its thread exited.
                # The built-in consumer has already been explicitly closed.
                return
            except Exception:
                pass
        if not concurrent_close and callable(close):
            await asyncio.to_thread(close)

    async def abandon_preparation(self, task: asyncio.Task) -> None:
        async def finish() -> None:
            try:
                iterator, _interval = await asyncio.shield(task)
            except Exception:
                return
            await self._close(iter(iterator), None)

        await self._run(finish())

    async def drain(self) -> None:
        if self.tasks:
            with anyio.CancelScope(shield=True):
                await asyncio.wait(set(self.tasks), timeout=1.0)
