"""R1 3.3: classification wins over remote retry flags and framework defects."""
from __future__ import annotations

import email.message
import io
import json
import urllib.error
import urllib.request
from unittest.mock import Mock

import pytest

from provider.adapters.gateway import KemoGatewayAdapter
from provider.schema import ChatResponse, ProviderError
from run.agents.retry_policy import _agent_tool_failure_is_retryable
from run.engine import iter_request_events
from run.retry import should_retry, view_from_event, view_from_exception
from events import RunEvent
from tests.media_input_support import MediaProvider, make_root
from tests.agents.test_general_worker import workspace  # noqa: F401
from run.agents import AgentRunner


@pytest.mark.parametrize("error_type", [KeyError, TypeError, AttributeError, AssertionError])
def test_framework_exception_is_not_retried_or_classified_as_provider_failure(tmp_path, monkeypatch, error_type):
    make_root(tmp_path)
    provider = MediaProvider([error_type("offline internal bug"), ChatResponse(text="must not retry")])
    sleep = Mock(side_effect=AssertionError("must not back off for internal errors"))
    monkeypatch.setattr("provider.retry.time.sleep", sleep)
    events = list(iter_request_events(
        {"user": "alice", "source": "web", "session_id": "internal", "prompt": "start"},
        root=tmp_path, provider_factory=lambda _: provider,
    ))
    assert len(provider.requests) == 1
    assert events[-1].type == "error" and events[-1].metadata["committed"] is True
    assert not any(event.type == "retrying" for event in events)
    failure = events[-1].error
    assert failure is not None
    assert failure["exception_type"] == error_type.__name__
    assert events[-1].metadata.get("stop_reason") != "provider_exception"
    sleep.assert_not_called()


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 418, 422, 425, 451])
@pytest.mark.parametrize("declared", [None, False, True])
def test_deterministic_http_status_wins_over_server_retryable(status, declared):
    kwargs = {} if declared is None else {"retryable": declared}
    error = ProviderError("offline", status_code=status, category="gateway_error", **kwargs)
    assert not should_retry(view_from_exception(error))
    assert not should_retry(view_from_event(RunEvent(
        type="error", error={"status_code": status, "retryable": True, "category": "connection_error"},
    )))


@pytest.mark.parametrize("status", [408, 429, 500, 501, 502, 503, 504, 599])
@pytest.mark.parametrize("declared", [None, False, True])
def test_transient_status_is_retryable_unless_explicitly_disabled(status, declared):
    kwargs = {} if declared is None else {"retryable": declared}
    error = ProviderError("offline", status_code=status, category="gateway_error", **kwargs)
    assert should_retry(view_from_exception(error)) is (declared is not False)


@pytest.mark.parametrize("error_type", [KeyError, TypeError, AttributeError, AssertionError])
def test_internal_error_cannot_be_promoted_by_retry_flag_or_status(error_type):
    error = error_type("offline")
    error.retryable = True
    error.retryable_declared = True
    error.status_code = 503
    assert not should_retry(view_from_exception(error))


def test_subagent_tool_retry_classifier_uses_deterministic_precedence():
    classifier = _agent_tool_failure_is_retryable
    assert not classifier({"error": {
        "category": "validation_error", "status_code": 422, "retryable": True,
    }}, "failed")
    assert not classifier({"error": {
        "exception_type": "KeyError", "status_code": 503, "retryable": True,
    }}, "failed")
    assert classifier({"error": {"category": "connection_error"}}, "failed")
    assert not classifier({"error": {"category": "connection_error", "retryable": False}}, "failed")


@pytest.mark.parametrize("status,declared,expected", [
    (400, True, False), (401, True, False), (403, True, False),
    (404, True, False), (422, True, False), (425, True, False),
    (408, True, True), (429, True, True), (501, None, True),
    (503, False, False), (503, True, True), (599, None, True),
])
def test_gateway_http_flags_can_only_narrow_transient_statuses(monkeypatch, status, declared, expected):
    detail = {"message": "offline"}
    if declared is not None:
        detail["retryable"] = declared
    http_error = urllib.error.HTTPError(
        "https://gateway.test/model/responses", status, "offline", email.message.Message(),
        io.BytesIO(json.dumps({"error": detail}).encode()),
    )
    opener = Mock(side_effect=http_error)
    monkeypatch.setattr(urllib.request, "urlopen", opener)
    adapter = KemoGatewayAdapter({
        "base_url": "https://gateway.test", "model": "offline", "api_key": "test-only",
    })
    with pytest.raises(ProviderError) as caught:
        adapter._open(urllib.request.Request(adapter.responses_url))
    assert caught.value.status_code == status
    assert caught.value.retryable is expected
    assert should_retry(view_from_exception(caught.value)) is expected
    assert opener.call_count == 1


def test_arbitrary_remote_retryable_flag_does_not_authorize_unknown_errors():
    assert not should_retry(view_from_event(RunEvent(
        type="error", error={"category": "validation_error", "retryable": True},
    )))
    assert not should_retry(view_from_event(RunEvent(
        type="error", error={"category": "unknown_kind", "retryable": True},
    )))


def test_legacy_connection_error_event_still_retries():
    assert should_retry(view_from_event(RunEvent(
        type="error", error={"exception_type": "ProviderConnectionError"},
    )))


def test_final_deterministic_error_does_not_advertise_remote_retryable_true(tmp_path):
    make_root(tmp_path)
    provider = MediaProvider([ProviderError(
        "bad request", status_code=400, category="gateway_error", retryable=True,
    )])
    events = list(iter_request_events(
        {"user": "alice", "source": "web", "session_id": "status-400", "prompt": "start"},
        root=tmp_path, provider_factory=lambda _: provider,
    ))
    assert len(provider.requests) == 1
    assert events[-1].metadata["committed"] is True
    assert events[-1].metadata["retryable"] is False
    failure = events[-1].error
    assert failure is not None
    assert failure["retryable"] is False


@pytest.mark.parametrize("error_type", [KeyError, TypeError, AttributeError, AssertionError])
def test_child_provider_framework_error_does_not_consume_retry_budget(workspace, error_type):
    root, config = workspace
    provider = MediaProvider([error_type("offline child framework error")])
    with pytest.raises(error_type):
        AgentRunner(root, "alice", config=config, provider_factory=lambda _: provider).run(
            "general_worker", {"task": "offline"}, source="web", session_id="child-internal",
        )
    assert len(provider.requests) == 1
