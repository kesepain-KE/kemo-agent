"""R1 2.2: explicit success contracts, without network or real user data."""
from __future__ import annotations

import copy
import json

import pytest

from plugins.expand_creater.tool import run as expand_creator
from plugins.get_current_time.tool import run as current_time
from plugins.history_search.tool import run as history
from plugins.memory_manage.tool import run as memory
from plugins.sense_creater.tool import run as sense_creator
from plugins.skill_creater.tool import run as skill_creator
from provider.schema import ChatResponse, ToolCall
from provider.protocol.models import JsonContent, ToolResultItem
from run.agents import AgentRunner
from run.engine import iter_request_events
from run.infra import runtime_event_snapshot
from run.tools import MAX_TOOL_RESULT_CHARS, ToolDefinition, ToolRegistry
from tests.media_input_support import MediaProvider, make_root
from tests.agents.test_general_worker import OUTPUT, plugin, workspace  # noqa: F401
from tests.agents.test_subagent_hotplug import ScriptedProvider


def run_results(root, value, *, repeats=2):
    make_root(root)
    executions = []

    def invoke(**kwargs):
        executions.append(1)
        return copy.deepcopy(value)

    tool = ToolDefinition(
        name="uncontracted", description="unknown plugin result",
        input_schema={"type": "object"}, version="1", enabled=True,
        entrypoint="tool.py:run", source="test", directory=root, _callable=invoke, execution_mode="thread",
    )
    provider = MediaProvider([
        ChatResponse(text="", tool_calls=[ToolCall(
            id=f"call-{number}", name=tool.name, arguments={},
        )]) for number in range(repeats)
    ] + [ChatResponse(text="done")])
    events = list(iter_request_events(
        {"user": "alice", "source": "web", "session_id": "contract", "prompt": "work"},
        root=root, provider_factory=lambda _: provider,
        tool_registry_factory=lambda *args: ToolRegistry({tool.name: tool}),
    ))
    return [event for event in events if event.type == "tool_call_result"], executions, provider


@pytest.mark.parametrize("value", [{"answer": 42}, "legacy success", ["legacy"], {"ok": 1}])
def test_only_boolean_true_is_success_and_missing_contract_is_not_cached(tmp_path, value):
    results, executions, _ = run_results(tmp_path, value)
    assert len(executions) == 2
    assert [result.metadata["status"] for result in results] == ["failed", "failed"]
    assert all(result.result["ok"] is False for result in results)
    if isinstance(value, dict) and "ok" in value:
        return
    diagnostics = runtime_event_snapshot(tmp_path, "alice")
    assert any(
        event["title"] == "tool_result_missing_ok" and event.get("tool_name") == "uncontracted"
        for event in diagnostics
    )


def test_explicit_failure_stays_failure_and_counts_towards_failure_limit(tmp_path):
    results, executions, _ = run_results(tmp_path, {"ok": False, "error": "business failed"})
    assert len(executions) == 2
    assert all(event.metadata["status"] == "failed" for event in results)
    assert all(event.result["ok"] is False for event in results)


@pytest.mark.parametrize("flag", ["cached", "duplicate"])
def test_plugin_cache_flags_have_a_model_visible_explanation(tmp_path, flag):
    results, _, provider = run_results(tmp_path, {"ok": True, flag: True}, repeats=1)
    assert results[0].result["ok"] is True
    note = results[0].result["execution"]["note"]
    assert flag in note
    assert note in provider.requests[-1].model_dump_json()


def test_oversized_uncontracted_result_cannot_be_promoted_to_success(tmp_path):
    results, _, _ = run_results(tmp_path, {"content": "X" * MAX_TOOL_RESULT_CHARS}, repeats=1)
    assert results[0].result["ok"] is False
    assert results[0].result["result"]["content"] == "X" * MAX_TOOL_RESULT_CHARS


@pytest.mark.parametrize("invoke", [
    lambda ctx: current_time(),
    lambda ctx: expand_creator("list", "user", context=ctx),
    lambda ctx: sense_creator("list", context=ctx),
    lambda ctx: skill_creator("list", "user_create", context=ctx),
    lambda ctx: memory("list", "seven_days", context=ctx),
    lambda ctx: memory("search_by_title", "seven_days", query="nothing", context=ctx),
    lambda ctx: history(action="dates", context=ctx),
    lambda ctx: history(query="", context=ctx),
])
def test_builtin_successful_read_paths_have_explicit_ok(tmp_path, invoke):
    make_root(tmp_path)
    result = invoke({"root": str(tmp_path), "user": "alice"})
    assert result.get("ok") is True


@pytest.mark.parametrize("invoke", [
    lambda ctx: expand_creator("validate", "user", name="missing", context=ctx),
    lambda ctx: sense_creator("validate", name="missing", context=ctx),
    lambda ctx: skill_creator("validate", "user_create", name="missing", context=ctx),
])
def test_builtin_validation_failure_has_explicit_false(tmp_path, invoke):
    make_root(tmp_path)
    result = invoke({"root": str(tmp_path), "user": "alice"})
    assert result["valid"] is False
    assert result.get("ok") is False


@pytest.mark.parametrize("value", [{"answer": 42}, {"ok": False, "error": "business failed"}, {"ok": True}])
def test_child_loop_uses_the_same_explicit_success_contract(workspace, value):
    root, config = workspace
    plugin(root, "report")
    (root / "plugins/report/tool.py").write_text(
        f"def run(*, context): return {value!r}\n", encoding="utf-8",
    )
    provider = ScriptedProvider([
        ChatResponse(text="", tool_calls=[ToolCall(id="child-call", name="report", arguments={})]),
        ChatResponse(text=json.dumps(OUTPUT)),
    ])
    result = AgentRunner(root, "alice", config=config, provider_factory=lambda _: provider).run(
        "general_worker", {"task": "report"}, source="web", session_id="child-contract",
    )
    record = result.metadata["tool_calls"][0]
    expected_ok = value.get("ok") is True
    assert record["result"]["ok"] is expected_ok
    assert record["status"] == ("completed" if expected_ok else "failed")


def test_child_final_boundary_includes_normalization_envelope(workspace):
    root, config = workspace
    plugin(root, "report")
    content = "X" * (MAX_TOOL_RESULT_CHARS - len(json.dumps({"ok": True, "content": ""})))
    (root / "plugins/report/tool.py").write_text(
        f"def run(*, context): return {{'ok': True, 'content': 'X' * {len(content)}}}\n",
        encoding="utf-8",
    )
    provider = MediaProvider([
        ChatResponse(text="", tool_calls=[ToolCall(id="child-call", name="report", arguments={})]),
        ChatResponse(text=json.dumps(OUTPUT)),
    ])
    result = AgentRunner(root, "alice", config=config, provider_factory=lambda _: provider).run(
        "general_worker", {"task": "report"}, source="web", session_id="child-limit",
    )
    assert result.metadata["tool_calls"][0]["result"]["result"]["content"] == content
    item = next(item for item in provider.requests[-1].input if isinstance(item, ToolResultItem))
    assert isinstance(item.content[0], JsonContent)
    receipt = item.content[0].data
    assert len(json.dumps(receipt, ensure_ascii=False)) <= MAX_TOOL_RESULT_CHARS
    assert receipt["ok"] is True and receipt["content_omitted"]
    artifact = json.loads((root / receipt["artifact"]["path"]).read_text("utf-8"))
    assert artifact["result"]["content"] == content
