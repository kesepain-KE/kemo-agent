"""Real decision queries through the native and threaded CardStream paths."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

import pytest

from plugins.user_decision import tool as decision_plugin
from run.cards import CardStore, IntegrationStore
from run.engine import iter_request_events
from run.tools import ToolRegistry, ToolRuntime, ToolValidationError, discover_tools, resolve_tool_timeout
from run.tools.user_decisions import DecisionClosedError, DecisionStore
from tests.cards.test_runtime import call, definition, response, root  # noqa: F401
from tests.cards.test_user_decisions import QUESTION, enable, pending, stored_question


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def context_for(integration):
    return {
        "root": str(integration.store.root), "user": integration.store.user,
        "source": integration.source, "session_id": integration.session_id, "integration_id": integration.id,
    }


@pytest.mark.parametrize(("status", "extra"), [
    ("answered", {"option_id": "pg"}),
    ("expired", {}),
    ("cancelled", {}),
    ("resolved_by_agent", {"resolution": "already resolved"}),
])
def test_get_wait_finishes_for_each_terminal_decision_and_plain_shape_is_unchanged(root, status, extra):
    store, integration, decision_id = stored_question(root, mode="async")
    decisions = DecisionStore(store)
    decisions.settle("web", "s1", decision_id, status=status, **extra)
    query = {"decision_id": decision_id, "context": context_for(integration)}
    original = {"ok": True, **decisions.get("web", "s1", decision_id)}
    assert decision_plugin.run("get", **query) == original
    assert decision_plugin.run("get", **query, wait=False) == original
    assert "wait_outcome" not in original
    assert decision_plugin.run("get", **query, wait=True, timeout=1) == {**original, "wait_outcome": "finished"}


def test_get_wait_through_watchdog_returns_completed_query_card_with_pending_snapshot(root):
    store, integration, decision_id = stored_question(root, mode="async")
    runtime = ToolRuntime(integration)
    tool = discover_tools(PROJECT_ROOT, "alice").get("user_decision")
    query = call("user_decision", "callid_get_timeout", action="get", decision_id=decision_id, wait=True, timeout=1)
    execution = runtime.start(query, tool, context=context_for(integration), timeout=0.25)
    assert runtime.wait_for_initial_result(execution, timeout=4) == "completed"
    event, = [event for event in runtime.events() if event.type == "tool_call_result"]
    snapshot = DecisionStore(store).get("web", "s1", decision_id)
    assert snapshot["status"] == "pending" and snapshot["answer"] is None
    assert event.metadata["status"] == "completed" and event.metadata["execution_live"] is False
    assert event.result == {"ok": True, "result": {"ok": True, **snapshot, "wait_outcome": "timeout"}}


def test_get_wait_observes_an_answer_committed_while_query_is_live(root, monkeypatch):
    store, integration, decision_id = stored_question(root, mode="async")
    waiting = threading.Event()
    get = DecisionStore.get

    def observe_get(self, *args, **kwargs):
        snapshot = get(self, *args, **kwargs)
        if snapshot["decision_id"] == decision_id and snapshot["status"] == "pending":
            waiting.set()
        return snapshot

    monkeypatch.setattr(DecisionStore, "get", observe_get)
    runtime = ToolRuntime(integration)
    tool = discover_tools(PROJECT_ROOT, "alice").get("user_decision")
    query = call("user_decision", "callid_get_answer", action="get", decision_id=decision_id, wait=True, timeout=6)
    execution = runtime.start(query, tool, context=context_for(integration), timeout=2)
    try:
        assert waiting.wait(3)
        DecisionStore(store).settle("web", "s1", decision_id, status="answered", option_id="pg")
        assert runtime.wait_for_initial_result(execution, timeout=4) == "completed"
        event, = [event for event in runtime.events() if event.type == "tool_call_result"]
        snapshot = event.result["result"]
        assert snapshot["wait_outcome"] == "finished" and snapshot["status"] == "answered"
        assert snapshot["answer"]["option_id"] == "pg"
    finally:
        runtime.cancel_event.set()


def test_waiting_get_does_not_block_main_run_stop(root):
    enable(root)
    tool = discover_tools(PROJECT_ROOT, "alice").get("user_decision")
    cancel, waiting = threading.Event(), threading.Event()
    requests = []

    class Provider:
        def create(self, request):
            requests.append(request)
            if len(requests) == 1:
                return response(request, "", calls=[call("user_decision", **QUESTION, mode="async")])
            assert len(requests) == 2
            question, = pending(root)
            return response(request, "", calls=[call(
                "user_decision", "callid_get_stop", action="get", decision_id=question["decision_id"], wait=True, timeout=20,
            )])

    def run():
        events = []
        for event in iter_request_events(
            {"user": "alice", "source": "web", "session_id": "s1", "prompt": "查询等待期间停止"},
            root=root, provider_factory=lambda _: Provider(),
            tool_registry_factory=lambda *_: ToolRegistry({tool.name: tool}), cancel_event=cancel,
        ):
            events.append(event)
            if event.type == "tool_call_start" and event.tool_call_id == "callid_get_stop":
                waiting.set()
        return events[-1]

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run)
        try:
            assert waiting.wait(6)
            assert not future.done() and pending(root)[0]["status"] == "pending"
            cancel.set()
            terminal = future.result(4)
        finally:
            cancel.set()
            if not future.done():
                decisions = DecisionStore(CardStore(root, "alice"))
                for item in pending(root):
                    try:
                        decisions.settle("web", "s1", item["decision_id"], status="cancelled")
                    except DecisionClosedError:
                        pass
    assert terminal.type == "done" and terminal.metadata["status"] in {"cancelled", "partial"}
    # The Run can close as partial with a live-tool receipt before the thread exits.
    assert terminal.metadata["error"]["exception_type"] == "RetryCancelled"
    assert len(requests) == 2
    assert pending(root) == []


def test_ask_close_and_plain_get_keep_native_inline_entry_and_result(root):
    enable(root)
    integration = IntegrationStore(CardStore(root, "alice"), "web", "s1", "native-decision")
    integration.start()
    runtime = ToolRuntime(integration)
    caller = threading.get_ident()
    invocations = []

    def observe_call(action, *, context, **arguments):
        invocations.append((action, threading.get_ident()))
        return decision_plugin.run(action, context=context, **arguments)

    tool = definition(root, observe_call, name="user_decision")
    context = context_for(integration)
    decision_id = runtime.start(call("user_decision", **QUESTION, mode="async"), tool, context=context, timeout=1)
    _, asked = runtime.events()
    assert asked.type == "tool_call_result" and asked.metadata["execution_live"] is True
    snapshot = asked.result
    assert snapshot["status"] == "pending" and snapshot["decision_id"] == decision_id
    for i, arguments in enumerate(({}, {"wait": False})):
        runtime.start(call("user_decision", f"callid_plain_get_{i}", action="get", decision_id=decision_id, **arguments),
                      tool, context=context, timeout=1)
        _, got = runtime.events()
        assert got.type == "tool_call_result" and got.metadata["status"] == "completed"
        assert got.result == snapshot and "wait_outcome" not in got.result
    runtime.start(call("user_decision", "callid_close", action="close", decision_id=decision_id,
                       resolution="resolved without another question"), tool, context=context, timeout=1)
    _, closed = runtime.events()
    assert closed.type == "tool_call_result" and closed.metadata["status"] == "completed"
    assert closed.result["status"] == "resolved_by_agent"
    assert invocations == [(action, caller) for action in ("ask", "get", "get", "close")]


def test_get_wait_timeout_range_and_watchdog_scope(root):
    _, integration, decision_id = stored_question(root, mode="async")
    tool = discover_tools(PROJECT_ROOT, "alice").get("user_decision")
    query = {"action": "get", "decision_id": decision_id, "wait": True}
    assert resolve_tool_timeout(tool, query, default_timeout=2) == 35
    assert resolve_tool_timeout(tool, {**query, "timeout": 60}, default_timeout=2) == 65
    for action in ("ask", "close", "get"):
        assert resolve_tool_timeout(tool, {"action": action, "wait": False, "timeout": 61}, default_timeout=2) == 2
    for timeout in (0, 61, True, 1.5):
        with pytest.raises(ValueError, match="1..60"):
            decision_plugin.run("get", decision_id=decision_id, wait=True, timeout=timeout,
                                context=context_for(integration))
        with pytest.raises(ToolValidationError, match="1..60"):
            resolve_tool_timeout(tool, {**query, "timeout": timeout}, default_timeout=2)
