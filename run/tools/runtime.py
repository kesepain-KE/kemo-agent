"""Execution IDs and late-result routing independent of context visibility."""
from __future__ import annotations

from concurrent.futures import CancelledError
import copy
import queue
import threading
import time
from typing import Any

from events import RunEvent
from run.cards import IntegrationStore
from run.infra import diagnostic_text
from run.retry import HOST_CONTROL_EXCEPTIONS
from run.tools import ToolCancelledError, ToolTimeoutError, execute_tool, tool_result_payload


class ToolRuntime:
    """Tools execute once; a watchdog response is not an execution exit."""

    def __init__(self, integration: IntegrationStore, *, cancel_event=None):
        self.integration = integration
        self.cancel_event = cancel_event or threading.Event()
        self._events: queue.Queue[RunEvent | BaseException] = queue.Queue()
        self._publish_lock = threading.Lock()
        self.changed = threading.Event()

    def start(self, call, definition, *, context: dict[str, Any], timeout: float,
              transform=None) -> str:
        original = {"name": call.name, "arguments": copy.deepcopy(call.arguments)}
        execution, created = self.integration.register_tool(call.call_id, original)
        if created:
            context = dict(context)
            if isinstance(context.get("shell_ownership"), dict):
                context["shell_ownership"] = {
                    **context["shell_ownership"],
                    "cardstream_integration_id": self.integration.id,
                    "cardstream_generation": self.integration.generation,
                    "cardstream_meter_key": self.integration.store.meter.key,
                    "original_call": original, "tool_call_id": call.call_id,
                }
            with self._publish_lock:
                self._events.put(RunEvent(
                    type="tool_call_start", tool_call_id=call.call_id,
                    tool_name=call.name, arguments=copy.deepcopy(call.arguments),
                    metadata={"execution_id": execution["execution_id"], "status": "running",
                              "execution_live": True},
                ))
                self.changed.set()
            thread = threading.Thread(
                target=self._execute, daemon=True, name=f"card-tool-{execution['execution_id']}",
                args=(execution, definition, original, dict(context), timeout, transform),
            )
            try:
                thread.start()
            except BaseException as error:
                self._publish(execution, self._error(error), "failed", time.monotonic())
                raise
        return execution["execution_id"]

    def record(self, call, value: Any, *, status: str) -> None:
        """Persist a known deduplicated outcome, never fake real execution."""
        original = {"name": call.name, "arguments": copy.deepcopy(call.arguments)}
        for field in ("arguments_raw", "parse_error"):
            if getattr(call, field, None) is not None:
                original[field] = copy.deepcopy(getattr(call, field))
        execution, created = self.integration.register_tool(
            call.call_id, original,
        )
        if created:
            self._publish(execution, value, status, time.monotonic())

    @staticmethod
    def _error(error: BaseException) -> dict[str, Any]:
        detail: dict[str, Any] = {
            "exception_type": str(getattr(error, "remote_exception_type", "") or type(error).__name__),
            "message": diagnostic_text(str(error)),
        }
        for field in ("category", "retryable", "retry_after_ms", "still_running",
                      "status_code", "attempt_count", "retry_budget_exhausted"):
            value = getattr(error, field, None)
            if isinstance(value, (bool, int, float)) or (
                isinstance(value, str) and value.strip()
            ):
                detail[field] = value
        error_payload = getattr(error, "error_payload", None)
        if callable(error_payload):
            try:
                reported = error_payload()
            except Exception:
                reported = None
            if isinstance(reported, dict):
                detail.update(reported)
        return {"ok": False, "error": detail}

    @staticmethod
    def _status(value: Any) -> str:
        return "completed" if isinstance(value, dict) and value.get("ok") is True else "failed"

    def _publish(self, execution, value, status, started, *, live=False):
        with self._publish_lock:
            try:
                card = self.integration.tool_result(
                    execution["execution_id"], value, status=status,
                    duration_ms=round((time.monotonic() - started) * 1000), execution_live=live,
                )
                self._publish_card(card)
            except BaseException as error:
                # Surface DB failure; never retry an already-executed tool.
                self._events.put(error)
            self.changed.set()

    def _publish_card(self, card):
        payload = card.raw_payload
        original = payload["original_call"]
        self._events.put(RunEvent(
            type="tool_call_result", tool_call_id=card.tool_call_id or "",
            tool_name=original["name"], arguments=original["arguments"],
            result=payload["result"],
            metadata={"execution_id": payload["execution_id"],
                      "status": payload["execution"]["status"],
                      "elapsed_ms": payload["execution"].get("duration_ms", 0),
                      "execution_live": payload["execution"]["execution_live"],
                      "card_seq": card.seq, "original_call": original},
        ))

    def handoff(self, execution_ids: list[str], *, grace_seconds: float = .15) -> None:
        """Wait briefly for fast calls; persist a live receipt before moving on."""
        deadline = time.monotonic() + max(0.0, grace_seconds)
        while True:
            missing = self.integration.missing_receipts(execution_ids)
            if not missing:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self.cancel_event.is_set():
                break
            self.changed.wait(min(.025, remaining))
            self.changed.clear()
        for execution_id in self.integration.missing_receipts(execution_ids):
            with self._publish_lock:
                card = self.integration.publish_running_notice(execution_id)
                if card is not None:
                    self._publish_card(card)
        self.changed.set()

    def wait_for_initial_result(self, execution_id: str, *, timeout: float) -> str:
        """Wait for one short coordination tool without blocking other tools."""
        deadline = time.monotonic() + max(0.0, timeout) + 1.0
        while True:
            status = self.integration.tool_execution_status(execution_id)
            if status is None:
                raise RuntimeError("Tool execution disappeared before its initial result")
            if status != "running":
                return status
            if self.cancel_event.is_set():
                return status
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Tool execution did not publish its initial result")
            self.changed.wait(min(.05, remaining))
            self.changed.clear()

    def _execute(self, execution, definition, original, context, timeout, transform):
        started = time.monotonic()
        def finish_value(value):
            if transform is not None:
                try:
                    value = transform(value)
                except Exception as error:
                    value = {**self._error(error), "execution_completed": True,
                             "unprocessed_result": value}
            payload = tool_result_payload(
                original["name"], value, root=self.integration.store.root,
                user=self.integration.store.user, session_id=self.integration.session_id,
            )
            self._publish(execution, payload, self._status(payload), started)

        try:
            value = execute_tool(
                definition, original["arguments"], context=context, timeout=timeout,
                cancel_event=self.cancel_event, preserve_result=True,
            )
        except BaseException as error:
            if isinstance(error, HOST_CONTROL_EXCEPTIONS):
                self._publish(execution, self._error(error), "failed", started)
                with self._publish_lock:
                    self._events.put(error)
                    self.changed.set()
                return
            completion = getattr(error, "completion_future", None)
            if completion is not None:
                # Persist the watchdog fact first, then attach the real result.
                # add_done_callback is also safe if exit raced with the timeout.
                status = "cancelling" if isinstance(error, ToolCancelledError) else "timed_out_running"
                self._publish(execution, self._error(error), status, started, live=True)

                def exited(future):
                    try:
                        value = future.result()
                    except BaseException as late_error:
                        status = "cancelled" if isinstance(late_error, CancelledError) else "failed"
                        self._publish(execution, self._error(late_error), status, started)
                    else:
                        finish_value(value)

                completion.add_done_callback(exited)
            else:
                status = ("cancelled" if isinstance(error, ToolCancelledError) else
                          "timed_out" if isinstance(error, ToolTimeoutError) else "failed")
                self._publish(execution, self._error(error), status, started)
        else:
            finish_value(value)

    def events(self) -> list[RunEvent]:
        with self._publish_lock:
            self.changed.clear()
            result = []
            while True:
                try:
                    value = self._events.get_nowait()
                except queue.Empty:
                    return result
                if isinstance(value, BaseException):
                    raise value
                result.append(value)

    def has_events(self) -> bool:
        with self._publish_lock:
            return not self._events.empty()

    def wait(self, seconds: float = .1) -> None:
        self.changed.wait(seconds)
