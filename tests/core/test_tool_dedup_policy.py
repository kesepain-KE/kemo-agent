"""Attempt-local query refresh and duplicate side-effect protection tests."""
from __future__ import annotations

import copy
import yaml
import json
import shutil
import threading
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from provider.schema import ChatResponse, ProviderError, ToolCall
from run.agents import AgentRunner
from run.modules import read_manifest, ModuleSpecError
from run.engine import iter_request_events
from run.tools import ConsecutiveIdenticalToolCallTracker
from tests.agents.test_general_worker import OUTPUT, workspace  # noqa: F401
from tests.agents.test_subagent_hotplug import ScriptedProvider
from tests.core.test_preview_runtime_safety import tool


def expand(command, *, module="ssh_control"):
    return ("expand_call", {"scope": "shared", "module": module, "command": command, "params": {}})


def run_loop(workspace, monkeypatch, agent, calls, *, values=None, retry=False, batch=False,
             scripted_provider=None, execute_function=None):
    root, config = workspace
    config["tools"].update({"max_iterations": 30, "consecutive_identical_call_limit": 3})
    (root / "config/global_config.yaml").write_text(json.dumps(config), "utf-8")
    (root / "users/alice/user_config.yaml").write_text(json.dumps(config), "utf-8")
    for name in {name for name, _ in calls}:
        tool(root, name)
    responses: list[ChatResponse | BaseException] = [
        ChatResponse(text="", tool_calls=[ToolCall(f"call-{i}", name, args)])
        for i, (name, args) in enumerate(calls)
    ]
    if batch:
        responses[:] = [ChatResponse(text="", tool_calls=[
            ToolCall(f"call-{i}", name, args) for i, (name, args) in enumerate(calls)
        ])]
    if retry:
        responses.insert(1, ProviderError(
            "offline transient", category="connection_error", retryable=True, retry_after_ms=0,
        ))
    responses.append(ChatResponse(text=json.dumps(OUTPUT) if agent == "child" else "done"))
    provider = ScriptedProvider(responses)
    executions = []

    def invoke(definition, arguments, **kwargs):
        executions.append((definition.name, copy.deepcopy(arguments)))
        if execute_function is not None:
            return execute_function(definition, arguments, **kwargs)
        value = values[len(executions) - 1] if values else {"ok": True, "sequence": len(executions)}
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)

    if scripted_provider is not None:
        provider = scripted_provider
    if agent == "child":
        monkeypatch.setattr("run.agents.model_loop.execute_tool", invoke)
        monkeypatch.setattr("run.agents.runner._agent_retry_delay_seconds", lambda *a: 0)
        result = AgentRunner(root, "alice", config=config, provider_factory=lambda _: provider).run(
        "general_worker", {"task": "offline dedup test"}, timeout=30,
            source="web", session_id="dedup",
        )
        records = result.metadata["tool_calls"]
    else:
        monkeypatch.setattr("run.tools.runtime.execute_tool", invoke)
        events = list(iter_request_events(
            {"user": "alice", "source": "web", "session_id": "dedup", "prompt": "offline test"},
            root=root, provider_factory=lambda _: provider,
        ))
        records = [{"result": e.result, **e.metadata} for e in events if e.type == "tool_call_result"]
    return records, executions, provider


@pytest.mark.parametrize("agent", ["main", "child"])
@pytest.mark.parametrize("command", ["read", "connect", "unrecognized"])
def test_undeclared_expand_commands_reexecute(workspace, monkeypatch, agent, command):
    records, executions, _ = run_loop(workspace, monkeypatch, agent, [expand(command)] * 2)
    assert len(executions) == 2
    assert [r["status"] for r in records] == ["completed", "completed"]
    assert records[1]["result"]["result"]["sequence"] == 2
    assert all("execution" not in r["result"] for r in records)


@pytest.mark.parametrize("agent", ["main", "child"])
def test_identical_expand_queries_are_reexecuted_for_fresh_results(workspace, monkeypatch, agent):
    records, executions, _ = run_loop(
        workspace, monkeypatch, agent, [expand("status")] * 3,
        values=[{"ok": True, "status": "old"}, {"ok": True, "status": "updated"},
                {"ok": True, "status": "latest"}],
    )
    assert len(executions) == 3
    assert [r["status"] for r in records] == ["completed"] * 3
    assert [r["result"]["result"]["status"] for r in records] == ["old", "updated", "latest"]
    assert all("execution" not in r["result"] for r in records)


@pytest.mark.parametrize("agent", ["main", "child"])
def test_expand_query_is_refreshed_after_a_mutation(workspace, monkeypatch, agent):
    calls = [expand("status"), expand("send"), expand("status")]
    records, executions, _ = run_loop(
        workspace, monkeypatch, agent, calls,
        values=[{"ok": True, "status": "before"}, {"ok": True}, {"ok": True, "status": "after"}],
    )
    assert len(executions) == 3
    assert [r["status"] for r in records] == ["completed"] * 3
    assert [records[0]["result"]["result"]["status"], records[2]["result"]["result"]["status"]] == [
        "before", "after",
    ]


@pytest.mark.parametrize("agent", ["main", "child"])
def test_failed_expand_query_does_not_suppress_later_refresh(workspace, monkeypatch, agent):
    records, executions, _ = run_loop(
        workspace, monkeypatch, agent, [expand("status"), expand("send"), expand("status")],
        values=[{"ok": True, "status": "old"}, {"ok": False, "error": "offline failure"},
                {"ok": True, "status": "new"}],
    )
    assert len(executions) == 3
    assert records[-1]["status"] == "completed"
    assert records[-1]["result"]["result"]["status"] == "new"


@pytest.mark.parametrize("agent", ["main", "child"])
@pytest.mark.parametrize("name,args", [
    ("file", {"action": "write", "path": "same.txt", "content": "x"}),
    ("shell", {"action": "run", "command": "offline-placeholder"}),
])
def test_repeat_side_effect_is_blocked_without_execution_count(workspace, monkeypatch, agent, name, args):
    records, executions, provider = run_loop(workspace, monkeypatch, agent, [(name, args)] * 5)
    assert len(executions) == 1
    assert [r["status"] for r in records] == ["completed"] + ["duplicate_blocked"] * 4
    for record in records[1:]:
        assert record["result"]["ok"] is False
        assert record["result"]["error"]["code"] == "duplicate_blocked"
        assert record["result"]["execution"]["executed"] is False
        assert record["consecutive_identical_calls"] == 1
    assert "duplicate_blocked" in json.dumps(provider.requests[-1].messages)


def test_inflight_duplicate_policy_blocks_side_effects_and_live_reads():
    from run.conversation.runtime_helpers import duplicate_execution_should_block

    class Integration:
        def __init__(self, live):
            self.live = live

        def live_executions(self):
            return self.live

    assert duplicate_execution_should_block(
        Integration([{"execution_id": "exec-1"}]), "exec-1",
    )
    assert not duplicate_execution_should_block(Integration([]), "exec-1")
    assert not duplicate_execution_should_block(
        Integration([{"execution_id": "exec-other"}]), "exec-1",
    )


def test_completed_live_read_is_reexecuted_after_finishing_during_provider_request(
    workspace,
    monkeypatch,
):
    finished = threading.Event()
    call = ("file", {"action": "read_range", "path": "same.txt", "start_line": 1})
    provider = ScriptedProvider([
        ChatResponse(text="", tool_calls=[ToolCall(f"call-{index}", *call)])
        for index in range(2)
    ] + [ChatResponse(text="done")])
    original_chat = provider.chat

    def wait_for_read_to_finish(request):
        response = original_chat(request)
        if len(provider.requests) == 2:
            assert finished.wait(5)
        return response

    provider.chat = wait_for_read_to_finish

    def read_file(*args, **kwargs):
        time.sleep(.35)
        finished.set()
        return {"ok": True, "sequence": len(executions)}

    executions = []

    def execute(definition, arguments, **kwargs):
        executions.append((definition.name, copy.deepcopy(arguments)))
        return read_file(definition, arguments, **kwargs)

    records, executions, _ = run_loop(
        workspace,
        monkeypatch,
        "main",
        [call] * 2,
        scripted_provider=provider,
        execute_function=execute,
    )

    completed_results = [
        record for record in records
        if record.get("execution_live") is False
        and record["result"].get("ok") is True
    ]
    assert len(executions) == 2
    assert [record["result"]["result"]["sequence"] for record in completed_results] == [1, 2]
    assert not any(record["status"] == "duplicate_blocked" for record in records)


def test_empty_file_arguments_are_rejected_before_tool_start(workspace, monkeypatch):
    from provider.schema import ChatResponse, ToolCall
    from run.engine import iter_request_events

    root, config = workspace
    config["tools"].update({"max_iterations": 5})
    (root / "config/global_config.yaml").write_text(json.dumps(config), "utf-8")
    (root / "users/alice/user_config.yaml").write_text(json.dumps(config), "utf-8")
    project_root = Path(__file__).resolve().parents[2]
    shutil.copytree(project_root / "plugins/file", root / "plugins/file")
    provider = ScriptedProvider([
        ChatResponse(text="", tool_calls=[ToolCall("call-empty-file", "file", {})]),
        ChatResponse(text="done"),
    ])
    executions = []
    monkeypatch.setattr(
        "run.tools.runtime.execute_tool",
        lambda *args, **kwargs: executions.append((args, kwargs)),
    )

    events = list(iter_request_events(
        {"user": "alice", "source": "web", "session_id": "empty-file", "prompt": "read a file"},
        root=root,
        provider_factory=lambda _: provider,
    ))

    assert events[-1].type == "done"
    assert executions == []
    assert not any(
        event.type == "tool_call_start" and event.tool_name == "file"
        for event in events
    )
    results = [event for event in events if event.type == "tool_call_result"]
    assert [event.metadata["status"] for event in results] == ["invalid_arguments"]


@pytest.mark.parametrize("agent", ["main", "child"])
def test_identical_search_file_and_network_queries_are_reexecuted(workspace, monkeypatch, agent):
    calls = [("web_search", {"query": "same"})] * 2
    calls += [("file", {"action": "read", "path": "same.txt"})] * 2
    calls += [("network", {"action": "get", "url": "http://localhost/test"})] * 2
    records, executions, _ = run_loop(
        workspace, monkeypatch, agent, calls,
        values=[{"ok": True, "sequence": i} for i in range(1, 7)],
    )
    assert len(executions) == 6
    assert [r["status"] for r in records] == ["completed"] * 6
    assert [r["result"]["result"]["sequence"] for r in records] == list(range(1, 7))


@pytest.mark.parametrize("agent", ["main", "child"])
def test_graph_write_guide_reexecutes_across_provider_retry(workspace, monkeypatch, agent):
    call = ("kemo_graph_write", {"action": "operation_guide", "operation": "sync"})
    records, executions, _ = run_loop(
        workspace, monkeypatch, agent, [call] * 2, retry=True,
    )
    assert len(executions) == 2
    assert records[-1]["result"]["result"]["sequence"] == 2
    assert records[-1]["status"] == "completed"


@pytest.mark.parametrize("agent", ["main", "child"])
def test_only_three_real_executions_and_fourth_is_rejected(workspace, monkeypatch, agent):
    records, executions, _ = run_loop(workspace, monkeypatch, agent, [expand("read")] * 5)
    assert len(executions) == 3
    assert [r["status"] for r in records] == ["completed"] * 3 + ["identical_call_blocked"] * 2
    assert [r["consecutive_identical_calls"] for r in records] == [1, 2, 3, 3, 3]


@pytest.mark.parametrize("agent", ["main", "child"])
def test_capacity_rejections_preserve_identical_budget_and_tool_availability(workspace, monkeypatch, agent):
    from run.tools import ToolExecutionCapacityError

    calls = [expand("read")] * 9
    records, executions, _ = run_loop(
        workspace, monkeypatch, agent, calls,
        values=[ToolExecutionCapacityError("offline capacity rejection")] * 5
        + [{"ok": True}] * 3,
    )
    assert len(executions) == 8
    assert [r["status"] for r in records] == ["failed"] * 5 + ["completed"] * 3 + ["identical_call_blocked"]
    assert [r["consecutive_identical_calls"] for r in records] == [0] * 5 + [1, 2, 3, 3]
    assert all(r["result"]["error"]["category"] == "execution_capacity" for r in records[:5])
    assert all("temporarily_unavailable" not in r["result"]["error"] for r in records[:5])


@pytest.mark.parametrize("agent", ["main", "child"])
def test_capacity_rejection_of_other_signature_keeps_previous_execution_streak(workspace, monkeypatch, agent):
    from run.tools import ToolExecutionCapacityError

    records, executions, _ = run_loop(
        workspace, monkeypatch, agent,
        [expand("read")] * 2 + [expand("connect")] + [expand("read")] * 2,
        values=[{"ok": True}] * 2 + [ToolExecutionCapacityError("offline rejection"), {"ok": True}],
    )
    assert len(executions) == 4
    assert [r["consecutive_identical_calls"] for r in records] == [1, 2, 0, 3, 3]
    assert records[-1]["status"] == "identical_call_blocked"


def test_tracker_counts_real_shell_executions_in_isolation():
    tracker = ConsecutiveIdenticalToolCallTracker(3)
    execute = Mock()
    for _ in range(4):
        next_count = tracker.next_count("shell", {"action": "run", "command": "offline"})
        if tracker.is_blocked(next_count):
            continue
        tracker.record("shell", {"action": "run", "command": "offline"})
        execute()
    assert execute.call_count == tracker.count == 3


@pytest.mark.parametrize("agent", ["main", "child"])
def test_provider_retry_blocks_successful_side_effect_without_old_result(workspace, monkeypatch, agent):
    calls = [("shell", {"action": "run", "command": "offline"})] * 3
    records, executions, provider = run_loop(
        workspace, monkeypatch, agent, calls, retry=True,
        values=[{"ok": True, "private_marker": "OLD_RESULT_SENTINEL"}],
    )
    assert len(executions) == 1
    assert [r["status"] for r in records] == [
        "recovered" if agent == "child" else "completed",
        "duplicate_blocked", "duplicate_blocked",
    ]
    assert all(r["result"]["execution"]["executed"] is False for r in records[1:])
    blocked_results = json.dumps([r["result"] for r in records[1:]], ensure_ascii=False)
    assert "duplicate_blocked" in blocked_results
    assert "OLD_RESULT_SENTINEL" not in blocked_results
    if agent == "child":
        retried_context = json.dumps(provider.requests[-1].messages, ensure_ascii=False)
        assert "OLD_RESULT_SENTINEL" not in retried_context


@pytest.mark.parametrize("agent", ["main", "child"])
def test_same_response_file_writes_are_not_reexecuted(workspace, monkeypatch, agent):
    call = ("file", {"action": "write", "path": "same.txt", "content": "x"})
    records, executions, _ = run_loop(workspace, monkeypatch, agent, [call] * 2, batch=True)
    assert len(executions) == 1
    assert [r["status"] for r in records] == ["completed", "duplicate_blocked"]


@pytest.mark.parametrize("agent", ["main", "child"])
def test_failed_recovery_retains_rejection_and_visible_note(workspace, monkeypatch, agent):
    call = ("shell", {"action": "run", "command": "offline"})
    records, executions, provider = run_loop(
        workspace, monkeypatch, agent, [call] * 2,
        values=[{"ok": False, "error": "not executed again"}], retry=True,
    )
    assert len(executions) == 1
    assert records[-1]["status"] == "duplicate_blocked"
    assert records[-1]["result"]["ok"] is False
    assert records[-1]["result"]["execution"]["executed"] is False
    blocked_result = json.dumps(records[-1]["result"], ensure_ascii=False)
    assert "duplicate_blocked" in blocked_result
    assert "not executed again" not in blocked_result
    if agent == "child":
        retried_context = json.dumps(provider.requests[-1].messages, ensure_ascii=False)
        assert "duplicate_blocked" in retried_context
        assert "not executed again" not in retried_context


@pytest.mark.parametrize("agent", ["main", "child"])
def test_undeclared_expand_read_stays_live_across_provider_retry(workspace, monkeypatch, agent):
    records, executions, _ = run_loop(
        workspace, monkeypatch, agent, [expand("read")] * 2, retry=True,
    )
    assert len(executions) == 2
    assert records[-1]["result"]["result"]["sequence"] == 2


@pytest.mark.parametrize("agent", ["main", "child"])
def test_failed_expand_query_is_reexecuted_after_provider_retry(workspace, monkeypatch, agent):
    records, executions, _ = run_loop(
        workspace, monkeypatch, agent, [expand("status")] * 2, retry=True,
        values=[{"ok": False, "error": "temporary read failure"},
                {"ok": True, "status": "latest"}],
    )
    assert len(executions) == 2
    assert records[-1]["status"] == "completed"
    assert records[-1]["result"]["result"]["status"] == "latest"


def test_removed_expand_reuse_policy_is_rejected_from_module_manifest(tmp_path):
    repository = Path(__file__).resolve().parents[2]
    raw = yaml.safe_load((repository / "template/expand/module.yaml").read_text("utf-8"))
    raw["reuse_policy"] = {"reuse": ["status"]}
    (tmp_path / "module.yaml").write_text(json.dumps(raw), "utf-8")
    with pytest.raises(ModuleSpecError, match="unknown_field"):
        read_manifest(tmp_path, kind_hint="expand", scope="user")


def test_every_builtin_action_has_an_explicit_classification():
    from run.retry.tool_dedup import (
        REEXECUTE_TOOLS, REEXECUTE_ACTIONS, BLOCK_TOOLS, BLOCK_ACTIONS,
    )
    from run.tools import discover_tools
    registry = discover_tools(Path(__file__).resolve().parents[2], "not-a-real-user")
    whole_tools = REEXECUTE_TOOLS | BLOCK_TOOLS | {"expand_call"}
    for name, definition in registry.tools.items():
        if name in whole_tools:
            continue
        actions = definition.input_schema["properties"]["action"]["enum"]
        classified = set().union(*(table.get(name, ()) for table in
                                   (REEXECUTE_ACTIONS, BLOCK_ACTIONS)))
        assert set(actions) <= classified, (name, set(actions) - classified)


@pytest.mark.parametrize("agent", ["main", "child"])
def test_prior_query_result_does_not_bypass_tool_unavailability(workspace, monkeypatch, agent):
    calls = [("web_search", {"query": "cached"})]
    calls += [("web_search", {"query": f"failed-{i}"}) for i in range(5)]
    calls += [("web_search", {"query": "cached"})]
    records, executions, _ = run_loop(
        workspace, monkeypatch, agent, calls,
        values=[{"ok": True}] + [{"ok": False, "error": "offline failure"}] * 5,
    )
    assert len(executions) == 6
    assert records[-1]["status"] == "temporarily_unavailable"
    assert records[-1]["result"]["ok"] is False
