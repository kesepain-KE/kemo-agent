"""The child uses real SQLite; Providers are local fixtures with inspected wire input."""
import json
from datetime import datetime, timezone

import pytest

from provider.protocol.models import (
    JsonContent, MessageItem, ProviderState, ReasoningItem, ToolCallItem, ToolResultItem, text_from_content,
)
from run.agents import AgentRunner
from run.agents.card_stream import SOURCE, agent_store, read_checkpoint
from run.agents.continuation import CheckpointStore
from run.cards import CardStore
from tests.agents.test_general_worker import OUTPUT, workspace  # noqa: F401
from tests.agents.test_subagent_continuation import Provider, call, legacy_task, write_plugin
from tests.type_assertions import require_type


def test_child_projects_native_call_order_and_tool_results_from_private_store(workspace, monkeypatch):
    root, config = workspace
    write_plugin(root)
    task_id = "agent-task-" + "2" * 32
    store = agent_store(root, "alice", task_id)
    token = "fixture-credential-not-for-disk"
    values = [json.dumps({"api_key": token}), "second"]
    reasoning = "fixture-reasoning-not-for-disk"
    first = [
        ReasoningItem(id="rs_fixture", summary=reasoning),
        MessageItem.text("assistant", "阶段说明", phase="commentary"),
        *[call(value=value, index=i) for i, value in enumerate(values)],
    ]
    calls = []
    executed_states = []

    def execute(_, arguments, **kwargs):
        with store.db() as db:
            executed_states.append([row[0] for row in db.execute(
                "SELECT status FROM card_tool_executions ORDER BY rowid")])
        calls.append(arguments["value"])
        return {"ok": True, "value": arguments["value"], "api_key": token}

    def verify(request):
        items = request.input
        assert [i.type for i in items] == [
            "message", "reasoning", "message",
            "tool_call", "tool_call", "tool_result", "tool_result",
        ]
        assert json.loads(text_from_content(require_type(items[0], MessageItem).content)) == {
            "task": "检查中文输入",
        }
        assert require_type(items[1], ReasoningItem).summary == reasoning
        assert require_type(items[2], MessageItem).phase == "commentary"
        called = [require_type(item, ToolCallItem) for item in items[3:5]]
        assert [item.arguments for item in called] == [{"value": value} for value in values]
        for i, item in enumerate(items[5:]):
            result = require_type(item, ToolResultItem)
            assert result.call_id == called[i].call_id and not result.is_error
            assert require_type(result.content[0], JsonContent).data == {
                "ok": True, "result": {"ok": True, "value": values[i], "api_key": token},
            }
        return OUTPUT

    monkeypatch.setattr("run.agents.model_loop.execute_tool", execute)
    provider = Provider([first, verify])
    result = AgentRunner(root, "alice", config=config, provider_factory=lambda _: provider).run(
        "general_worker", {"task": "检查中文输入"}, task_id=task_id,
        source="web", session_id="parent", timeout=10,
    )
    assert result.data == OUTPUT
    initial_budget = require_type(provider.requests[0].input[0], MessageItem)
    assert "本段工具额度" in text_from_content(initial_budget.content)
    assert calls == values
    assert executed_states == [["running", "planned"], ["completed", "running"]]
    assert not CardStore(root, "alice").path.exists()
    assert not CheckpointStore(root, "alice", task_id).path.exists()
    assert store.path == root / "users/alice/runtime/subagent_stores" / task_id / "cards.sqlite3"
    raw = store.raw_page(SOURCE, task_id)
    assert [card.raw_kind for card in raw] == [
        "user_text", "agent_text", "tool_call", "tool_call", "tool_result", "tool_result", "agent_text",
    ]
    snapshot = store.snapshot(SOURCE, task_id)
    assert snapshot.view_epoch == 0 and len(snapshot.cards) == len(raw)
    saved = read_checkpoint(store, task_id, 1)
    assert saved["last_seq"] == raw[-1].seq and saved["generation"] == snapshot.generation
    assert saved["version"] == 2 and "items" not in saved
    encoded = "\n".join(card.raw_payload_json for card in raw)
    assert all(text not in encoded for text in (token, reasoning, "本段工具额度"))
    assert "[redacted]" in encoded


def test_failed_response_registration_rolls_back_before_tool_runs(workspace, monkeypatch):
    from run.cards import IntegrationStore

    root, config = workspace
    write_plugin(root)
    task_id = "agent-task-" + "3" * 32
    provider = Provider([[MessageItem.text("assistant", "未提交"), call(value="never")]])

    def fail_registration(self, *args, **kwargs):
        raise OSError("fixture: SQLite write failed")

    monkeypatch.setattr(IntegrationStore, "register_tool", fail_registration)
    with pytest.raises(OSError, match="SQLite write failed"):
        AgentRunner(root, "alice", config=config, provider_factory=lambda _: provider).run(
            "general_worker", {"task": "work"}, task_id=task_id,
            source="web", session_id="parent", timeout=10,
        )
    raw = agent_store(root, "alice", task_id).raw_page(SOURCE, task_id)
    assert [card.raw_kind for card in raw] == ["user_text"]
    assert not (root / "effects.txt").exists()


def test_replayed_tool_rounds_keep_their_valid_reasoning(workspace, monkeypatch):
    root, config = workspace
    write_plugin(root)
    task_id = "agent-task-" + "5" * 32
    monkeypatch.setattr("run.agents.model_loop.execute_tool", lambda *a, **kw: {"ok": True})

    def turn(i):
        def reply(request):
            state = ProviderState(kind="opaque", provider="fixture", model=request.model,
                                  data=f"opaque-step-{i}")
            return [
                ReasoningItem(id="rs_valid", summary=f"private-step-{i}", provider_state=state),
                ReasoningItem(id="rs_expired", provider_state=state.model_copy(update={
                    "data": "stale-opaque", "expires_at": datetime(2000, 1, 1, tzinfo=timezone.utc),
                })),
                ReasoningItem(id="rs_wrong_model", provider_state=state.model_copy(update={
                    "data": "wrong-model-opaque", "model": "other-model",
                })),
                call(value=str(i), index=i),
            ]
        return reply

    provider = Provider([*(turn(i) for i in range(8)), OUTPUT])
    runner = AgentRunner(root, "alice", config=config, provider_factory=lambda _: provider)
    assert runner.run("general_worker", {"task": "work"}, timeout=10, max_tool_calls=8,
                      task_id=task_id, source="web", session_id="parent").data == OUTPUT
    assert len(provider.requests) == 9
    for i, request in enumerate(provider.requests):
        reasoning = [item for item in request.input if isinstance(item, ReasoningItem)]
        states = []
        for item in reasoning:
            state, summary = item.provider_state, item.summary
            assert state is not None and summary is not None
            states.append(state.data)
            following = request.input[request.input.index(item) + 1]
            assert isinstance(following, ToolCallItem)
            assert following.arguments["value"] == summary.removeprefix("private-step-")
        assert states == [f"opaque-step-{j}" for j in range(i)]
    assert [sum("msg_budget_" in item.id for item in request.input)
            for request in provider.requests] == [1, 0, 0, 0, 0, 0, 0, 1, 0]
    raw = agent_store(root, "alice", task_id).raw_page(SOURCE, task_id)
    assert not any("private-step-" in card.raw_payload_json or "opaque-" in card.raw_payload_json
                   for card in raw)


def test_child_commits_consumed_shell_receipt_once_without_parent_history(workspace):
    from run.tools.background_jobs import read_background_job
    from tests.core.test_shell_completion_notifications import job

    root, config = workspace
    write_plugin(root)
    task_id = "agent-task-" + "4" * 32
    job_id = job(root, session="parent", owner=task_id)

    def script(request):
        body = request.model_dump_json()
        assert body.count(job_id) == 1
        return OUTPUT

    provider = Provider([[call(value="second round")], script])
    runner = AgentRunner(root, "alice", config=config, provider_factory=lambda _: provider)
    assert runner.run("general_worker", {"task": "work"}, task_id=task_id,
                      source="web", session_id="parent", timeout=10).data == OUTPUT
    assert read_background_job(root, "alice", job_id)["notification_handled"]
    raw = agent_store(root, "alice", task_id).raw_page(SOURCE, task_id)
    assert [card.raw_payload["job_ids"] for card in raw if "job_ids" in card.raw_payload] == [[job_id]]
    assert not CardStore(root, "alice").path.exists()


def test_zero_manifest_budget_projects_200_tools_without_stopping(workspace, monkeypatch):
    root, config = workspace
    write_plugin(root)
    manifest = root / "agents/general_worker/agent-config.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["tools"]["max_iterations"] = 0
    manifest.write_text(json.dumps(data), encoding="utf-8")
    count = 0

    def execute(*args, **kwargs):
        nonlocal count
        count += 1
        return {"ok": True}

    monkeypatch.setattr("run.agents.model_loop.execute_tool", execute)
    provider = Provider([[call(value=str(i), index=i) for i in range(200)], OUTPUT])
    # 200 dispatches plus their CardStream commits take far longer than the old
    # 30s cap on a loaded Windows runner. The scenario is "unlimited budget does
    # not stop", not "finishes inside 30 seconds".
    result = AgentRunner(root, "alice", config=config, provider_factory=lambda _: provider).run(
        "general_worker", {"task": "work"}, timeout=180,
    )
    assert result.data == OUTPUT and count == 200
    assert result.metadata["budget"]["unlimited"]
    assert result.metadata["budget"]["tool_calls_remaining"] is None
    items = provider.requests[1].input
    assert len([item for item in items if isinstance(item, ToolCallItem)]) == 200
    assert len([item for item in items if isinstance(item, ToolResultItem)]) == 200


def test_second_resume_reuses_v2_cards_without_importing_v1_again(workspace):
    root, config = workspace
    write_plugin(root)
    provider = Provider([[call(value="a"), call(value="b", index=2)],
                         {**OUTPUT, "status": "partial"}, OUTPUT])
    scheduler, parent, ownership, task_id, old = legacy_task(root, config, provider, [])
    store = agent_store(root, "alice", task_id)
    original = old.path.read_bytes()
    try:
        scheduler.resume(task_id, source="web", session_id="scope", ownership=ownership,
                         timeout=10, max_tool_calls=1)
        scheduler.wait(task_id, 15, source="web", session_id="scope")
        assert parent.ledger.get(task_id, "web", "scope")["status"] == "tool_limit"
        first = store.snapshot(SOURCE, task_id)
        scheduler.resume(task_id, source="web", session_id="scope", ownership=ownership,
                         timeout=10, max_tool_calls=0)
        assert scheduler.wait(task_id, 15, source="web", session_id="scope").data == OUTPUT
        assert old.path.read_bytes() == original
        second = store.snapshot(SOURCE, task_id)
        assert second.cards[:len(first.cards)] == first.cards and second.generation == first.generation
        assert len([card for card in second.cards if card.raw_kind == "user_text"]) == 1
        assert parent.ledger.get(task_id, "web", "scope")["run_count"] == 3
        assert (root / "effects.txt").read_text().splitlines() == ["a"]
    finally:
        scheduler.close(wait=True, cancel_pending=True)


def test_unexported_large_result_keeps_success_without_a_parent_history_reference(workspace, monkeypatch):
    root, config = workspace
    write_plugin(root)
    count = 0

    def execute(*args, **kwargs):
        nonlocal count
        count += 1
        return {"ok": True, "text": "x" * 120_000}

    def export(*args, **kwargs):
        raise OSError("fixture: export unavailable")

    def verify(request):
        result, = [item for item in request.input if isinstance(item, ToolResultItem)]
        body = require_type(result.content[0], JsonContent).data
        assert not result.is_error and body["ok"] and body["content_omitted"]
        assert "raw_card_id" not in body and "不要" in body["instruction"]
        return OUTPUT

    monkeypatch.setattr("run.agents.model_loop.execute_tool", execute)
    monkeypatch.setattr("run.tools.result_limits.save_result_artifact", export)
    provider = Provider([[call(value="large")], verify])
    assert AgentRunner(root, "alice", config=config, provider_factory=lambda _: provider).run(
        "general_worker", {"task": "work"}, timeout=10,
    ).data == OUTPUT
    assert count == 1
