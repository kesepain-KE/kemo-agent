"""Bound cooperative background executions that outlive their caller timeout.

Python cannot safely terminate an already-running thread.  This registry makes
that limitation explicit: completed calls clean themselves up and calls that miss
their cancellation grace remain registered. Independent work can use process
isolation when its own role/tool bucket is exhausted or contains an aged deadlock.
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any


MAX_ABANDONED_EXECUTIONS = 8
DEADLOCK_SECONDS = 5 * 60


class ExecutionCapacityError(RuntimeError):
    pass


class ExecutionIsolationRequired(ExecutionCapacityError):
    """Only a new, independently protected call may use process isolation."""


@dataclass(slots=True)
class _Execution:
    label: str
    state: str
    created_at: float
    bucket: str = ""
    abandoned_at: float | None = None
    future: Future[Any] | None = None
    executor: ThreadPoolExecutor | None = None


_EXECUTIONS: dict[str, _Execution] = {}
_EXECUTIONS_LOCK = threading.Lock()


def _remove_execution(execution_id: str) -> None:
    executor: ThreadPoolExecutor | None = None
    with _EXECUTIONS_LOCK:
        entry = _EXECUTIONS.pop(execution_id, None)
        if entry is not None:
            executor = entry.executor
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)


def _prune_completed_locked() -> list[ThreadPoolExecutor]:
    completed: list[ThreadPoolExecutor] = []
    for execution_id, entry in list(_EXECUTIONS.items()):
        if entry.future is not None and entry.future.done():
            _EXECUTIONS.pop(execution_id, None)
            if entry.executor is not None:
                completed.append(entry.executor)
    return completed


def reserve_execution(label: str, *, bucket: str | None = None) -> str:
    completed: list[ThreadPoolExecutor]
    with _EXECUTIONS_LOCK:
        completed = _prune_completed_locked()
        if any(
            entry.state == "abandoned" and entry.label == str(label or "execution")
            for entry in _EXECUTIONS.values()
        ):
            raise ExecutionCapacityError(
                "同一执行仍在超时后的后台退出过程中，已拒绝重复启动"
            )
        abandoned = [
            entry for entry in _EXECUTIONS.values()
            if entry.state == "abandoned" and entry.bucket == (bucket or label)
        ]
        if len(abandoned) >= MAX_ABANDONED_EXECUTIONS or any(
            entry.abandoned_at is not None
            and time.monotonic() - entry.abandoned_at >= DEADLOCK_SECONDS
            for entry in abandoned
        ):
            raise ExecutionIsolationRequired(
                "本角色工具存在未退出的超时执行；新独立调用须使用进程隔离"
            )
        execution_id = uuid.uuid4().hex
        _EXECUTIONS[execution_id] = _Execution(
            label=str(label or "execution"),
            state="reserved",
            created_at=time.monotonic(),
            bucket=bucket or label,
        )
    for executor in completed:
        executor.shutdown(wait=False, cancel_futures=True)
    return execution_id


def attach_execution(
    execution_id: str,
    future: Future[Any],
    executor: ThreadPoolExecutor,
) -> None:
    with _EXECUTIONS_LOCK:
        entry = _EXECUTIONS.get(execution_id)
        if entry is None:
            executor.shutdown(wait=False, cancel_futures=True)
            raise RuntimeError("执行登记已失效")
        entry.state = "running"
        entry.future = future
        entry.executor = executor
    future.add_done_callback(lambda _future: _remove_execution(execution_id))


def abandon_execution(execution_id: str) -> bool:
    """Mark a live execution as abandoned and return whether it is still running."""

    with _EXECUTIONS_LOCK:
        entry = _EXECUTIONS.get(execution_id)
        if entry is None or entry.future is None or entry.future.done():
            return False
        entry.state = "abandoned"
        if entry.abandoned_at is None:
            entry.abandoned_at = time.monotonic()
        return True


def release_execution(execution_id: str) -> None:
    _remove_execution(execution_id)


def execution_watchdog_snapshot() -> dict[str, int]:
    completed: list[ThreadPoolExecutor]
    with _EXECUTIONS_LOCK:
        completed = _prune_completed_locked()
        snapshot = {
            "running": sum(entry.state == "running" for entry in _EXECUTIONS.values()),
            "abandoned": sum(entry.state == "abandoned" for entry in _EXECUTIONS.values()),
            "reserved": sum(entry.state == "reserved" for entry in _EXECUTIONS.values()),
        }
    for executor in completed:
        executor.shutdown(wait=False, cancel_futures=True)
    return snapshot
