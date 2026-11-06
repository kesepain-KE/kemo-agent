"""Preview hardening at real runtime boundaries, with no network or user data."""
from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from provider.schema import ChatResponse, ProviderError, ToolCall
from run.agents import AgentRunner, AgentScheduler
from run.engine import iter_request_events
from run.retry import HOST_CONTROL_EXCEPTIONS, RetryLedger, run_attempts
from run.tools import execution_watchdog_snapshot
from tests.agents.test_general_worker import workspace, OUTPUT  # noqa: F401
from tests.agents.test_subagent_hotplug import ScriptedProvider


@pytest.mark.parametrize("kind", HOST_CONTROL_EXCEPTIONS)
def test_host_exception_does_not_consume_business_retry_ledger(kind):
    error = kind("test-only")
    run = Mock(side_effect=error)
    retry, wait = Mock(return_value=True), Mock()
    ledger = RetryLedger()
    with pytest.raises(kind) as caught:
        run_attempts(run, ledger=ledger, should_retry_error=retry, wait_before_retry=wait)
    assert caught.value is error
    assert run.call_count == ledger.attempts == 1
    assert ledger.consecutive_failures == 0
    retry.assert_not_called()
    wait.assert_not_called()
    assert not hasattr(error, "retry_exhausted")


def tool(root, name):
    path = root / "plugins" / name
    path.mkdir()
    manifest = {"name": name, "description": "offline-only", "version": "1",
                "enabled": True, "entrypoint": "tool.py:run", "execution_mode": "thread",
                "input_schema": {"type": "object", "additionalProperties": True}}
    (path / "SKILL.md").write_text(f"# {name}\n## Tool\n```json\n" + json.dumps(manifest) + "\n```\n", "utf-8")
    (path / "tool.py").write_text("def run(**kwargs): return {}\n", "utf-8")


@pytest.mark.parametrize("kind", HOST_CONTROL_EXCEPTIONS)
@pytest.mark.parametrize("phase", ["prepare", "provider", "tool"])
@pytest.mark.parametrize("agent", ["main", "child"])
def test_host_exception_propagates_unchanged_and_releases_slots(workspace, monkeypatch, kind, phase, agent):
    root, config = workspace
    error = kind("offline-test")
    before = execution_watchdog_snapshot()
    tool(root, "echo")
    first = ChatResponse(text="", tool_calls=[ToolCall("call-one", "echo", {})])
    provider = ScriptedProvider([first] if phase == "tool" else [error])
    factory = Mock(side_effect=error) if phase == "prepare" else Mock(return_value=provider)
    if phase == "tool":
        def fail(*args, **kwargs):
            raise error
        # Exercise the real tool executor and its worker Future, not a bypass.
        monkeypatch.setattr("run.tools.ToolDefinition.load_callable", lambda _: fail)
    events = []
    with pytest.raises(kind) as caught:
        if agent == "main":
            for event in iter_request_events(
                {"user": "alice", "source": "web", "session_id": "isolated", "prompt": "test"},
                root=root, provider_factory=factory,
            ):
                events.append(event)
        else:
            AgentRunner(root, "alice", config=config, provider_factory=factory).run(
                "general_worker", {"task": "test"}, timeout=10, event_callback=events.append)
    assert caught.value is error
    assert factory.call_count == 1
    assert len(provider.requests) == (0 if phase == "prepare" else 1)
    assert not any(e.type == "retrying" or e.metadata.get("status") == "retrying" for e in events)
    assert execution_watchdog_snapshot() == before


@pytest.mark.parametrize("kind", HOST_CONTROL_EXCEPTIONS)
def test_background_child_records_terminal_and_wait_preserves_host_exception(workspace, kind):
    root, config = workspace
    error = kind("offline-test")
    provider = ScriptedProvider([error])
    scheduler = AgentScheduler.from_runner(AgentRunner(root, "alice", config=config,
                                                      provider_factory=lambda _: provider))
    try:
        task_id = scheduler.submit("general_worker", {"task": "test"}, allow_sync=True,
                                   source="web", session_id="isolated", timeout=10)
        with pytest.raises(kind) as caught:
            scheduler.wait(task_id, 5, source="web", session_id="isolated")
        assert caught.value is error
        task = scheduler.get(task_id, source="web", session_id="isolated")
        assert task["status"] == "failed" and task["stop_reason"] == "host_exception"
        assert not task["resumable"] and "host_exception" not in task
        assert len(provider.requests) == 1
    finally:
        scheduler.close(wait=True, cancel_pending=True)


@pytest.mark.parametrize("kind", HOST_CONTROL_EXCEPTIONS)
def test_main_registration_host_exception_is_not_a_best_effort_warning(workspace, monkeypatch, kind):
    root, _ = workspace
    error = kind("offline registration")
    provider = ScriptedProvider([ChatResponse(text="done")])
    monkeypatch.setattr("run.cards.IntegrationStore.start", Mock(side_effect=error))
    with pytest.raises(kind) as caught:
        list(iter_request_events(
            {"user": "alice", "source": "web", "session_id": "isolated", "prompt": "test"},
            root=root, provider_factory=lambda _: provider))
    assert caught.value is error
    assert not provider.requests


LIVE = [("subagent_dispatch", {"action": action}) for action in ("list", "status")]
LIVE += [("shell", {"action": "status"}), ("network", {"action": "get"}),
         ("network", {"action": "read"}), ("file", {"action": "read"}),
         ("expand_call", {"command": "status"})]
OPERATIONS = [("subagent_dispatch", {"action": action})
              for action in ("call", "resume", "split", "create", "guide", "cancel")]
OPERATIONS += [("shell", {"action": "run"}), ("shell", {"action": "cancel"}),
               ("external_message", {"action": "send_message"}),
               ("external_message", {"action": "send_file"})]
OPERATIONS += [("network", {"action": action}) for action in ("post", "put", "patch", "delete")]
OPERATIONS += [("file", {"action": "write"}), ("custom_plugin", {})]


@pytest.mark.parametrize("name,args", LIVE + OPERATIONS)
def test_shared_dedup_policy_is_action_specific(name, args):
    from run.agents.retry_policy import _agent_tool_result_dedup_required
    from run.retry.tool_dedup import attempt_dedup_policy
    expected = (name, args) in OPERATIONS
    assert (attempt_dedup_policy(name, args) == "block") is expected
    assert _agent_tool_result_dedup_required(name, args) is expected


@pytest.mark.parametrize("name,args,agent", [
    (name, args, agent) for name, args in LIVE + OPERATIONS for agent in ("main", "child")
    if agent == "main" or name != "subagent_dispatch"
])
def test_real_tool_loops_refresh_queries_and_block_repeated_effects_across_retry(workspace, monkeypatch, name, args, agent):
    root, config = workspace
    tool(root, name)
    final = json.dumps(OUTPUT) if agent == "child" else "done"
    provider = ScriptedProvider([
        ChatResponse(text="", tool_calls=[ToolCall("call-one", name, args)]),
        ProviderError("offline transient", category="connection_error", retryable=True, retry_after_ms=0),
        ChatResponse(text="", tool_calls=[ToolCall("call-two", name, args)]),
        ChatResponse(text=final),
    ])
    executed = Mock(side_effect=[{"ok": True, "status": "running"}, {"ok": True, "status": "completed"}])
    if agent == "child":
        # Child cannot dispatch other agents; these cases are only run on main.
        monkeypatch.setattr("run.agents.model_loop.execute_tool", executed)
        monkeypatch.setattr("run.agents.runner._agent_retry_delay_seconds", lambda *a: 0)
        result = AgentRunner(root, "alice", config=config, provider_factory=lambda _: provider).run(
            "general_worker", {"task": "test"}, timeout=10)
        assert result.data == OUTPUT
    else:
        monkeypatch.setattr("run.tools.runtime.execute_tool", executed)
        events = list(iter_request_events(
            {"user": "alice", "source": "web", "session_id": "isolated", "prompt": "test"},
            root=root, provider_factory=lambda _: provider))
        assert any(e.type == "done" for e in events)
    assert executed.call_count == (1 if (name, args) in OPERATIONS else 2)
    last = provider.requests[-1].messages
    serialized = json.dumps(last)
    if (name, args) in LIVE:
        assert "completed" in serialized
    else:
        assert "duplicate_blocked" in serialized
