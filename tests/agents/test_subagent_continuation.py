"""Timeout/resume integration with real scheduling and fake local providers."""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from plugins.subagent_dispatch.tool import run as dispatch
from provider.protocol.enums import MessageRole, ResponseStatus
from provider.protocol.models import Item, JsonContent, KemoResponse, MessageItem, ToolCallItem, ToolResultItem, Usage
from run.agents import AgentQueueError, AgentRunner, AgentScheduler, AgentTimeoutError
from run.agents.conclusions import conclusion_data, delivery
from run.agents.continuation import CheckpointStore, MAX_RESUME_ROUNDS, restore_items
from run.agents.task_ledger import TaskCoordinator, TaskLedger
from tests.agents.test_general_worker import OUTPUT, workspace  # noqa: F401
from tests.agents.test_subagent_runtime import MockProvider


class Provider(MockProvider):
    def __init__(self, steps):
        super().__init__(reasoning_supported=False)
        self.steps = list(steps)

    def create(self, request):
        self.requests.append(request)
        step = self.steps.pop(0)
        if callable(step):
            step = step(request)
        if isinstance(step, Exception):
            raise step
        output: list[Item] = step if isinstance(step, list) else [
            MessageItem.text(MessageRole.ASSISTANT, json.dumps(step, ensure_ascii=False))
        ]
        return KemoResponse(
            request_id=request.request_id, model=request.model,
            status=ResponseStatus.COMPLETED, output=output,
            usage=Usage(input_tokens=2, output_tokens=1, total_tokens=3),
        )


def timeout_from_provider_entry(monkeypatch, provider):
    """Keep tiny in-flight timeout scenarios independent of startup latency."""
    from types import SimpleNamespace
    origin = time.monotonic()
    entered = []
    create = provider.create
    def clock():
        return origin + (time.monotonic() - entered[0] if entered else 0)
    def start(*args, **kwargs):
        if not entered:
            entered.append(time.monotonic())
        return create(*args, **kwargs)
    monkeypatch.setattr(provider, "create", start)
    monkeypatch.setattr("run.agents.runner.time", SimpleNamespace(monotonic=clock, sleep=time.sleep))


@pytest.fixture(autouse=True)
def stable_inflight_timeout_scenarios(monkeypatch):
    # These tests target provider/tool in-flight expiry. Production initialization
    # accounting is covered separately in test_subagent_budgets.py.
    timeout_from_provider_entry(monkeypatch, Provider)


def call(name="write", value="first", index=1):
    return ToolCallItem(id=f"call_{index}", call_id=f"callid_{index}",
                        name=name, arguments={"value": value})


def eventually(predicate, seconds=5):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.01)
    assert predicate()


def write_plugin(root):
    directory = root / "plugins/write"
    directory.mkdir()
    manifest = {
        "name": "write", "description": "test", "version": "1", "enabled": True,
        "entrypoint": "tool.py:run", "execution_mode": "thread",
        "input_schema": {"type": "object", "properties": {"value": {"type": "string"}},
                         "required": ["value"], "additionalProperties": False},
    }
    (directory / "SKILL.md").write_text(
        "# write\nTest\n## Tool\n```json\n" + json.dumps(manifest) + "\n```\n", "utf-8")
    (directory / "tool.py").write_text(
        "from pathlib import Path\nimport time\n"
        "def run(value, *, context):\n"
        "    with (Path(context['root']) / 'effects.txt').open('a') as f: f.write(value+'\\n')\n"
        "    if value == 'first': time.sleep(.4)\n"
        "    return {'written': value}\n", "utf-8")


def start(root, config, provider, *, timeout=.2, survival=.6):
    config["agent_runtime"] = {"default_timeout": timeout, "timeout_survival_seconds": survival}
    runner = AgentRunner(root, "alice", config=config, provider_factory=lambda _: provider)
    scheduler = AgentScheduler.from_runner(runner)
    coordinator = TaskCoordinator(root, "alice", "web", "scope", "run-test", {"prompt": "do work"})
    ownership = coordinator.ownership("call-test")
    task_id = scheduler.submit(
        "general_worker", {"task": "work"}, source="web", session_id="scope",
        ownership=ownership, allow_sync=True, timeout=timeout,
        timeout_survival_seconds=survival, config=config,
    )
    return scheduler, coordinator, ownership, task_id


def test_timeout_conclusions_then_same_task_resume_retimes_and_does_not_replay(workspace):
    root, config = workspace
    write_plugin(root)
    partial = {**OUTPUT, "status": "partial", "summary": "已确认 first 完成", "remaining": ["second"]}
    provider = Provider([[call(), call(value="second", index=2)], partial,
                         [call(value="second", index=3)], OUTPUT])
    scheduler, coordinator, ownership, task_id = start(root, config, provider)
    try:
        result = scheduler.wait(task_id, 5, source="web", session_id="scope")
        snapshot = scheduler.get(task_id, source="web", session_id="scope")
        assert snapshot["status"] == "timed_out" and snapshot["resumable"]
        assert result.data["summary"] == partial["summary"]
        assert (root / "effects.txt").read_text().splitlines() == ["first"]
        assert not coordinator.pending()
        message = coordinator.notifications()
        assert message is not None
        assert partial["summary"] in message["content"] and '"tool_calls":' not in message["content"]
        coordinator.acknowledge()
        assert "超时收尾指令" in provider.requests[1].model_dump_json()
        assert not provider.requests[1].tools
        assert provider.requests[1].generation.max_output_tokens <= 4000
        second_round_start = time.monotonic()
        assert scheduler.resume(task_id, source="web", session_id="scope", timeout=1,
                                ownership=ownership, config=config) == task_id
        final = scheduler.wait(task_id, 5, source="web", session_id="scope")
        assert final.data == OUTPUT
        after = scheduler.get(task_id, source="web", session_id="scope")
        assert after["status"] == "completed" and after["run_count"] == 2
        assert after["submission_order"] == snapshot["submission_order"]
        assert after["result"]["usage"]["total_tokens"] == 12
        assert time.monotonic() - second_round_start < 1
        assert (root / "effects.txt").read_text().splitlines() == ["first", "second"]
        assert "超时收尾指令" not in provider.requests[2].model_dump_json()
        for request in provider.requests[1:]:
            calls = {i.call_id for i in request.input if isinstance(i, ToolCallItem)}
            results = {i.call_id for i in request.input if isinstance(i, ToolResultItem)}
            assert calls == results
        ledger = coordinator.ledger.get(task_id, "web", "scope")
        assert ledger["round_history"][0]["status"] == "timed_out"
        assert ledger["active_elapsed_ms"] >= 100
    finally:
        scheduler.close(wait=True, cancel_pending=True)


def test_timeout_live_provider_keeps_barrier_and_disallows_resume(workspace, monkeypatch):
    root, config = workspace
    entered, release = threading.Event(), threading.Event()
    def blocked(_):
        entered.set()
        release.wait(5)
        return RuntimeError("stopped in provider")
    # Provider.create raises steps returned as exceptions.
    provider = Provider([blocked])
    monkeypatch.setattr("run.agents.runner._AGENT_TIMEOUT_CLEANUP_GRACE", .01)
    scheduler, coordinator, ownership, task_id = start(root, config, provider, timeout=.5, survival=0)
    try:
        assert entered.wait(3)
        eventually(lambda: scheduler.get(task_id, source="web", session_id="scope")["status"] == "timed_out_running")
        assert coordinator.pending()
        assert coordinator.notifications() is None
        with pytest.raises(AgentQueueError):
            scheduler.resume(task_id, source="web", session_id="scope", ownership=ownership)
        release.set()
        eventually(lambda: scheduler.get(task_id, source="web", session_id="scope")["status"] == "timed_out")
        assert not coordinator.pending()
        record = coordinator.ledger.get(task_id, "web", "scope")
        assert record["result"] is not None and record["resumable"]
    finally:
        release.set()
        scheduler.close(wait=True, cancel_pending=True)


def timed_out_task(workspace):
    root, config = workspace
    write_plugin(root)
    provider = Provider([[call()], {**OUTPUT, "status": "partial"}])
    scheduler, coordinator, ownership, task_id = start(root, config, provider)
    scheduler.wait(task_id, 5, source="web", session_id="scope")
    return root, config, provider, scheduler, coordinator, ownership, task_id


def test_resume_concurrent_claim_only_one_queue_entry(workspace):
    root, config, provider, scheduler, coordinator, ownership, task_id = timed_out_task(workspace)
    gate = threading.Event()
    def block(_):
        gate.wait(3)
        return OUTPUT
    provider.steps.append(block)
    def attempt():
        try:
            return scheduler.resume(task_id, source="web", session_id="scope", timeout=2,
                                    ownership=ownership, config=config)
        except AgentQueueError:
            return None
    try:
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        assert results.count(task_id) == 1
        gate.set()
        scheduler.wait(task_id, 5, source="web", session_id="scope")
        assert len(provider.requests) == 3
    finally:
        gate.set()
        scheduler.close(wait=True, cancel_pending=True)


@pytest.mark.parametrize("change", ["source", "session", "cancelled", "completed", "corrupt", "definition", "limit", "unowned"])
def test_resume_rejects_invalid_state_scope_checkpoint_and_definition(workspace, change):
    root, config, provider, scheduler, coordinator, ownership, task_id = timed_out_task(workspace)
    # A timed-out round may exhaust its survival budget before wrap-up HTTP
    # starts. This test verifies rejection sends no new request, not the
    # separate wrap-up behavior covered by the integration test above.
    requests_before_resume = len(provider.requests)
    source, session_id = "web", "scope"
    if change == "source":
        source = "app"
    elif change == "session":
        session_id = "other"
    elif change in {"cancelled", "completed"}:
        coordinator.ledger.update(task_id, status=change)
    elif change == "corrupt":
        CheckpointStore(root, "alice", task_id).path.write_text("{broken", "utf-8")
    elif change == "definition":
        with (root / "agents/general_worker/AGENT.md").open("a", encoding="utf-8") as f:
            f.write("\nChanged definition\n")
    elif change == "limit":
        coordinator.ledger.update(task_id, run_count=MAX_RESUME_ROUNDS)
    elif change == "unowned":
        ownership = None
    try:
        with pytest.raises((AgentQueueError, ValueError)):
            scheduler.resume(task_id, source=source, session_id=session_id, ownership=ownership)
        assert len(provider.requests) == requests_before_resume
    finally:
        scheduler.close(wait=True, cancel_pending=True)


def test_resume_queue_full_rolls_back_old_result(workspace, monkeypatch):
    root, config, provider, scheduler, coordinator, ownership, task_id = timed_out_task(workspace)
    before = coordinator.ledger.get(task_id, "web", "scope")
    import queue
    monkeypatch.setattr(scheduler._queue, "put", lambda *a, **k: (_ for _ in ()).throw(queue.Full()))
    try:
        with pytest.raises(AgentQueueError, match="队列已满"):
            scheduler.resume(task_id, source="web", session_id="scope", ownership=ownership)
        after = coordinator.ledger.get(task_id, "web", "scope")
        for key in ("result", "status", "run_count", "resumable", "handled"):
            assert after.get(key) == before.get(key)
    finally:
        scheduler.close(wait=True, cancel_pending=True)


def test_resume_actual_space_admission_failure_preserves_old_timeout_result(workspace):
    root, config, provider, old, coordinator, ownership, task_id = timed_out_task(workspace)
    old.close(wait=True)
    before = coordinator.ledger.get(task_id, "web", "scope")
    config["provider_runtime"] = {"max_concurrent_requests": 1}
    config["agent_runtime"]["queue_maxsize"] = 2
    scheduler = AgentScheduler.from_runner(AgentRunner(
        root, "alice", config=config, provider_factory=lambda _: provider,
    ))
    started, release = threading.Event(), threading.Event()

    def blocked(cancel):
        started.set()
        assert release.wait(5)
        return {"ok": True}

    try:
        scheduler.submit_callable("fixture", {}, blocked, source="web", session_id="other")
        assert started.wait(3)
        scheduler.submit_callable("fixture", {}, lambda _: {}, source="web", session_id="scope")
        with pytest.raises(AgentQueueError, match="当前对话空间"):
            scheduler.resume(task_id, source="web", session_id="scope", ownership=ownership)
        after = coordinator.ledger.get(task_id, "web", "scope")
        for key in ("result", "status", "run_count", "resumable", "handled"):
            assert after.get(key) == before.get(key)
        assert task_id not in scheduler._tasks
    finally:
        release.set()
        scheduler.close(wait=True, cancel_pending=True)


def test_restart_can_resume_stopped_builtin_task_from_private_snapshot(workspace):
    root, config, provider, scheduler, coordinator, ownership, task_id = timed_out_task(workspace)
    scheduler.close(wait=True)
    provider.steps.append(OUTPUT)
    new = AgentScheduler.from_runner(AgentRunner(root, "alice", config=config, provider_factory=lambda _: provider))
    try:
        new.resume(task_id, source="web", session_id="scope", timeout=1, ownership=ownership, config=config)
        assert new.wait(task_id, 5, source="web", session_id="scope").data == OUTPUT
        assert new.get(task_id, source="web", session_id="scope")["run_count"] == 2
    finally:
        new.close(wait=True, cancel_pending=True)


def test_default_delivery_filters_legacy_tool_traces_but_keeps_evidence():
    value = {"summary": "done", "results": ["file.py:12 verified"],
             "metadata": {"tool_calls": [{"arguments": "secret"}]},
             "events": [{"status": "tool_running"}], "trace": ["x"], "raw_text": "raw"}
    assert conclusion_data(value) == {"summary": "done", "results": ["file.py:12 verified"]}
    assert delivery({"id": "a", "agent": "w", "status": "timed_out",
                     "result": value, "resumable": True})["resumable"]


def test_checkpoint_repairs_unstarted_calls_and_never_replays_paired_calls():
    one, two = call(index=1), call(index=2)
    paired = ToolResultItem(id="result_1", call_id=one.call_id, name=one.name,
                            content=[JsonContent(data={"ok": True})])
    items = restore_items({"items": [i.model_dump(mode="json") for i in [one, two, paired]]})
    assert len(items) == 4
    last = items[-1]
    assert isinstance(last, ToolResultItem) and last.call_id == two.call_id and last.is_error
    content = last.content[0]
    assert isinstance(content, JsonContent) and content.data["not_executed"] is True


def test_private_checkpoint_masks_credentials_and_fails_closed_on_size(workspace, monkeypatch):
    root, _ = workspace
    store = CheckpointStore(root, "alice", "agent-task-" + "a" * 32)
    store.write({"secret": "private", "items": [{"text": '{"api_key":"never-save","answer":"public"}'}]})
    content = store.path.read_text("utf-8")
    assert "never-save" not in content and "public" in content
    monkeypatch.setattr("run.agents.continuation.MAX_CHECKPOINT_BYTES", 10)
    with pytest.raises(ValueError, match="上限"):
        store.write({"data": "x" * 100})


def test_live_tool_future_is_drained_and_checkpoint_contains_its_actual_result(workspace, monkeypatch):
    from concurrent.futures import Future
    from run.tools import ToolTimeoutError
    root, config = workspace
    write_plugin(root)
    future = Future()
    entered = threading.Event()
    def detached(*a, **k):
        entered.set()
        raise ToolTimeoutError("tool still running", still_running=True, completion_future=future)
    monkeypatch.setattr("run.agents.runner.execute_tool", detached)
    monkeypatch.setattr("run.agents.runner._AGENT_TIMEOUT_CLEANUP_GRACE", .01)
    provider = Provider([[call()]])
    scheduler, coordinator, ownership, task_id = start(root, config, provider, timeout=.5, survival=0)
    try:
        assert entered.wait(3)
        eventually(lambda: scheduler.get(task_id, source="web", session_id="scope")["status"] == "timed_out_running")
        assert coordinator.pending()
        with pytest.raises(AgentQueueError):
            scheduler.resume(task_id, source="web", session_id="scope", ownership=ownership)
        future.set_result({"written": "actual-result"})
        eventually(lambda: scheduler.get(task_id, source="web", session_id="scope")["status"] == "timed_out")
        checkpoint = CheckpointStore(root, "alice", task_id).load(source="web", session_id="scope")
        actual = [i for i in restore_items(checkpoint) if isinstance(i, ToolResultItem)]
        assert len(actual) == 1
        content = actual[0].content[0]
        assert isinstance(content, JsonContent) and content.data["result"]["written"] == "actual-result"
        assert not coordinator.pending()
    finally:
        if not future.done():
            future.set_result(None)
        scheduler.close(wait=True, cancel_pending=True)


def test_dispatch_call_status_and_resume_do_not_return_tool_history(workspace, monkeypatch):
    root, config, provider, scheduler, coordinator, ownership, task_id = timed_out_task(workspace)
    monkeypatch.setattr("plugins.subagent_dispatch.tool.get_agent_scheduler", lambda *a, **k: scheduler)
    provider.steps.append(OUTPUT)
    context = {"root": str(root), "user": "alice", "source": "web", "session_id": "scope",
               "task_ownership": ownership}
    try:
        status = dispatch("status", task_id=task_id, context=context)
        assert status["status"] == "timed_out" and status["resumable"]
        result = dispatch("resume", task_id=task_id, timeout=1, wait=True, context=context)
        assert result["status"] == "completed" and result["run_count"] == 2
        provider.steps.append(OUTPUT)
        normal = dispatch("call", agent="general_worker", input={"task": "normal"}, timeout=1, wait=True, context=context)
        assert normal["status"] == "completed"
        for response in (status, result, normal):
            assert not {"events", "metadata", "tool_calls", "trace"} & set(response)
            assert "arguments" not in json.dumps(response)
        assert coordinator.ledger.get(task_id, "web", "scope")["events"]
    finally:
        scheduler.close(wait=True, cancel_pending=True)


@pytest.mark.parametrize("return_valid", [False, True])
def test_cancel_priority_never_starts_wrap_up_or_resume(workspace, monkeypatch, return_valid):
    root, config = workspace
    entered, release = threading.Event(), threading.Event()
    def blocked(_):
        entered.set()
        release.wait(3)
        return OUTPUT if return_valid else RuntimeError("cancelled")
    provider = Provider([blocked])
    monkeypatch.setattr("run.agents.runner._AGENT_CANCEL_CLEANUP_GRACE", .01)
    scheduler, coordinator, ownership, task_id = start(root, config, provider, timeout=2)
    try:
        assert entered.wait(3)
        scheduler.cancel(task_id, source="web", session_id="scope")
        eventually(lambda: scheduler.get(task_id, source="web", session_id="scope")["status"] == "cancelled")
        assert coordinator.pending()
        release.set()
        eventually(lambda: not coordinator.pending())
        with pytest.raises(AgentQueueError):
            scheduler.resume(task_id, source="web", session_id="scope", ownership=ownership)
        assert len(provider.requests) == 1
        assert "超时收尾" not in provider.requests[0].model_dump_json()
    finally:
        release.set()
        scheduler.close(wait=True, cancel_pending=True)


def test_completed_parent_can_transfer_resume_to_new_same_session_parent(workspace):
    root, config, provider, scheduler, coordinator, ownership, task_id = timed_out_task(workspace)
    provider.steps.append(OUTPUT)
    try:
        coordinator.notifications()
        coordinator.acknowledge()
        coordinator.finish("completed")
        new_parent = TaskCoordinator(root, "alice", "web", "scope", "next-run", {"prompt": "continue"})
        new_ownership = new_parent.ownership("resume-call")
        scheduler.resume(task_id, source="web", session_id="scope", timeout=1,
                         ownership=new_ownership, config=config)
        scheduler.wait(task_id, 5, source="web", session_id="scope")
        assert not coordinator.children()
        children = new_parent.children()
        assert [c["id"] for c in children] == [task_id]
        assert children[0]["round_history"][0]["root_task_id"] == coordinator.id
        assert children[0]["run_count"] == 2
    finally:
        scheduler.close(wait=True, cancel_pending=True)


def test_main_model_receives_timeout_conclusion_resumes_same_task_and_finishes(workspace, monkeypatch):
    from run.engine import iter_request_events
    import shutil
    from pathlib import Path
    root, config = workspace
    write_plugin(root)
    repository = Path(__file__).resolve().parents[2]
    dispatch_dir = root / "plugins/subagent_dispatch"
    dispatch_dir.mkdir()
    shutil.copyfile(repository / "plugins/subagent_dispatch/SKILL.md", dispatch_dir / "SKILL.md")
    (dispatch_dir / "tool.py").write_text("from plugins.subagent_dispatch.tool import run\n", "utf-8")
    config["agent_runtime"] = {"default_timeout": 1, "timeout_survival_seconds": .6}
    (root / "config/global_config.json").write_text(json.dumps(config), "utf-8")
    child = Provider([[call()], {**OUTPUT, "status": "partial", "summary": "timeout-conclusion"}, OUTPUT])
    # The main fake model uses the same class; start this scenario's clock only
    # when its child model enters, rather than on the parent's dispatch response.
    timeout_from_provider_entry(monkeypatch, child)
    scheduler = AgentScheduler.from_runner(AgentRunner(root, "alice", config=config, provider_factory=lambda _: child))
    monkeypatch.setattr("plugins.subagent_dispatch.tool.get_agent_scheduler", lambda *a, **k: scheduler)
    observed = {}
    def choose_resume(request):
        wire = request.model_dump_json()
        assert "timeout-conclusion" in wire
        tasks, _ = TaskLedger(root, "alice").records("web", "scope", parent=None)
        task = next(t for t in tasks if t["agent"] == "general_worker")
        observed["task_id"] = task["id"]
        assert task["status"] == "timed_out"
        return [ToolCallItem(
            id="call_resume", call_id="callid_resume", name="subagent_dispatch",
            arguments={"action": "resume", "task_id": task["id"], "wait": True, "timeout": 1},
        )]
    main = Provider([
        [ToolCallItem(id="call_dispatch", call_id="callid_dispatch", name="subagent_dispatch",
                      arguments={"action": "call", "agent": "general_worker",
                                 "input": {"task": "work"}, "wait": False, "timeout": .2})],
        {"draft": "waiting for worker"},
        choose_resume,
        {"answer": "merged"},
    ])
    try:
        events = list(iter_request_events(
            {"user": "alice", "source": "web", "session_id": "scope", "prompt": "perform work"},
            root=root, provider_factory=lambda _: main,
        ))
        assert any(event.type == "done" for event in events)
        record = TaskLedger(root, "alice").get(observed["task_id"], "web", "scope")
        assert record["run_count"] == 2 and record["status"] == "completed" and record["handled"], main.requests[-1].model_dump_json()
        assert len(main.requests) == 4
        for request in main.requests[1:]:
            tool_results = [i for i in request.input if isinstance(i, ToolResultItem)]
            for result in tool_results:
                encoded = result.model_dump_json()
                assert '"tool_calls":' not in encoded and '"arguments":' not in encoded
        assert (root / "effects.txt").read_text().splitlines() == ["first"]
    finally:
        scheduler.close(wait=True, cancel_pending=True)


def test_retry_during_resume_keeps_latest_safe_point_and_does_not_repeat_writes(workspace, monkeypatch):
    from run.agents.contracts import AgentProviderError
    root, config, provider, scheduler, coordinator, ownership, task_id = timed_out_task(workspace)
    provider.steps.extend([
        [call(value="second", index=3)],
        AgentProviderError("transient", category="connection_error", retryable=True),
        OUTPUT,
    ])
    monkeypatch.setattr("run.agents.runner._agent_retry_delay_seconds", lambda *a: 0)
    try:
        scheduler.resume(task_id, source="web", session_id="scope", timeout=10,
                         ownership=ownership, config=config)
        final = scheduler.wait(task_id, 15, source="web", session_id="scope")
        assert final.data == OUTPUT and len(provider.requests) == 5, {
            "requests": len(provider.requests), "remaining_steps": len(provider.steps),
            "snapshot": scheduler.get(task_id, source="web", session_id="scope"),
        }
        assert (root / "effects.txt").read_text().splitlines() == ["first", "second"]
        paired_results = [i for i in provider.requests[-1].input if isinstance(i, ToolResultItem)]
        assert len(paired_results) == 2
        assert "预算不重置" in provider.requests[-1].model_dump_json()
    finally:
        scheduler.close(wait=True, cancel_pending=True)


def test_resume_uses_current_permissions_and_rejects_stale_events(workspace):
    root, config, provider, scheduler, coordinator, ownership, task_id = timed_out_task(workspace)
    old_task = scheduler._tasks[task_id]
    events = []
    old_task.event_callback = events.append
    restricted = {**config, "plugins": {"whitelist": ["subagent_dispatch"]}}
    provider.steps.append(OUTPUT)
    try:
        scheduler.resume(task_id, source="web", session_id="scope", timeout=1,
                         ownership=ownership, config=restricted)
        scheduler.wait(task_id, 5, source="web", session_id="scope")
        assert not any(t.name == "write" for t in provider.requests[-1].tools)
        before = coordinator.ledger.get(task_id, "web", "scope")
        assert not coordinator.ledger.event(task_id, {"status": "failed", "run_count": 1})
        scheduler._emit(old_task, "failed")
        after = coordinator.ledger.get(task_id, "web", "scope")
        assert before == after and not events
    finally:
        scheduler.close(wait=True, cancel_pending=True)


def test_checkpoint_hardlinks_and_unsafe_task_ids_are_rejected(workspace):
    import os
    root, _ = workspace
    with pytest.raises(ValueError, match="ID"):
        CheckpointStore(root, "alice", "../../outside")
    store = CheckpointStore(root, "alice", "agent-task-" + "b" * 32)
    store.write({"version": 1, "available": True})
    os.link(store.path, store.directory / "linked.json")
    with pytest.raises(ValueError, match="链接"):
        store.write({"version": 1})
    with pytest.raises(ValueError, match="链接"):
        store.load(source="web", session_id="scope")


def test_resumable_business_timeout_stays_stopped_even_if_inflight_request_returns_complete(workspace):
    root, config = workspace
    def late(_):
        time.sleep(.4)
        return OUTPUT
    provider = Provider([late])
    scheduler, coordinator, _, task_id = start(root, config, provider)
    try:
        result = scheduler.wait(task_id, 5, source="web", session_id="scope")
        snapshot = scheduler.get(task_id, source="web", session_id="scope")
        assert snapshot["status"] == "timed_out" and snapshot["resumable"]
        assert result.data["summary"] == OUTPUT["summary"]
        assert result.data["status"] == "partial"
        assert result.metadata["partial"] and not snapshot["completed_after_timeout"]
        assert len(provider.requests) == 1 and not coordinator.pending()
    finally:
        scheduler.close(wait=True, cancel_pending=True)


def test_late_success_after_detach_supplements_timeout_conclusion_without_false_completion(workspace, monkeypatch):
    root, config = workspace
    entered, release = threading.Event(), threading.Event()
    def late(_):
        entered.set()
        release.wait(5)
        return OUTPUT
    monkeypatch.setattr("run.agents.runner._AGENT_TIMEOUT_CLEANUP_GRACE", .01)
    scheduler, coordinator, _, task_id = start(root, config, Provider([late]), timeout=.5, survival=0)
    try:
        assert entered.wait(3)
        eventually(lambda: scheduler.get(task_id, source="web", session_id="scope")["status"] == "timed_out_running")
        assert coordinator.pending()
        release.set()
        eventually(lambda: scheduler.get(task_id, source="web", session_id="scope")["status"] == "timed_out")
        snapshot = scheduler.get(task_id, source="web", session_id="scope")
        assert snapshot["result"]["data"]["summary"] == OUTPUT["summary"]
        assert snapshot["resumable"] and not coordinator.pending()
    finally:
        release.set()
        scheduler.close(wait=True, cancel_pending=True)


def test_wrap_up_keeps_only_structured_submission_and_validates_original_schema(workspace):
    root, config = workspace
    write_plugin(root)
    partial = {**OUTPUT, "status": "partial", "summary": "structured partial"}
    provider = Provider([
        [call()],
        [ToolCallItem(id="call_submit", call_id="callid_submit",
                      name="submit_structured_output", arguments=partial)],
    ])
    runner = AgentRunner(root, "alice", config=config, provider_factory=lambda _: provider)
    with pytest.raises(AgentTimeoutError) as caught:
        runner.run("general_worker", {"task": "work"}, timeout=.2, timeout_survival_seconds=.6,
                   structured_output_tool=True, task_id="agent-task-" + "c" * 32,
                   source="web", session_id="scope")
    assert caught.value.process_terminated
    assert caught.value.partial_result.data["summary"] == "structured partial"
    assert [t.name for t in provider.requests[-1].tools] == ["submit_structured_output"]
