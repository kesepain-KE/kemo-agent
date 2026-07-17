"""用于具有写入副作用的子代理的单工作后台调度程序。"""

from __future__ import annotations

import copy
import queue
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from events import RunEvent
from run.agent_runner import AgentCancelledError, AgentRunResult, AgentRunner


TaskStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
_TERMINAL = {"completed", "failed", "cancelled"}
_SENTINEL = object()
_BACKGROUND_SERIAL_LOCK = threading.Lock()


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
            maxsize=int(runtime.get("queue_maxsize", 0)),
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
        model_override: str | None = None,
        max_tokens: int | None = None,
        result_handler: Callable[[AgentRunResult], None] | None = None,
        block: bool = True,
        enqueue_timeout: float | None = None,
    ) -> str:
        definition = self.runner.registry.get(agent)
        if definition.execution != "background_serial":
            raise AgentQueueError(f"子代理 {agent} 未声明 background_serial 执行模式")
        if not isinstance(input_data, dict):
            raise AgentQueueError("子代理输入必须是 JSON 对象")
        with self._lock:
            if self._closed:
                raise AgentQueueClosedError("子代理调度队列已关闭")
            task = AgentTask(
                id=f"agent-task-{uuid.uuid4().hex}",
                agent=agent,
                input_data=copy.deepcopy(input_data),
                timeout=timeout,
                model_override=model_override,
                max_tokens=max_tokens,
                result_handler=result_handler,
            )
            self._tasks[task.id] = task
        try:
            self._queue.put(task, block=block, timeout=enqueue_timeout)
        except queue.Full as exc:
            with self._lock:
                self._tasks.pop(task.id, None)
            raise AgentQueueError("子代理调度队列已满") from exc
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
            if self._closed:
                if wait:
                    self._worker.join()
                return
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
        self._queue.put(_SENTINEL)
        if wait:
            self._worker.join()

    def _work(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _SENTINEL:
                    return
                task = item
                assert isinstance(task, AgentTask)
                with self._lock:
                    if task.status == "cancelled":
                        continue
                    task.status = "running"
                    task.started_at = datetime.now(timezone.utc).isoformat()
                try:
                                        # 所有后台写入代理共享一个进程范围的通道，
                                        # 即使调度程序对每个用户仍然是隔离的。
                    with _BACKGROUND_SERIAL_LOCK:
                        if task.cancel_event.is_set():
                            raise AgentCancelledError("子代理任务已取消")
                        result = self.runner.run(
                            task.agent,
                            task.input_data,
                            cancel_event=task.cancel_event,
                            timeout=task.timeout,
                            model_override=task.model_override,
                            event_callback=self.event_callback,
                            task_id=task.id,
                            max_tokens=task.max_tokens,
                        )
                        if task.result_handler is not None:
                            task.result_handler(result)
                    with self._lock:
                        task.result = result
                        task.status = "completed"
                        task.finished_at = datetime.now(timezone.utc).isoformat()
                except BaseException as exc:
                    if isinstance(exc, (KeyboardInterrupt, GeneratorExit)):
                        raise
                    with self._lock:
                        task.status = (
                            "cancelled" if isinstance(exc, AgentCancelledError) else "failed"
                        )
                        task.error = {
                            "message": str(exc),
                            "exception_type": type(exc).__name__,
                        }
                        task.finished_at = datetime.now(timezone.utc).isoformat()
                finally:
                    task.done_event.set()
            finally:
                self._queue.task_done()
