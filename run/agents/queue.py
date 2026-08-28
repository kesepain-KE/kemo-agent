"""用于具有写入副作用的子代理的单工作后台调度程序。"""

from __future__ import annotations

import copy
import math
import queue
import threading
import time
import uuid
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from events import RunEvent
from provider.protocol.diagnostics import safe_provider_message
from run.agents.runner import (
    AgentCancelledError,
    AgentRunResult,
    AgentRunner,
    AgentTimeoutError,
)


TaskStatus = Literal[
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
    "timed_out",
    "timed_out_running",
]
_TERMINAL = {"completed", "failed", "cancelled", "timed_out", "timed_out_running"}
_RETAINABLE_TERMINAL = {"completed", "failed", "cancelled", "timed_out"}
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
)


def _agent_task_error_detail(error: BaseException) -> dict[str, Any]:
    """Keep a bounded, classified error snapshot for scheduler clients."""

    detail: dict[str, Any] = {
        "message": safe_provider_message(str(error), "子代理任务失败"),
        "exception_type": type(error).__name__,
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


@dataclass(slots=True)
class AgentTask:
    id: str
    agent: str
    input_data: dict[str, Any]
    status: TaskStatus = "queued"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str = ""
    finished_at: str = ""
    timeout: float | None = None
    survival_seconds: float = 120.0
    completed_after_timeout: bool = False
    model_override: str | None = None
    max_tokens: int | None = None
    config: dict[str, Any] | None = field(default=None, repr=False)
    job: Callable[[threading.Event], Any] | None = field(default=None, repr=False)
    serial: bool = True
    result_handler: Callable[[AgentRunResult], None] | None = field(default=None, repr=False)
    result: Any = None
    error: dict[str, Any] | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    done_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def snapshot(self) -> dict[str, Any]:
        result = self.result
        if isinstance(result, AgentRunResult):
            result_snapshot: Any = {
                "agent": result.agent,
                "data": copy.deepcopy(result.data),
                "usage": copy.deepcopy(result.usage),
                "model": result.model,
                "metadata": copy.deepcopy(result.metadata),
            }
        elif result is not None:
            result_snapshot = copy.deepcopy(result)
        else:
            result_snapshot = None
        return {
            "id": self.id,
            "agent": self.agent,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "timeout": self.timeout,
            "survival_seconds": self.survival_seconds,
            "completed_after_timeout": self.completed_after_timeout,
            "model_override": self.model_override,
            "result": result_snapshot,
            "error": copy.deepcopy(self.error),
        }


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
        self._queue: queue.Queue[AgentTask | object] = queue.Queue(maxsize=max(0, maxsize))
        self._tasks: dict[str, AgentTask] = {}
        self._lock = threading.RLock()
        self._serial_lock = threading.Lock()
        self._enqueue_inflight = 0
        self._closed = False
        self._thread_name = thread_name
        self._worker: threading.Thread | None = None
        self._last_activity = time.monotonic()

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
        terminal.sort(key=lambda task: (task.finished_at or task.created_at, task.id))
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

    def _watch_detached_completion(self, task: AgentTask, future: Any) -> None:
        """Reconcile a task after the caller timed out but its worker survived."""

        add_done_callback = getattr(future, "add_done_callback", None)
        if not callable(add_done_callback):
            return

        def reconcile(done_future: Any) -> None:
            try:
                result = done_future.result()
            except BaseException as exc:
                with self._lock:
                    current = self._tasks.get(task.id)
                    if current is not task:
                        return
                    previous = task.error if isinstance(task.error, dict) else {}
                    user_cancel_requested = bool(
                        previous.get("user_cancel_requested")
                    )
                    if task.status == "cancelled" or user_cancel_requested:
                        task.status = "cancelled"
                    elif task.status == "timed_out_running":
                        task.status = "timed_out"
                    else:
                        task.status = "failed"
                    detail = _agent_task_error_detail(exc)
                    detail.update(
                        {
                            "cancel_requested": bool(
                                previous.get("cancel_requested")
                            ),
                            "process_terminated": True,
                        }
                    )
                    if user_cancel_requested:
                        detail["user_cancel_requested"] = True
                    task.error = detail
                    task.finished_at = datetime.now(timezone.utc).isoformat()
                    self._last_activity = time.monotonic()
                    self._prune_terminal_tasks_locked()
                    self._emit(task, task.status, detail=detail)
                return

            try:
                if task.result_handler is not None:
                    task.result_handler(result)
            except BaseException as exc:
                with self._lock:
                    current = self._tasks.get(task.id)
                    if current is not task:
                        return
                    previous = task.error if isinstance(task.error, dict) else {}
                    detail = _agent_task_error_detail(exc)
                    detail.update(
                        {
                            "cancel_requested": bool(
                                previous.get("cancel_requested")
                            ),
                            "process_terminated": True,
                        }
                    )
                    if previous.get("user_cancel_requested"):
                        detail["user_cancel_requested"] = True
                    task.status = "failed"
                    task.error = detail
                    task.finished_at = datetime.now(timezone.utc).isoformat()
                    self._last_activity = time.monotonic()
                    self._prune_terminal_tasks_locked()
                    self._emit(task, "failed", detail=detail)
                return

            with self._lock:
                current = self._tasks.get(task.id)
                if current is not task:
                    return
                was_timeout = task.status == "timed_out_running"
                was_cancel_requested = bool(
                    isinstance(task.error, dict)
                    and task.error.get("user_cancel_requested")
                )
                if isinstance(result, AgentRunResult):
                    result.metadata = {
                        **result.metadata,
                        "completed_after_detach": True,
                        "cancel_requested": was_cancel_requested,
                    }
                task.result = result
                task.completed_after_timeout = was_timeout
                task.status = "completed"
                task.error = None
                task.finished_at = datetime.now(timezone.utc).isoformat()
                self._last_activity = time.monotonic()
                self._prune_terminal_tasks_locked()
                self._emit(
                    task,
                    "completed_after_detach",
                    detail={
                        "completed_after_timeout": was_timeout,
                        "cancel_requested": was_cancel_requested,
                        "process_terminated": True,
                    },
                )

        try:
            add_done_callback(reconcile)
        except (RuntimeError, TypeError, ValueError):
            # A future that is already shutting down may reject callback
            # registration; the original timeout state remains truthful.
            return

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
        if self.event_callback is None:
            return
        metadata = {
            "phase": "subagent",
            "agent": task.agent,
            "status": status,
            "task_id": task.id,
            **detail,
        }
        self.event_callback(RunEvent(type="reasoning_delta", metadata=metadata))

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
        block: bool = False,
        enqueue_timeout: float | None = None,
        allow_sync: bool = False,
        config: dict[str, Any] | None = None,
    ) -> str:
        registry = (
            self.runner.refresh_registry()
            if hasattr(self.runner, "refresh_registry")
            else self.runner.registry
        )
        definition = registry.get(agent)
        if not allow_sync and definition.execution != "background_serial":
            raise AgentQueueError(f"子代理 {agent} 未声明 background_serial 执行模式")
        if not isinstance(input_data, dict):
            raise AgentQueueError("子代理输入必须是 JSON 对象")
        if config is not None and not isinstance(config, dict):
            raise AgentQueueError("子代理 config 必须是 JSON 对象")
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
                agent=agent,
                input_data=copy.deepcopy(input_data),
                timeout=timeout,
                survival_seconds=survival_seconds,
                model_override=model_override,
                max_tokens=max_tokens,
                config=copy.deepcopy(config) if config is not None else None,
                serial=definition.execution == "background_serial",
                result_handler=result_handler,
            )
            self._tasks[task.id] = task
            self._last_activity = time.monotonic()
            # 标记正在入队的提交者，使 close 后的工作线程不会在 put 真正
            # 完成前退出；put 本身不能持有 _lock，否则阻塞入队会卡住关闭。
            self._enqueue_inflight += 1
        try:
            self._queue.put(task, block=block, timeout=enqueue_timeout)
        except queue.Full as exc:
            with self._lock:
                self._tasks.pop(task.id, None)
            raise AgentQueueError("子代理调度队列已满") from exc
        except BaseException:
            with self._lock:
                self._tasks.pop(task.id, None)
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
    ) -> str:
        """Queue a trusted framework-owned job with the same status/cancel contract."""

        if not isinstance(input_data, dict):
            raise AgentQueueError("子代理输入必须是 JSON 对象")
        if not callable(job):
            raise AgentQueueError("子代理任务入口不可调用")
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
                timeout=timeout,
                survival_seconds=survival_seconds,
                job=job,
                serial=False,
            )
            self._tasks[task.id] = task
            self._last_activity = time.monotonic()
            self._enqueue_inflight += 1
        try:
            self._queue.put(task, block=block, timeout=enqueue_timeout)
        except queue.Full as exc:
            with self._lock:
                self._tasks.pop(task.id, None)
            raise AgentQueueError("子代理调度队列已满") from exc
        except BaseException:
            with self._lock:
                self._tasks.pop(task.id, None)
            raise
        finally:
            with self._lock:
                self._enqueue_inflight = max(0, self._enqueue_inflight - 1)
        with self._lock:
            emit_queued = task.status == "queued"
        if emit_queued:
            self._emit(task, "queued")
        return task.id

    def get(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise AgentTaskNotFoundError(f"未知子代理任务：{task_id}")
            self._last_activity = time.monotonic()
            return task.snapshot()

    def wait(self, task_id: str, timeout: float | None = None) -> Any:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise AgentTaskNotFoundError(f"未知子代理任务：{task_id}")
        if not task.done_event.wait(timeout):
            raise AgentTaskWaitTimeout(f"等待子代理任务超时：{task_id}")
        with self._lock:
            if task.status == "completed" and task.result is not None:
                return task.result
            detail = task.error or {"message": f"任务状态：{task.status}"}
            if task.status == "cancelled":
                raise AgentCancelledError(str(detail.get("message") or "任务已取消"))
            raise AgentQueueError(str(detail.get("message") or "子代理任务失败"))

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise AgentTaskNotFoundError(f"未知子代理任务：{task_id}")
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
        if wait:
            # join 不能持有 _lock；工作线程完成当前任务和退出检查都需要该锁。
            if worker is not None and worker is not threading.current_thread():
                worker.join()

    def _work(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                with self._lock:
                    if self._closed and self._enqueue_inflight == 0:
                        if self._worker is threading.current_thread():
                            self._worker = None
                        return
                    if (
                        not self._closed
                        and self._queue.empty()
                        and not any(
                            task.status in {"queued", "running"}
                            for task in self._tasks.values()
                        )
                        and time.monotonic() - self._last_activity >= _WORKER_IDLE_SECONDS
                    ):
                        if self._worker is threading.current_thread():
                            self._worker = None
                        return
                continue
            try:
                task = item
                with self._lock:
                    if task.status == "cancelled":
                        continue
                    task.status = "running"
                    task.started_at = datetime.now(timezone.utc).isoformat()
                    self._last_activity = time.monotonic()
                try:
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
                                event_callback=self.event_callback,
                                task_id=task.id,
                                max_tokens=task.max_tokens,
                            )
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
                    if isinstance(exc, (KeyboardInterrupt, GeneratorExit)):
                        raise
                    detached_future = getattr(exc, "completion_future", None)
                    with self._lock:
                        previous_detail = (
                            task.error if isinstance(task.error, dict) else {}
                        )
                        if isinstance(exc, AgentTimeoutError):
                            task.status = (
                                "timed_out"
                                if exc.process_terminated
                                else "timed_out_running"
                            )
                        elif isinstance(exc, AgentCancelledError) or task.cancel_event.is_set():
                            task.status = "cancelled"
                        else:
                            task.status = "failed"
                        task.error = _agent_task_error_detail(exc)
                        if previous_detail.get("user_cancel_requested"):
                            task.error["user_cancel_requested"] = True
                        if isinstance(exc, AgentTimeoutError):
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
                        self._watch_detached_completion(task, detached_future)
                finally:
                    task.done_event.set()
            finally:
                self._queue.task_done()
