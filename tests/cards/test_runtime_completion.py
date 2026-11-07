"""Completion depends on persisted execution facts, including late receipts."""
from provider.protocol.models import RefusalContent
from provider.protocol.enums import MessagePhase, MessageRole, ResponseStatus
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import json
import threading
import time

import pytest

from run.cards import CardStore, IntegrationStore
from run.tools.background_jobs import prepare_background_job
from run.tools.background_jobs import publish_background_result, read_background_job
from run.tools.background_notifications import ShellInbox
from run.tools.background_worker import _finish
from provider.protocol.models import KemoResponse, MessageItem, ReasoningItem, ToolResultItem, text_from_content
from run.tools import ToolRuntime
from tests.cards.test_runtime import call, definition, history, response, root, run


def eventually(predicate, seconds=4):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    assert predicate()


def test_fast_tool_receipt_is_in_the_next_request_and_only_executes_once(root):
    executions, requests = [], []

    class Provider:
        def create(self, request):
            requests.append(request)
            if len(requests) == 1:
                return response(request, calls=[call()])
            assert "fast-output" in request.model_dump_json()
            return response(request)

    events = list(run(root, Provider(), tools=[definition(
        root, lambda: executions.append(1) or {"ok": True, "data": "fast-output"},
    )]))
    assert events[-1].metadata["status"] == "completed"
    assert len(requests) == 2 and executions == [1]


def test_sync_tool_finishes_before_task_plan_boundary_closes_turn(root, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr("run.conversation.main_orchestration.detect_task_plan_creation_boundary",
        lambda **kwargs: SimpleNamespace(
            stop_reason="task_plan_created", plan_id="test-plan", awaiting_user_approval=True,
        ) if kwargs["tool_name"] == "checkpoint" else None)
    entered, release = threading.Event(), threading.Event()
    executions, requests = [], []

    def work():
        executions.append(1)
        entered.set()
        assert release.wait(6)
        return {"ok": True, "data": "work-result"}

    class Provider:
        def create(self, request):
            requests.append(request)
            return response(request, calls=[call(
                name="work" if len(requests) == 1 else "checkpoint",
                call_id=f"callid_{len(requests)}")])

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(lambda: list(run(root, Provider(), tools=[
            definition(root, work), definition(root, lambda: {"ok": True}, name="checkpoint")])))
        try:
            assert entered.wait(3)
            time.sleep(.1)
            assert len(requests) == 1 and not future.done()
        finally:
            release.set()
        events = future.result(5)
    terminal = events[-1]
    assert terminal.type == "done" and terminal.metadata["status"] == "limited"
    assert terminal.metadata["requested_stop_reason"] == "task_plan_created"
    assert terminal.metadata["unfinished_task_count"] == 0
    assert executions == [1] and len(requests) == 2


@pytest.mark.parametrize("plan_status", ["pending", "approved", "failed"])
@pytest.mark.parametrize("async_before", [False, True])
def test_mixed_plan_batch_preserves_prior_work_and_defers_following_effects(root, plan_status, async_before):
    executed, requests = [], []
    prior_done = threading.Event()
    plan_entered, release_plan, next_request = threading.Event(), threading.Event(), threading.Event()

    def work(*, value):
        executed.append(value)
        prior_done.set()
        return {"ok": True}

    def dispatch(*, agent, **_):
        if agent == "general_worker":
            return work(value="before")
        assert prior_done.wait(3)
        plan_entered.set()
        assert release_plan.wait(4)
        if plan_status == "failed":
            return {"ok": False, "error": {"message": "计划未创建"}}
        return {
            "ok": True, "status": "completed", "data": {"action": "create"},
            "plan": {"plan_id": "plan_test", "status": plan_status,
                     "auto_accept": plan_status == "approved"},
        }

    class Provider:
        def create(self, request):
            requests.append(request)
            if len(requests) == 1:
                return response(request, "", calls=[
                    (call("subagent_dispatch", "callid_before", action="call",
                          agent="general_worker", wait=False) if async_before
                     else call("work", "callid_before", value="before")),
                    call("subagent_dispatch", "callid_plan",
                         action="call", agent="task_plan", wait=True),
                    call("work", "callid_deferred", value="after"),
                ])
            next_request.set()
            assert plan_status == "failed"
            if len(requests) == 2:
                return response(request, "", calls=[
                    call("work", "callid_after_failure", value="after"),
                ])
            return response(request, "计划创建失败，其余独立工作已完成")

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(lambda: list(run(root, Provider(), tools=[
            definition(root, work),
            definition(root, dispatch, name="subagent_dispatch"),
        ])))
        try:
            assert plan_entered.wait(3)
            assert not next_request.wait(.3)
        finally:
            release_plan.set()
        events = future.result(5)
    terminal = events[-1]
    assert terminal.type == "done", terminal.to_dict()
    assert executed == (["before", "after"] if plan_status == "failed" else ["before"])
    assert len(requests) == (3 if plan_status == "failed" else 1)
    assert terminal.metadata["status"] == ("completed" if plan_status == "failed" else "limited")
    if plan_status != "failed":
        assert terminal.metadata["awaiting_user_approval"] == (plan_status == "pending")
    skipped = [event for event in events if event.type == "tool_call_result"
               and event.tool_call_id == "callid_deferred"]
    assert len(skipped) == 1 and skipped[0].metadata["status"] == "not_executed"
    assert skipped[0].result["execution"]["executed"] is False
    assert not any(event.type == "tool_call_start" and event.tool_call_id == "callid_deferred"
                   for event in events)


@pytest.mark.parametrize("plan_status", ["pending", "approved"])
def test_plan_receipt_after_event_drain_stops_before_another_request(root, monkeypatch, plan_status):
    original_events = ToolRuntime.events
    delayed, requests = [], []

    def defer_plan_receipt_once(self):
        events = original_events(self)
        for event in list(events):
            if (not delayed and event.type == "tool_call_result"
                    and event.tool_call_id == "callid_plan"
                    and not event.metadata.get("execution_live")):
                delayed.append(event)
                events.remove(event)
                # Reproduce publication just after the main loop's event drain.
                with self._publish_lock:
                    self._events.put(event)
                    self.changed.set()
        return events

    monkeypatch.setattr(ToolRuntime, "events", defer_plan_receipt_once)

    class Provider:
        def create(self, request):
            requests.append(request)
            assert len(requests) == 1, "a created plan must stop before another inference"
            return response(request, "", [
                call("subagent_dispatch", "callid_plan",
                     action="call", agent="task_plan", wait=True),
            ])

    events = list(run(root, Provider(), tools=[definition(root, lambda **_: {
        "ok": True, "status": "completed", "data": {"action": "create"},
        "plan": {"plan_id": "plan_test", "status": plan_status,
                 "auto_accept": plan_status == "approved"},
    }, name="subagent_dispatch")]))
    assert delayed
    assert events[-1].type == "done", events[-1].to_dict()
    assert events[-1].metadata["status"] == "limited"
    assert len(requests) == 1
    assert events[-1].metadata["awaiting_user_approval"] == (plan_status == "pending")


def test_required_shell_waits_for_receipt_then_resumes_without_reexecution(root, monkeypatch):
    config_path = root / "config/global_config.yaml"
    config = json.loads(config_path.read_text("utf-8"))
    config["agent_runtime"] = {"default_timeout": .1}
    config_path.write_text(json.dumps(config), "utf-8")
    job_ids, requests = [], []

    def fake_execute(tool, arguments, *, context, **kwargs):
        record, _ = prepare_background_job(
            root, "alice", source="web", session_id="s1", working_dir=str(root),
            shell_type="auto", command_digest="test-only", ownership=context["shell_ownership"],
        )
        job_ids.append(record["job_id"])
        return {"ok": True, "status": "running", "job_id": record["job_id"]}

    monkeypatch.setattr("run.tools.runtime.execute_tool", fake_execute)

    class Provider:
        def create(self, request):
            requests.append(request)
            if len(requests) == 1:
                return response(request, "draft", [
                    call(name="shell", call_id="callid_first", label="first"),
                    call(name="shell", call_id="callid_second", label="second"),
                ])
            return response(request)

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(lambda: list(run(
            root, Provider(), tools=[definition(root, lambda: None, name="shell")],
        )))
        eventually(lambda: len(requests) == 2 and len(job_ids) == 2)
        try:
            state = json.loads(text_from_content(requests[-1].input[-1].content).split("\n", 1)[1])["async_tasks"]
            assert state["pending_count"] == 2 and state["completion_blocked"]
            assert {task["id"] for task in state["pending_tasks"]} == set(job_ids)
            with pytest.raises(FutureTimeoutError):
                future.result(timeout=1.25)
            assert len(requests) == 2
            _finish(root, "alice", job_ids[0], exit_code=0, output_tail=["[stdout] first complete"])
            eventually(lambda: len(requests) == 3)
            state = json.loads(text_from_content(requests[-1].input[-1].content).split("\n", 1)[1])["async_tasks"]
            assert state["pending_count"] == 1 and state["completion_blocked"]
            assert state["pending_tasks"][0]["id"] == job_ids[1]
            assert not future.done()
        finally:
            for job_id in job_ids:
                _finish(root, "alice", job_id, exit_code=0, output_tail=["[stdout] complete"])
        events = future.result(4)
    assert events[-1].metadata["status"] == "completed"
    assert len(requests) == 4 and len(job_ids) == 2
    state = json.loads(text_from_content(requests[-1].input[-1].content).split("\n", 1)[1])["async_tasks"]
    assert state["pending_count"] == 0 and not state["completion_blocked"]
    assert state["pending_tasks"] == []
    receipts = [card for card in history(root) if card.raw_payload.get("execution_id") in job_ids]
    assert len(receipts) == 2
    assert {card.raw_payload["original_call"]["arguments"]["label"] for card in receipts} == {"first", "second"}
    assert all('"async_tasks"' not in card.raw_payload_json for card in history(root))
    assert {card.raw_payload["original_call"]["arguments"]["label"]:
            card.raw_payload["result"]["output_tail"] for card in receipts} == {
        "first": ["[stdout] first complete"], "second": ["[stdout] complete"],
    }


def shell_job(root, integration, *, mode="required"):
    return prepare_background_job(
        root, "alice", source="web", session_id="s1", working_dir=str(root),
        shell_type="auto", command_digest="test-only", completion_mode=mode,
        ownership={"root_task_id": "root-task",
                   "cardstream_integration_id": integration.id,
                   "cardstream_generation": integration.generation,
                   "cardstream_meter_key": integration.store.meter.key,
                   "original_call": {"name": "shell", "arguments": {"command": "test-command"}},
                   "tool_call_id": "shell-call"},
    )[0]


def test_late_shell_receipt_is_raw_after_partial_response_and_is_idempotent(root):
    integration = IntegrationStore(CardStore(root, "alice"), "web", "s1", "i-late")
    integration.start()
    job = shell_job(root, integration, mode="detached")
    integration.finish("partial", metadata={"status": "partial"})
    _finish(root, "alice", job["job_id"], exit_code=0, output_tail=["late-shell"])
    record = read_background_job(root, "alice", job["job_id"])
    assert record["card_result_seq"] > 0
    publish_background_result(root, "alice", {**record, "card_result_seq": None})
    receipts = [card for card in history(root) if card.raw_payload.get("execution_id") == job["job_id"]]
    assert len(receipts) == 1 and receipts[0].raw_payload["result"]["output_tail"] == ["late-shell"]
    with integration.store.db() as db:
        assert db.execute("SELECT status FROM card_integrations").fetchone()[0] == "partial"


def test_required_shell_result_commit_failure_keeps_completion_fenced_until_repaired(root, monkeypatch):
    integration = IntegrationStore(CardStore(root, "alice"), "web", "s1", "i-repair")
    integration.start()
    job = shell_job(root, integration)
    original = CardStore.append

    def unavailable(self, *args, **kwargs):
        if kwargs.get("event_key", "").startswith("shell-result:"):
            raise OSError("test-only unavailable card store")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(CardStore, "append", unavailable)
    _finish(root, "alice", job["job_id"], exit_code=0)
    inbox = ShellInbox(root, "alice", "web", "s1", root_task_id="root-task")
    assert inbox.pending()[0]["status"] == "result_commit_pending"
    assert inbox.ready() == []
    assert read_background_job(root, "alice", job["job_id"])["status"] == "completed"
    monkeypatch.setattr(CardStore, "append", original)
    assert inbox.pending() == []
    assert inbox.ready()[0]["card_result_seq"] > 0


def test_failed_terminal_commit_never_becomes_a_completed_integration(root, monkeypatch):
    original = IntegrationStore.finish
    attempted = []

    def fail_once(self, status, **kwargs):
        attempted.append(status)
        if status == "completed":
            raise OSError("isolated terminal failure")
        return original(self, status, **kwargs)

    monkeypatch.setattr(IntegrationStore, "finish", fail_once)
    provider = type("Provider", (), {"create": lambda self, request: response(request)})
    events = list(run(root, provider()))
    assert events[-1].type == "error"
    assert attempted == ["completed", "failed"]
    with CardStore(root, "alice").db() as db:
        record = db.execute("SELECT * FROM card_integrations").fetchone()
        assert record["status"] == "failed" and record["response_closed"]
    assert not any(card.raw_level == "final" for card in history(root))


def test_delayed_ui_receipt_cannot_discard_a_final_that_already_consumed_it(root, monkeypatch):
    requests, held, executions = [], [], []
    original_events = ToolRuntime.events

    def delayed_events(self):
        events = original_events(self)
        if len(requests) == 1:
            held.extend(event for event in events if event.type == "tool_call_result")
            return [event for event in events if event.type != "tool_call_result"]
        events = [*held, *events]
        held.clear()
        return events

    def handoff(self, ids):
        for execution_id in ids:
            self.wait_for_initial_result(execution_id, timeout=2)

    monkeypatch.setattr(ToolRuntime, "events", delayed_events)
    monkeypatch.setattr(ToolRuntime, "handoff", handoff)

    class Provider:
        def create(self, request):
            requests.append(request)
            if len(requests) == 1:
                return response(request, "checking", [call()])
            assert "consumed-result" in request.model_dump_json()
            if len(requests) == 2:
                return KemoResponse(
                    request_id=request.request_id, model=request.model, status=ResponseStatus.COMPLETED,
                    output=[MessageItem.text("assistant", "final result", phase="final_answer")],
                )
            raise AssertionError("a final that consumed the result must close the turn")

    events = list(run(root, Provider(), tools=[definition(
        root, lambda: executions.append(1) or {"ok": True, "data": "consumed-result"},
    )]))
    assert events[-1].metadata["status"] == "completed"
    assert len(requests) == 2 and executions == [1]
    finals = [card for card in history(root) if card.raw_level == "final"]
    assert len(finals) == 1 and finals[0].raw_payload["text"] == "final result"
    assert events[-1].metadata["text"] == "final result"


@pytest.mark.parametrize("kind", ["reasoning", "commentary", "unclassified", "blank"])
@pytest.mark.parametrize("recover", [False, True])
def test_missing_final_body_is_bounded_and_never_a_false_completion(root, monkeypatch, kind, recover):
    (root / "config/global_config.yaml").write_text(json.dumps({
        "provider_runtime": {"max_retries": 5}, "agents": {"output_reserve": 64},
    }), encoding="utf-8")
    monkeypatch.setattr("provider.retry.RetryManager.delay", lambda *args, **kwargs: 0)
    requests = []

    class Provider:
        def create(self, request):
            requests.append(request)
            if recover and len(requests) == 2:
                return response(request, "actual final")
            output = {
                "reasoning": [ReasoningItem(id="rs_pending", content="still thinking")],
                "commentary": [MessageItem.text("assistant", "checking", phase="commentary")],
                "unclassified": [MessageItem.text("assistant", "phase is unknown")],
                "blank": [MessageItem.text("assistant", " \n ")],
            }[kind]
            return KemoResponse(request_id=request.request_id, model=request.model,
                                status=ResponseStatus.COMPLETED, output=output)

    events = list(run(root, Provider()))
    assert len(requests) == (2 if recover else 6), events[-1].error
    assert len({request.request_id for request in requests}) == len(requests)
    assert all(request.input == requests[0].input for request in requests)
    with CardStore(root, "alice").db() as db:
        record = json.loads(db.execute("SELECT record_json FROM card_integrations").fetchone()[0])
    assert record["usage"]["provider_request_count"] == len(requests)
    finals = [card for card in history(root) if card.raw_level == "final"]
    if recover:
        assert events[-1].metadata["status"] == "completed"
        assert len(finals) == 1 and finals[0].raw_payload["text"] == "actual final"
        assert events[-1].metadata["text"] == "actual final"
    else:
        assert events[-1].type == "error" and events[-1].metadata["status"] == "failed"
        assert events[-1].error is not None
        assert events[-1].error["code"] == "missing_final_answer"
        assert events[-1].error["attempt_count"] == 6
        assert not finals


def test_commentary_and_final_answer_are_persisted_separately(root):
    class Provider:
        def create(self, request):
            return KemoResponse(
                request_id=request.request_id, model=request.model, status=ResponseStatus.COMPLETED,
                output=[
                    MessageItem.text("assistant", "checking", phase="commentary"),
                    MessageItem.text("assistant", "finished", phase="final_answer"),
                ],
            )

    events = list(run(root, Provider()))
    assert events[-1].metadata["text"] == "finished"
    bodies = [card for card in history(root) if card.raw_kind == "agent_text"]
    assert [(card.raw_level, card.raw_payload["text"]) for card in bodies] == [
        ("interim", "checking"), ("final", "finished"),
    ]
    from run.cards.assembly import protocol_items

    replayed = [
        item.phase
        for item in protocol_items(tuple(history(root)))
        if isinstance(item, MessageItem) and item.role == "assistant"
    ]
    assert replayed == ["commentary", "final_answer"]


def test_tool_task_accepts_a_typed_final_after_interim_prose(root):
    requests = []

    class Provider:
        def create(self, request):
            requests.append(request)
            if len(requests) == 1:
                return response(request, "正在检查文件", [call()])
            assert "正在检查文件" in request.model_dump_json()
            assert all(tool.name != "submit_final_answer" for tool in request.tools or [])
            return KemoResponse(
                request_id=request.request_id, model=request.model, status=ResponseStatus.COMPLETED,
                output=[
                    MessageItem.text("assistant", "还需要核对执行结果", phase="commentary"),
                    MessageItem.text("assistant", "已核验的最终结果", phase="final_answer"),
                ],
            )

    events = list(run(root, Provider(), tools=[definition(
        root, lambda: {"ok": True, "data": "verified"},
    )]))
    assert events[-1].type == "done" and events[-1].metadata["status"] == "completed"
    assert events[-1].metadata["text"] == "已核验的最终结果"
    assert len(requests) == 2
    assert any(event.type == "text_delta" and event.content == "已核验的最终结果"
               for event in events)
    bodies = [card for card in history(root)
              if card.raw_kind == "agent_text" and card.integration_id == events[-1].metadata["integration_id"]]
    assert [(card.raw_level, card.raw_payload["text"]) for card in bodies] == [
        ("interim", "正在检查文件"),
        ("interim", "还需要核对执行结果"),
        ("final", "已核验的最终结果"),
    ]


def test_runtime_waits_for_sync_tool_before_requesting_final(root):
    entered, release = threading.Event(), threading.Event()
    requests = []

    def work():
        entered.set()
        assert release.wait(5)
        return {"ok": True, "data": "finished"}

    class Provider:
        def create(self, request):
            requests.append(request)
            if len(requests) == 1:
                return response(request, calls=[call()])
            return response(request)

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(lambda: list(run(
            root, Provider(), tools=[definition(root, work)],
        )))
        try:
            assert entered.wait(3)
            time.sleep(.1)
            assert len(requests) == 1 and not future.done()
        finally:
            release.set()
        events = future.result(5)

    assert events[-1].type == "done" and events[-1].metadata["status"] == "completed"
    assert len(requests) == 2
    assert events[-1].metadata["text"] == "done"


def test_schema_invalid_empty_response_still_fails_without_replay(root):
    requests = []

    class Provider:
        def create(self, request):
            requests.append(request)
            return KemoResponse(request_id=request.request_id, model=request.model,
                                status=ResponseStatus.COMPLETED, output=[])

    events = list(run(root, Provider()))
    assert len(requests) == 1
    assert events[-1].type == "error"
    assert events[-1].error is not None
    assert events[-1].error["exception_type"] == "ValidationError"
    assert not any(card.raw_level == "final" for card in history(root))


@pytest.mark.parametrize("encoding", ["field", "block"])
def test_explicit_refusal_is_a_final_deliverable_without_a_text_block(root, encoding):
    requests = []

    class Provider:
        def create(self, request):
            requests.append(request)
            message = (MessageItem(
                           id="msg_refusal", role=MessageRole.ASSISTANT, phase=MessagePhase.FINAL_ANSWER,
                           refusal="cannot perform this action",
                       )
                       if encoding == "field" else
                       MessageItem(id="msg_refusal", role=MessageRole.ASSISTANT, content=[RefusalContent(text="cannot perform this action")], phase=MessagePhase.FINAL_ANSWER))
            return KemoResponse(request_id=request.request_id, model=request.model, status=ResponseStatus.COMPLETED,
                                output=[message])

    events = list(run(root, Provider()))
    assert len(requests) == 1
    assert events[-1].metadata["status"] == "completed", events[-1].error
    assert events[-1].metadata["text"] == "cannot perform this action"
    final = next(card for card in history(root) if card.raw_level == "final")
    assert events[-1].metadata["final_item_ids"] == [final.raw_payload["provider_output"][0]["id"]]


def test_sync_tool_result_precedes_the_final_request(root):
    entered, release = threading.Event(), threading.Event()
    requests = []

    def work():
        entered.set()
        assert release.wait(6)
        return {"ok": True, "data": "required-result"}

    class Provider:
        def create(self, request):
            requests.append(request)
            if len(requests) == 1:
                return response(request, "", [call()])
            assert "required-result" in request.model_dump_json()
            return response(request, "actual final")

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(lambda: list(run(root, Provider(), tools=[definition(root, work)])))
        try:
            assert entered.wait(3)
            time.sleep(.1)
            assert len(requests) == 1
            assert not future.done()
        finally:
            release.set()
        events = future.result(4)
    assert len(requests) == 2
    assert events[-1].metadata["status"] == "completed"
    assert events[-1].metadata["text"] == "actual final"
