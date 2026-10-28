"""Conversation-space admission and dispatch, without real providers or users."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
import queue

import pytest

from run.agents import AgentQueueError
from run.agents.queue_models import AgentTask
from run.agents.ready_queue import ReadyTaskQueue
from tests.agents.test_parallel_scheduler import (
    eventually, isolated_gate, scheduler_for,  # noqa: F401
)


def test_late_space_gets_next_freed_slot_before_old_space_backlog():
    names = ("a1", "a2", "a3", "a4", "b1")
    release = {name: threading.Event() for name in names}
    started = []

    def work(agent, data, cancel):
        started.append(data["name"])
        assert release[data["name"]].wait(8)
        return data

    scheduler = scheduler_for(work, limit=2)
    try:
        for name in names[:2]:
            scheduler.submit("worker", {"name": name}, allow_sync=True, source="web", session_id="a")
        eventually(lambda: len(started) == 2)
        for name in names[2:]:
            scheduler.submit("worker", {"name": name}, allow_sync=True,
                             source="web", session_id="b" if name == "b1" else "a")
        release["a1"].set()
        eventually(lambda: len(started) == 3)
        assert started[-1] == "b1"
    finally:
        for event in release.values():
            event.set()
        scheduler.close(wait=True, cancel_pending=True)


def test_space_admission_is_atomic_for_concurrent_producers():
    pending = ReadyTaskQueue(maxsize=6)

    def submit(index):
        try:
            pending.put(AgentTask(f"a{index}", "worker", {}, source="web", session_id="a"), block=False)
            return True
        except queue.Full:
            return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        accepted = list(executor.map(submit, range(30)))
    assert sum(accepted) == 3
    assert pending.qsize() == 3
    for index in range(2):
        pending.put(AgentTask(f"b{index}", "worker", {}, source="web", session_id="b"), block=False)
    with pytest.raises(queue.Full, match="预留"):
        pending.put(AgentTask("b3", "worker", {}, source="web", session_id="b"), block=False)
    pending.put(AgentTask("c1", "worker", {}, source="web", session_id="c"), block=False)
    assert pending.qsize() == 6


def test_several_existing_spaces_cannot_consume_new_space_headroom():
    pending = ReadyTaskQueue(maxsize=50)
    assert pending.per_space_maxsize == 25
    assert pending.new_space_reserve == 5
    for space, count in (("a", 25), ("b", 20)):
        for index in range(count):
            pending.put(AgentTask(f"{space}{index}", "worker", {}, source="web", session_id=space), block=False)
    with pytest.raises(queue.Full, match="预留"):
        pending.put(AgentTask("b21", "worker", {}, source="web", session_id="b"), block=False)
    for space in ("c", "d", "e", "f", "g"):
        pending.put(AgentTask(space, "worker", {}, source="web", session_id=space), block=False)
    assert pending.qsize() == 50
    with pytest.raises(queue.Full, match="队列已满"):
        pending.put(AgentTask("h", "worker", {}, source="web", session_id="h"), block=False)


def test_blocking_space_producer_wakes_only_when_its_own_capacity_is_free():
    pending = ReadyTaskQueue(maxsize=4)
    first = AgentTask("a1", "worker", {}, source="web", session_id="a")
    for task in (first, AgentTask("a2", "worker", {}, source="web", session_id="a")):
        pending.put(task)
    completed = threading.Event()
    errors = []

    def blocked():
        try:
            pending.put(AgentTask("a3", "worker", {}, source="web", session_id="a"), timeout=2)
            completed.set()
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=blocked)
    worker.start()
    try:
        assert not completed.wait(.05)
        # Discard is also used by transactional resume rollback.
        pending.discard(first)
        assert completed.wait(2)
    finally:
        worker.join(3)
    assert not errors and not worker.is_alive()
    assert pending.qsize() == 2
    while not pending.empty():
        pending.get_nowait()
        pending.task_done()
    assert pending.unfinished_tasks == 0


def test_space_admission_timeout_and_cancelled_pending_entry_do_not_leak_capacity():
    pending = ReadyTaskQueue(maxsize=2)
    first = AgentTask("a1", "worker", {}, source="web", session_id="a")
    pending.put(first)
    with pytest.raises(queue.Full, match="当前对话空间"):
        pending.put(AgentTask("a2", "worker", {}, source="web", session_id="a"), timeout=.01)
    first.status = "cancelled"
    assert pending.take_ready(lambda task: task.status == "cancelled") is first
    pending.task_done()
    pending.put(AgentTask("a3", "worker", {}, source="web", session_id="a"), block=False)
    assert pending.qsize() == 1


def test_serial_capacity_one_rotates_spaces_and_preserves_each_space_fifo():
    gate = threading.Event()
    started = []

    def work(agent, data, cancel):
        started.append(data["name"])
        if data["name"] == "a0":
            assert gate.wait(8)
        return data

    scheduler = scheduler_for(work, limit=1)
    try:
        scheduler.submit("worker", {"name": "a0"}, allow_sync=True, source="web", session_id="a")
        eventually(lambda: started == ["a0"])
        for name in ("a1", "a2", "b1", "b2", "c1", "c2"):
            scheduler.submit("worker", {"name": name}, allow_sync=True, source="web", session_id=name[0])
        gate.set()
        eventually(lambda: len(started) == 7)
        assert started == ["a0", "b1", "c1", "a1", "b2", "c2", "a2"]
    finally:
        gate.set()
        scheduler.close(wait=True, cancel_pending=True)


def test_one_space_cannot_fill_all_bounded_pending_positions():
    gate = threading.Event()
    started = threading.Event()
    scheduler = scheduler_for(
        lambda *args: started.set() or gate.wait(8), limit=1, maxsize=6,
    )
    try:
        scheduler.submit("worker", {}, allow_sync=True, source="web", session_id="a")
        assert started.wait(3)
        for _ in range(3):
            scheduler.submit("worker", {}, allow_sync=True, source="web", session_id="a")
        with pytest.raises(AgentQueueError, match="当前对话空间"):
            scheduler.submit("other_worker", {}, allow_sync=True, source="web", session_id="a")
        # A different window of a is not a new quota. Different source/session
        # identities are distinct spaces, even with the same session string.
        scheduler.submit("worker", {}, allow_sync=True, source="web", session_id="b")
        scheduler.submit("worker", {}, allow_sync=True, source="app", session_id="a")
    finally:
        gate.set()
        scheduler.close(wait=True, cancel_pending=True)
