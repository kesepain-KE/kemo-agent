"""Default dispatch reaches the real main loop, scheduler, pool and barrier."""
from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import provider.factory as factory
from plugins.subagent_dispatch.tool import run as dispatch, _effective_wait
from provider.schema import ChatResponse, ToolCall
from run.agents import AgentRunner, AgentScheduler
from run.agents.task_ledger import TaskLedger
from run.engine import iter_request_events
from tests.agents.test_general_worker import workspace, OUTPUT  # noqa: F401
from tests.agents.test_subagent_hotplug import ScriptedProvider


def eventually(test, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if test():
            return
        time.sleep(.01)
    assert test()


@pytest.mark.parametrize("executor,execution,special,expected", [
    ("builtin:llm", "sync", False, False),
    ("builtin:llm", "background", False, False),
    ("builtin:llm", "background_serial", False, True),
    ("executor.py:run", "sync", False, True),
    ("builtin:llm", "sync", True, True),
])
def test_wait_policy_preserves_explicit_and_special_contracts(executor, execution, special, expected):
    definition = SimpleNamespace(executor=executor, execution=execution)
    assert _effective_wait(None, definition, synchronous_only=special) is expected
    assert _effective_wait(True, definition, synchronous_only=special) is True
    assert _effective_wait(False, definition, synchronous_only=special) is False


def test_default_five_calls_register_before_first_finishes_and_share_pool(workspace, monkeypatch):
    root, config = workspace
    config["provider_runtime"] = {"max_concurrent_requests": 3}
    for path in ("config/global_config.json", "users/alice/user_config.json"):
        (root / path).write_text(json.dumps(config), "utf-8")
    plugin = root / "plugins/subagent_dispatch"
    plugin.mkdir()
    repository = Path(__file__).resolve().parents[2]
    shutil.copyfile(repository / "plugins/subagent_dispatch/SKILL.md", plugin / "SKILL.md")
    (plugin / "tool.py").write_text("from plugins.subagent_dispatch.tool import run\n", "utf-8")
    monkeypatch.setattr(factory, "_provider_semaphore", None)
    monkeypatch.setattr(factory, "_provider_semaphore_limit", 0)
    entered = []
    release = threading.Event()
    class Child(ScriptedProvider):
        def create(self, request):
            entered.append(factory.provider_semaphore_status()["active_requests"])
            assert release.wait(20)
            return super().create(request)
    scheduler = AgentScheduler(AgentRunner(root, "alice", config=config,
        provider_factory=lambda _: Child([ChatResponse(text=json.dumps(OUTPUT), model="mock")])))
    monkeypatch.setattr("plugins.subagent_dispatch.tool.get_agent_scheduler", lambda *a, **k: scheduler)
    monkeypatch.setattr("run.agents.get_agent_scheduler", lambda *a, **k: scheduler)
    calls = [ToolCall(f"call-{i}", "subagent_dispatch",
        {"action": "call", "agent": "general_worker", "input": {"task": f"independent-{i}"}})
        for i in range(5)]  # Deliberately no wait: this is the reported path.
    main_responses: list[ChatResponse | BaseException] = [ChatResponse(text="", tool_calls=calls)]
    main_responses.extend(ChatResponse(text="merge results", model="mock") for _ in range(8))
    main = ScriptedProvider(main_responses)
    events, errors = [], []
    def run():
        try:
            events.extend(iter_request_events(
                {"user": "alice", "source": "web", "session_id": "scope", "prompt": "parallel work"},
                root=root, provider_factory=lambda _: main))
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=run, daemon=True)
    ledger = TaskLedger(root, "alice")
    def children():
        return [r for r in ledger.records("web", "scope")[0] if r.get("root_task_id")]
    try:
        thread.start()
        eventually(lambda: len(children()) == 5)
        eventually(lambda: len(entered) == 3)
        assert all(not c.get("finished_at") for c in children())
        assert max(entered) <= 3
        assert thread.is_alive() and not any(e.type == "done" for e in events)
        release.set()
        thread.join(15)
        assert not thread.is_alive() and not errors, errors
        assert len(entered) == 5 and max(entered) <= 3
        assert any(e.type == "done" for e in events)
        records = sorted(children(), key=lambda r: r["submission_order"])
        assert [r["submission_order"] for r in records] == [1, 2, 3, 4, 5]
        assert all(r["status"] == "completed" and r["handled"] for r in records)
        assert factory.provider_semaphore_status()["active_requests"] == 0
    finally:
        release.set()
        thread.join(15)
        scheduler.close(wait=True, cancel_pending=True)


def test_explicit_wait_true_still_waits_for_completion(workspace, monkeypatch):
    root, config = workspace
    entered, release = threading.Event(), threading.Event()
    class Child(ScriptedProvider):
        def create(self, request):
            entered.set()
            assert release.wait(10)
            return super().create(request)
    scheduler = AgentScheduler(AgentRunner(root, "alice", config=config,
        provider_factory=lambda _: Child([ChatResponse(text=json.dumps(OUTPUT), model="mock")])))
    monkeypatch.setattr("plugins.subagent_dispatch.tool.get_agent_scheduler", lambda *a, **k: scheduler)
    result = []
    thread = threading.Thread(target=lambda: result.append(dispatch(
        "call", agent="general_worker", input={"task": "sync"}, wait=True,
        context={"root": str(root), "user": "alice", "source": "web", "session_id": "scope"})))
    try:
        thread.start()
        assert entered.wait(5)
        assert not result and thread.is_alive()
        release.set()
        thread.join(10)
        assert result[0]["status"] == "completed"
    finally:
        release.set()
        thread.join(10)
        scheduler.close(wait=True, cancel_pending=True)


def test_default_resume_returns_without_waiting_and_list_exposes_default(workspace, monkeypatch):
    root, _ = workspace
    snapshot = {"id": "agent-task-" + "a" * 32, "agent": "general_worker", "status": "running"}
    scheduler = SimpleNamespace(
        get=lambda *a, **k: snapshot,
        resume=lambda *a, **k: None,
        wait=lambda *a, **k: pytest.fail("default resume must not synchronously wait"),
    )
    monkeypatch.setattr("plugins.subagent_dispatch.tool.get_agent_scheduler", lambda *a, **k: scheduler)
    context = {"root": str(root), "user": "alice", "source": "web", "session_id": "scope"}
    response = dispatch("resume", task_id=snapshot["id"], context=context)
    assert response["status"] == "running"
    worker = next(a for a in dispatch("list", context=context)["agents"] if a["name"] == "general_worker")
    assert worker["default_wait"] is False
