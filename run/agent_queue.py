"""用于具有写入副作用的子代理的单工作后台调度程序。"""

from __future__ import annotations

import copy
import math
import queue
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from events import RunEvent
from run.agent_runner import (
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
    result_handler: Callable[[AgentRunResult], None] | None = field(default=None, repr=False)
    result: AgentRunResult | None = None
    error: dict[str, Any] | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    done_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def snapshot(self) -> dict[str, Any]:
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
            "result": (
                {
                    "agent": self.result.agent,
                    "data": copy.deepcopy(self.result.data),
                    "usage": copy.deepcopy(self.result.usage),
                    "model": self.result.model,
                    "metadata": copy.deepcopy(self.result.metadata),
                }
                if self.result is not None
                else None
            ),
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
        self._worker = threading.Thread(target=self._work, name=thread_name, daemon=True)
        self._worker.start()

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
    ) -> str:
        registry = (
            self.runner.refresh_registry()
            if hasattr(self.runner, "refresh_registry")
            else self.runner.registry
        )
        definition = registry.get(agent)
        if definition.execution != "background_serial":
            raise AgentQueueError(f"子代理 {agent} 未声明 background_serial 执行模式")
        if not isinstance(input_data, dict):
            raise AgentQueueError("子代理输入必须是 JSON 对象")
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
            task = AgentTask(
                id=f"agent-task-{uuid.uuid4().hex}",
                agent=agent,
                input_data=copy.deepcopy(input_data),
                timeout=timeout,
                survival_seconds=survival_seconds,
                model_override=model_override,
                max_tokens=max_tokens,
                result_handler=result_handler,
            )
            self._tasks[task.id] = task
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

    def get(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise AgentTaskNotFoundError(f"未知子代理任务：{task_id}")
            return task.snapshot()

    def wait(self, task_id: str, timeout: float | None = None) -> AgentRunResult:
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
            if task.status in _TERMINAL:
                return False
            task.cancel_event.set()
            if task.status == "queued":
                task.status = "cancelled"
                task.finished_at = datetime.now(timezone.utc).isoformat()
                task.error = {"message": "任务在排队时取消", "exception_type": "AgentCancelledError"}
                task.done_event.set()
                self._emit(task, "cancelled")
            return True

    def close(self, *, wait: bool = True, cancel_pending: bool = False) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                if cancel_pending:
                    for task in self._tasks.values():
                        if task.status in {"queued", "running"}:
                            task.cancel_event.set()
                            if task.status == "queued":
                                task.status = "cancelled"
                                task.finished_at = datetime.now(timezone.utc).isoformat()
                                task.error = {
                                    "message": "调度器关闭时取消",
                                    "exception_type": "AgentCancelledError",
                                }
                                task.done_event.set()
                                self._emit(task, "cancelled")
        if wait:
            # join 不能持有 _lock；工作线程完成当前任务和退出检查都需要该锁。
            self._worker.join()

    def _work(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                with self._lock:
                    if self._closed and self._enqueue_inflight == 0:
                        return
                continue
            try:
                task = item
                with self._lock:
                    if task.status == "cancelled":
                        continue
                    task.status = "running"
                    task.started_at = datetime.now(timezone.utc).isoformat()
                try:
                    # 每个用户拥有独立 AgentScheduler；仅在本实例内串行写入。
                    with self._serial_lock:
                        if task.cancel_event.is_set():
                            raise AgentCancelledError("子代理任务已取消")
                        result = self.runner.run(
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
                            result.metadata.get("completed_after_timeout")
                        )
                        task.status = "completed"
                        task.finished_at = datetime.now(timezone.utc).isoformat()
                except BaseException as exc:
                    if isinstance(exc, (KeyboardInterrupt, GeneratorExit)):
                        raise
                    with self._lock:
                        if isinstance(exc, AgentCancelledError):
                            task.status = "cancelled"
                        elif isinstance(exc, AgentTimeoutError):
                            task.status = (
                                "timed_out"
                                if exc.process_terminated
                                else "timed_out_running"
                            )
                        else:
                            task.status = "failed"
                        task.error = {
                            "message": str(exc),
                            "exception_type": type(exc).__name__,
                        }
                        if isinstance(exc, AgentTimeoutError):
                            task.error.update(
                                {
                                    "cancel_requested": True,
                                    "process_terminated": exc.process_terminated,
                                }
                            )
                        task.finished_at = datetime.now(timezone.utc).isoformat()
                finally:
                    task.done_event.set()
            finally:
                self._queue.task_done()
