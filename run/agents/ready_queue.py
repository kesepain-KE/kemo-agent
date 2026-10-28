"""Bounded space-fair backlog; FIFO within each runnable conversation space."""
from __future__ import annotations

import queue
import time
from typing import Callable, Mapping

from provider.fairness import SpaceTurns
from run.agents.queue_models import AgentTask


class ReadyTaskQueue(queue.Queue[AgentTask]):
    def __init__(self, maxsize: int = 0) -> None:
        super().__init__(maxsize=maxsize)
        self.per_space_maxsize = max(1, (maxsize + 1) // 2) if maxsize > 0 else 0
        self.new_space_reserve = min(5, maxsize // 5) if maxsize > 0 else 0
        self._turns: SpaceTurns[tuple[str, str]] = SpaceTurns()

    def put(self, item: AgentTask, block: bool = True, timeout: float | None = None) -> None:
        """Bound total backlog and keep one scoped producer from filling it."""
        if timeout is not None and timeout < 0:
            raise ValueError("'timeout' must be a non-negative number")
        deadline = None if timeout is None else time.monotonic() + timeout
        with self.not_full:
            while True:
                scoped = bool(item.source and item.session_id)
                space_pending = sum(
                    task.scope == item.scope and task.status == "queued" for task in self.queue
                )
                scope_full = bool(
                    self.per_space_maxsize and scoped and space_pending >= self.per_space_maxsize
                )
                headroom_full = bool(
                    scoped and space_pending and self.new_space_reserve
                    and self._qsize() >= self.maxsize - self.new_space_reserve
                )
                if not scope_full and not headroom_full and (self.maxsize <= 0 or self._qsize() < self.maxsize):
                    break
                reason = (
                    "子代理调度队列已满（当前对话空间排队额度已满，请等待已有任务执行）"
                    if scope_full else
                    "子代理调度队列已满（剩余排队名额为其他对话空间预留，请等待已有任务执行）"
                    if headroom_full else "子代理调度队列已满"
                )
                if not block:
                    raise queue.Full(reason)
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise queue.Full(reason)
                self.not_full.wait(remaining)
            self._put(item)
            self.unfinished_tasks += 1
            self.not_empty.notify()

    def discard(self, task: AgentTask) -> None:
        """Undo a nonblocking enqueue if its durable transaction cannot commit."""
        with self.not_full:
            for index, candidate in enumerate(self.queue):
                if candidate is task:
                    del self.queue[index]
                    self.unfinished_tasks -= 1
                    if not self.unfinished_tasks:
                        self.all_tasks_done.notify_all()
                    self.not_full.notify_all()
                    return

    def take_ready(
        self, ready: Callable[[AgentTask], bool],
        active: Mapping[tuple[str, str], int] | None = None,
    ) -> AgentTask | None:
        """Take a fair space's oldest runnable task, skipping blocked lanes.

        The dispatcher holds its state lock while selecting and reserving work.
        Producers never hold that lock while waiting for queue capacity.
        """
        with self.not_full:
            candidates = [task for task in self.queue if ready(task)]
            cancelled = next((task for task in candidates if task.status == "cancelled"), None)
            if not candidates:
                return None
            chosen = None if cancelled is not None else self._turns.choose(
                (task.scope for task in candidates), active or {},
            )
            task = cancelled or next((task for task in candidates if task.scope == chosen), None)
            if task is not None:
                self.queue.remove(task)
                if task.status != "cancelled":
                    self._turns.served(task.scope)
                # Global capacity and space capacity may unblock different producers.
                self.not_full.notify_all()
                return task
        return None
