"""Execution IDs and late-result routing independent of context visibility."""
from __future__ import annotations

from concurrent.futures import CancelledError
import copy
import logging
from multiprocessing.util import Finalize, is_exiting
import queue
import threading
import time
from typing import TYPE_CHECKING, Any, Callable, Literal

from events import RunEvent
from run.cards import Card, IntegrationStore
from run.infra import diagnostic_text
from run.retry import HOST_CONTROL_EXCEPTIONS
from run.tools import (
    ToolCancelledError, ToolTimeoutError, _CancellationSignal, _is_foreground_shell_run,
    execute_tool, tool_result_payload,
)

if TYPE_CHECKING:
    from run.tools.foreground_handoff import ForegroundHandoff


class _ShellExecution:
    """Control and terminal reply for a foreground Shell driven by one tool thread."""

    def __init__(self, run_cancel: _CancellationSignal, runtime_changed: threading.Event,
                 publish_recovered_handoff: Callable[[Card], None]):
        self._run_cancel = run_cancel
        self._publish_recovered_handoff = publish_recovered_handoff
        self._runtime_changed = runtime_changed
        self._cancel = threading.Event()
        self._changed = threading.Condition()
        self._command_started: dict[str, Any] | None = None
        self._terminal_claimed = False
        self._preparing_handoff = False
        self._handoff_job: ForegroundHandoff | None = None
        self._handoff_card: Card | None = None
        self._next_cancel_poll = 0.0
        self._reply: tuple[Any, BaseException | None] | None = None

    def command_started(self, notice: dict[str, Any]) -> None:
        with self._changed:
            self._command_started = notice
            self._runtime_changed.set()

    def claim_terminal(self) -> None:
        # Claim before cleanup, which may take time even after a reply arrived.
        with self._changed:
            self._terminal_claimed = True

    def is_set(self) -> bool:
        with self._changed:
            if self._cancel.is_set():
                return True
            job = self._handoff_job
            if job is None:
                return self._run_cancel.is_set()
            # Once committed (or unresolved), the original Run cannot stop this job.
            now = time.monotonic()
            if now < self._next_cancel_poll:
                return False
            self._next_cancel_poll = now + .2
        if job.cancel_requested():
            self.request_cancel()
            return True
        return False

    def request_cancel(self) -> None:
        with self._changed:
            self._cancel.set()

    def _has_handoff_job(self) -> bool:
        with self._changed:
            return self._handoff_job is not None

    def _recover_handoff_card(self, job: ForegroundHandoff) -> Card | None:
        # The caller holds _changed, so at most one observer publishes a
        # receipt recovered from an uncertain commit.
        if self._handoff_card is None:
            card = job.receipt()
            if card is not None:
                self._handoff_card = card
                self._publish_recovered_handoff(card)
        return self._handoff_card

    def close(self) -> None:
        # Shutdown only asks the execution thread to cancel. That thread still
        # owns IPC, group cleanup and PID release.
        self.request_cancel()
        with self._changed:
            self._changed.wait_for(lambda: self._reply is not None)

    def subscribe(self) -> _ShellReply:
        return _ShellReply(self)

    def _settle(self, value: Any, error: BaseException | None) -> None:
        with self._changed:
            self._terminal_claimed = True
            job, card = self._handoff_job, self._handoff_card
        try:
            # Never acquire the user metadata lock while holding _changed.
            # A terminal claim prevents a new commit while this write runs.
            if job is not None and card is None:
                from run.tools.foreground_handoff import ShellHandoffUncertain
                try:
                    with self._changed:
                        card = self._recover_handoff_card(job)
                        if card is None:
                            self._handoff_job = None
                except Exception as exc:
                    # Preserve the terminal snapshot behind the same visibility
                    # gate. A failed read is not proof that we can publish anew.
                    job.finish(value, error)
                    raise ShellHandoffUncertain("无法核对原 Shell 移交回执") from exc
                if card is None:
                    try:
                        job.discard()
                    except OSError:
                        logging.getLogger(__name__).exception("未提交的 Shell 移交记录无法撤销")
                    job = None
            if job is not None:
                job.finish(value, error)
        except BaseException as exc:
            error = exc
            raise
        finally:
            with self._changed:
                self._reply = (value, error)
                self._changed.notify_all()

    def drive(self, invoke: Callable[[], Any]) -> Any:
        # Run before multiprocessing joins children or releases its Event
        # resources. A daemon thread alone is not a shutdown guarantee.
        shutdown = Finalize(None, self.close, exitpriority=1)
        try:
            if is_exiting():
                self.request_cancel()
            value = invoke()
        except BaseException as error:
            self._settle(None, error)
            if not self._has_handoff_job():
                raise
            return None
        else:
            self._settle(value, None)
            return value
        finally:
            shutdown.cancel()


class _ShellReply:
    def __init__(self, execution: _ShellExecution):
        self._execution = execution
        self._subscribed = True

    def wait_reply(self, timeout: float | None = None) -> Any:
        """Return the original outcome, or None if this waiter unsubscribed."""
        execution = self._execution
        with execution._changed:
            if not execution._changed.wait_for(
                lambda: not self._subscribed or execution._reply is not None, timeout,
            ):
                raise TimeoutError("等待前台 Shell 结果超时")
            if not self._subscribed:
                return None
            reply = execution._reply
        assert reply is not None
        value, error = reply
        if error is not None:
            raise error
        return value

    def unsubscribe(self) -> None:
        with self._execution._changed:
            self._subscribed = False
            self._execution._changed.notify_all()


class ToolRuntime:
    """Tools execute once; a watchdog response is not an execution exit."""

    def __init__(self, integration: IntegrationStore, *, cancel_event=None):
        self.integration = integration
        self.cancel_event = cancel_event or threading.Event()
        self._events: queue.Queue[RunEvent | BaseException] = queue.Queue()
        self._publish_lock = threading.Lock()
        self.changed = threading.Event()
        self._decision_sequences: set[int] = set()
        self._has_decisions = False
        self._shell_executions: dict[str, _ShellExecution] = {}

    def shell_execution(self, execution_id: str) -> _ShellExecution | None:
        """Find an in-flight Shell controller in this runtime, not a job record."""
        with self._publish_lock:
            return self._shell_executions.get(execution_id)

    def shell_handoff_state(self, execution_id: str) -> Literal["ready", "waiting"] | None:
        """Schedule an attempt; only handoff_shell can authorize the commit."""
        shell = self.shell_execution(execution_id)
        if shell is None:
            return None
        with shell._changed:
            if (shell._command_started is None or shell._terminal_claimed
                    or shell._preparing_handoff or shell._handoff_job is not None):
                return "waiting"
            return "ready"

    def handoff_shell(self, execution_id: str) -> dict[str, Any] | None:
        """Commit a running Shell's receipt, or return None if it is not eligible."""
        from run.history import index_lock
        from run.tools.background_jobs import _jobs_dir, _reconcile_stale_jobs
        from run.tools.foreground_handoff import ForegroundHandoff, ShellHandoffUncertain, find_handoff_receipt

        integration = self.integration
        root, user = integration.store.root, integration.store.user
        shell = self.shell_execution(execution_id)
        if shell is None:
            card = find_handoff_receipt(
                root, user, integration.source, integration.session_id, integration.id,
                integration.generation, execution_id,
            )
            return card.raw_payload["result"] if card is not None else None
        with shell._changed:
            shell._changed.wait_for(lambda: not shell._preparing_handoff)
            if shell._handoff_job is not None:
                try:
                    card = shell._recover_handoff_card(shell._handoff_job)
                except Exception as exc:
                    raise ShellHandoffUncertain("无法核对原 Shell 移交回执") from exc
                return card.raw_payload["result"] if card is not None else None
            notice = shell._command_started
            if notice is None or shell._terminal_claimed or shell.is_set():
                return None
            shell._preparing_handoff = True

        try:
            _reconcile_stale_jobs(root, user, _jobs_dir(root, user))
            with index_lock(root, user):
                job = ForegroundHandoff.prepare(integration, execution_id, notice)
                keep_record = False
                try:
                    with shell._changed, self._publish_lock:
                        if shell._command_started is not notice or shell._terminal_claimed or shell.is_set():
                            return None
                        receipt = {
                            "ok": True, "status": "handed_off", "job_id": job.job_id,
                            "message": "命令已转为后台作业，请查询或续等此 job_id，不要重新 run。",
                            "next_action": {"name": "shell", "arguments": {
                                "action": "status", "job_id": job.job_id,
                            }},
                        }
                        interrupted = None
                        try:
                            with integration.store.db(write=True) as db:
                                if shell.is_set():
                                    return None
                                card = integration.tool_result(
                                    execution_id, receipt, status="handed_off", execution_live=False,
                                    duration_ms=round((time.monotonic() - notice["started_at"]) * 1000),
                                    transaction=db,
                                )
                        except BaseException as error:
                            # A writer can fail after SQLite committed. The
                            # original result event, not a file flag, decides.
                            try:
                                card = job.receipt()
                            except Exception as exc:
                                shell._handoff_job = job
                                keep_record = True
                                if not isinstance(error, Exception):
                                    raise error from exc
                                raise ShellHandoffUncertain("无法核对原 Shell 移交回执") from exc
                            if card is None:
                                raise
                            if not isinstance(error, Exception):
                                interrupted = error
                        shell._handoff_job, shell._handoff_card = job, card
                        keep_record = True
                        self._publish_card(card)
                        self.changed.set()
                        if interrupted is not None:
                            raise interrupted
                        return card.raw_payload["result"]
                finally:
                    if not keep_record:
                        job.discard()
        finally:
            with shell._changed:
                shell._preparing_handoff = False
                shell._changed.notify_all()

    def _publish_recovered_handoff(self, card: Card) -> None:
        with self._publish_lock:
            self._publish_card(card)
            self.changed.set()

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
            decision_wait = (call.name == "user_decision" and call.arguments.get("action") == "get"
                             and call.arguments.get("wait") is True)
            if call.name == "user_decision" and not decision_wait:
                # Ask (including mode=wait) and ordinary queries stay on the short DB path.
                started = time.monotonic()
                try:
                    value = definition.load_callable()(
                        **call.arguments,
                        context={**context, "decision_execution_id": execution["execution_id"]},
                    )
                except (ValueError, KeyError) as error:
                    self._publish(execution, self._error(error), "failed", started)
                else:
                    if call.arguments["action"] == "ask":
                        self._has_decisions = True
                        with self._publish_lock:
                            # An HTTP answer can win immediately after tool
                            # registration. Publish the initial pending receipt
                            # only while still live; poll the committed answer.
                            card = self.integration.publish_running_notice(
                                execution["execution_id"], result=value,
                            )
                            if card is not None:
                                self._publish_card(card)
                            self.changed.set()
                    else:
                        self._publish(execution, value, "completed", started)
                return execution["execution_id"]
            shell = (
                _ShellExecution(self.cancel_event, self.changed, self._publish_recovered_handoff)
                if _is_foreground_shell_run(definition.name, original["arguments"]) else None
            )
            thread = threading.Thread(
                target=self._execute, daemon=True, name=f"card-tool-{execution['execution_id']}",
                args=(execution, definition, original, dict(context), timeout, transform, shell),
            )
            if shell is not None:
                with self._publish_lock:
                    self._shell_executions[execution["execution_id"]] = shell
            try:
                thread.start()
            except BaseException as error:
                if shell is not None:
                    shell._settle(None, error)
                    with self._publish_lock:
                        self._shell_executions.pop(execution["execution_id"])
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
        if original["name"] == "user_decision":
            self._decision_sequences.add(card.seq)
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

    def restore_decisions(self) -> None:
        self._has_decisions = True

    def poll_decisions(self, *, cancel=False) -> bool:
        if not self._has_decisions:
            return False
        from run.tools.user_decisions import DecisionStore
        decisions = DecisionStore(self.integration.store)
        decisions.refresh(self.integration.source, self.integration.session_id,
                          integration_id=self.integration.id, cancel=cancel)
        cards = decisions.result_cards(self.integration)
        with self._publish_lock:
            for card in cards:
                if card.seq not in self._decision_sequences:
                    self._publish_card(card)
                    self.changed.set()
        return self.has_events()

    def waiting_for_decision(self) -> bool:
        if not self._has_decisions:
            return False
        from run.tools.user_decisions import DecisionStore
        return any(item["mode"] == "wait" for item in DecisionStore(
            self.integration.store,
        ).list(self.integration.source, self.integration.session_id,
               integration_id=self.integration.id))

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

    def _execute(self, execution, definition, original, context, timeout, transform, shell):
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

        def invoke():
            return execute_tool(
                definition, original["arguments"], context=context, timeout=timeout,
                cancel_event=shell if shell is not None else self.cancel_event, preserve_result=True,
                shell_execution=shell,
            )

        try:
            # The existing thread runs the entire isolated call, including its
            # receive loop, cleanup and any handed-off terminal record.
            value = shell.drive(invoke) if shell is not None else invoke()
        except BaseException as error:
            if shell is not None and shell._has_handoff_job():
                logging.getLogger(__name__).exception("Shell 移交作业收尾失败：%s", execution["execution_id"])
                return
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
            if shell is None or not shell._has_handoff_job():
                finish_value(value)
        finally:
            if shell is not None:
                with self._publish_lock:
                    self._shell_executions.pop(execution["execution_id"])

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
