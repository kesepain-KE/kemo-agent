"""The real public entry, with temporary users and native fake Providers only."""
from __future__ import annotations

from provider.protocol.models import IncompleteDetails, Usage
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
from unittest.mock import patch

import pytest

from events import RunEvent
from provider.protocol.enums import (
    ErrorCode,
    IncompleteReason,
    ResponseStatus,
    StreamEventType,
)
from provider.protocol.models import (
    KemoResponse,
    MessageItem,
    ToolCallItem,
    UnifiedError,
    text_from_content,
)
from provider.protocol.streaming import ProviderStreamEvent
from provider.schema import ProviderError
from run.cards import CardConflictError, CardStore, IntegrationStore, compact
from run.conversation import GuidanceMailbox
from run.engine import handle_request, iter_request_events
from run.infra import EngineError
from run.tools import ToolDefinition, ToolRegistry, ToolRuntime


@pytest.fixture
def root(tmp_path, monkeypatch):
    # Different temporary gateways reuse this fake identity; their declared
    # capacities are not evidence for one another or later test modules.
    monkeypatch.setattr("run.extensions.model_capabilities._capability_cache", {})
    (tmp_path / "config").mkdir()
    user = tmp_path / "users" / "alice"
    user.mkdir(parents=True)
    (tmp_path / "agents.md").write_text("MANUAL", encoding="utf-8")
    (tmp_path / "config" / "global_soul.md").write_text("GLOBAL", encoding="utf-8")
    (user / "user_soul.md").write_text("PERSONA", encoding="utf-8")
    (tmp_path / "config" / "global_config.yaml").write_text(
        json.dumps({"agents": {"output_reserve": 64}, "provider_runtime": {"max_retries": 0}}),
        encoding="utf-8",
    )
    (user / "user_config.yaml").write_text(json.dumps({
        "schema_version": 1,
        "provider": {"type": "kemo", "model": "fake", "base_url": "http://127.0.0.1:1",
                     "api_key_env": "KEMO_API_KEY", "stream": False},
    }), encoding="utf-8")
    monkeypatch.setenv("KEMO_API_KEY", "fake-test-only")
    return tmp_path


def response(request, text="done", calls=()):
    calls = list(calls)
    return KemoResponse(
        request_id=request.request_id, model=request.model,
        status=ResponseStatus.REQUIRES_ACTION if calls else ResponseStatus.COMPLETED,
        output=[
            *([MessageItem.text("assistant", text, phase="final_answer")] if text else []),
            *calls,
        ],
        usage=Usage.model_validate({"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}),
    )


def call(name="work", call_id="callid_a", **arguments):
    return ToolCallItem(id="call_" + call_id, call_id=call_id, name=name, arguments=arguments)


def definition(root, function, name="work", schema=None):
    return ToolDefinition(
        name=name, description="test", input_schema=schema or {"type": "object"},
        version="1", enabled=True, entrypoint="tool.py:run", source="test",
        directory=root, _callable=function,
    )


def run(root, provider, *, text="hello", tools=(), **extra):
    return iter_request_events(
        {"user": "alice", "source": "web", "session_id": "s1", "prompt": text, **extra},
        root=root, provider_factory=lambda _: provider,
        tool_registry_factory=lambda *_: ToolRegistry({tool.name: tool for tool in tools}),
    )


def history(root):
    return CardStore(root, "alice").raw_page("web", "s1", limit=500)


def test_public_runtime_writes_only_cards_and_restores_native_history(root):
    seen = []

    class Provider:
        def create(self, request):
            seen.append(request)
            user = next(item for item in reversed(request.input)
                        if isinstance(item, MessageItem) and item.role == "user")
            return response(request, "reply:" + text_from_content(user.content))

    history_dir = root / "users" / "alice" / "history"
    history_dir.mkdir()
    legacy = history_dir / "history.sqlite3"
    legacy.write_bytes(b"DO NOT OPEN OR MIGRATE THIS")
    for text in ("one", "two"):
        events = list(run(root, Provider(), text=text))
        assert events[-1].type == "done" and events[-1].metadata["status"] == "completed"
        assert events[-1].metadata["usage"]["total_tokens"] == 5
    assert [str(item.role) for item in seen[1].input] == ["user", "assistant", "user"]
    assert [card.raw_kind for card in history(root)] == ["user_text", "agent_text"] * 2
    assert [card.raw_level for card in history(root)] == ["initial", "final"] * 2
    assert legacy.read_bytes() == b"DO NOT OPEN OR MIGRATE THIS"
    assert sorted(path.name for path in history_dir.iterdir()) == ["cards.sqlite3", "history.sqlite3"]
    assert "PERSONA" in seen[0].system_prompt
    assert not any("window" in str(path) for path in root.rglob("*.json")
                   if "user_config" not in path.name and "global_config" not in path.name)


def test_virtual_tail_changes_without_rewriting_fixed_or_historical_prefix(root):
    seen = []

    class Provider:
        def create(self, request):
            seen.append(request)
            return response(request)

    with patch("run.conversation.main_orchestration.read_dynamic_tail",
               side_effect=[{"perception": {"text": "OLD"}}, {"perception": {"text": "NEW"}}]):
        list(run(root, Provider(), text="first"))
        list(run(root, Provider(), text="second"))
    assert seen[0].system_prompt == seen[1].system_prompt
    assert seen[0].input[0] == seen[1].input[0]
    assert "OLD" in text_from_content(seen[0].input[-1].content)
    assert "NEW" in text_from_content(seen[1].input[-1].content)
    assert all("perception" not in card.raw_payload_json for card in history(root))


def test_invalid_parallel_batch_executes_nothing(root):
    executions = []
    tool = definition(root, lambda **_: executions.append("BAD"), schema={
        "type": "object", "properties": {"required": {"type": "string"}}, "required": ["required"],
    })

    class Provider:
        def create(self, request):
            return response(request, calls=[call(required="valid"),
                                             call(call_id="callid_b", other="invalid")])

    events = list(run(root, Provider(), tools=[tool]))
    assert events[-1].type == "error"
    assert not executions
    results = [card for card in history(root) if card.raw_kind == "tool_result"]
    assert results and all(card.raw_payload["result"]["execution"]["executed"] is False for card in results)


def test_model_argument_correction_is_bounded_and_executes_only_the_valid_batch(root):
    executed, requests = [], []
    tool = definition(root, lambda **kwargs: (executed.append(kwargs["value"]) or {"ok": True}),
                      schema={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]})
    class Provider:
        def create(self, request):
            requests.append(request)
            if len(requests) == 1:
                return response(request, calls=[call(other="invalid")])
            if len(requests) == 2:
                return response(request, calls=[call(call_id="callid_corrected", value="once")])
            return response(request, "done")
    events = list(run(root, Provider(), tools=[tool]))
    assert events[-1].metadata["status"] == "completed"
    assert executed == ["once"] and len(requests) == 3
    assert events[-1].metadata["tool_calls"] == 1
    results = [card.raw_payload for card in history(root) if card.raw_kind == "tool_result"]
    assert len(results) == 2 and results[0]["execution"]["status"] == "invalid_arguments"


def test_response_level_invalid_tool_arguments_without_output_is_repaired(root):
    executed, requests = [], []
    tool = definition(
        root,
        lambda **kwargs: (executed.append(kwargs["value"]) or {"ok": True}),
        schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    )

    class Provider:
        def create(self, request):
            requests.append(request)
            if len(requests) == 1:
                return KemoResponse(
                    request_id=request.request_id,
                    model=request.model,
                    status=ResponseStatus.INCOMPLETE,
                    output=[],
                    incomplete_details=IncompleteDetails.model_validate({
                        "reason": IncompleteReason.INVALID_TOOL_ARGUMENTS,
                        "details": {
                            "invalid_tool_calls": [
                                {
                                    "name": "work",
                                    "call_id": "callid_rejected",
                                    "arguments_raw": '{"value":',
                                }
                            ]
                        },
                    }),
                )
            if len(requests) == 2:
                return response(request, calls=[call(value="corrected")])
            return response(request, "done")

    events = list(run(root, Provider(), tools=[tool]))

    assert (
        events[-1].type == "done"
        and events[-1].metadata["status"] == "completed"
    ), events[-1].to_dict()
    assert len(requests) == 3
    assert len({item.request_id for item in requests}) == 3
    assert "provider_tool_argument_repair" in requests[1].system_prompt
    assert "work" in requests[1].system_prompt
    assert executed == ["corrected"]
    assert events[-1].metadata["tool_argument_retries"] == 1


def test_stream_invalid_tool_arguments_error_enters_bounded_repair_loop(root):
    executed, requests = [], []
    tool = definition(
        root,
        lambda **kwargs: (executed.append(kwargs["value"]) or {"ok": True}),
        schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    )

    class Provider:
        mode = "kemo"

        def stream(self, request, *, cancel_event=None):
            del cancel_event
            requests.append(request)
            if len(requests) == 1:
                yield ProviderStreamEvent(
                    type=StreamEventType.ERROR,
                    sequence=0,
                    request_id=request.request_id,
                    response_id="resp_invalid_arguments",
                    error=UnifiedError(
                        type="validation_error",
                        code=ErrorCode.VALIDATION_ERROR,
                        message="invalid tool arguments",
                        details={
                            "reason": "invalid_tool_arguments",
                            "invalid_tool_calls": [
                                {"name": "work", "call_id": "callid_rejected"}
                            ],
                        },
                    ),
                )
            else:
                result = (
                    response(request, calls=[call(value="corrected")])
                    if len(requests) == 2
                    else response(request, "done")
                )
                yield ProviderStreamEvent(
                    type=StreamEventType.RESPONSE_COMPLETED,
                    sequence=0,
                    request_id=request.request_id,
                    response_id=result.id,
                    response=result,
                )

    events = list(run(root, Provider(), tools=[tool], stream=True))

    assert (
        events[-1].type == "done"
        and events[-1].metadata["status"] == "completed"
    ), events[-1].to_dict()
    assert len(requests) == 3
    assert len({item.request_id for item in requests}) == 3
    assert "provider_tool_argument_repair" in requests[1].system_prompt
    assert "work" in requests[1].system_prompt
    assert executed == ["corrected"]
    assert events[-1].metadata["tool_argument_retries"] == 1


def test_cleanup_database_failure_preserves_the_original_failure_and_does_not_retry_cleanup(root):
    class Provider:
        def create(self, request):
            raise RuntimeError("original-provider-failure")
    with patch.object(IntegrationStore, "finish", side_effect=OSError("cleanup disk failure")) as finish:
        events = list(run(root, Provider()))
    assert finish.call_count == 1
    error = events[-1]
    assert error.error is not None
    assert error.type == "error" and error.error["exception_type"] == "RuntimeError"
    assert "original-provider-failure" in error.error["message"]
    assert error.metadata["cleanup_errors"] == [{"exception_type": "OSError"}]
    assert error.metadata["terminal_committed"] is False
    with CardStore(root, "alice").db() as db:
        assert db.execute("SELECT response_closed FROM card_integrations").fetchone()[0] == 0


def test_a_model_body_does_not_complete_a_live_tool(root):
    entered, release = threading.Event(), threading.Event()
    requests = []

    def work():
        entered.set()
        assert release.wait(5)
        return {"ok": True, "output": "actual result"}

    class Provider:
        def create(self, request):
            requests.append(request)
            return response(request, "stage", calls=[call()]) if len(requests) == 1 else response(request, "body")

    events = []
    with ThreadPoolExecutor(1) as executor:
        future = executor.submit(lambda: events.extend(run(root, Provider(), tools=[definition(root, work)])))
        try:
            assert entered.wait(3)
            time.sleep(.1)
            assert len(requests) == 1
            assert not future.done()
            assert not any(event.type == "done" for event in events)
        finally:
            release.set()
        future.result(5)
    assert events[-1].type == "done"
    cards = history(root)
    tool_result = next(card for card in cards if card.raw_kind == "tool_result")
    assert tool_result.raw_payload["original_call"] == {"name": "work", "arguments": {}}
    assert tool_result.raw_payload["execution_id"].startswith("exec_")
    assert cards[-1].raw_level == "final"
    assert any(card.raw_kind == "agent_text" and card.raw_level == "interim" for card in cards)


def test_guidance_waits_for_sync_tool_and_is_applied_before_final(root):
    entered, release, guided = threading.Event(), threading.Event(), threading.Event()
    mailbox = GuidanceMailbox()
    requests = []

    def work():
        entered.set()
        assert release.wait(5)
        return {"ok": True, "result": "late"}

    class Provider:
        def create(self, request):
            requests.append(request)
            combined = "\n".join(text_from_content(item.content) for item in request.input
                                 if isinstance(item, MessageItem))
            if "LATEST DIRECTION" in combined:
                guided.set()
            return response(request, "interim", calls=[call()]) if len(requests) == 1 else response(request)

    with ThreadPoolExecutor(1) as executor:
        future = executor.submit(lambda: list(run(
            root, Provider(), tools=[definition(root, work)], _guidance_queue=mailbox)))
        try:
            assert entered.wait(3)
            assert mailbox.offer({"id": "g1", "text": "LATEST DIRECTION"})[0]
            assert not guided.wait(.1)
            release.set()
            assert guided.wait(3), "queued guidance must reach the next request after tool completion"
            assert not future.done()
        finally:
            release.set()
        events = future.result(5)
    assert any(event.type == "guidance_applied" for event in events)
    guidance = [card for card in history(root) if card.raw_level == "guidance"]
    assert len(guidance) == 1 and guidance[0].raw_payload["text"] == "LATEST DIRECTION"
    assert not mailbox.accepting


def test_full_tool_result_over_old_inline_limit_is_in_raw(root):
    huge = "COMPLETE-RESULT" * 10000
    requests = []

    class Provider:
        def create(self, request):
            requests.append(request)
            return response(request, calls=[call()]) if len(requests) == 1 else response(request)

    events = list(run(root, Provider(), tools=[definition(root, lambda: {"ok": True, "data": huge})]))
    assert events[-1].type == "done", events[-1]
    result = next(card for card in history(root) if card.raw_kind == "tool_result")
    assert result.raw_payload["result"]["result"]["data"] == huge
    assert "content_omitted" not in result.raw_payload_json


def test_retry_does_not_restart_integration_or_reexecute_a_completed_tool(root):
    executions = []
    calls = []
    config = root / "config" / "global_config.yaml"
    config.write_text(json.dumps({"provider_runtime": {"max_retries": 1},
                                 "agents": {"output_reserve": 64}}), encoding="utf-8")

    class Provider:
        def create(self, request):
            calls.append(request)
            if len(calls) == 1:
                return response(request, calls=[call()])
            if len(calls) == 2:
                raise ProviderError("temporary", category="transport_error", retryable=True)
            return response(request)

    def work():
        executions.append(True)
        return {"ok": True}

    events = list(run(root, Provider(), tools=[definition(root, work)]))
    assert events[-1].type == "done"
    assert len(executions) == 1
    assert any(event.type == "retrying" for event in events)
    assert len([card for card in history(root) if card.raw_level == "initial"]) == 1
    assert len([card for card in history(root) if card.raw_kind == "tool_call"]) == 1
    assert calls[1].request_id == calls[2].request_id
    assert calls[1].model_dump(mode="json") == calls[2].model_dump(mode="json")


@pytest.mark.parametrize("partial_kind", ["reasoning_delta", "text_delta"])
def test_partial_response_recovers_without_restarting_tools(root, monkeypatch, partial_kind):
    config = root / "config" / "global_config.yaml"
    config.write_text(json.dumps({"provider_runtime": {"max_retries": 5},
                                 "agents": {"output_reserve": 64}}), encoding="utf-8")
    monkeypatch.setattr("provider.retry.RetryManager.delay", lambda *args, **kwargs: 0)
    executions, requests = [], []
    def projected(provider, request, **kwargs):
        requests.append(request)
        if len(requests) == 1:
            accepted = response(request, text="", calls=[call()])
        elif len(requests) <= 6:
            yield RunEvent(type=partial_kind, content=f"unfinished {len(requests)}")
            raise ProviderError("upstream busy", category="upstream_error", status_code=503)
        else:
            accepted = response(request, text="recovered")
        kwargs["on_response"](accepted)
        yield RunEvent(type="done")

    monkeypatch.setattr("run.conversation.provider_exchange.provider_events", projected)

    def work():
        executions.append(True)
        return {"ok": True}

    events = list(run(root, object(), tools=[definition(root, work)]))
    assert events[-1].type == "done" and events[-1].metadata["status"] == "completed"
    assert events[-1].metadata["text"] == "recovered"
    assert len(executions) == 1
    assert len(requests) == 7
    assert len({request.request_id for request in requests[1:]}) == 6
    assert all(request.parent_request_id == previous.request_id
               for previous, request in zip(requests[1:], requests[2:]))
    assert all(request.input == requests[1].input for request in requests[2:])
    retries = [event for event in events if event.type == "retrying"]
    assert [event.metadata["next_attempt"] for event in retries] == [2, 3, 4, 5, 6]
    assert all(event.metadata["partial_response"] is True for event in retries)
    snapshots = [card for card in history(root) if card.raw_payload.get("retry_snapshot")]
    assert len(snapshots) == 5
    assert len([card for card in history(root) if card.raw_kind == "tool_call"]) == 1


def test_transient_terminal_failure_starts_a_new_request_and_records_its_usage(root, monkeypatch):
    config = root / "config" / "global_config.yaml"
    config.write_text(json.dumps({"provider_runtime": {"max_retries": 1}}), encoding="utf-8")
    monkeypatch.setattr("provider.retry.RetryManager.delay", lambda *args, **kwargs: 0)
    requests = []

    class Provider:
        def create(self, request):
            requests.append(request)
            if len(requests) == 1:
                return KemoResponse(
                    request_id=request.request_id, model=request.model, status=ResponseStatus.FAILED,
                    output=[], error=UnifiedError.model_validate({"type": "upstream_error", "code": "PROVIDER_UNAVAILABLE",
                                      "message": "busy", "retryable": True}),
                    usage=Usage.model_validate({"input_tokens": 11, "output_tokens": 2}),
                )
            return response(request)

    events = list(run(root, Provider()))
    assert events[-1].type == "done"
    assert len(requests) == 2 and requests[0].request_id != requests[1].request_id
    assert requests[1].parent_request_id == requests[0].request_id
    assert requests[1].input == requests[0].input
    assert events[-1].usage is not None
    assert events[-1].usage["provider_request_count"] == 2
    assert events[-1].usage["input_tokens"] == 13
    retry = next(event for event in events if event.type == "retrying")
    assert retry.metadata["partial_response"] is False


@pytest.mark.parametrize("failures,retryable,expected_attempts", [
    (1, True, 2),
    (6, True, 6),
    (1, False, 1),
])
def test_provider_unavailable_terminal_uses_existing_retry_budget(
    root, monkeypatch, failures, retryable, expected_attempts,
):
    config = root / "config" / "global_config.yaml"
    config.write_text(json.dumps({"provider_runtime": {"max_retries": 5}}), encoding="utf-8")
    monkeypatch.setattr("provider.retry.RetryManager.delay", lambda *args, **kwargs: 0)
    requests = []

    class Provider:
        def create(self, request):
            requests.append(request)
            if len(requests) <= failures:
                return KemoResponse(
                    request_id=request.request_id, model=request.model, status=ResponseStatus.FAILED,
                    error=UnifiedError.model_validate({
                        "type": "provider_unavailable",
                        "code": "PROVIDER_UNAVAILABLE",
                        "message": "Cannot reliably connect to CommandCode.",
                        "retryable": retryable,
                        "retry_scope": "same_sequence",
                        "details": {"exception_type": "RemoteProtocolError"},
                    }),
                )
            return response(request, text="recovered")

    events = list(run(root, Provider()))
    assert len(requests) == expected_attempts
    assert len({request.request_id for request in requests}) == expected_attempts
    assert all(request.parent_request_id == previous.request_id
               for previous, request in zip(requests, requests[1:]))
    assert all(request.input == requests[0].input for request in requests[1:])
    assert len([event for event in events if event.type == "retrying"]) == expected_attempts - 1
    if failures == 1 and retryable:
        assert events[-1].type == "done"
        assert events[-1].metadata["text"] == "recovered"
    else:
        assert events[-1].type == "error"
        error = events[-1].error
        assert error is not None
        assert error["category"] == "provider_unavailable"
        assert error["attempt_count"] == expected_attempts
        assert error["retry_budget_exhausted"] is retryable
        assert events[-1].metadata["committed"] is True


def test_terminal_failure_retains_provider_details_in_history(root):
    class Provider:
        def create(self, request):
            raise ProviderError("safe failure detail", category="upstream_error", status_code=503)

    events = list(run(root, Provider()))
    assert events[-1].type == "error"
    with CardStore(root, "alice").db() as db:
        record = json.loads(db.execute("SELECT record_json FROM card_integrations").fetchone()[0])
    assert record["error"]["message"] == "Provider 请求失败；保留错误类别和状态码供诊断"
    assert record["error"]["category"] == "upstream_error"
    assert record["error"]["status_code"] == 503
    assert record["error"]["attempt_count"] == 1
    assert record["failed_request_id"].startswith("req_")


@pytest.mark.parametrize("failure", ["validation", "auth", "internal", "cancelled", "exhausted", "media"])
def test_partial_output_does_not_bypass_non_retryable_boundaries(root, monkeypatch, failure):
    config = root / "config" / "global_config.yaml"
    config.write_text(json.dumps({"provider_runtime": {"max_retries": 5}}), encoding="utf-8")
    requests = []

    def projected(provider, request, **kwargs):
        requests.append(request)
        yield RunEvent(type="reasoning_delta", content="partial")
        if failure == "media":
            yield RunEvent(type="media_output", result={"kind": "image"})
        if failure == "internal":
            raise TypeError("internal")
        error = ProviderError("do not replay", category={
            "validation": "validation_error", "auth": "auth_error",
        }.get(failure, "upstream_error"), status_code=503, retryable=True,
                              retry_budget_exhausted=failure == "exhausted")
        error.cancelled = failure == "cancelled"
        raise error

    monkeypatch.setattr("run.conversation.provider_exchange.provider_events", projected)
    events = list(run(root, object()))
    assert len(requests) == 1
    assert not any(event.type == "retrying" for event in events)
    assert events[-1].type in {"done", "error"}
    assert events[-1].metadata["status"] in {"failed", "cancelled"}


def test_raw_registration_and_execution_result_stay_related_after_compression(root):
    store = CardStore(root, "alice")
    integration = IntegrationStore(store, "web", "s1", "integration_test")
    integration.start()
    store.append("web", "s1", integration.id, "user_text", {"text": "context " * 2000}, level="initial")
    original = {"name": "work", "arguments": {"value": "parameters must not be shortened"}}
    execution, created = integration.register_tool("callid_a", original)
    assert created
    compact(store, "web", "s1", summarize=lambda _: {
        "current_work": {"pending_execution": execution["execution_id"]},
        "success_cases": [], "failure_cases": [], "background": [],
    })
    result = integration.tool_result(execution["execution_id"], {"ok": True}, status="completed", duration_ms=1)
    assert result.raw_payload["original_call"] == original
    assert result.raw_payload["execution_id"] == execution["execution_id"]
    assert integration.pending() == (0, 1)
    with pytest.raises(CardConflictError):
        integration.finish("completed")
    integration.request_started()
    integration.request_finished(accepted_seq=result.seq)
    assert integration.pending() == (0, 0)
    integration.finish("completed")
    assert store.snapshot("web", "s1").cards[0].view_kind == "summary"
    assert len(store.raw_page("web", "s1")) == 3


def test_watchdog_is_not_tool_exit_and_late_result_is_not_reexecuted(root):
    entered, release = threading.Event(), threading.Event()
    executions = []
    store = CardStore(root, "alice")
    integration = IntegrationStore(store, "web", "s1", "integration_timeout")
    integration.start()
    runtime = ToolRuntime(integration)

    def work():
        entered.set()
        executions.append(True)
        assert release.wait(5)
        return {"ok": True, "late": "full original"}

    runtime.start(call(), definition(root, work), context={
        "root": str(root), "user": "alice", "source": "web", "session_id": "s1",
    }, timeout=.01)
    try:
        assert entered.wait(2)
        deadline = time.monotonic() + 3
        events = []
        while not any(
            event.type == "tool_call_result"
            and event.metadata["status"] == "timed_out_running"
            for event in events
        ) and time.monotonic() < deadline:
            runtime.wait(.05)
            events += runtime.events()
        notice = next((
            event for event in events
            if event.type == "tool_call_result"
            and event.metadata["status"] == "timed_out_running"
        ), None)
        assert notice is not None
        assert notice.metadata["execution_live"]
        integration.finish("cancelled")
        with pytest.raises(CardConflictError):
            IntegrationStore(store, "web", "s1", "new").start()
    finally:
        release.set()
    deadline = time.monotonic() + 3
    while integration.pending()[0] and time.monotonic() < deadline:
        runtime.wait(.05)
    assert integration.pending()[0] == 0
    results = [card for card in store.raw_page("web", "s1") if card.raw_kind == "tool_result"]
    assert len(results) == 2
    assert results[-1].raw_payload["result"] == {
        "ok": True, "result": {"ok": True, "late": "full original"},
    }
    assert len(executions) == 1
    IntegrationStore(store, "web", "s1", "new").start()
