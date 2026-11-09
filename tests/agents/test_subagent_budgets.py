"""Budget stops use real scheduling/checkpoints and fake providers, no user data."""
from __future__ import annotations

from provider.protocol.models import IncompleteDetails
import threading

import pytest

from plugins.subagent_dispatch.tool import run as dispatch
from provider.protocol.enums import IncompleteReason, ResponseStatus
from provider.protocol.models import KemoResponse, Usage
from run.agents import AgentRunner, AgentScheduler, AgentQueueError, AgentProviderError
from run.agents.budgets import effective_limit
from run.agents.continuation import CheckpointStore, restore_items
from run.agents.controls import describe_agent, guide_task, split_task, failure_advice
from run.agents import TaskCoordinator
from tests.agents.test_general_worker import workspace, OUTPUT, plugin  # noqa: F401
from tests.agents.test_subagent_continuation import Provider, call, write_plugin
from provider.protocol.models import ToolCallItem, ToolResultItem


TASK_TIMEOUT = 10
# Completion waits also cover scheduling, agent setup and checkpoint finalization.
TASK_WAIT_TIMEOUT = TASK_TIMEOUT + 5


def start(root, config, provider, *, limit=None, timeout: float = TASK_TIMEOUT):
    runner = AgentRunner(root, "alice", config=config, provider_factory=lambda _: provider)
    scheduler = AgentScheduler.from_runner(runner)
    parent = TaskCoordinator(root, "alice", "web", "scope", "test", {"prompt": "audit"})
    ownership = parent.ownership("dispatch")
    task_id = scheduler.submit("general_worker", {"task": "work"}, allow_sync=True,
        source="web", session_id="scope", ownership=ownership, config=config, timeout=timeout,
        max_tool_calls=limit)
    return scheduler, parent, ownership, task_id


@pytest.mark.parametrize('parent_status', ['running', 'waiting_children'])
def test_budget_stop_retains_usage_results_and_resume_gets_new_segment(workspace, parent_status):
    root, config = workspace
    write_plugin(root)
    partial = {**OUTPUT, "status": "partial", "summary": "已完成前两项", "remaining": ["继续剩余项"]}
    provider = Provider([[call(value=v, index=i) for i, v in enumerate(["a", "b", "c"])], partial,
                         [call(value=v, index=i) for i, v in enumerate(["c", "d", "e"])], partial, OUTPUT])
    scheduler, parent, ownership, task_id = start(root, config, provider, limit=2)
    try:
        first = scheduler.wait(task_id, TASK_WAIT_TIMEOUT, source="web", session_id="scope")
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
        parent.ledger.update(parent.id, status=parent_status)
        scheduler.resume(task_id, source="web", session_id="scope", ownership=ownership, config=config)
        scheduler.wait(task_id, TASK_WAIT_TIMEOUT, source="web", session_id="scope")
        second = parent.ledger.get(task_id, "web", "scope")
        assert second["budget"]["tool_calls_used"] == 2
        assert second["budget"]["total_tool_calls"] == 4
        assert second["budget"]["max_tool_calls"] == 2
        assert second["budget"]["timeout"] == 10
        assert second["usage"]["total_tokens"] == 12
        assert (root / "effects.txt").read_text().splitlines() == ["a", "b", "c", "d"]
        scheduler.resume(task_id, source="web", session_id="scope", ownership=ownership,
                         max_tool_calls=0, config=config)
        assert scheduler.wait(task_id, TASK_WAIT_TIMEOUT, source="web", session_id="scope").data == OUTPUT
        final = parent.ledger.get(task_id, "web", "scope")
        assert final["run_count"] == 3 and final["budget"]["unlimited"]
        assert final["budget"]["total_tool_calls"] == 4
        assert final["round_history"][0]["budget"]["tool_calls_used"] == 2
        assert "工具预算收尾指令" not in provider.requests[2].model_dump_json()
        notice = parent.notifications()
        assert notice is not None and '"tool_calls":' not in notice["content"]
    finally:
        scheduler.close(wait=True, cancel_pending=True)


@pytest.mark.parametrize("limit", [None, 0, 4])
def test_segment_budget_is_independent_of_retired_global_limit(workspace, limit):
    root, config = workspace
    write_plugin(root)
    config["tools"]["max_iterations"] = 1
    provider = Provider([[call(value="a")], [call(value="b", index=2)], OUTPUT])
    scheduler, parent, _, task_id = start(root, config, provider, limit=limit)
    try:
        assert scheduler.wait(task_id, TASK_WAIT_TIMEOUT, source="web", session_id="scope").data == OUTPUT
        budget = parent.ledger.get(task_id, "web", "scope")["budget"]
        assert budget["max_tool_calls"] == (80 if limit is None else limit)
        assert budget["tool_calls_used"] == 2 and budget["timeout"] == 10
    finally:
        scheduler.close(wait=True, cancel_pending=True)


@pytest.mark.parametrize("value", [-1, True, 1.5, "80"])
def test_invalid_budget_rejected_before_enqueue(workspace, value):
    root, config = workspace
    runner = AgentRunner(root, "alice", config=config)
    with pytest.raises(Exception, match="max_tool_calls"):
        effective_limit(runner.registry.get("general_worker"), value)


def test_failed_wrap_up_keeps_latest_usage_and_is_still_resumable(workspace):
    root, config = workspace
    write_plugin(root)
    provider = Provider([[call(value="a"), call(value="b", index=2)], RuntimeError("wrap failed"), OUTPUT])
    scheduler, parent, ownership, task_id = start(root, config, provider, limit=1)
    try:
        partial = scheduler.wait(task_id, TASK_WAIT_TIMEOUT, source="web", session_id="scope")
        record = parent.ledger.get(task_id, "web", "scope")
        assert record["status"] == "tool_limit" and record["resumable"]
        assert record["usage"]["total_tokens"] == 3 and partial.data["status"] == "partial"
        scheduler.resume(task_id, source="web", session_id="scope", ownership=ownership)
        assert scheduler.wait(task_id, TASK_WAIT_TIMEOUT, source="web", session_id="scope").data == OUTPUT
        assert (root / "effects.txt").read_text().splitlines() == ["a"]
    finally:
        scheduler.close(wait=True, cancel_pending=True)


def test_execution_error_keeps_usage_but_does_not_gain_resume_authority(workspace, monkeypatch):
    root, config = workspace
    write_plugin(root)
    monkeypatch.setattr("run.agents.runner._MAX_AGENT_RETRY_ATTEMPTS", 1)
    scheduler, parent, ownership, task_id = start(root, config, Provider([[call(value="a")],
        AgentProviderError("bad", category="invalid_request", retryable=False)]))
    try:
        with pytest.raises(AgentQueueError):
            scheduler.wait(task_id, TASK_WAIT_TIMEOUT, source="web", session_id="scope")
        record = parent.ledger.get(task_id, "web", "scope")
        assert record["status"] == "failed" and not record["resumable"]
        assert record["usage"]["total_tokens"] == 3
        assert record["usage"]["provider_attempt_count"] == 2
        assert record["result"]["status"] == "partial"
        assert record["stop_reason"] == "execution_error"
        with pytest.raises(AgentQueueError):
            scheduler.resume(task_id, source="web", session_id="scope", ownership=ownership)
    finally:
        scheduler.close(wait=True, cancel_pending=True)


@pytest.mark.parametrize("truncations", [1, 6])
@pytest.mark.parametrize("category", [None, "output_truncated"])
def test_output_truncation_recovers_without_replaying_tools(
    workspace, monkeypatch, truncations, category,
):
    root, config = workspace
    write_plugin(root)
    monkeypatch.setattr("run.agents.runner._agent_retry_delay_seconds", lambda *_: 0.0)

    class TruncatedProvider(Provider):
        def create(self, request):
            if 1 <= len(self.requests) <= truncations:
                self.requests.append(request)
                return KemoResponse(
                    request_id=request.request_id,
                    model=request.model,
                    status=ResponseStatus.INCOMPLETE,
                    output=[call(value="must_not_run", index=99)],
                    incomplete_details=IncompleteDetails.model_validate({
                        "reason": IncompleteReason.OUTPUT_TRUNCATED,
                        "details": {"finish_reason": "length", "category": category},
                    }),
                    usage=Usage(input_tokens=2, output_tokens=0, total_tokens=2),
                )
            return super().create(request)

    provider = TruncatedProvider([[call(value="once")], OUTPUT])
    scheduler, parent, ownership, task_id = start(root, config, provider)
    try:
        if truncations == 6:
            with pytest.raises(AgentQueueError, match="output_truncated"):
                scheduler.wait(task_id, TASK_WAIT_TIMEOUT, source="web", session_id="scope")

            record = parent.ledger.get(task_id, "web", "scope")
            assert record["status"] == "failed" and record["resumable"]
            assert record["error"]["code"] == "output_truncated"
            assert record["error"]["retry_budget_exhausted"] is True
            assert record["error"]["process_terminated"] is True
            assert record["result"]["status"] == "partial"
            saved = CheckpointStore(root, "alice", task_id).load(
                source="web", session_id="scope",
            )
            items = restore_items(saved)
            completed_results = [
                item for item in items
                if isinstance(item, ToolResultItem) and item.name == "write"
            ]
            assert len(completed_results) == 1 and not completed_results[0].is_error

            legacy_error = dict(record["error"])
            legacy_error.pop("process_terminated")
            parent.ledger.update(task_id, resumable=False, error=legacy_error)
            scheduler.resume(task_id, source="web", session_id="scope", ownership=ownership)
        assert scheduler.wait(task_id, TASK_WAIT_TIMEOUT, source="web", session_id="scope").data == OUTPUT
        record = parent.ledger.get(task_id, "web", "scope")
        assert record["status"] == "completed"
        assert record["usage"]["provider_attempt_count"] == truncations + 2
        assert record["usage"]["total_tokens"] == 6 + 2 * truncations
        assert record["budget"]["total_tool_calls"] == 1
        assert record["run_count"] == (2 if truncations == 6 else 1)
        assert (root / "effects.txt").read_text().splitlines() == ["once"]
        assert len(provider.requests) == truncations + 2
        assert "must_not_run" not in provider.requests[-1].model_dump_json()
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
        scheduler.wait(task_id, TASK_WAIT_TIMEOUT, source="web", session_id="scope")
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
        scheduler.wait(task_id, TASK_WAIT_TIMEOUT, source="web", session_id="scope")
        assert "接近额度" in provider.requests[1].model_dump_json()
        description = describe_agent(root, "alice", scheduler.runner.registry.get("general_worker"), config)
        assert description["max_iterations"] == 80
        assert description["authorized_plugins"] == ["write"]
        assert description["supports_resume"] and description["supports_budget_override"]
    finally:
        scheduler.close(wait=True, cancel_pending=True)


@pytest.mark.parametrize('parent_status', ['running', 'waiting_children'])
def test_running_guidance_is_scope_bound_and_delivered_at_next_request(workspace, parent_status):
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
        parent.ledger.update(parent.id, status=parent_status)
        with pytest.raises(Exception, match="不存在|权限"):
            guide_task(root, "alice", task_id, "web", "foreign", ownership, "bad")
        reply = guide_task(root, "alice", task_id, "web", "scope", ownership, "只整理现有结果")
        assert reply["status"] == "guidance_queued"
        release.set()
        scheduler.wait(task_id, TASK_WAIT_TIMEOUT, source="web", session_id="scope")
        assert "只整理现有结果" in provider.requests[1].model_dump_json()
        public = parent.ledger.public(parent.ledger.get(task_id, "web", "scope"))
        assert "_guidance" not in public
    finally:
        release.set()
        scheduler.close(wait=True, cancel_pending=True)


def test_closed_parent_can_be_controlled_without_model_ownership(workspace):
    root, config = workspace
    write_plugin(root)
    entered, release = threading.Event(), threading.Event()

    def blocked(_request):
        entered.set()
        assert release.wait(5)
        return [call()]

    def verify_guidance(request):
        assert "跨轮追加的用户指导" in request.model_dump_json()
        return OUTPUT

    provider = Provider([blocked, verify_guidance])
    scheduler, parent, _, task_id = start(root, config, provider)
    try:
        assert entered.wait(3)
        with pytest.raises(Exception, match="主响应已关闭"):
            guide_task(
                root, "alice", task_id, "web", "scope", None,
                "too early", allow_closed_parent=True,
            )
        parent.ledger.update(
            parent.id, status="partial", response_closed=True, execution_live=True,
        )
        result = guide_task(
            root, "alice", task_id, "web", "scope", None,
            "跨轮追加的用户指导", allow_closed_parent=True,
        )
        assert result["status"] == "guidance_queued"
        release.set()
        scheduler.wait(task_id, TASK_WAIT_TIMEOUT, source="web", session_id="scope")
        record = parent.ledger.get(task_id, "web", "scope")
        assert record["guidance_delivered_count"] == 1
        public = parent.ledger.public(record)
        assert public["guidance_pending"] == 0
        assert public["guidance_delivered"] == 1
        assert "跨轮追加的用户指导" not in str(public)
    finally:
        release.set()
        scheduler.close(wait=True, cancel_pending=True)


@pytest.mark.parametrize('parent_status', ['running', 'waiting_children'])
def test_explicit_split_preserves_origin_and_aggregate_advice(workspace, parent_status):
    root, config = workspace
    write_plugin(root)
    provider = Provider([[call(value="a"), call(value="b", index=2)],
                         {**OUTPUT, "status": "partial"}, OUTPUT, OUTPUT])
    scheduler, parent, ownership, task_id = start(root, config, provider, limit=1)
    try:
        scheduler.wait(task_id, TASK_WAIT_TIMEOUT, source="web", session_id="scope")
        parent.ledger.update(parent.id, status=parent_status)
        result = split_task(scheduler, task_id, source="web", session_id="scope",
            ownership=ownership, tasks=[{"task": "stage b"}, {"task": "stage c"}],
            config=config, max_tool_calls=2, timeout=10)
        assert result["accepted_count"] == 2
        for child in result["task_ids"]:
            scheduler.wait(child, TASK_WAIT_TIMEOUT, source="web", session_id="scope")
            assert parent.ledger.get(child, "web", "scope")["origin_task_id"] == task_id
        records = [{"id": "1", "status": "tool_limit"}, {"id": "2", "status": "tool_limit"}]
        assert failure_advice(records)[0]["count"] == 2
        assert not failure_advice(records[:1])
    finally:
        scheduler.close(wait=True, cancel_pending=True)


@pytest.mark.parametrize("parent_status", ["running", "limited"])
def test_split_after_limited_parent_is_allowed_but_active_parent_is_protected(workspace, parent_status):
    root, config = workspace
    write_plugin(root)
    provider = Provider([[call(value="a"), call(value="b", index=2)],
                         {**OUTPUT, "status": "partial"}, OUTPUT])
    scheduler, parent, ownership, task_id = start(root, config, provider, limit=1)
    try:
        scheduler.wait(task_id, TASK_WAIT_TIMEOUT, source="web", session_id="scope")
        if parent_status == "limited":
            parent.finish("limited")
        new_parent = TaskCoordinator(root, "alice", "web", "scope", "next-run", {"prompt": "continue"})
        new_owner = new_parent.ownership("split")
        if parent_status == "running":
            with pytest.raises(Exception, match="其他活动主任务"):
                split_task(scheduler, task_id, source="web", session_id="scope",
                           ownership=new_owner, tasks=[{"task": "continue b"}], config=config)
            assert not new_parent.children()
        else:
            result = split_task(
                scheduler, task_id, source="web", session_id="scope", ownership=new_owner,
                tasks=[{"task": "continue b"}], config=config, max_tool_calls=2, timeout=10,
            )
            assert result["accepted_count"] == 1
            scheduler.wait(result["task_ids"][0], TASK_WAIT_TIMEOUT, source="web", session_id="scope")
            child = new_parent.children()[0]
            assert child["origin_task_id"] == task_id
            assert child["root_task_id"] == new_parent.id
            assert parent.ledger.get(task_id, "web", "scope")["root_task_id"] == parent.id
        assert parent.ledger.get(parent.id, "web", "scope")["status"] == parent_status
        assert (root / "effects.txt").read_text().splitlines() == ["a"]
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
        scheduler.wait(task_id, TASK_WAIT_TIMEOUT, source="web", session_id="scope")
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
        scheduler.wait(task_id, TASK_WAIT_TIMEOUT, source="web", session_id="scope")
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
    shutil.copyfile(Path(__file__).resolve().parents[2] / "plugins/subagent_dispatch/plugin.yaml",
                    directory / "plugin.yaml")
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
        {"draft": "awaiting worker"}, resume, {"answer": "merged"}, {"answer": "merged"}, {"answer": "merged"},
    ])
    try:
        events = list(iter_request_events(
            {"user": "alice", "source": "web", "session_id": "scope", "prompt": "do work"},
            root=root, provider_factory=lambda _: main))
        assert any(e.type == "done" for e in events), [e.to_dict() for e in events if e.type in {"error", "done"}]
        record = TaskLedger(root, "alice").get(observed["id"], "web", "scope")
        assert record["status"] == "completed" and record["run_count"] == 2 and record["handled"]
        assert record["budget"]["total_tool_calls"] == 2
        assert (root / "effects.txt").read_text().splitlines() == ["first", "second"]
        assert 4 <= len(main.requests) <= 7
    finally:
        scheduler.close(wait=True, cancel_pending=True)


def test_main_model_can_guide_its_running_subagent(workspace, monkeypatch):
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
    shutil.copyfile(Path(__file__).resolve().parents[2] / "plugins/subagent_dispatch/plugin.yaml",
                    directory / "plugin.yaml")
    (directory / "tool.py").write_text("from plugins.subagent_dispatch.tool import run\n", "utf-8")

    provider_entered = threading.Event()
    release_provider = threading.Event()
    guidance_received = threading.Event()

    def wait_for_guidance(_request):
        provider_entered.set()
        assert release_provider.wait(5)
        return [call()]

    def verify_guidance(request):
        assert "只整理现有结果" in request.model_dump_json()
        guidance_received.set()
        return OUTPUT

    child = Provider([wait_for_guidance, verify_guidance])
    scheduler = AgentScheduler.from_runner(AgentRunner(
        root, "alice", config=config, provider_factory=lambda _: child,
    ))
    monkeypatch.setattr("plugins.subagent_dispatch.tool.get_agent_scheduler", lambda *a, **k: scheduler)

    def guide_when_running(_request):
        assert provider_entered.wait(3)
        records, _ = TaskLedger(root, "alice").records("web", "scope", parent=None)
        record = next(item for item in records if item["agent"] == "general_worker")
        unrelated = TaskCoordinator(root, "alice", "web", "scope", "unrelated", {"prompt": "other"})
        with pytest.raises(Exception, match="不能控制其他活动主任务"):
            guide_task(root, "alice", record["id"], "web", "scope",
                       unrelated.ownership("guide"), "must not cross roots")
        return [ToolCallItem(
            id="call_guide", call_id="callid_guide", name="subagent_dispatch",
            arguments={
                "action": "guide",
                "task_id": record["id"],
                "guidance": "只整理现有结果",
            },
        )]

    def finish_main(_request):
        release_provider.set()
        return {"answer": "指导已发送"}

    main = Provider([
        [ToolCallItem(
            id="call_dispatch", call_id="callid_dispatch", name="subagent_dispatch",
            arguments={
                "action": "call",
                "agent": "general_worker",
                "input": {"task": "work"},
                "timeout": 10,
                "wait": False,
            },
        )],
        guide_when_running,
        finish_main,
    ])
    try:
        list(iter_request_events(
            {"user": "alice", "source": "web", "session_id": "scope", "prompt": "do work"},
            root=root, provider_factory=lambda _: main,
        ))
        assert guidance_received.wait(3)
        records, _ = TaskLedger(root, "alice").records("web", "scope", parent=None)
        record = next(item for item in records if item["agent"] == "general_worker")
        assert record["status"] == "completed"
    finally:
        release_provider.set()
        scheduler.close(wait=True, cancel_pending=True)


def test_legacy_tool_limit_failure_can_resume_only_with_valid_matching_checkpoint(workspace):
    root, config = workspace
    write_plugin(root)
    provider = Provider([[call(value="a"), call(value="b", index=2)], {**OUTPUT, "status": "partial"}, OUTPUT])
    scheduler, parent, ownership, task_id = start(root, config, provider, limit=1)
    try:
        scheduler.wait(task_id, TASK_WAIT_TIMEOUT, source="web", session_id="scope")
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
        scheduler.wait(task_id, TASK_WAIT_TIMEOUT, source="web", session_id="scope")
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
        scheduler.wait(task_id, TASK_WAIT_TIMEOUT, source="web", session_id="scope")
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
