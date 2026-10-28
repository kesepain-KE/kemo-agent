"""Explicitly closeable Web consumers; never close a running Run generator."""

from __future__ import annotations

from collections.abc import Callable, Iterator
import queue
import threading

from events import RunEvent


class QueueEventStream(Iterator[RunEvent]):
    """One reader, with thread-safe, idempotent close even before first next().

    Cancellation of a Run is NOT closure of its consumer: an explicit user
    cancellation must still deliver the worker's terminal event. Only close()
    ends the queue wait. The producer owns its Run's thread-affine cleanup.
    """

    def __init__(
        self,
        output: queue.Queue,
        worker_done: object,
        consumer_closed: threading.Event,
        cancel_event: threading.Event,
        on_close: Callable[[], None],
    ) -> None:
        self._output = output
        self._worker_done = worker_done
        self._closed = consumer_closed
        self._cancel_event = cancel_event
        self._on_close = on_close
        self._close_lock = threading.Lock()

    def __next__(self) -> RunEvent:
        try:
            while not self._closed.is_set():
                try:
                    value = self._output.get(timeout=0.1)
                except queue.Empty:
                    continue
                if self._closed.is_set() or value is self._worker_done:
                    break
                if isinstance(value, BaseException):
                    raise value
                if isinstance(value, RunEvent):
                    return value
        except BaseException:
            self.close()
            raise
        self.close()
        raise StopIteration

    def close(self) -> None:
        # A Python generator's close() cannot interrupt a running next(). This
        # consumer has no suspended generator frame and never joins the Run
        # worker: queue waits and Web slots can end even if a tool is still busy.
        with self._close_lock:
            if self._closed.is_set():
                return
            self._closed.set()
            self._cancel_event.set()
            self._on_close()


class FinalizingEventStream(Iterator[RunEvent]):
    """Add plan finalization to a QueueEventStream, including unstarted close."""

    def __init__(
        self,
        source: QueueEventStream,
        on_close: Callable[[], None],
    ) -> None:
        self._source = source
        self._on_close = on_close
        self._closed = False
        self._close_lock = threading.Lock()

    def __next__(self) -> RunEvent:
        if self._closed:
            raise StopIteration
        try:
            return next(self._source)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._source.close()
            finally:
                self._on_close()
