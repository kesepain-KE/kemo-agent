"""Status waits use the real scheduler and keep durable query permissions."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

import pytest

from plugins.subagent_dispatch.tool import run as dispatch
from run.agents.conclusions import delivery
from run.agents.coordinator import TaskCoordinator
from run.agents.queue import AgentTaskNotFoundError
from run.agents.queue_models import AgentTask
from run.agents.service import get_agent_scheduler
from run.config import load_config
from run.tools import ToolValidationError, discover_tools, execute_tool, resolve_tool_timeout
from tests.cards.test_runtime import root  # noqa: F401


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCOPE = {"source": "web", "session_id": "s1"}


@pytest.fixture
def scheduled(root):
    scheduler = get_agent_scheduler(root, "alice", config=load_config("alice", root))
    try:
        yield scheduler, {"root": str(root), "user": "alice", **SCOPE}
    finally:
        scheduler.close(wait=True, cancel_pending=True)


def test_wait_timeout_through_watchdog_then_resume_same_task(scheduled, monkeypatch):
    scheduler, context = scheduled
    started, release = threading.Event(), threading.Event()

    def run_task(_cancel):
        started.set()
        assert release.wait(10)
        return {"summary": "finished once"}

    task_id = scheduler.submit_callable("general_worker", {}, run_task, **SCOPE)
    try:
        assert started.wait(3)
        arguments = {"action": "status", "task_id": task_id, "wait": True, "timeout": 1}
        definition = discover_tools(PROJECT_ROOT, "alice").get("subagent_dispatch")
        timed_out = execute_tool(definition, arguments, context=context, timeout=0.25)
        assert timed_out["wait_outcome"] == "timeout" and timed_out["status"] == "running"
        assert scheduler.get(task_id, **SCOPE)["status"] == "running"

        # Signal progress only after the second wait has entered the real scheduler.
        original_wait = scheduler.wait

        def resume_wait(*args, **kwargs):
            release.set()
            return original_wait(*args, **kwargs)

        monkeypatch.setattr(scheduler, "wait", resume_wait)
        finished = dispatch("status", task_id=task_id, wait=True, timeout=2, context=context)
        assert finished["wait_outcome"] == "finished" and finished["status"] == "completed"
        assert finished["task_id"] == task_id and finished["data"] == {"summary": "finished once"}
    finally:
        release.set()


def test_already_terminal_wait_returns_original_snapshot_without_calling_wait(scheduled, monkeypatch):
    scheduler, context = scheduled
    task_id = scheduler.submit_callable("general_worker", {}, lambda _: {"summary": "done"}, **SCOPE)
    scheduler.wait(task_id, 3, **SCOPE)
    monkeypatch.setattr(scheduler, "wait", lambda *args, **kwargs: pytest.fail("terminal task need not wait"))
    original = delivery(scheduler.get(task_id, **SCOPE))
    assert dispatch("status", task_id=task_id, context=context) == original
    assert dispatch("status", task_id=task_id, wait=False, context=context) == original
    assert "wait_outcome" not in original
    assert dispatch("status", task_id=task_id, wait=True, context=context) == {
        **original, "wait_outcome": "finished",
    }


@pytest.mark.parametrize("ending", ["failed", "cancelled"])
def test_wait_reports_terminal_failure_or_cancellation_with_original_detail(scheduled, monkeypatch, ending):
    scheduler, context = scheduled
    started, release, waiting = threading.Event(), threading.Event(), threading.Event()
    original_wait = scheduler.wait

    def run_task(_cancel):
        started.set()
        assert release.wait(8)
        if ending == "failed":
            raise RuntimeError("task failed for test")
        return {}

    def observe_wait(*args, **kwargs):
        waiting.set()
        return original_wait(*args, **kwargs)

    monkeypatch.setattr(scheduler, "wait", observe_wait)
    task_id = scheduler.submit_callable("general_worker", {}, run_task, **SCOPE)
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            assert started.wait(3)
            future = pool.submit(dispatch, "status", task_id=task_id, wait=True, timeout=5, context=context)
            assert waiting.wait(3)
            if ending == "cancelled":
                assert scheduler.cancel(task_id, **SCOPE)
            release.set()
            result = future.result(3)
        finally:
            release.set()
    assert result["wait_outcome"] == "finished" and result["status"] == ending and result["ok"] is False
    assert result["error"] == delivery(scheduler.get(task_id, **SCOPE))["error"]
    if ending == "failed":
        assert "task failed for test" in result["error"]["message"]
    else:
        assert result["error"]["exception_type"] == "AgentCancelledError"


def test_wait_rejects_unknown_task_and_wrong_source_or_session(scheduled):
    scheduler, context = scheduled
    task_id = scheduler.submit_callable("general_worker", {}, lambda _: {}, **SCOPE)
    cases = [
        ("agent-task-missing", context),
        (task_id, {**context, "source": "app"}),
        (task_id, {**context, "session_id": "another"}),
    ]
    for identifier, scope in cases:
        with pytest.raises(AgentTaskNotFoundError):
            dispatch("status", task_id=identifier, wait=True, timeout=1, context=scope)


@pytest.fixture
def ledger_only(root):
    coordinator = TaskCoordinator(root, "alice", "web", "s1", "run-ledger-only", {})
    task = AgentTask("agent-task-ledger-only", "general_worker", {}, **SCOPE)
    coordinator.ledger.register(task, coordinator.ownership("dispatch-call"))
    return coordinator.ledger, task.id


def test_ledger_only_interrupted_is_finished_without_native_wait(scheduled, ledger_only, monkeypatch):
    scheduler, context = scheduled
    ledger, task_id = ledger_only
    ledger.update(task_id, status="interrupted", execution_live=False, error={"message": "previous host exited"})
    assert not scheduler.has_local_task(task_id, **SCOPE)
    monkeypatch.setattr(scheduler, "wait", lambda *args, **kwargs: pytest.fail("no local wait for a terminal record"))
    result = dispatch("status", task_id=task_id, wait=True, context=context)
    assert result["status"] == "interrupted" and result["wait_outcome"] == "finished"
    assert result["error"]["message"] == "previous host exited"


def test_ledger_only_active_wait_rechecks_scope_and_last_snapshot(scheduled, ledger_only, monkeypatch):
    scheduler, context = scheduled
    ledger, task_id = ledger_only
    ledger.update(task_id, status="running", execution_live=True)
    result = dispatch("status", task_id=task_id, wait=True, timeout=1, context=context)
    assert result["wait_outcome"] == "timeout" and result["status"] == "running"

    original_wait = scheduler.wait

    def finish_externally(*args, **kwargs):
        ledger.update(task_id, status="completed", execution_live=False, result={"summary": "from ledger"})
        return original_wait(*args, **kwargs)

    monkeypatch.setattr(scheduler, "wait", finish_externally)
    result = dispatch("status", task_id=task_id, wait=True, timeout=1, context=context)
    assert result["wait_outcome"] == "finished" and result["status"] == "completed"
    assert result["data"] == {"summary": "from ledger"}


@pytest.mark.parametrize("ending", ["deleted", "out_of_scope"])
def test_lookup_failure_after_native_not_found_is_not_finished(scheduled, ledger_only, monkeypatch, ending):
    scheduler, context = scheduled
    ledger, task_id = ledger_only
    ledger.update(task_id, status="running", execution_live=True)
    original_wait = scheduler.wait

    def remove_visibility(*args, **kwargs):
        with ledger.db(write=True) as db:
            if ending == "deleted":
                db.execute("DELETE FROM agent_tasks WHERE id=?", (task_id,))
            else:
                db.execute("UPDATE agent_tasks SET session=? WHERE id=?", ("another", task_id))
        return original_wait(*args, **kwargs)

    monkeypatch.setattr(scheduler, "wait", remove_visibility)
    with pytest.raises(AgentTaskNotFoundError):
        dispatch("status", task_id=task_id, wait=True, timeout=1, context=context)


def test_status_wait_limit_does_not_change_submission_watchdog(scheduled, ledger_only):
    _, context = scheduled
    _, task_id = ledger_only
    definition = discover_tools(PROJECT_ROOT, "alice").get("subagent_dispatch")
    query = {"action": "status", "task_id": task_id, "wait": True}
    assert resolve_tool_timeout(definition, query, default_timeout=1) == 35
    assert resolve_tool_timeout(definition, {**query, "timeout": 60}, default_timeout=1) == 65
    assert resolve_tool_timeout(definition, {**query, "wait": False, "timeout": 61}, default_timeout=2) == 2
    assert resolve_tool_timeout(definition, {**query, "action": "call", "timeout": 61}, default_timeout=2) == 2
    for timeout in (0, 61, True, 1.5):
        with pytest.raises(ValueError, match="1..60"):
            dispatch("status", task_id=task_id, wait=True, timeout=timeout, context=context)
        with pytest.raises(ToolValidationError, match="1..60"):
            resolve_tool_timeout(definition, {**query, "timeout": timeout}, default_timeout=2)
