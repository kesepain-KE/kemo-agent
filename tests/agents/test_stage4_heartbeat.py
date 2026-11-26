"""Managed execution, heartbeat delivery, and cancellation at real runtime boundaries."""
from __future__ import annotations

import json
import threading
import time
from importlib import import_module
from types import SimpleNamespace

import pytest

from plugins.subagent_dispatch import tool as dispatch
from provider.protocol.models import MessageItem, ReasoningItem, ToolCallItem, text_from_content
from provider.schema import ProviderError
from run.agents import AgentCancelledError, AgentQueueError, AgentRunner, AgentScheduler, TaskCoordinator, TaskLedger
from run.cards import CardStore
from run.engine import iter_request_events
from run.tools import ToolDefinition, ToolRegistry, resolve_tool_timeout
from tests.agents.test_subagent_continuation import OUTPUT, Provider, call, eventually, write_plugin
from tests.agents.test_subagent_input_preflight import setup_agents


SCOPE = {"source": "web", "session_id": "stage4"}


def registry(root):
    tool = ToolDefinition(
        name="subagent_dispatch", description="production dispatch", input_schema={"type": "object"},
        version="1", enabled=True, directory=root, entrypoint="tool.py:run", source="test",
        execution_mode="thread", timeout_policy="submission", _callable=dispatch.run,
    )
    return ToolRegistry({tool.name: tool})


def notices(request):
    for item in request.input:
        if isinstance(item, MessageItem):
            text = text_from_content(item.content)
            if text.startswith("[CardStream dynamic_tail:"):
                tail = json.loads(text.split("\n", 1)[1])
                content = tail.get("subagent_events", {}).get("content")
                return json.loads(content.splitlines()[1]) if content else []
    return []


def tool_call(agent, data):
    return ToolCallItem(
        id="call_dispatch", call_id="callid_dispatch", name="subagent_dispatch",
        arguments={"action": "call", "agent": agent, "input": data, "wait": True, "timeout": 600},
    )


def test_heartbeat_reaches_main_provider_and_acknowledges_only_the_frozen_sequence(tmp_path, monkeypatch):
    root = tmp_path
    config = setup_agents(root)
    write_plugin(root)
    model = import_module("run.agents.model_loop")
    budgets = import_module("run.agents.budgets")
    clock = [10000.]
    monkeypatch.setattr(budgets, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(model, "_heartbeat_clock", lambda: clock[0])
    points = [599, 600, 1199, 1200, 1500, 7200]
    entered = [threading.Event() for _ in range(len(points) + 1)]
    release = [threading.Event() for _ in entered]
    heartbeats = []
    observed = []
    first_on_wire = threading.Event()
    accept_first = threading.Event()
    secret = "sk-abcdefghijklmnopqrstuvwxyzABCDE"

    def tool(_definition, arguments, **_):
        i = int(arguments["value"].split(":")[0])
        entered[i].set()
        assert release[i].wait(15)
        clock[0] = 10000 + points[i]
        return {"ok": True, "never_public_result": arguments["value"]}

    monkeypatch.setattr(model, "execute_tool", tool)

    def final(_):
        entered[-1].set()
        assert release[-1].wait(15)
        return OUTPUT

    child = Provider([
        [ReasoningItem(id=f"rs_{i}", content="never_public_reasoning"),
         MessageItem.text("assistant", "进度 " + secret + " 公开 " * 100, phase="commentary"),
         call(value=f"{i}:never_public_argument", index=i)] for i in range(len(points))
    ] + [final])
    scheduler = AgentScheduler(AgentRunner(root, "alice", config=config, provider_factory=lambda _: child))
    monkeypatch.setattr(dispatch, "get_agent_scheduler", lambda *args, **kwargs: scheduler)
    monkeypatch.setattr("run.agents.get_agent_scheduler", lambda *args, **kwargs: scheduler)
    ledger = TaskLedger(root, "alice")
    delivered = []

    def respond(request):
        rows = notices(request)
        delivered.extend(r for r in rows if r.get("status") == "completed")
        if heartbeat := next((r for r in rows if r.get("heartbeat")), None):
            observed.append(heartbeat)
            if len(observed) == 1:
                first_on_wire.set()
                assert accept_first.wait(15)
                # The shared provider exchange must retry the frozen batch.
                raise ProviderError("test transient rate limit", status_code=429, retry_after_ms=0)
        return {"answer": "process available results"}

    main = Provider([[tool_call("general_worker", {"task": "private_child_input"})], *[respond] * 14])
    events = []

    def progress(event):
        if event.metadata.get("heartbeat") is True:
            heartbeats.append(event.metadata)

    def run():
        events.extend(iter_request_events(
            {"user": "alice", **SCOPE, "prompt": "run child", "_subagent_event_callback": progress},
            root=root, provider_factory=lambda _: main, tool_registry_factory=lambda *_: registry(root),
        ))

    thread = threading.Thread(target=run, daemon=True)
    try:
        thread.start()
        assert entered[0].wait(10)
        release[0].set()
        assert entered[1].wait(10) and not heartbeats
        release[1].set()
        assert entered[2].wait(10) and first_on_wire.wait(10)
        release[2].set()
        assert entered[3].wait(10)
        assert [h["elapsed_seconds"] for h in heartbeats] == [600]
        release[3].set()
        assert entered[4].wait(10)
        task_id = heartbeats[0]["task_id"]
        assert ledger.get(task_id, **SCOPE)["_heartbeat"]["heartbeat_seq"] == 2
        accept_first.set()
        eventually(lambda: [h["heartbeat_seq"] for h in observed] == [1, 1, 2], seconds=10)
        release[4].set()
        assert entered[5].wait(10)
        assert [h["elapsed_seconds"] for h in heartbeats] == [600, 1200]
        # Advancing while the tool is blocked must not synthesize a heartbeat.
        clock[0] = 17200
        assert [h["elapsed_seconds"] for h in heartbeats] == [600, 1200]
        assert ledger.get(task_id, **SCOPE)["status"] == "running"
        release[5].set()
        assert entered[-1].wait(10)
        eventually(lambda: len(observed) == 4, seconds=10)
        assert [h["elapsed_seconds"] for h in heartbeats] == [600, 1200, 7200]
        assert observed[0] == observed[1]
        latest = observed[-1]
        assert latest["usage"] == {"input_tokens": 12, "output_tokens": 6, "total_tokens": 18}
        assert latest["tool_calls_used"] == 6 and latest["budget"]["timeout"] is None
        assert latest["status"] == "running" and latest["run_count"] == 1
        cards = latest["recent_cards"]
        assert len(cards) == 10
        assert [c["seq"] for c in cards] == sorted((c["seq"] for c in cards), reverse=True)
        assert {c["kind"] for c in cards} == {"agent_text", "tool_call", "tool_result"}
        assert all(0 < len(c["summary"]) <= 200 for c in cards)
        encoded = json.dumps(latest)
        assert "[redacted]" in encoded
        assert not any(value in encoded for value in (secret, "never_public", "private_child_input"))
        assert thread.is_alive()
        release[-1].set()
        thread.join(15)
        assert not thread.is_alive()
        assert events[-1].type == "done" and events[-1].metadata["status"] == "completed"
        record = ledger.get(task_id, **SCOPE)
        assert record["handled"] and not record["execution_live"] and "_heartbeat" not in record
        assert len(delivered) == 1 and delivered[0]["result"] == OUTPUT
        cards = CardStore(root, "alice").raw_page(SCOPE["source"], SCOPE["session_id"])
        assert sum((card.event_key or "").startswith(f"subagent-result:{task_id}:") for card in cards) == 1
        assert all("heartbeat_seq" not in card.raw_payload_json for card in cards)
    finally:
        accept_first.set()
        for gate in release:
            gate.set()
        thread.join(15)
        scheduler.close(wait=True, cancel_pending=True)


def test_cancel_reports_request_live_executor_then_confirmed_exit(tmp_path, monkeypatch):
    config = setup_agents(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def block(_):
        entered.set()
        assert release.wait(10)
        return OUTPUT

    provider = Provider([block])
    scheduler = AgentScheduler(AgentRunner(tmp_path, "alice", config=config, provider_factory=lambda _: provider))
    monkeypatch.setattr(dispatch, "get_agent_scheduler", lambda *args, **kwargs: scheduler)
    parent = TaskCoordinator(tmp_path, "alice", **SCOPE, run_id="cancel", request={})
    context = {"root": str(tmp_path), "user": "alice", **SCOPE, "task_ownership": parent.ownership("call")}
    receipt = dispatch.run("call", "general_worker", input={"task": "test"}, timeout=0, wait=True, context=context)
    task_id = receipt["task_id"]
    try:
        assert entered.wait(5)
        requested = dispatch.run("cancel", task_id=task_id, context=context)
        assert requested["cancel_requested"] and not requested["cancelled"]
        assert requested["cancel_state"] in {"requested", "still_running"}
        eventually(lambda: dispatch.run("status", task_id=task_id, context=context)["cancel_state"] == "still_running")
        assert parent.pending()[0]["execution_live"]
        assert parent.notifications() is None
        with pytest.raises(AgentQueueError):
            scheduler.resume(task_id, **SCOPE, ownership=context["task_ownership"])
        release.set()
        eventually(lambda: not parent.pending())
        status = dispatch.run("status", task_id=task_id, context=context)
        assert status["status"] == "cancelled" and status["cancel_state"] == "exited"
        assert len(provider.requests) == 1
    finally:
        release.set()
        scheduler.close(wait=True, cancel_pending=True)


def test_plan_heartbeat_allows_progress_only_until_plan_is_persisted(tmp_path, monkeypatch):
    from run.tasks import PlanStore

    config = setup_agents(tmp_path)
    model = import_module("run.agents.model_loop")
    budgets = import_module("run.agents.budgets")
    clock = [10000.]
    monkeypatch.setattr(budgets, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(model, "_heartbeat_clock", lambda: clock[0])
    entered, advance, release, on_wire = (threading.Event() for _ in range(4))

    def planner(_):
        assert release.wait(15)
        return {"action": "create", "title": "待审批计划", "description": "检查审批边界",
                "steps": [{"step_id": "step_1", "title": "检查", "description": "读取记录", "depends_on": []}]}

    def factory(_):
        entered.set()
        assert advance.wait(15)
        # The bound Python executor has entered the cancellable model loop.
        # Even a legacy timeout=600 cannot expire this still-running model.
        clock[0] = 11500.
        return child

    child = Provider([planner])
    scheduler = AgentScheduler(AgentRunner(tmp_path, "alice", config=config, provider_factory=factory))
    monkeypatch.setattr(dispatch, "get_agent_scheduler", lambda *args, **kwargs: scheduler)
    monkeypatch.setattr("run.agents.get_agent_scheduler", lambda *args, **kwargs: scheduler)
    tools = registry(tmp_path)
    ran = []
    tools.tools["side_effect"] = ToolDefinition(
        name="side_effect", description="must not run", input_schema={"type": "object"},
        version="1", enabled=True, directory=tmp_path, entrypoint="tool.py:run", source="test",
        execution_mode="thread", _callable=lambda: ran.append(True),
    )

    def effect(i):
        return ToolCallItem(id=f"call_effect_{i}", call_id=f"callid_effect_{i}", name="side_effect", arguments={})

    def progress(request):
        assert not request.tools
        heartbeat, = notices(request)
        assert heartbeat["agent"] == "task_plan" and heartbeat["elapsed_seconds"] == 1500
        assert heartbeat["budget"]["timeout"] is None
        on_wire.set()
        return [effect(2)]  # A nonconforming provider cannot start a business action.

    main = Provider([[tool_call("task_plan", {"action": "create", "goal": "做计划"}), effect(1)], progress])
    events = []
    thread = threading.Thread(target=lambda: events.extend(iter_request_events(
        {"user": "alice", **SCOPE, "prompt": "先生成计划"},
        root=tmp_path, provider_factory=lambda _: main, tool_registry_factory=lambda *_: tools,
    )), daemon=True)
    try:
        thread.start()
        assert entered.wait(10)
        assert len(main.requests) == 1 and thread.is_alive()
        advance.set()
        assert on_wire.wait(10)
        assert PlanStore(tmp_path, "alice").list_plans(**SCOPE) == []
        assert not ran and thread.is_alive()
        release.set()
        thread.join(15)
        assert not thread.is_alive() and not ran
        assert len(main.requests) == 2
        assert events[-1].type == "done"
        assert events[-1].metadata["stop_reason"] == "task_plan_approval_required"
        plan, = PlanStore(tmp_path, "alice").list_plans(**SCOPE)
        assert plan["status"] == "pending" and events[-1].metadata["plan_id"] == plan["plan_id"]
        records, _ = TaskLedger(tmp_path, "alice").records(**SCOPE)
        record = next(r for r in records if r["agent"] == "task_plan")
        assert record["plan"]["plan_id"] == plan["plan_id"] and record["card_result_seq"]
        assert any(e.type == "tool_call_result" and e.tool_call_id == "callid_effect_1"
                   and e.metadata["status"] == "not_executed" for e in events)
    finally:
        advance.set()
        release.set()
        thread.join(15)
        scheduler.close(wait=True, cancel_pending=True)


def test_python_only_executor_still_has_a_finite_deadline(tmp_path, monkeypatch):
    config = setup_agents(tmp_path)
    seen = []

    def execute(context, _):
        seen.append(context.budget.timeout)
        context.cancel_event.wait(3)
        raise AgentCancelledError("cooperatively exited")

    monkeypatch.setattr("run.agents.runner._load_executor", lambda _: execute)
    parent = TaskCoordinator(tmp_path, "alice", **SCOPE, run_id="python", request={})
    scheduler = AgentScheduler(AgentRunner(tmp_path, "alice", config=config))
    try:
        task_id = scheduler.submit("task_plan", {"action": "create"}, **SCOPE, timeout=.1,
                                   timeout_survival_seconds=0, ownership=parent.ownership("call"))
        scheduler.wait(task_id, **SCOPE, timeout=5)
        assert seen == [.1]
        record = parent.ledger.get(task_id, **SCOPE)
        assert record["status"] == "timed_out" and not record["execution_live"]
        assert record["error"]["exception_type"] == "AgentTimeoutError"
    finally:
        scheduler.close(wait=True, cancel_pending=True)


@pytest.mark.parametrize("timeout", [None, 0, 600])
def test_dispatch_watchdog_uses_submission_timeout_not_the_task_argument(tmp_path, timeout):
    tool = registry(tmp_path).get("subagent_dispatch")
    assert resolve_tool_timeout(
        tool, {"timeout": timeout}, default_timeout=12, context={"agent_timeout": 600},
    ) == 12
