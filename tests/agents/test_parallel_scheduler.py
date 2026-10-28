"""Deterministic parallel dispatch, one real Provider gate, and durable ordering."""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from types import SimpleNamespace

import pytest

import provider.factory as factory
from provider.schema import ChatResponse
from run.agents import AgentQueueError, AgentRunner, AgentScheduler, AgentTimeoutError
from run.agents.task_ledger import TaskCoordinator, TaskLedger
from tests.agents.test_general_worker import OUTPUT, workspace  # noqa: F401
from tests.agents.test_subagent_hotplug import ScriptedProvider


def eventually(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.005)
    assert predicate()


@pytest.fixture(autouse=True)
def isolated_gate(monkeypatch):
    monkeypatch.setattr(factory, "_provider_semaphore", None)
    monkeypatch.setattr(factory, "_provider_semaphore_limit", 0)


def scheduler_for(job, limit=2, maxsize=50):
    runner = SimpleNamespace(
        config={"provider_runtime": {"max_concurrent_requests": limit}},
        registry=SimpleNamespace(get=lambda name: SimpleNamespace(
            execution="background_serial" if name == "maintenance" else "sync")),
        run=lambda name, data, **kw: job(name, data, kw["cancel_event"]),
    )
    return AgentScheduler(runner, maxsize=maxsize)


def test_normal_tasks_really_overlap_and_excess_stays_queued():
    release = threading.Event()
    started = []
    scheduler = scheduler_for(lambda name, data, cancel: started.append(data["n"]) or release.wait(5))
    try:
        ids = [scheduler.submit("worker", {"n": i}, allow_sync=True) for i in range(3)]
        eventually(lambda: len(started) == 2)
        assert scheduler.get(ids[2])["status"] == "queued"
        # Tool/non-model work does not reserve an API request.
        assert factory.provider_semaphore_status()["active_requests"] == 0
        release.set()
        for task_id in ids:
            scheduler.wait(task_id, 3)
        assert sorted(started) == [0, 1, 2]
    finally:
        release.set()
        scheduler.close(wait=True, cancel_pending=True)


def test_waiting_maintenance_does_not_block_independent_business_tasks():
    release = threading.Event()
    started = []
    def work(name, data, cancel):
        started.append(data["n"])
        if data["n"] == 1:
            release.wait(5)
        return data
    scheduler = scheduler_for(work, limit=3)
    try:
        first = scheduler.submit("maintenance", {"n": 1})
        eventually(lambda: started == [1])
        second = scheduler.submit("maintenance", {"n": 2})
        normal = scheduler.submit("worker", {"n": 3}, allow_sync=True)
        assert scheduler.wait(normal, 2) == {"n": 3}
        assert scheduler.get(second)["status"] == "queued"
        assert started == [1, 3]
        release.set()
        scheduler.wait(first, 2)
        scheduler.wait(second, 2)
        assert started == [1, 3, 2]
    finally:
        release.set()
        scheduler.close(wait=True, cancel_pending=True)


def test_bounded_queue_and_cancelled_pending_job_never_executes():
    release = threading.Event()
    started = []
    scheduler = scheduler_for(lambda name, data, cancel: started.append(data["n"]) or release.wait(5),
                              limit=1, maxsize=1)
    try:
        first = scheduler.submit("worker", {"n": 1}, allow_sync=True)
        eventually(lambda: started == [1])
        second = scheduler.submit("worker", {"n": 2}, allow_sync=True)
        with pytest.raises(AgentQueueError, match="队列已满"):
            scheduler.submit("worker", {"n": 3}, allow_sync=True)
        scheduler.cancel(second)
        eventually(lambda: scheduler._queue.empty())
        release.set()
        scheduler.wait(first, 2)
        assert started == [1]
    finally:
        release.set()
        scheduler.close(wait=True, cancel_pending=True)


def test_detached_executor_keeps_parallel_capacity_until_actual_exit():
    completion = Future()
    started = []
    def work(name, data, cancel):
        started.append(data["n"])
        if data["n"] == 1:
            raise AgentTimeoutError("still executing", process_terminated=False,
                                    completion_future=completion)
        return data
    scheduler = scheduler_for(work, limit=1)
    try:
        first = scheduler.submit("worker", {"n": 1}, allow_sync=True)
        eventually(lambda: scheduler.get(first)["status"] == "timed_out_running")
        second = scheduler.submit("worker", {"n": 2}, allow_sync=True)
        time.sleep(.15)
        assert started == [1] and scheduler.get(second)["status"] == "queued"
        scheduler.cancel(first)
        time.sleep(.1)
        assert started == [1]  # Cancellation alone is not a capacity release.
        completion.set_result({"late": True})
        assert scheduler.wait(second, 2) == {"n": 2}
    finally:
        if not completion.done():
            completion.set_result({})
        scheduler.close(wait=True, cancel_pending=True)


def test_saved_api_limit_is_reused_without_a_separate_subagent_setting():
    release = threading.Event()
    started = []
    scheduler = scheduler_for(lambda name, data, cancel: started.append(data["n"]) or release.wait(5),
                              limit=1)
    try:
        scheduler.submit("worker", {"n": 1}, allow_sync=True)
        eventually(lambda: len(started) == 1)
        scheduler.submit("worker", {"n": 2}, allow_sync=True)
        scheduler.configure_concurrency({"provider_runtime": {"max_concurrent_requests": 2}})
        eventually(lambda: len(started) == 2)
        assert factory.provider_semaphore_status()["max_requests"] == 2
    finally:
        release.set()
        scheduler.close(wait=True, cancel_pending=True)


def test_idle_scheduler_does_not_keep_reconfiguring_the_shared_api_pool():
    scheduler = scheduler_for(lambda *args: {"ok": True}, limit=2)
    try:
        scheduler.wait(scheduler.submit("worker", {}, allow_sync=True), 2)
        factory.get_provider_semaphore(7)
        time.sleep(.25)
        assert factory.provider_semaphore_status()["max_requests"] == 7
    finally:
        scheduler.close(wait=True, cancel_pending=True)


@pytest.mark.parametrize("limit", [1, 2])
def test_real_workers_two_users_and_parent_share_one_provider_pool(workspace, limit):
    root, config = workspace
    config["provider_runtime"] = {"max_concurrent_requests": limit}
    release = threading.Event()
    entered = []
    lock = threading.Lock()
    class BlockingProvider(ScriptedProvider):
        def create(self, request):
            with lock:
                entered.append(factory.provider_semaphore_status()["active_requests"])
            assert release.wait(5)
            return super().create(request)
    schedulers = [
        AgentScheduler(AgentRunner(root, user, config=config,
            provider_factory=lambda _: BlockingProvider([ChatResponse(text=json.dumps(OUTPUT), model="mock")])))
        for user in ("alice", "bob")
    ]
    tasks = []
    try:
        # Parent takes one shared slot, including the limit=1 deadlock boundary.
        with factory.provider_request_slot(config):
            for scheduler in schedulers:
                tasks.extend((scheduler, scheduler.submit("general_worker", {"task": "check"}, allow_sync=True))
                             for _ in range(2))
            eventually(lambda: len(entered) == limit - 1
                       and factory.provider_semaphore_status()["waiting_estimate"] == limit + 1)
            assert factory.provider_semaphore_status()["active_requests"] == limit
        eventually(lambda: len(entered) == limit)
        assert max(entered) == limit
        release.set()
        for scheduler, task_id in tasks:
            assert scheduler.wait(task_id, 5).data == OUTPUT
        assert len(entered) == 4 and max(entered) <= limit
        assert factory.provider_semaphore_status()["active_requests"] == 0
        assert factory.provider_semaphore_status()["waiting_estimate"] == 0
    finally:
        release.set()
        for scheduler in schedulers:
            scheduler.close(wait=True, cancel_pending=True)


def test_parent_cancel_waits_for_all_parallel_children_to_really_exit(workspace, monkeypatch):
    root, _ = workspace
    release = threading.Event()
    started, cancellations = [], []
    scheduler = scheduler_for(lambda *args: None)
    scheduler.runner.root, scheduler.runner.user = root, "alice"
    monkeypatch.setattr("run.agents.get_agent_scheduler", lambda *args, **kw: scheduler)
    coordinator = TaskCoordinator(root, "alice", "web", "scope", "run", {})
    def job(index, cancel):
        started.append(index)
        assert cancel.wait(5)
        cancellations.append(index)
        assert release.wait(5)
        return {"answer": index}
    for index in range(2):
        scheduler.submit_callable("worker", {}, lambda cancel, i=index: job(i, cancel),
            source="web", session_id="scope", ownership=coordinator.ownership(f"call-{index}"))
    errors = []
    def finish():
        try:
            coordinator.finish("cancelled")
        except BaseException as exc:
            errors.append(exc)
    parent = threading.Thread(target=finish)
    try:
        eventually(lambda: len(started) == 2)
        parent.start()
        eventually(lambda: len(cancellations) == 2)
        assert parent.is_alive() and len(coordinator.pending()) == 2
        assert coordinator.ledger.get(coordinator.id, "web", "scope")["status"] == "cancelling"
        release.set()
        parent.join(3)
        assert not parent.is_alive() and not errors and not coordinator.pending()
        assert coordinator.ledger.get(coordinator.id, "web", "scope")["status"] == "cancelled"
    finally:
        release.set()
        scheduler.close(wait=True, cancel_pending=True)
        if parent.ident is not None:
            parent.join(3)


def test_durable_order_same_timestamps_reverse_completion_and_batches(tmp_path, monkeypatch):
    (tmp_path / "users/alice").mkdir(parents=True)
    monkeypatch.setattr("run.agents.task_ledger.now", lambda: "2026-10-25T00:00:00Z")
    coordinator = TaskCoordinator(tmp_path, "alice", "web", "scope", "run", {})
    from run.agents.queue_models import AgentTask
    ids = ["z", "a", "y", "b", "x", "c"]
    for task_id in ids:
        task = AgentTask(task_id, "worker", {}, source="web", session_id="scope",
                         created_at="2026-10-25T00:00:00Z")
        coordinator.ledger.register(task, coordinator.ownership(task_id))
    for task_id in reversed(ids):
        coordinator.ledger.update(task_id, status="completed", execution_live=False,
                                  finished_at="2026-10-25T00:00:00Z", result={"answer": task_id})
    assert [r["id"] for r in coordinator.children()] == ids
    assert [r["submission_order"] for r in coordinator.children()] == [1, 2, 3, 4, 5, 6]
    for expected in (ids[:4], ids[4:]):
        notice = coordinator.notifications()
        assert notice is not None
        payload = json.loads(notice["content"].split("\n", 1)[1])
        assert [r["task_id"] for r in payload] == expected
        assert [r["submission_order"] for r in payload] == [ids.index(i) + 1 for i in expected]
    assert coordinator.notifications() is None
    coordinator.acknowledge()
    coordinator.finish("completed")
    assert [r["id"] for r in TaskLedger(tmp_path, "alice").records(parent=coordinator.id)[0]] == ids


def test_ready_results_do_not_wait_for_an_earlier_slow_task(tmp_path):
    (tmp_path / "users/alice").mkdir(parents=True)
    coordinator = TaskCoordinator(tmp_path, "alice", "web", "scope", "run", {})
    from run.agents.queue_models import AgentTask
    for task_id in ("slow", "fast"):
        coordinator.ledger.register(AgentTask(task_id, "worker", {}, source="web", session_id="scope"),
                                    coordinator.ownership(task_id))
    coordinator.ledger.update("fast", status="completed", execution_live=False, result={"answer": "ready"})
    notification = coordinator.notifications()
    assert notification is not None
    assert '"submission_order": 2' in notification["content"]
    coordinator.acknowledge()
    with pytest.raises(RuntimeError, match="尚未"):
        coordinator.finish("completed")


def test_concurrent_registration_allocates_unique_durable_ordinals(tmp_path):
    (tmp_path / "users/alice").mkdir(parents=True)
    coordinator = TaskCoordinator(tmp_path, "alice", "web", "scope", "run", {})
    owner = coordinator.ownership("batch")
    from run.agents.queue_models import AgentTask
    def register(index):
        task = AgentTask(f"child-{index}", "worker", {}, source="web", session_id="scope")
        coordinator.ledger.register(task, {**owner, "tool_call_id": f"call-{index}"})
        return task.submission_order
    with ThreadPoolExecutor(max_workers=4) as executor:
        allocated = list(executor.map(register, range(12)))
    assert sorted(allocated) == list(range(1, 13))
    assert [r["submission_order"] for r in coordinator.children()] == list(range(1, 13))
    # A new attempt continues numbering; it does not reuse ordinals.
    retry = TaskCoordinator(tmp_path, "alice", "web", "scope", "retry",
                            {"_agent_root_task_id": coordinator.id})
    last = AgentTask("last", "worker", {}, source="web", session_id="scope")
    retry.ledger.register(last, retry.ownership("last-call"))
    assert last.submission_order == 13


def test_legacy_child_order_is_stable_without_schema_migration(tmp_path):
    (tmp_path / "users/alice").mkdir(parents=True)
    ledger = TaskLedger(tmp_path, "alice")
    with ledger.db(write=True) as db:
        for task_id in ("z", "a"):
            ledger.put(db, {"id": task_id, "root_task_id": "old-root", "source": "web",
                "session_id": "scope", "status": "completed", "updated_at": "same"})
    ledger.update("z", updated_at="changed")
    assert [r["id"] for r in ledger.records(parent="old-root")[0]] == ["z", "a"]


def test_same_database_lifecycle_is_locked_without_serializing_other_users(tmp_path):
    for user in ("alice", "bob"):
        (tmp_path / "users" / user).mkdir(parents=True)
    ledger = TaskLedger(tmp_path, "alice")
    second = TaskLedger(tmp_path, "alice")
    attempted, finished = threading.Event(), threading.Event()
    def read():
        attempted.set()
        second.records()
        finished.set()
    with ledger.db(write=True):
        thread = threading.Thread(target=read)
        thread.start()
        assert attempted.wait(1)
        assert not finished.wait(.1)
        # A different user's database remains independently accessible.
        assert TaskLedger(tmp_path, "bob").records()[1] == 0
    thread.join(2)
    assert finished.is_set() and not thread.is_alive()


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm"])
def test_parallel_storage_does_not_relax_hardlink_guards(tmp_path, suffix):
    (tmp_path / "users/alice/runtime").mkdir(parents=True)
    ledger = TaskLedger(tmp_path, "alice")
    target = tmp_path / "target"
    target.write_bytes(b"do-not-open")
    os.link(target, str(ledger.path) + suffix)
    with pytest.raises(ValueError, match="不能是链接"):
        with ledger.db():
            pass
