"""Budget stops use real scheduling/checkpoints and fake providers, no user data."""
from __future__ import annotations

import json
import threading

import pytest

from plugins.subagent_dispatch.tool import run as dispatch
from run.agents import AgentRunner, AgentScheduler, AgentQueueError, AgentProviderError
from run.agents.budgets import effective_limit
from run.agents.continuation import CheckpointStore, restore_items
from run.agents.controls import describe_agent, guide_task, split_task, failure_advice
from run.agents.task_ledger import TaskCoordinator
from tests.agents.test_general_worker import workspace, OUTPUT, plugin  # noqa: F401
from tests.agents.test_subagent_continuation import Provider, call, write_plugin
from provider.protocol.models import ToolCallItem, ToolResultItem


def start(root, config, provider, *, limit=None, timeout: float = 10):
    runner = AgentRunner(root, "alice", config=config, provider_factory=lambda _: provider)
    scheduler = AgentScheduler.from_runner(runner)
    parent = TaskCoordinator(root, "alice", "web", "scope", "test", {"prompt": "audit"})
    ownership = parent.ownership("dispatch")
    task_id = scheduler.submit("general_worker", {"task": "work"}, allow_sync=True,
        source="web", session_id="scope", ownership=ownership, config=config, timeout=timeout,
        max_tool_calls=limit)
    return scheduler, parent, ownership, task_id


def test_budget_stop_retains_usage_results_and_resume_gets_new_segment(workspace):
    root, config = workspace
    write_plugin(root)
    partial = {**OUTPUT, "status": "partial", "summary": "已完成前两项", "remaining": ["继续剩余项"]}
    provider = Provider([[call(value=v, index=i) for i, v in enumerate(["a", "b", "c"])], partial,
                         [call(value=v, index=i) for i, v in enumerate(["c", "d", "e"])], partial, OUTPUT])
    scheduler, parent, ownership, task_id = start(root, config, provider, limit=2)
    try:
        first = scheduler.wait(task_id, 5, source="web", session_id="scope")
        record = parent.ledger.get(task_id, "web", "scope")
        assert record["status"] == "tool_limit" and record["resumable"]
        assert first.data["summary"] == partial["summary"]
        assert record["usage"]["total_tokens"] == 6
        assert record["usage"]["provider_request_count"] == 2
        assert record["stop_reason"] == "tool_budget_exhausted"
        assert record["budget"]["tool_calls_used"] == record["budget"]["total_tool_calls"] == 2
        snapshot = CheckpointStore(root, "alice", task_id).load(source="web", session_id="scope")
        items = restore_items(snapshot)
        assert {i.call_id for i in items if isinstance(i, ToolCallItem)} == {
            i.call_id for i in items if isinstance(i, ToolResultItem)}
        assert "工具预算收尾指令" in provider.requests[1].model_dump_json()
        assert not provider.requests[1].tools
        scheduler.resume(task_id, source="web", session_id="scope", ownership=ownership, config=config)
        scheduler.wait(task_id, 5, source="web", session_id="scope")
        second = parent.ledger.get(task_id, "web", "scope")
        assert second["budget"]["tool_calls_used"] == 2
        assert second["budget"]["total_tool_calls"] == 4
        assert second["budget"]["max_tool_calls"] == 2
        assert second["budget"]["timeout"] == 10
        assert second["usage"]["total_tokens"] == 12
        assert (root / "effects.txt").read_text().splitlines() == ["a", "b", "c", "d"]
        scheduler.resume(task_id, source="web", session_id="scope", ownership=ownership,
                         max_tool_calls=0, config=config)
        assert scheduler.wait(task_id, 5, source="web", session_id="scope").data == OUTPUT
        final = parent.ledger.get(task_id, "web", "scope")
        assert final["run_count"] == 3 and final["budget"]["unlimited"]
        assert final["budget"]["total_tool_calls"] == 4
        assert final["round_history"][0]["budget"]["tool_calls_used"] == 2
        assert "工具预算收尾指令" not in provider.requests[2].model_dump_json()
        notice = parent.notifications()
        assert notice is not None and '"tool_calls":' not in notice["content"]
    finally:
        scheduler.close(wait=True, cancel_pending=True)


@pytest.mark.parametrize("limit", [0, 4])
def test_explicit_limit_overrides_defaults_and_unlimited_still_has_timeout(workspace, limit):
    root, config = workspace
    write_plugin(root)
    config["tools"]["max_iterations"] = 1
    provider = Provider([[call(value="a")], [call(value="b", index=2)], OUTPUT])
    scheduler, parent, _, task_id = start(root, config, provider, limit=limit)
    try:
        assert scheduler.wait(task_id, 5, source="web", session_id="scope").data == OUTPUT
        budget = parent.ledger.get(task_id, "web", "scope")["budget"]
        assert budget["max_tool_calls"] == limit
        assert budget["tool_calls_used"] == 2 and budget["timeout"] == 10
    finally:
        scheduler.close(wait=True, cancel_pending=True)


@pytest.mark.parametrize("value", [-1, True, 1.5, "80"])
def test_invalid_budget_rejected_before_enqueue(workspace, value):
    root, config = workspace
    runner = AgentRunner(root, "alice", config=config)
    with pytest.raises(Exception, match="max_tool_calls"):
        effective_limit(runner.registry.get("general_worker"), config, value)


def test_failed_wrap_up_keeps_latest_usage_and_is_still_resumable(workspace):
    root, config = workspace
    write_plugin(root)
    provider = Provider([[call(value="a"), call(value="b", index=2)], RuntimeError("wrap failed"), OUTPUT])
    scheduler, parent, ownership, task_id = start(root, config, provider, limit=1)
    try:
        partial = scheduler.wait(task_id, 5, source="web", session_id="scope")
        record = parent.ledger.get(task_id, "web", "scope")
        assert record["status"] == "tool_limit" and record["resumable"]
        assert record["usage"]["total_tokens"] == 3 and partial.data["status"] == "partial"
        scheduler.resume(task_id, source="web", session_id="scope", ownership=ownership)
        assert scheduler.wait(task_id, 5, source="web", session_id="scope").data == OUTPUT
        assert (root / "effects.txt").read_text().splitlines() == ["a"]
    finally:
        scheduler.close(wait=True, cancel_pending=True)


def test_execution_error_keeps_usage_but_does_not_gain_resume_authority(workspace, monkeypatch):
    root, config = workspace
    write_plugin(root)
    monkeypatch.setattr("run.agents.runner._MAX_AGENT_RETRY_ATTEMPTS", 1)
    scheduler, parent, _, task_id = start(root, config, Provider([[call(value="a")],
        AgentProviderError("bad", category="invalid_request", retryable=False)]))
    try:
        with pytest.raises(AgentQueueError):
            scheduler.wait(task_id, 5, source="web", session_id="scope")
        record = parent.ledger.get(task_id, "web", "scope")
        assert record["status"] == "failed" and not record["resumable"]
        assert record["usage"]["total_tokens"] == 3
        assert record["usage"]["provider_attempt_count"] == 2
        assert record["result"]["status"] == "partial"
        assert record["stop_reason"] == "execution_error"
    finally:
        scheduler.close(wait=True, cancel_pending=True)


def test_internal_retry_does_not_reset_segment_tool_count(workspace):
    from provider.schema import ProviderError
    root, config = workspace
    write_plugin(root)
    provider = Provider([[call(value="a")],
        ProviderError("transient", category="gateway_error", retryable=True),
        [call(value="b", index=2)], {**OUTPUT, "status": "partial"}])
    scheduler, parent, _, task_id = start(root, config, provider, limit=1)
    try:
        scheduler.wait(task_id, 5, source="web", session_id="scope")
        record = parent.ledger.get(task_id, "web", "scope")
        assert record["status"] == "tool_limit"
        assert record["budget"]["tool_calls_used"] == 1
        assert (root / "effects.txt").read_text().splitlines() == ["a"]
    finally:
        scheduler.close(wait=True, cancel_pending=True)


def test_soft_budget_reminder_and_public_list_permissions(workspace):
    root, config = workspace
    write_plugin(root)
    plugin(root, "disabled", enabled=False)
    provider = Provider([[call(value=str(i), index=i) for i in range(4)], OUTPUT])
    scheduler, parent, _, task_id = start(root, config, provider, limit=5)
    try:
        scheduler.wait(task_id, 5, source="web", session_id="scope")
        assert "接近额度" in provider.requests[1].model_dump_json()
        description = describe_agent(root, "alice", scheduler.runner.registry.get("general_worker"), config)
        assert description["max_iterations"] == 80
        assert description["authorized_plugins"] == ["write"]
        assert description["supports_resume"] and description["supports_budget_override"]
    finally:
        scheduler.close(wait=True, cancel_pending=True)


def test_running_guidance_is_scope_bound_and_delivered_at_next_request(workspace):
    root, config = workspace
    write_plugin(root)
    entered, release = threading.Event(), threading.Event()
    def blocked(_):
        entered.set()
        assert release.wait(5)
        return [call(value="a")]
    provider = Provider([blocked, OUTPUT])
    scheduler, parent, ownership, task_id = start(root, config, provider)
    try:
        assert entered.wait(3)
        with pytest.raises(Exception, match="不存在|权限"):
            guide_task(root, "alice", task_id, "web", "foreign", ownership, "bad")
        reply = guide_task(root, "alice", task_id, "web", "scope", ownership, "只整理现有结果")
        assert reply["status"] == "guidance_queued"
        release.set()
        scheduler.wait(task_id, 5, source="web", session_id="scope")
        assert "只整理现有结果" in provider.requests[1].model_dump_json()
        public = parent.ledger.public(parent.ledger.get(task_id, "web", "scope"))
        assert "_guidance" not in public
    finally:
        release.set()
        scheduler.close(wait=True, cancel_pending=True)


def test_explicit_split_preserves_origin_and_aggregate_advice(workspace):
    root, config = workspace
    write_plugin(root)
    provider = Provider([[call(value="a"), call(value="b", index=2)],
                         {**OUTPUT, "status": "partial"}, OUTPUT, OUTPUT])
    scheduler, parent, ownership, task_id = start(root, config, provider, limit=1)
    try:
        scheduler.wait(task_id, 5, source="web", session_id="scope")
        result = split_task(scheduler, task_id, source="web", session_id="scope",
            ownership=ownership, tasks=[{"task": "stage b"}, {"task": "stage c"}],
            config=config, max_tool_calls=2, timeout=10)
        assert result["accepted_count"] == 2
        for child in result["task_ids"]:
            scheduler.wait(child, 5, source="web", session_id="scope")
            assert parent.ledger.get(child, "web", "scope")["origin_task_id"] == task_id
        records = [{"id": "1", "status": "tool_limit"}, {"id": "2", "status": "tool_limit"}]
        assert failure_advice(records)[0]["count"] == 2
        assert not failure_advice(records[:1])
    finally:
        scheduler.close(wait=True, cancel_pending=True)


def test_dispatch_budget_status_and_resume_no_tool_history(workspace, monkeypatch):
    root, config = workspace
    write_plugin(root)
    provider = Provider([[call(value="a"), call(value="b", index=2)], {**OUTPUT, "status": "partial"}, OUTPUT])
    scheduler, _, ownership, task_id = start(root, config, provider, limit=1)
    monkeypatch.setattr("plugins.subagent_dispatch.tool.get_agent_scheduler", lambda *a, **k: scheduler)
    context = {"root": str(root), "user": "alice", "source": "web", "session_id": "scope", "task_ownership": ownership}
    try:
        scheduler.wait(task_id, 5, source="web", session_id="scope")
        response = dispatch("status", task_id=task_id, context=context)
        assert response["status"] == "tool_limit" and response["resumable"]
        assert response["usage"]["total_tokens"] == 6
        response = dispatch("resume", task_id=task_id, max_tool_calls=0, wait=True, context=context)
        assert response["status"] == "completed"
        assert not {"metadata", "events", "tool_calls", "arguments"} & set(response)
    finally:
        scheduler.close(wait=True, cancel_pending=True)


def test_main_receives_soft_threshold_progress_once_after_accepted_response(workspace):
    root, config = workspace
    write_plugin(root)
    entered, release = threading.Event(), threading.Event()
    def blocked(_):
        entered.set()
        assert release.wait(5)
        return OUTPUT
    provider = Provider([[call(value=str(i), index=i) for i in range(4)], blocked])
    scheduler, parent, _, task_id = start(root, config, provider, limit=5)
    try:
        assert entered.wait(3)
        first = parent.notifications()
        assert first is not None and '"budget_warning"' in first["content"]
        assert '"arguments":' not in first["content"]
        assert parent.notifications() is None
        # Failed provider IO leaves the durable warning unhandled.
        parent.progress_delivered.clear()
        assert parent.notifications() is not None
        parent.acknowledge()
        assert parent.notifications() is None
        release.set()
        scheduler.wait(task_id, 5, source="web", session_id="scope")
    finally:
        release.set()
        scheduler.close(wait=True, cancel_pending=True)


def test_unlimited_tools_still_stops_at_runtime_deadline(workspace):
    import time
    root, config = workspace
    def slow(_):
        time.sleep(.35)
        return OUTPUT
    scheduler, parent, _, task_id = start(root, config, Provider([slow]), limit=0, timeout=.15)
    try:
        scheduler.wait(task_id, 5, source="web", session_id="scope")
        record = parent.ledger.get(task_id, "web", "scope")
        assert record["status"] == "timed_out" and record["resumable"]
        assert record["budget"]["unlimited"]
    finally:
        scheduler.close(wait=True, cancel_pending=True)


def test_production_runtime_budget_includes_provider_initialization(workspace):
    import time
    from run.agents import AgentTimeoutError
    root, config = workspace
    provider = Provider([OUTPUT])
    def slow_factory(_):
        time.sleep(.3)
        return provider
    runner = AgentRunner(root, "alice", config=config, provider_factory=slow_factory)
    with pytest.raises(AgentTimeoutError):
        runner.run("general_worker", {"task": "work"}, timeout=.05,
                   timeout_survival_seconds=0, max_tool_calls=0)
    assert not provider.requests


def test_main_model_receives_budget_conclusion_and_resumes_without_replaying(workspace, monkeypatch):
    import shutil
    from pathlib import Path
    from run.engine import iter_request_events
    from run.agents.task_ledger import TaskLedger
    root, config = workspace
    write_plugin(root)
    directory = root / "plugins/subagent_dispatch"
    directory.mkdir()
    shutil.copyfile(Path(__file__).resolve().parents[2] / "plugins/subagent_dispatch/SKILL.md",
                    directory / "SKILL.md")
    (directory / "tool.py").write_text("from plugins.subagent_dispatch.tool import run\n", "utf-8")
    partial = {**OUTPUT, "status": "partial", "summary": "first 已确认，剩余 second"}
    child = Provider([[call(), call(value="second", index=2)], partial,
                      [call(value="second", index=3)], OUTPUT])
    scheduler = AgentScheduler.from_runner(AgentRunner(root, "alice", config=config,
                                                     provider_factory=lambda _: child))
    monkeypatch.setattr("plugins.subagent_dispatch.tool.get_agent_scheduler", lambda *a, **k: scheduler)
    observed = {}
    def resume(request):
        records, _ = TaskLedger(root, "alice").records("web", "scope", parent=None)
        record = next(r for r in records if r["agent"] == "general_worker")
        if record["status"] == "running":
            assert '"budget_warning"' in request.model_dump_json() or "budget_warning" in request.model_dump_json()
            main.steps.insert(0, resume)
            return {"draft": "budget warning received"}
        assert partial["summary"] in request.model_dump_json()
        assert record["status"] == "tool_limit" and record["resumable"]
        observed["id"] = record["id"]
        return [ToolCallItem(id="call_resume", call_id="callid_resume", name="subagent_dispatch",
            arguments={"action": "resume", "task_id": record["id"], "wait": True,
                       "max_tool_calls": 2, "timeout": 2})]
    main = Provider([
        [ToolCallItem(id="call_dispatch", call_id="callid_dispatch", name="subagent_dispatch",
            arguments={"action": "call", "agent": "general_worker", "input": {"task": "work"},
                       "max_tool_calls": 1, "timeout": 10, "wait": False})],
        {"draft": "awaiting worker"}, resume, {"answer": "merged"},
    ])
    try:
        events = list(iter_request_events(
            {"user": "alice", "source": "web", "session_id": "scope", "prompt": "do work"},
            root=root, provider_factory=lambda _: main))
        assert any(e.type == "done" for e in events)
        record = TaskLedger(root, "alice").get(observed["id"], "web", "scope")
        assert record["status"] == "completed" and record["run_count"] == 2 and record["handled"]
        assert record["budget"]["total_tool_calls"] == 2
        assert (root / "effects.txt").read_text().splitlines() == ["first", "second"]
        assert 4 <= len(main.requests) <= 5
    finally:
        scheduler.close(wait=True, cancel_pending=True)


def test_legacy_tool_limit_failure_can_resume_only_with_valid_matching_checkpoint(workspace):
    root, config = workspace
    write_plugin(root)
    provider = Provider([[call(value="a"), call(value="b", index=2)], {**OUTPUT, "status": "partial"}, OUTPUT])
    scheduler, parent, ownership, task_id = start(root, config, provider, limit=1)
    try:
        scheduler.wait(task_id, 5, source="web", session_id="scope")
        # Recreate the exact old failure envelope without touching any real user.
        scheduler._tasks[task_id].status = "failed"
        scheduler._tasks[task_id].resumable = False
        parent.ledger.update(task_id, status="failed", resumable=False,
            error={"exception_type": "AgentToolLimitError", "message": "old limit"})
        store = CheckpointStore(root, "alice", task_id)
        data = store.load(source="web", session_id="scope")
        data.pop("budget", None)
        store.write(data)
        scheduler.resume(task_id, source="web", session_id="scope", ownership=ownership,
                         config=config, max_tool_calls=2)
        scheduler.wait(task_id, 5, source="web", session_id="scope")
        record = parent.ledger.get(task_id, "web", "scope")
        assert record["status"] == "completed" and record["budget"]["total_tool_calls"] == 1
        assert (root / "effects.txt").read_text().splitlines() == ["a"]
    finally:
        scheduler.close(wait=True, cancel_pending=True)


def test_invalid_resume_budget_and_split_batch_do_not_mutate_stopped_task(workspace):
    root, config = workspace
    write_plugin(root)
    provider = Provider([[call(value="a"), call(value="b", index=2)], {**OUTPUT, "status": "partial"}])
    scheduler, parent, ownership, task_id = start(root, config, provider, limit=1)
    try:
        scheduler.wait(task_id, 5, source="web", session_id="scope")
        with pytest.raises(Exception, match="max_tool_calls"):
            scheduler.resume(task_id, source="web", session_id="scope", ownership=ownership, max_tool_calls=-1)
        with pytest.raises(Exception):
            split_task(scheduler, task_id, source="web", session_id="scope", ownership=ownership,
                       tasks=[{"task": "valid"}, {"wrong": "invalid"}], config=config)
        record = parent.ledger.get(task_id, "web", "scope")
        assert record["status"] == "tool_limit" and record["run_count"] == 1 and record["resumable"]
        assert len(parent.children()) == 1
    finally:
        scheduler.close(wait=True, cancel_pending=True)


def test_synchronous_failed_call_returns_partial_usage_without_claiming_success(workspace, monkeypatch):
    root, config = workspace
    write_plugin(root)
    monkeypatch.setattr("run.agents.runner._MAX_AGENT_RETRY_ATTEMPTS", 1)
    provider = Provider([[call(value="a")], AgentProviderError("business error", retryable=False)])
    scheduler = AgentScheduler.from_runner(AgentRunner(root, "alice", config=config,
                                                     provider_factory=lambda _: provider))
    parent = TaskCoordinator(root, "alice", "web", "scope", "sync", {"prompt": "work"})
    ownership = parent.ownership("call")
    monkeypatch.setattr("plugins.subagent_dispatch.tool.get_agent_scheduler", lambda *a, **k: scheduler)
    context = {"root": str(root), "user": "alice", "source": "web",
               "session_id": "scope", "task_ownership": ownership}
    try:
        response = dispatch("call", agent="general_worker", input={"task": "work"},
                            wait=True, timeout=10, context=context)
        assert response["status"] == "failed" and response["ok"] is False
        assert response["data"]["status"] == "partial"
        assert response["usage"]["total_tokens"] == 3
        assert response["usage"]["provider_attempt_count"] == 2
        assert response["stop_reason"] == "execution_error"
    finally:
        scheduler.close(wait=True, cancel_pending=True)
