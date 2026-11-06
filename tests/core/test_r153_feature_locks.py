"""Offline feature locks required before changing R1 tool success semantics.

T4 lives in test_message_route_policy::test_bound_user_does_not_bypass_known_sender_identity.
"""
from __future__ import annotations

from tests.card_history_support import session_history

import copy
import logging
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from plugins.subagent_dispatch import tool as dispatch
from provider.schema import ChatResponse, ToolCall
from run.agents import AgentRunResult
from run.agents import TaskCoordinator
from run.engine import handle_request, iter_request_events



from run.infra.runtime_diagnostics import runtime_event_snapshot
from run.tasks import PlanConflictError, PlanStore, normalize_plan, persist_agent_result
from run.tasks.service import PlanGenerationError
from run.tools import ToolDefinition, ToolRegistry
from tests.media_input_support import MediaProvider, make_root


def test_t1_concurrent_requests_in_one_session_keep_both_committed_rounds(tmp_path):
    make_root(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    attempted = threading.Event()
    errors = []

    class HeldProvider(MediaProvider):
        def create(self, request):
            entered.set()
            assert release.wait(10), "test did not release first Provider"
            return super().create(request)

    first = HeldProvider([ChatResponse(text="first answer")])
    second = MediaProvider([ChatResponse(text="second answer")])

    def submit(prompt, provider, signal=None):
        try:
            if signal is not None:
                signal.set()
            handle_request(
                {"user": "alice", "source": "web", "session_id": "same", "prompt": prompt},
                root=tmp_path, provider_factory=lambda _: provider,
            )
        except BaseException as exc:
            errors.append(exc)

    one = threading.Thread(target=submit, args=("first prompt", first), daemon=True)
    two = threading.Thread(target=submit, args=("second prompt", second, attempted), daemon=True)
    one.start()
    try:
        assert entered.wait(10), "first request never reached Provider"
        two.start()
        assert attempted.wait(10), "second request was never submitted"
    finally:
        release.set()
        one.join(15)
        if two.ident is not None:
            two.join(15)
    assert not one.is_alive() and not two.is_alive()
    assert not errors
    archive = session_history(tmp_path, 'alice', 'web', 'same')
    assert archive is not None
    window = archive.refresh()
    assert len(window.integrations) == 2
    assert [item["content"] for item in window.messages] == [
        "first prompt", "first answer", "second prompt", "second answer",
    ]
    assert "first answer" in second.requests[0].model_dump_json()




@pytest.mark.parametrize("stop_reason", ["timeout", "tool_budget_exhausted"])
def test_t3_timed_out_child_is_failed_and_not_acknowledged_as_success(tmp_path, monkeypatch, stop_reason):
    make_root(tmp_path)
    definition = SimpleNamespace(
        name="worker", timeout=1, execution="sync", executor="builtin:llm",
    )
    monkeypatch.setattr(dispatch, "_public", lambda *args: [definition])
    monkeypatch.setattr(
        dispatch, "prepare_main_agent_invocation",
        lambda **kwargs: SimpleNamespace(synchronous_only=False, payload={}),
    )
    # The scheduler's returned segment has really exited. No live execution or
    # fabricated terminal ledger row is involved in this return-contract test.
    scheduler = SimpleNamespace(
        submit=lambda *args, **kwargs: "timed-out-task",
        wait=lambda *args, **kwargs: AgentRunResult(
            agent="worker", data={"answer": "partial"}, raw_text="partial",
            usage={}, model="offline-test",
            metadata={"partial": True, "stop_reason": stop_reason, "resumable": True},
        ),
    )
    monkeypatch.setattr(dispatch, "get_agent_scheduler", lambda *args, **kwargs: scheduler)
    acknowledge = Mock()
    monkeypatch.setattr(TaskCoordinator, "acknowledge_sync", acknowledge)
    tool = ToolDefinition(
        name="subagent_dispatch", description="production dispatch, stub scheduler",
        input_schema={"type": "object"}, version="1", enabled=True,
        entrypoint="tool.py:run", source="plugins", directory=tmp_path,
        _callable=dispatch.run,
    )
    provider = MediaProvider([
        ChatResponse(text="", tool_calls=[ToolCall(
            id="timeout-call", name="subagent_dispatch",
            arguments={"action": "call", "agent": "worker", "wait": True},
        )]),
        ChatResponse(text="The child timed out."),
    ])
    events = list(iter_request_events(
        {"user": "alice", "source": "web", "session_id": "timeout", "prompt": "work"},
        root=tmp_path, provider_factory=lambda _: provider,
        tool_registry_factory=lambda *args: ToolRegistry({"subagent_dispatch": tool}),
    ))
    result = next(event for event in events if event.type == "tool_call_result")
    assert "result" in result.result, result.result
    assert result.result["ok"] is False
    assert result.result["result"]["ok"] is False
    assert result.metadata["status"] == "failed"
    acknowledge.assert_not_called()


def test_t5_stale_plan_revision_is_rejected_without_overwriting_newer_plan(tmp_path, monkeypatch):
    make_root(tmp_path)
    steps = [{
        "step_id": "step_1", "title": "original", "description": "do work",
        "tool_name": None, "tool_arguments": {}, "critical": True,
    }]
    store = PlanStore(tmp_path, "alice")
    stale = store.create(normalize_plan(title="original", description="goal", user="alice", steps=steps))
    newer = store.update(stale["plan_id"], lambda plan: {**plan, "title": "newer edit"})
    monkeypatch.setattr("run.tasks.service._runtime_tools", lambda *args: ToolRegistry({}))
    with pytest.raises(PlanConflictError) as conflict:
        persist_agent_result(
            root=tmp_path, user="alice", input_data={"existing_plan": stale, "goal": "goal"},
            result_data={"action": "edit", "title": "stale edit", "steps": steps},
            config={"tools": {"enabled": False}},
        )
    assert conflict.value.code == "plan_revision_conflict"
    assert store.read(stale["plan_id"]) == newer


def test_t5_model_cannot_set_plan_status_and_attempt_is_recorded(tmp_path, caplog):
    make_root(tmp_path)
    steps = [{
        "step_id": "step_1", "title": "work", "description": "do work",
        "tool_name": None, "tool_arguments": {}, "critical": True,
    }]
    result = {
        "action": "create",
        "title": "server-owned status",
        "description": "goal",
        "status": "completed",
        "steps": steps,
    }

    with caplog.at_level(logging.WARNING, logger="run.tasks.service"):
        persisted = persist_agent_result(
            root=tmp_path,
            user="alice",
            input_data={"goal": "goal"},
            result_data=result,
            config={"tools": {"enabled": False}},
        )

    assert persisted is not None
    assert persisted["status"] == "pending"
    assert "忽略模型返回的计划 status" in caplog.text


def test_completed_plan_cannot_accept_persisted_agent_append(tmp_path, monkeypatch):
    make_root(tmp_path)
    steps = [{
        "step_id": "step_1", "title": "done", "description": "already done",
        "tool_name": None, "tool_arguments": {}, "critical": True,
        "status": "completed", "result": {"ok": True},
    }]
    store = PlanStore(tmp_path, "alice")
    completed = store.create(normalize_plan(
        title="completed", description="goal", user="alice",
        status="completed", steps=steps,
    ))
    monkeypatch.setattr("run.tasks.service._runtime_tools", lambda *args: ToolRegistry({}))

    with pytest.raises(PlanGenerationError, match="只能编辑"):
        persist_agent_result(
            root=tmp_path,
            user="alice",
            input_data={"existing_plan": completed, "goal": "goal"},
            result_data={
                "action": "edit",
                "title": "bad append",
                "steps": steps + [{
                    "step_id": "step_2", "title": "new", "description": "new",
                    "tool_name": None, "tool_arguments": {}, "critical": True,
                }],
            },
            config={"tools": {"enabled": False}},
        )
    assert store.read(completed["plan_id"])["revision"] == completed["revision"]
