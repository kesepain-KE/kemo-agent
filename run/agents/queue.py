"""Bounded parallel subagents with a separate serialized maintenance lane."""

from __future__ import annotations

import copy
import math
import queue
import threading
import time
import uuid
from collections import Counter
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Any, Callable

from events import RunEvent
from provider.protocol.diagnostics import safe_provider_message
from provider.factory import provider_semaphore_status
from run.agents.runner import (
    AgentCancelledError,
    AgentRunResult,
    AgentRunner,
    AgentTimeoutError,
)
from run.agents.task_ledger import register_owned_task, task_progress
from run.agents.queue_models import AgentTask, TaskStatus
from run.agents.ready_queue import ReadyTaskQueue
from run.agents.budgets import effective_limit, SegmentBudget
from run.agents.contracts import AgentToolLimitError
from run.retry import HOST_CONTROL_EXCEPTIONS



_TERMINAL = {"completed", "failed", "cancelled", "timed_out", "timed_out_running", "tool_limit"}
_RETAINABLE_TERMINAL = {"completed", "failed", "cancelled", "timed_out", "tool_limit"}
_WORKER_IDLE_SECONDS = 300.0
_MAX_RETAINED_TERMINAL_TASKS = 256
_ERROR_DETAIL_FIELDS = (
    "category",
    "code",
    "status_code",
    "retryable",
    "retry_after_ms",
    "retry_attempts",
    "retry_max_attempts",
    "retry_exhausted",
    "retry_budget_exhausted",
    "stop_reason",
    "process_terminated",
)


def _agent_task_error_detail(error: BaseException) -> dict[str, Any]:
    """Keep a bounded, classified error snapshot for scheduler clients."""

    detail: dict[str, Any] = {
        "message": safe_provider_message(str(error), "子代理任务失败"),
        "exception_type": type(error).__name__,
        "stop_reason": ("tool_budget_exhausted" if isinstance(error, AgentToolLimitError)
                        else "timeout" if isinstance(error, AgentTimeoutError)
                        else "cancelled" if isinstance(error, AgentCancelledError)
                        else "host_exception" if isinstance(error, HOST_CONTROL_EXCEPTIONS)
                        else "execution_error"),
    }
    for field_name in _ERROR_DETAIL_FIELDS:
        value = getattr(error, field_name, None)
        if isinstance(value, (bool, int, float)):
            detail[field_name] = value
        elif isinstance(value, str) and value.strip():
            detail[field_name] = value.strip()[:160]
    return detail


class AgentQueueError(RuntimeError):
    pass


class AgentQueueClosedError(AgentQueueError):
    pass


class AgentTaskNotFoundError(AgentQueueError):
    pass


class AgentTaskWaitTimeout(AgentQueueError):
    pass




class AgentScheduler:
    def __init__(
        self,
        runner: AgentRunner,
        *,
        maxsize: int = 0,
        event_callback: Callable[[RunEvent], None] | None = None,
        thread_name: str = "kemo-agent-subagents",
    ) -> None:
        self.runner = runner
        self.event_callback = event_callback
        self._queue = ReadyTaskQueue(maxsize=max(0, maxsize))
        self._tasks: dict[str, AgentTask] = {}
        self._lock = threading.RLock()
        self._serial_lock = threading.Lock()
        self._enqueue_inflight = 0
        self._closed = False
        self._thread_name = thread_name
        self._worker: threading.Thread | None = None
        self._executors: set[threading.Thread] = set()
        self._wake = threading.Event()
        self._concurrency_config = copy.deepcopy(runner.config)
        self._submission_counter = 0
        self._last_activity = time.monotonic()

    def configure_concurrency(self, config: dict[str, Any]) -> None:
        """Next scheduling decisions use saved limits, not active task config."""
        with self._lock:
            self._concurrency_config = copy.deepcopy(config)
            self._wake.set()

    def _ensure_worker_locked(self) -> None:
        if self._closed:
            raise AgentQueueClosedError("子代理调度队列已关闭")
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(
                target=self._work,
                name=self._thread_name,
                daemon=True,
            )
            self._worker.start()

    def _prune_terminal_tasks_locked(self) -> None:
        terminal = [
            task
            for task in self._tasks.values()
            if self._is_retainable_terminal(task)
        ]
        excess = len(terminal) - _MAX_RETAINED_TERMINAL_TASKS
        if excess <= 0:
            return
        # Python's sort is stable, so equal clock values retain the insertion
        # order from _tasks.  Do not use the random task id as a tie-breaker:
        # Windows clocks can assign the same timestamp to many fast tasks.
        terminal.sort(key=lambda task: task.finished_at or task.created_at)
        for task in terminal[:excess]:
            self._tasks.pop(task.id, None)

    @staticmethod
    def _is_retainable_terminal(task: AgentTask) -> bool:
        if task.status not in _RETAINABLE_TERMINAL:
            return False
        detail = task.error if isinstance(task.error, dict) else {}
        return not (
            task.status == "cancelled"
            and detail.get("process_terminated") is False
        )

    def _watch_detached_completion(self, task: AgentTask, future: Any, continuation=None) -> None:
        from run.agents.detached_completion import watch_detached_completion
        watch_detached_completion(self, task, future, continuation)

    @classmethod
    def from_runner(
        cls,
        runner: AgentRunner,
        *,
        event_callback: Callable[[RunEvent], None] | None = None,
    ) -> "AgentScheduler":
        runtime = runner.config.get("agent_runtime") or {}
        return cls(
            runner,
            maxsize=int(runtime.get("queue_maxsize", 50)),
            event_callback=event_callback,
        )

    def _emit(self, task: AgentTask, status: str, **detail: Any) -> None:
        # Serialize snapshot+durable write with state transitions. Otherwise a
        # slow queued write can arrive *after* completed and resurrect a child.
        with self._lock:
            if self._tasks.get(task.id) is not None and self._tasks.get(task.id) is not task:
                return
            if status == "queued" and task.status != "queued":
                return
            if task.ledger is not None:
                task.ledger.scheduler_update(task, status)
        callback = task.event_callback or self.event_callback
        if callback is None:
            return
        metadata = {
            "phase": "subagent",
            "agent": task.agent,
            "status": status,
            "task_id": task.id,
            "submission_order": task.submission_order,
            **detail,
        }
        # Task ownership is authoritative; diagnostic detail cannot redirect
        # an event into another conversation space.
        metadata["source"] = task.source
        metadata["session_id"] = task.session_id
        metadata["run_count"] = task.run_count
        callback(RunEvent(type="reasoning_delta", metadata=metadata))

    @staticmethod
    def _require_task_scope_locked(
        task: AgentTask,
        *,
        source: str = "",
        session_id: str = "",
    ) -> None:
        expected_source = str(source or "").strip()
        expected_session = str(session_id or "").strip()
        if not expected_source and not expected_session:
            if task.source or task.session_id:
                # Scoped tasks must never be readable or cancellable through
                # the historical unscoped API.  Keep truly legacy unscoped
                # tasks usable for trusted internal callers.
                raise AgentTaskNotFoundError(f"未知子代理任务：{task.id}")
            return
        if (
            not expected_source
            or not expected_session
            or task.source != expected_source
            or task.session_id != expected_session
        ):
            # Keep ownership failures indistinguishable from an unknown task
            # so a caller cannot probe another conversation space by id.
            raise AgentTaskNotFoundError(f"未知子代理任务：{task.id}")

    def submit(
        self,
        agent: str,
        input_data: dict[str, Any],
        *,
        timeout: float | None = None,
        timeout_survival_seconds: float | None = None,
        model_override: str | None = None,
        max_tokens: int | None = None,
        result_handler: Callable[[AgentRunResult], None] | None = None,
        event_callback: Callable[[RunEvent], None] | None = None,
        block: bool = False,
        enqueue_timeout: float | None = None,
        allow_sync: bool = False,
        config: dict[str, Any] | None = None,
        source: str = "",
        session_id: str = "",
        ownership: dict[str, Any] | None = None,
        max_tool_calls: int | None = None,
        origin_task_id: str = "",
    ) -> str:
        registry = (
            self.runner.refresh_registry()
            if hasattr(self.runner, "refresh_registry")
            else self.runner.registry
        )
        definition = registry.get(agent)
        if max_tool_calls is not None:
            effective_limit(definition, config or self.runner.config, max_tool_calls)
        if not allow_sync and definition.execution != "background_serial":
            raise AgentQueueError(f"子代理 {agent} 未声明 background_serial 执行模式")
        if not isinstance(input_data, dict):
            raise AgentQueueError("子代理输入必须是 JSON 对象")
        if config is not None and not isinstance(config, dict):
            raise AgentQueueError("子代理 config 必须是 JSON 对象")
        normalized_source = str(source or "").strip()
        normalized_session_id = str(session_id or "").strip()
        if bool(normalized_source) != bool(normalized_session_id):
            raise AgentQueueError("子代理 source 和 session_id 必须同时提供或同时省略")
        if timeout_survival_seconds is None:
            raw_survival = (self.runner.config.get("agent_runtime") or {}).get(
                "timeout_survival_seconds", 0.0
            )
        else:
            raw_survival = timeout_survival_seconds
        try:
            survival_seconds = float(raw_survival)
        except (TypeError, ValueError) as exc:
            raise AgentQueueError(
                "timeout_survival_seconds 必须是非负数"
            ) from exc
        if not math.isfinite(survival_seconds) or survival_seconds < 0:
            raise AgentQueueError("timeout_survival_seconds 必须是非负数")
        with self._lock:
            if self._closed:
                raise AgentQueueClosedError("子代理调度队列已关闭")
            if config is not None:
                self._concurrency_config = copy.deepcopy(config)
            self._ensure_worker_locked()
            task = AgentTask(
                id=f"agent-task-{uuid.uuid4().hex}",
                agent=agent,
                input_data=copy.deepcopy(input_data),
                source=normalized_source,
                session_id=normalized_session_id,
                timeout=timeout,
                survival_seconds=survival_seconds,
                model_override=model_override,
                max_tokens=max_tokens,
                config=copy.deepcopy(config) if config is not None else None,
                serial=definition.execution == "background_serial",
                result_handler=result_handler,
                event_callback=event_callback,
                max_tool_calls=max_tool_calls,
                origin_task_id=origin_task_id,
            )
            if isinstance(self.runner, AgentRunner):
                task.progress = {"status": "queued", "budget": SegmentBudget(
                    definition, config or self.runner.config, max_tool_calls,
                    timeout if timeout is not None else definition.timeout).snapshot()}
                task.progress["budget"]["elapsed_seconds"] = 0
            self._submission_counter += 1
            task.submission_order = self._submission_counter
            register_owned_task(self.runner, task, ownership)
            self._tasks[task.id] = task
            self._last_activity = time.monotonic()
            # 标记正在入队的提交者，使 close 后的工作线程不会在 put 真正
            # 完成前退出；put 本身不能持有 _lock，否则阻塞入队会卡住关闭。
            self._enqueue_inflight += 1
        try:
            self._queue.put(task, block=block, timeout=enqueue_timeout)
            self._wake.set()
        except queue.Full as exc:
            message = str(exc) or "子代理调度队列已满"
            with self._lock:
                self._tasks.pop(task.id, None)
            if task.ledger is not None:
                task.ledger.update(task.id, status="failed", execution_live=False,
                                   error={"message": message})
            raise AgentQueueError(message) from exc
        except BaseException:
            with self._lock:
                self._tasks.pop(task.id, None)
            if task.ledger is not None:
                task.ledger.update(task.id, status="failed", execution_live=False,
                                   error={"message": "子代理入队失败"})
            raise
        finally:
            with self._lock:
                self._enqueue_inflight = max(0, self._enqueue_inflight - 1)
        with self._lock:
            emit_queued = task.status == "queued"
        if emit_queued:
            self._emit(task, "queued")
        return task.id

    def submit_callable(
        self,
        agent: str,
        input_data: dict[str, Any],
        job: Callable[[threading.Event], Any],
        *,
        timeout: float | None = None,
        timeout_survival_seconds: float | None = None,
        block: bool = False,
        enqueue_timeout: float | None = None,
        source: str = "",
        session_id: str = "",
        event_callback: Callable[[RunEvent], None] | None = None,
        ownership: dict[str, Any] | None = None,
    ) -> str:
        """Queue a trusted framework-owned job with the same status/cancel contract."""

        if not isinstance(input_data, dict):
            raise AgentQueueError("子代理输入必须是 JSON 对象")
        if not callable(job):
            raise AgentQueueError("子代理任务入口不可调用")
        normalized_source = str(source or "").strip()
        normalized_session_id = str(session_id or "").strip()
        if bool(normalized_source) != bool(normalized_session_id):
            raise AgentQueueError("子代理 source 和 session_id 必须同时提供或同时省略")
        if timeout_survival_seconds is None:
            raw_survival = (self.runner.config.get("agent_runtime") or {}).get(
                "timeout_survival_seconds", 0.0
            )
        else:
            raw_survival = timeout_survival_seconds
        try:
            survival_seconds = float(raw_survival)
        except (TypeError, ValueError) as exc:
            raise AgentQueueError(
                "timeout_survival_seconds 必须是非负数"
            ) from exc
        if not math.isfinite(survival_seconds) or survival_seconds < 0:
            raise AgentQueueError("timeout_survival_seconds 必须是非负数")
        with self._lock:
            if self._closed:
                raise AgentQueueClosedError("子代理调度队列已关闭")
            self._ensure_worker_locked()
            task = AgentTask(
                id=f"agent-task-{uuid.uuid4().hex}",
                agent=str(agent or "external"),
                input_data=copy.deepcopy(input_data),
                source=normalized_source,
                session_id=normalized_session_id,
                timeout=timeout,
                survival_seconds=survival_seconds,
                job=job,
                serial=False,
                event_callback=event_callback,
            )
            self._submission_counter += 1
            task.submission_order = self._submission_counter
            register_owned_task(self.runner, task, ownership)
            self._tasks[task.id] = task
            self._last_activity = time.monotonic()
            self._enqueue_inflight += 1
        try:
            self._queue.put(task, block=block, timeout=enqueue_timeout)
            self._wake.set()
        except queue.Full as exc:
            message = str(exc) or "子代理调度队列已满"
            with self._lock:
                self._tasks.pop(task.id, None)
            if task.ledger is not None:
                task.ledger.update(task.id, status="failed", execution_live=False,
                                   error={"message": message})
            raise AgentQueueError(message) from exc
        except BaseException:
            with self._lock:
                self._tasks.pop(task.id, None)
            if task.ledger is not None:
                task.ledger.update(task.id, status="failed", execution_live=False,
                                   error={"message": "子代理入队失败"})
            raise
        finally:
            with self._lock:
                self._enqueue_inflight = max(0, self._enqueue_inflight - 1)
        with self._lock:
            emit_queued = task.status == "queued"
        if emit_queued:
            self._emit(task, "queued")
        return task.id

    def get(
        self,
        task_id: str,
        *,
        source: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                if isinstance(self.runner, AgentRunner) and source and session_id:
                    from run.agents.task_ledger import TaskLedger
                    record = TaskLedger(self.runner.root, self.runner.user).get(task_id, source, session_id)
                    if record and record.get("agent") != "main":
                        return record
                raise AgentTaskNotFoundError(f"未知子代理任务：{task_id}")
            self._require_task_scope_locked(task, source=source, session_id=session_id)
            self._last_activity = time.monotonic()
            snapshot = task.snapshot()
            if task.ledger is not None:
                record = task.ledger.get(task_id, source or task.source, session_id or task.session_id)
                for key in ("background_jobs", "stop_reason"):
                    if record and record.get(key) is not None:
                        snapshot[key] = record[key]
            return snapshot

    def resume(self, task_id: str, *, source: str, session_id: str,
               guidance: str = "", timeout: float | None = None, ownership=None,
               config=None, event_callback=None, max_tool_calls=None) -> str:
        from run.agents.resume import resume_task
        return resume_task(self, task_id, source=source, session_id=session_id,
                           guidance=guidance, timeout=timeout, ownership=ownership,
                           config=config, event_callback=event_callback, max_tool_calls=max_tool_calls)

    def wait(
        self,
        task_id: str,
        timeout: float | None = None,
        *,
        source: str = "",
        session_id: str = "",
    ) -> Any:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise AgentTaskNotFoundError(f"未知子代理任务：{task_id}")
            self._require_task_scope_locked(task, source=source, session_id=session_id)
        if not task.done_event.wait(timeout):
            raise AgentTaskWaitTimeout(f"等待子代理任务超时：{task_id}")
        with self._lock:
            if task.host_exception is not None:
                raise task.host_exception
            if task.status == "completed" and task.result is not None:
                return task.result
            if task.status in {"timed_out", "tool_limit"} and task.result is not None:
                return task.result
            detail = task.error or {"message": f"任务状态：{task.status}"}
            if task.status == "cancelled":
                raise AgentCancelledError(str(detail.get("message") or "任务已取消"))
            raise AgentQueueError(str(detail.get("message") or "子代理任务失败"))

    def cancel(
        self,
        task_id: str,
        *,
        source: str = "",
        session_id: str = "",
    ) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise AgentTaskNotFoundError(f"未知子代理任务：{task_id}")
            self._require_task_scope_locked(task, source=source, session_id=session_id)
            detail = task.error if isinstance(task.error, dict) else {}
            still_running = (
                task.status == "timed_out_running"
                or (
                    task.status == "cancelled"
                    and detail.get("process_terminated") is False
                )
            )
            if task.status in _TERMINAL and not still_running:
                return False
            task.cancel_event.set()
            self._last_activity = time.monotonic()
            if task.status != "queued":
                detail = dict(detail)
                detail["cancel_requested"] = True
                detail["user_cancel_requested"] = True
                task.error = detail
            if still_running:
                detail = dict(detail)
                detail["cancel_requested"] = True
                detail["process_terminated"] = False
                detail["user_cancel_requested"] = True
                task.error = detail
                self._emit(task, "cancel_requested")
                return True
            if task.status == "queued":
                task.status = "cancelled"
                task.finished_at = datetime.now(timezone.utc).isoformat()
                task.error = {
                    "message": "任务在排队时取消",
                    "exception_type": "AgentCancelledError",
                    "cancel_requested": True,
                    "user_cancel_requested": True,
                    "process_terminated": True,
                }
                task.done_event.set()
                self._emit(task, "cancelled")
            else:
                self._emit(task, "cancel_requested")
            return True

    def close(self, *, wait: bool = True, cancel_pending: bool = False) -> None:
        worker: threading.Thread | None
        with self._lock:
            if not self._closed:
                self._closed = True
                if cancel_pending:
                    for task in self._tasks.values():
                        if task.status in {"queued", "running", "timed_out_running"}:
                            task.cancel_event.set()
                            if task.status == "timed_out_running":
                                detail = task.error if isinstance(task.error, dict) else {}
                                detail = dict(detail)
                                detail["cancel_requested"] = True
                                task.error = detail
                            if task.status == "queued":
                                task.status = "cancelled"
                                task.finished_at = datetime.now(timezone.utc).isoformat()
                                task.error = {
                                    "message": "调度器关闭时取消",
                                    "exception_type": "AgentCancelledError",
                                }
                                task.done_event.set()
                                self._emit(task, "cancelled")
            worker = self._worker
            self._wake.set()
        if wait:
            # join 不能持有 _lock；工作线程完成当前任务和退出检查都需要该锁。
            if worker is not None and worker is not threading.current_thread():
                worker.join()

    def _work(self) -> None:
        """Dispatch runnable tasks; serial maintenance never blocks normal work."""
        while True:
            self._wake.clear()
            with self._lock:
                live = [
                    task for task in self._tasks.values()
                    if task.status in {"running", "timed_out_running"}
                    or (task.status == "cancelled"
                        and isinstance(task.error, dict)
                        and task.error.get("process_terminated") is False)
                ]
                limit = (provider_semaphore_status(self._concurrency_config)["max_requests"]
                         if not self._queue.empty() else 0)
                serial_busy = any(task.serial for task in live)
                task = self._queue.take_ready(
                    lambda item: item.status == "cancelled"
                    or (len(live) < limit and (not item.serial or not serial_busy)),
                    active=Counter(item.scope for item in live),
                )
                if task is None:
                    if self._closed and self._enqueue_inflight == 0:
                        if self._queue.empty() and not self._executors:
                            self._worker = None
                            return
                    if (
                        not self._closed
                        and self._queue.empty()
                        and not self._executors
                        and not any(
                            task.status in {"queued", "running"}
                            for task in self._tasks.values()
                        )
                        and time.monotonic() - self._last_activity >= _WORKER_IDLE_SECONDS
                    ):
                        if self._worker is threading.current_thread():
                            self._worker = None
                        return
                else:
                    if task.status == "cancelled":
                        self._queue.task_done()
                        continue
                    task.status = "running"
                    task.started_at = datetime.now(timezone.utc).isoformat()
                    self._last_activity = time.monotonic()
                    executor = threading.Thread(
                        target=self._execute_task, args=(task,),
                        name=f"{self._thread_name}-{task.id[-8:]}", daemon=True,
                    )
                    self._executors.add(executor)
                    executor.start()
                    continue
            self._wake.wait(.1)

    def _execute_task(self, task: AgentTask) -> None:
        try:
            self._run_task(task)
        finally:
            self._queue.task_done()
            with self._lock:
                self._executors.discard(threading.current_thread())
            self._wake.set()

    def _run_task(self, task: AgentTask) -> None:
        try:
            self._emit(task, "running")
            # 每个用户拥有独立 AgentScheduler；仅对声明 background_serial
            # 的本地代理串行执行，桥接/同步任务使用自己的模块锁。
            with (self._serial_lock if task.serial else nullcontext()):
                if task.cancel_event.is_set():
                    raise AgentCancelledError("子代理任务已取消")
                if task.job is not None:
                    result = task.job(task.cancel_event)
                else:
                    runner = self.runner
                    if task.config is not None and isinstance(self.runner, AgentRunner):
                        runner = AgentRunner(
                            self.runner.root,
                            self.runner.user,
                            config=copy.deepcopy(task.config),
                            provider_factory=self.runner.provider_factory,
                        )
                    result = runner.run(
                        task.agent,
                        task.input_data,
                        cancel_event=task.cancel_event,
                        timeout=task.timeout,
                        timeout_survival_seconds=task.survival_seconds,
                        model_override=task.model_override,
                        event_callback=task_progress(task, self.event_callback),
                        task_id=task.id,
                        max_tokens=task.max_tokens,
                        source=task.source,
                        session_id=task.session_id,
                        **({"max_tool_calls": task.max_tool_calls} if task.max_tool_calls is not None else {}),
                        **({"resume_state": task.resume_state, "run_count": task.run_count}
                           if task.resume_state is not None else {}),
                    )
                if task.cancel_event.is_set():
                    raise AgentCancelledError("子代理任务已取消", process_terminated=True)
                if task.result_handler is not None:
                    task.result_handler(result)
            with self._lock:
                task.result = result
                task.completed_after_timeout = bool(
                    isinstance(result, AgentRunResult)
                    and result.metadata.get("completed_after_timeout")
                )
                task.status = "completed"
                task.finished_at = datetime.now(timezone.utc).isoformat()
                self._last_activity = time.monotonic()
                self._prune_terminal_tasks_locked()
        except BaseException as exc:
            detached_future = getattr(exc, "completion_future", None)
            with self._lock:
                if isinstance(exc, HOST_CONTROL_EXCEPTIONS):
                    task.host_exception = exc
                previous_detail = (
                    task.error if isinstance(task.error, dict) else {}
                )
                if isinstance(exc, HOST_CONTROL_EXCEPTIONS):
                    task.status = "failed"
                elif isinstance(exc, AgentTimeoutError):
                    task.status = (
                        "timed_out"
                        if exc.process_terminated
                        else "timed_out_running"
                    )
                elif isinstance(exc, AgentCancelledError) or task.cancel_event.is_set():
                    task.status = "cancelled"
                elif isinstance(exc, AgentToolLimitError):
                    task.status = "tool_limit"
                else:
                    task.status = "failed"
                task.error = _agent_task_error_detail(exc)
                if task.status == "cancelled":
                    task.error["stop_reason"] = "cancelled"
                task.result = getattr(exc, "partial_result", None)
                if previous_detail.get("user_cancel_requested"):
                    task.error["user_cancel_requested"] = True
                if isinstance(exc, (AgentTimeoutError, AgentToolLimitError)):
                    task.result = exc.partial_result
                    task.resumable = bool(
                        exc.continuation is not None and exc.continuation.available
                        and exc.process_terminated
                    )
                    task.error.update(
                        {
                            "cancel_requested": True,
                            "process_terminated": exc.process_terminated,
                        }
                    )
                elif isinstance(exc, AgentCancelledError):
                    task.error.update(
                        {
                            "cancel_requested": True,
                            "process_terminated": getattr(
                                exc,
                                "process_terminated",
                                True,
                            ),
                        }
                    )
                task.finished_at = datetime.now(timezone.utc).isoformat()
                self._last_activity = time.monotonic()
                self._prune_terminal_tasks_locked()
            if (
                detached_future is not None
                and task.status in {"timed_out_running", "cancelled"}
            ):
                self._watch_detached_completion(task, detached_future, getattr(exc, "continuation", None))
        finally:
            try:
                self._emit(task, task.status)
            finally:
                task.done_event.set()
