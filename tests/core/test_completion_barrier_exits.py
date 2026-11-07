"""R2 C1: every controlled/exceptional exit closes the root once."""
from __future__ import annotations

import json

import pytest

from provider.schema import ChatResponse, ProviderError, ToolCall
from run.agents import TaskCoordinator
from run.cards import IntegrationStore
from run.engine import iter_request_events
from tests.core.test_agent_task_lifecycle import (
    JOBS, setup, root_record,  # noqa: F401
)
from tests.media_input_support import MediaProvider


def dispatch(*, wait=False):
    return ChatResponse(text="", tool_calls=[ToolCall(
        id="call-worker", name="subagent_dispatch",
        arguments={"action": "call", "agent": "worker", "wait": wait},
    )])


def request():
    return dict(user="alice", source="web", session_id="scope", prompt="perform task")


@pytest.mark.parametrize("status", ["completed", "limited", "failed", "cancelled", "interrupted"])
def test_finish_is_idempotent_and_does_not_rewrite_terminal_truth(tmp_path, status):
    (tmp_path / "users/alice").mkdir(parents=True)
    coordinator = TaskCoordinator(tmp_path, "alice", "web", "scope", "run", request())
    coordinator.ownership("call")
    coordinator.finish(status)
    first = coordinator.ledger.get(coordinator.id, "web", "scope")
    coordinator.finish("interrupted")
    again = coordinator.ledger.get(coordinator.id, "web", "scope")
    assert first["status"] == again["status"] == status
    assert again["finished_at"] == first["finished_at"]
    assert again["updated_at"] == first["updated_at"]


def test_limited_exit_closes_root_as_limited_not_failed_or_waiting(setup, monkeypatch):
    root, _ = setup
    from types import SimpleNamespace
    monkeypatch.setattr("run.conversation.main_orchestration.detect_task_plan_creation_boundary",
        lambda **kwargs: SimpleNamespace(stop_reason="task_plan_created", plan_id="test",
                                         awaiting_user_approval=True)
        if kwargs["result_payload"].get("result", {}).get("status") == "completed" else None)
    JOBS["worker"] = lambda cancel: {"answer": "ready"}
    provider = MediaProvider([dispatch(wait=True)])
    events = list(iter_request_events(request(), root=root, provider_factory=lambda _: provider))
    assert events[-1].type == "done" and events[-1].metadata["status"] == "limited", events[-1].to_dict()
    assert root_record(root)["status"] == "limited"
    assert root_record(root)["finished_at"]


@pytest.mark.parametrize("route", ["completed", "failed", "cancelled"])
def test_completed_error_and_generator_close_all_close_the_root(setup, route):
    root, _ = setup
    JOBS["worker"] = lambda cancel: {"answer": "ready"}
    responses: list[ChatResponse | BaseException] = [
        dispatch(), ChatResponse(text="draft"), ChatResponse(text="merged"),
    ]
    if route == "failed":
        responses[1] = ProviderError("small invalid parameter", status_code=400)
    provider = MediaProvider(responses)
    iterator = iter_request_events(request(), root=root, provider_factory=lambda _: provider)
    if route == "cancelled":
        for event in iterator:
            if event.type == "tool_call_result":
                break
        iterator.close()
    else:
        events = list(iterator)
        assert events[-1].type == ("error" if route == "failed" else "done")
    record = root_record(root)
    assert record["status"] == route and record["finished_at"]


def test_integration_commit_failure_cannot_leave_premature_completed_root(setup, monkeypatch):
    root, _ = setup
    JOBS["worker"] = lambda cancel: {"answer": "ready"}
    original = IntegrationStore.finish
    attempts = []

    def fail_first(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("isolated terminal commit failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(IntegrationStore, "finish", fail_first)
    provider = MediaProvider([dispatch(), ChatResponse(text="draft"), ChatResponse(text="merged")])
    events = list(iter_request_events(request(), root=root, provider_factory=lambda _: provider))
    assert attempts and events[-1].type == "error"
    assert root_record(root)["status"] == "failed"


def test_limited_integration_commit_failure_is_not_locked_as_limited(setup, monkeypatch):
    root, _ = setup
    from types import SimpleNamespace
    monkeypatch.setattr("run.conversation.main_orchestration.detect_task_plan_creation_boundary",
        lambda **kwargs: SimpleNamespace(stop_reason="task_plan_created", plan_id="test",
                                         awaiting_user_approval=True)
        if kwargs["result_payload"].get("result", {}).get("status") == "completed" else None)
    JOBS["worker"] = lambda cancel: {"answer": "ready"}
    original, attempts = IntegrationStore.finish, []

    def fail_first(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("isolated limited commit failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(IntegrationStore, "finish", fail_first)
    events = list(iter_request_events(
        request(), root=root, provider_factory=lambda _: MediaProvider([dispatch(wait=True)]),
    ))
    assert events[-1].type == "error", events[-1].to_dict()
    assert len(attempts) == 2
    assert root_record(root)["status"] == "failed"
