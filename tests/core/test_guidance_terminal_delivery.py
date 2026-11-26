"""R1 3.1: accepted guidance must have a truthful terminal disposition."""
from __future__ import annotations

import json
import threading

import pytest

from provider.schema import ChatResponse, ProviderError, ToolCall
from run.conversation import GuidanceInput, GuidanceMailbox
from run.engine import iter_request_events
from run.cards import CardStore
from run.tools.background_jobs import prepare_background_job
from run.tools.background_worker import _finish
from tests.core.test_preview_runtime_safety import tool
from tests.media_input_support import MediaProvider, make_root
from web.service import ActiveRun, WebRunService


def test_atomic_final_close_returns_every_accepted_guidance():
    for _ in range(40):
        mailbox = GuidanceMailbox()
        barrier = threading.Barrier(2)
        offered = []

        def submit():
            barrier.wait()
            offered.append(mailbox.offer(GuidanceInput(id="late", text="revised")))

        thread = threading.Thread(target=submit)
        thread.start()
        barrier.wait()
        pending = mailbox.close_and_drain()
        thread.join(2)
        assert not thread.is_alive()
        assert len(offered) == 1
        assert [value.id for value in pending] == (["late"] if offered[0][0] else [])
        assert not mailbox.accepting
        assert mailbox.close_and_drain() == []


@pytest.mark.parametrize("boundary", [
    "last_iteration", "plan_boundary", "plan_boundary_write_failure", "cancelled", "provider_failure", "prepare_failure",
])
@pytest.mark.parametrize("mode", ["interrupt", "background"])
def test_terminal_guidance_is_reported_and_persisted_not_claimed_applied(tmp_path, monkeypatch, boundary, mode):
    config, _ = make_root(tmp_path)
    (tmp_path / "config/global_config.yaml").write_text(json.dumps(config), "utf-8")
    tool(tmp_path, "echo")
    mailbox = GuidanceMailbox()
    cancelled = threading.Event()
    late = GuidanceInput(id="late-guidance", text="do not silently discard", mode=mode)
    first = ChatResponse(text="", tool_calls=[ToolCall("first", "echo", {})])
    failed = False
    if boundary in {"plan_boundary", "plan_boundary_write_failure"}:
        from types import SimpleNamespace
        monkeypatch.setattr("run.conversation.main_orchestration.detect_task_plan_creation_boundary",
            lambda **kwargs: SimpleNamespace(stop_reason="task_plan_created", plan_id="test",
                                             awaiting_user_approval=True))
    if boundary == "plan_boundary_write_failure":
        append = CardStore.append

        def fail_first_guidance_write(self, *args, **kwargs):
            nonlocal failed
            if kwargs.get("level") == "guidance" and not failed:
                failed = True
                raise OSError("guidance RAW write failed during close")
            return append(self, *args, **kwargs)

        monkeypatch.setattr(CardStore, "append", fail_first_guidance_write)
    responses: list[ChatResponse | ProviderError] = [first, ChatResponse(text="done")]
    if boundary == "provider_failure":
        responses[1] = ProviderError("offline deterministic failure", status_code=403, retryable=False)

    class BoundaryProvider(MediaProvider):
        def create(self, request):
            if boundary == "last_iteration" and len(self.requests) == 1:
                assert mailbox.offer(late)[0]
            return super().create(request)

    provider = BoundaryProvider(responses)

    def execute(*args, **kwargs):
        if boundary != "last_iteration":
            assert mailbox.offer(late)[0]
        if boundary == "cancelled":
            cancelled.set()
        return {"ok": True}

    monkeypatch.setattr("run.tools.runtime.execute_tool", execute)
    if boundary == "prepare_failure":
        def reject_preparation(*args, **kwargs):
            raise ValueError("offline attachment preparation failed")
        monkeypatch.setattr("run.conversation.main_orchestration.prepare_guidance", reject_preparation)
    events = list(iter_request_events(
        {"user": "alice", "source": "web", "session_id": "terminal-guidance",
         "prompt": "start", "_guidance_queue": mailbox, "_auto_retry": False},
        root=tmp_path, provider_factory=lambda _: provider, cancel_event=cancelled,
    ))
    terminal = events[-1]
    assert terminal.type in {"done", "error"}
    applied = any(event.type == "guidance_applied" for event in events)
    details = terminal.metadata["guidance_details"]
    assert len(details) == 1 and details[0]["id"] == late.id
    assert details[0]["ok"] is applied
    assert details[0]["status"] == ("applied" if applied else "not_applied")
    if boundary == "plan_boundary_write_failure":
        assert failed and details[0]["reason"] == "run_failed"
    if not applied:
        assert details[0]["reason"] and details[0]["note"]
    assert not mailbox.accepting and mailbox.qsize() == 0
    store = CardStore(tmp_path, "alice")
    guidance = [card for card in store.raw_page("web", "terminal-guidance")
                if card.raw_level == "guidance"]
    assert len(guidance) == 1
    with store.db() as db:
        record = json.loads(db.execute(
            "SELECT record_json FROM card_integrations ORDER BY created_at DESC LIMIT 1"
        ).fetchone()[0])
    assert record["guidance_details"] == details
    history = WebRunService(tmp_path).history("alice", "terminal-guidance")
    assert history["integrations"][-1]["guidance_details"][0]["status"] == details[0]["status"]


@pytest.mark.parametrize("ending", ["completed", "cancelled", "failed"])
def test_delayed_guidance_waits_for_every_required_job_in_its_batch(tmp_path, monkeypatch, ending):
    make_root(tmp_path)
    tool(tmp_path, "shell")
    mailbox = GuidanceMailbox()
    cancelled = threading.Event()
    session = "guidance-required-jobs"
    job_ids = {}

    def execute(_tool, arguments, *, context, **kwargs):
        label = arguments["label"]
        record, _ = prepare_background_job(
            tmp_path, "alice", source="web", session_id=session, working_dir=str(tmp_path),
            shell_type="auto", command_digest="test-only", ownership=context["shell_ownership"],
        )
        job_ids[label] = record["job_id"]
        if label == "first":
            mailbox.offer(GuidanceInput(id="alt", text="apply after both jobs", mode="background"))
            mailbox.offer(GuidanceInput(id="ctrl", text="apply immediately", mode="interrupt"))
        return {"ok": True, "status": "running", "job_id": record["job_id"]}

    monkeypatch.setattr("run.tools.runtime.execute_tool", execute)

    class Provider(MediaProvider):
        def create(self, request):
            number = len(self.requests)
            response = super().create(request)
            if number == 1:
                _finish(tmp_path, "alice", job_ids["first"], exit_code=0, output_tail=["FIRST TERMINAL"])
                if ending != "completed":
                    _finish(tmp_path, "alice", job_ids["second"], exit_code=0)
                    if ending == "cancelled":
                        cancelled.set()
                    else:
                        raise ProviderError("offline", status_code=403, retryable=False)
            elif number == 2:
                _finish(tmp_path, "alice", job_ids["second"], exit_code=0, output_tail=["SECOND TERMINAL"])
            return response

    provider = Provider([ChatResponse(text="", tool_calls=[
        ToolCall(label, "shell", {"action": "run", "background": True, "label": label})
        for label in ("first", "second")
    ])])
    events = list(iter_request_events(
        {"user": "alice", "source": "web", "session_id": session, "prompt": "start",
         "_guidance_queue": mailbox, "_auto_retry": False},
        root=tmp_path, provider_factory=lambda _: provider, cancel_event=cancelled,
    ))
    requests = [request.model_dump_json() for request in provider.requests]
    assert "apply after both jobs" not in requests[0]
    assert "apply after both jobs" not in requests[1]
    assert "apply immediately" in requests[1]
    details = {detail["id"]: detail for detail in events[-1].metadata["guidance_details"]}
    applied = [event.metadata["id"] for event in events if event.type == "guidance_applied"]
    assert not mailbox.accepting and mailbox.qsize() == 0
    if ending == "completed":
        assert len(requests) == 4
        assert "FIRST TERMINAL" in requests[2] and "apply after both jobs" not in requests[2]
        assert requests[3].index("SECOND TERMINAL") < requests[3].index("apply after both jobs")
        assert applied == ["ctrl", "alt"]
        assert details["alt"]["status"] == "applied"
    else:
        assert len(requests) == 2
        assert "alt" not in applied
        assert details["alt"]["status"] == "not_applied" and details["alt"]["ok"] is False
        assert details["alt"]["reason"] == f"run_{ending}"


def test_closed_web_mailbox_returns_explicit_failure_not_fake_next_turn_queue(tmp_path):
    make_root(tmp_path)
    service = WebRunService(tmp_path)
    active = ActiveRun("run_closed_guidance", "alice", "scope")
    service._active_runs[active.run_id] = active
    active.guidance.close()
    result = service.submit_guidance(
        "alice", active.run_id, "late", source="web", session_id="scope", guidance_id="late",
    )
    assert result["ok"] is False
    assert result["status"] == "not_applied" and result["error"]["message"]
    assert active.guidance.qsize() == 0


def test_terminal_failure_attachment_details_do_not_expose_local_paths():
    from run.conversation import guidance_failure_details

    details = guidance_failure_details([GuidanceInput(
        id="asset", uploaded_files=[{
            "name": "note.txt", "path": "E:/private/user/note.txt",
            "absolute_path": "E:/private/user/note.txt", "asset_id": "asset_note",
            "data": "private-inline-data",
        }],
    )], "preparation_failed")
    serialized = json.dumps(details)
    assert "note.txt" in serialized
    assert "E:/private" not in serialized and "private-inline-data" not in serialized
    assert details[0]["status"] == "not_applied"


def test_provisional_provider_failure_keeps_mailbox_open_and_replays_guidance(tmp_path, monkeypatch):
    make_root(tmp_path)
    tool(tmp_path, "echo")
    mailbox = GuidanceMailbox()
    late = GuidanceInput(id="retry-guidance", text="keep the revision")
    provider = MediaProvider([
        ChatResponse(text="", tool_calls=[ToolCall("first", "echo", {})]),
        ProviderError("temporary outage", status_code=503, retryable=True),
        ChatResponse(text="recovered"),
    ])

    def execute(*args, **kwargs):
        assert mailbox.offer(late)[0]
        return {"ok": True}

    monkeypatch.setattr("run.tools.runtime.execute_tool", execute)
    events = []
    for event in iter_request_events(
        {"user": "alice", "source": "web", "session_id": "guidance-retry",
         "prompt": "start", "_guidance_queue": mailbox},
        root=tmp_path, provider_factory=lambda _: provider,
    ):
        if event.type == "retrying":
            assert mailbox.accepting
        events.append(event)
    assert len([event for event in events if event.type == "retrying"]) == 1
    assert events[-1].type == "done"
    details = events[-1].metadata["guidance_details"]
    assert len(details) == 1 and details[0]["id"] == late.id
    assert details[0]["status"] == "applied" and details[0]["ok"] is True
    assert "keep the revision" in provider.requests[-1].model_dump_json()
    assert not mailbox.accepting


def test_guidance_with_remaining_request_budget_still_applies_in_order(tmp_path):
    make_root(tmp_path)
    mailbox = GuidanceMailbox()

    class BoundaryProvider(MediaProvider):
        def create(self, request):
            if not self.requests:
                assert mailbox.offer(GuidanceInput(id="first", text="first revision"))[0]
                assert mailbox.offer(GuidanceInput(id="second", text="second revision"))[0]
            return super().create(request)

    provider = BoundaryProvider([ChatResponse(text="draft"), ChatResponse(text="revised")])
    events = list(iter_request_events(
        {"user": "alice", "source": "web", "session_id": "normal-guidance",
         "prompt": "start", "_guidance_queue": mailbox},
        root=tmp_path, provider_factory=lambda _: provider,
    ))
    assert events[-1].type == "done"
    assert len(events[-1].metadata["guidance"]) == 2
    details = events[-1].metadata["guidance_details"]
    assert [item["id"] for item in details] == ["first", "second"]
    assert all(item["ok"] is True and item["status"] == "applied" for item in details)
    assert len(provider.requests) == 2
    wire = provider.requests[-1].model_dump_json()
    assert wire.index("first revision") < wire.index("second revision")
    assert wire.count("first revision") == 1
    assert wire.count("second revision") == 1


def test_web_custom_event_source_settles_accepted_guidance_at_final_publish(tmp_path):
    from events import RunEvent

    make_root(tmp_path)

    def source(request, **kwargs):
        assert request["_guidance_queue"].offer(GuidanceInput(id="custom", text="late"))[0]
        yield RunEvent(type="done", metadata={"committed": False, "status": "completed"})

    service = WebRunService(tmp_path, event_source=source)
    events = list(service.stream_chat(
        "alice", "custom-source", "start", cancel_event=threading.Event(),
        run_id="run_custom_source",
    ))
    details = events[-1].metadata["guidance_details"]
    assert details[0]["id"] == "custom" and details[0]["ok"] is False
    assert details[0]["status"] == "not_applied"
