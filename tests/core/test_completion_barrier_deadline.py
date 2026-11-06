"""R2 C2: bounded response closure retains real execution responsibility."""
from __future__ import annotations

import json
import threading
import time

import pytest

from provider.schema import ChatResponse, ProviderError
from run.agents import TaskCoordinator, TaskLedger
from run.engine import iter_request_events
from run.cards import CardStore
from run.conversation import session_lock
from tests.core.test_agent_task_lifecycle import JOBS, eventually, root_record, setup  # noqa: F401
from tests.core.test_completion_barrier_exits import dispatch, request
from tests.media_input_support import MediaProvider


def bounded_config(root):
    path = root / "config/global_config.yaml"
    config = json.loads(path.read_text("utf-8"))
    config["agent_runtime"] = {"default_timeout": .1}
    path.write_text(json.dumps(config), "utf-8")


def integration_record(root):
    with CardStore(root, "alice").db() as db:
        row = db.execute("SELECT * FROM card_integrations ORDER BY created_at DESC LIMIT 1").fetchone()
        return dict(row), json.loads(row["record_json"])


@pytest.mark.parametrize("route", ["limited", "failed", "cancelled", "waiting"])
def test_deadline_keeps_partial_body_and_blocks_same_session_until_real_exit(setup, monkeypatch, route):
    root, scheduler = setup
    bounded_config(root)
    if route == "limited":
        from types import SimpleNamespace
        monkeypatch.setattr("run.conversation.main_orchestration.detect_task_plan_creation_boundary",
            lambda **kwargs: SimpleNamespace(stop_reason="task_plan_created", plan_id="test",
                                             awaiting_user_approval=True))
    release, started, cancel = threading.Event(), threading.Event(), threading.Event()
    calls, events, errors = [], [], []

    def uncooperative(_):
        calls.append(1)
        started.set()
        release.wait(10)
        return {"answer": "late child"}

    JOBS["worker"] = uncooperative
    second = ProviderError("small invalid parameter", status_code=400) if route == "failed" else ChatResponse(text="saved draft")
    initial = dispatch()
    initial.text = "saved draft"
    provider = MediaProvider([initial, second])
    if route == "limited":
        provider.responses = [initial, dispatch()]
    original_create = provider.create

    def create(value):
        if len(provider.requests) == 1:
            assert started.wait(2)
            if route == "cancelled":
                cancel.set()
        return original_create(value)

    setattr(provider, "create", create)

    def run():
        try:
            events.extend(iter_request_events(
                request(), root=root, provider_factory=lambda _: provider, cancel_event=cancel,
            ))
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    began = time.monotonic()
    thread.start()
    try:
        assert started.wait(3)
        thread.join(2.8)
        assert not thread.is_alive(), "response waited past its one-second completion deadline"
        assert not errors
        assert time.monotonic() - began < 3.5
        terminal = events[-1]
        assert terminal.type == "done" and terminal.metadata["status"] == "partial"
        assert terminal.metadata["retryable"] is False
        assert terminal.metadata["unfinished_tasks"]
        assert "saved draft" in terminal.metadata["text"]
        record = root_record(root)
        assert record["status"] == "partial" and record["execution_live"] is True
        closed_at = record["finished_at"]
        children, total = TaskLedger(root, "alice").records("web", "scope", parent=record["id"])
        assert total == 1 and children[0]["execution_live"]
        assert children[0]["status"] in {"cancelling", "running", "timed_out_running"}
        child_id = children[0]["id"]
        assert not scheduler._tasks[child_id].done_event.is_set()
        assert calls == [1]
        lock = session_lock(root, "alice", "web", "scope")
        assert lock.acquire(blocking=False) is False
        integration, saved = integration_record(root)
        assert integration["response_closed"] == 1
        assert saved["status"] == "partial"
        assert child_id in {item["id"] for item in saved["unfinished_tasks"]}
        raw = CardStore(root, "alice").raw_page("web", "scope")
        assert any("saved draft" in card.raw_payload_json for card in raw)
        before = len(provider.requests)
        blocked = list(iter_request_events(request(), root=root, provider_factory=lambda _: provider))
        assert len(blocked) == 1 and blocked[0].type == "error"
        blocked_error = blocked[0].error
        assert blocked_error is not None
        assert blocked_error["exception_type"] == "SessionExecutionBusyError"
        assert blocked[0].metadata["retryable"] is False
        assert len(provider.requests) == before
        other = list(iter_request_events(
            {**request(), "session_id": "other"}, root=root, provider_factory=lambda _: MediaProvider(),
        ))
        assert other[-1].type == "done"
    finally:
        release.set()
        thread.join(6)
    assert not thread.is_alive() and not errors
    eventually(lambda: not TaskLedger(root, "alice").get(record["id"], "web", "scope").get("execution_live"))
    assert TaskLedger(root, "alice").get(record["id"], "web", "scope")["finished_at"] == closed_at
    assert len(provider.requests) == before and calls == [1]
    following = list(iter_request_events(request(), root=root, provider_factory=lambda _: MediaProvider()))
    assert following[-1].type == "done"


def test_repeated_finish_uses_one_deadline_and_does_not_fake_child_exit(setup):
    root, scheduler = setup
    bounded_config(root)
    coordinator = TaskCoordinator(root, "alice", "web", "direct", "run", request())
    release, started = threading.Event(), threading.Event()

    def uncooperative(_):
        started.set()
        release.wait(8)
        return {"answer": "late"}

    child = scheduler.submit_callable("worker", {}, uncooperative, source="web", session_id="direct",
                                      ownership=coordinator.ownership("call"))
    assert started.wait(2)
    thread = threading.Thread(target=lambda: coordinator.finish("failed"), daemon=True)
    thread.start()
    try:
        thread.join(2)
        assert not thread.is_alive(), "finish keeps resetting the expired deadline"
        first = coordinator.ledger.get(coordinator.id, "web", "direct")
        assert first["status"] == "partial" and first["unfinished_tasks"][0]["id"] == child
        began = time.monotonic()
        coordinator.finish("interrupted")
        assert time.monotonic() - began < .2
        again = coordinator.ledger.get(coordinator.id, "web", "direct")
        assert again["finished_at"] == first["finished_at"]
        assert coordinator.ledger.get(child, "web", "direct")["execution_live"] is True
    finally:
        release.set()
        thread.join(5)


def test_user_stop_keeps_children_running_unless_cascade_was_explicit(setup):
    root, scheduler = setup
    bounded_config(root)
    for cancel_children in (False, True):
        request_with_policy = {
            **request(),
            "_cancel_subagents_event": threading.Event(),
        }
        if cancel_children:
            request_with_policy["_cancel_subagents_event"].set()
        coordinator = TaskCoordinator(
            root, "alice", "web", f"stop-{cancel_children}", "run", request_with_policy,
        )
        release, started = threading.Event(), threading.Event()

        def uncooperative(_):
            started.set()
            release.wait(5)
            return {"answer": "late"}

        child = scheduler.submit_callable(
            "worker", {}, uncooperative, source="web",
            session_id=f"stop-{cancel_children}",
            ownership=coordinator.ownership("call"),
        )
        assert started.wait(2)
        coordinator.finish("cancelled")
        child_task = scheduler._tasks[child]
        root_record = coordinator.ledger.get(
            coordinator.id, "web", f"stop-{cancel_children}",
        )
        assert root_record["status"] == "partial"
        assert root_record["response_closed"] and root_record["execution_live"]
        assert child_task.cancel_event.is_set() is cancel_children
        release.set()
        assert child_task.done_event.wait(3)


def test_generator_close_keeps_partial_archive_and_fence_until_real_exit(setup):
    root, scheduler = setup
    bounded_config(root)
    release, started, closed = threading.Event(), threading.Event(), threading.Event()
    JOBS["worker"] = lambda cancel: started.set() or release.wait(10) or {"answer": "late"}
    errors = []

    def consume_and_close():
        iterator = iter_request_events(request(), root=root, provider_factory=lambda _: MediaProvider([dispatch()]))
        try:
            for event in iterator:
                if event.type == "tool_call_result":
                    assert started.wait(2)
                    break
            iterator.close()
            closed.set()
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=consume_and_close, daemon=True)
    thread.start()
    try:
        assert closed.wait(3) and not errors
        record = root_record(root)
        assert record["status"] == "partial" and record["execution_live"]
        integration, saved = integration_record(root)
        assert integration["response_closed"] == 1 and saved["status"] == "partial"
        assert not session_lock(root, "alice", "web", "scope").acquire(blocking=False)
    finally:
        release.set()
        thread.join(5)


def test_host_interrupt_during_fence_check_does_not_leak_session_lock(setup, monkeypatch):
    root, _ = setup
    import run.agents.task_ledger as ledger_module

    lock = session_lock(root, "alice", "web", "host-interrupt")
    with monkeypatch.context() as patcher:
        def interrupt(*args):
            raise KeyboardInterrupt()
        patcher.setattr(ledger_module, "ensure_session_execution_available", interrupt)
        with pytest.raises(KeyboardInterrupt):
            lock.acquire()
    acquired = []

    def check_from_other_thread():
        if lock.acquire(blocking=False):
            acquired.append(True)
            lock.release()

    thread = threading.Thread(target=check_from_other_thread)
    thread.start()
    thread.join(1)
    assert acquired == [True]
