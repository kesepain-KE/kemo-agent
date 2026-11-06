"""Usage is completed once per request across main, summary and child paths."""
from types import SimpleNamespace

import pytest

from provider.protocol.models import KemoResponse, MessageItem
from provider.protocol.streaming import ProviderStreamEvent
from provider.tokenizer import count_request, count_text
from run.cards import CardStore
from run.history import session_statistics
from tests.card_history_support import seed_history
from tests.cards.test_runtime import call, definition, root, run
from tests.cards.test_store import add, store, summary


@pytest.mark.parametrize("usage", [
    {},
    {"input_tokens": 17},
    {"input_tokens": 9999, "output_tokens": 9999, "measurement": {"mode": "estimated"}},
])
def test_main_request_fills_usage_and_only_reported_input_calibrates(root, usage):
    requests = []

    class Provider:
        def create(self, request):
            requests.append(request)
            return KemoResponse(
                request_id=request.request_id, model=request.model, status="completed",
                output=[MessageItem.text("assistant", "done", phase="final_answer")], usage=usage,
            )

    events = list(run(root, Provider()))
    assert events[-1].type == "done", events[-1].error
    result = events[-1].usage
    assert result["provider_request_count"] == 1
    assert result["output_tokens"] == count_text("done")
    expected_input = (
        17 if usage.get("input_tokens") == 17 else
        count_request(requests[0].system_prompt, requests[0].input, requests[0].tools)
    )
    assert result["input_tokens"] == expected_input
    assert result["total_tokens"] == expected_input + count_text("done")
    with CardStore(root, "alice").db() as db:
        observations = db.execute("SELECT actual_tokens FROM card_meter_observations").fetchall()
    assert [row[0] for row in observations] == ([17] if expected_input == 17 else [])


def test_stream_preserves_usage_from_prior_events_and_does_not_accumulate_snapshots(root):
    class Provider:
        def stream(self, request):
            response = KemoResponse(
                request_id=request.request_id, model=request.model, status="completed",
                output=[MessageItem.text("assistant", "done", phase="final_answer")],
            )
            yield ProviderStreamEvent(
                type="response.created", sequence=0,
                request_id=request.request_id, response_id=response.id,
            )
            for sequence in (1, 2):
                yield ProviderStreamEvent(
                    type="usage.updated", sequence=sequence, previous_sequence=sequence - 1,
                    request_id=request.request_id, response_id=response.id,
                    usage={"input_tokens": 17},
                )
            yield ProviderStreamEvent(
                type="response.completed", sequence=3, previous_sequence=2,
                request_id=request.request_id, response_id=response.id, response=response,
            )

    events = list(run(root, Provider(), stream=True))
    assert events[-1].type == "done", events[-1].error
    assert events[-1].usage["input_tokens"] == 17
    assert events[-1].usage["output_tokens"] == count_text("done")
    assert events[-1].usage["provider_request_count"] == 1


def test_session_cache_rate_is_live_and_partial_snapshots_do_not_count_twice(root):
    messages = [{"role": "user", "content": "old"}, {"role": "assistant", "content": "old reply"}]
    seed_history(root, "alice", "web", "s1", messages, usage={
        "input_tokens": 100, "cached_input_tokens": 80, "provider_request_count": 1,
    })
    seed_history(root, "alice", "app", "s1", messages, usage={
        "input_tokens": 1000, "cached_input_tokens": 1000,
    })
    seed_history(root, "alice", "web", "other", messages, usage={
        "input_tokens": 1000, "cached_input_tokens": 0,
    })

    class Provider:
        def stream(self, request):
            response = KemoResponse(
                request_id=request.request_id, model=request.model, status="completed",
                output=[MessageItem.text("assistant", "done", phase="final_answer")],
            )
            snapshots = [
                {"input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 1},
                {"input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 1},
                {"output_tokens": 2},
                {"cached_input_tokens": 90},
            ]
            for sequence, usage in enumerate(snapshots):
                yield ProviderStreamEvent(
                    type="usage.updated", sequence=sequence,
                    previous_sequence=sequence - 1 if sequence else None,
                    request_id=request.request_id, response_id=response.id, usage=usage,
                )
            yield ProviderStreamEvent(
                type="response.completed", sequence=4, previous_sequence=3,
                request_id=request.request_id, response_id=response.id, response=response,
            )

    events = []
    for event in run(root, Provider(), stream=True):
        events.append(event)
        if event.type != "usage":
            continue
        usage = session_statistics(root, "alice", "web", "s1")["usage"]
        assert usage["input_tokens"] == 200
        assert usage["provider_request_count"] == 2
        assert usage["cache_hit_rate"] == event.metadata["session_cache_hit_rate"]
        with CardStore(root, "alice").db() as db:
            assert db.execute("SELECT count(*) FROM card_integrations WHERE response_closed=0").fetchone()[0] == 1
    assert events[-1].type == "done", events[-1].error
    rates = [event.metadata["session_cache_hit_rate"] for event in events if event.type == "usage"]
    assert rates[:4] == [0.7, 0.7, 0.7, 0.85]
    assert rates[-1] == 0.85
    saved = session_statistics(root, "alice", "web", "s1")["usage"]
    assert saved["input_tokens"] == 200
    assert saved["cached_input_tokens"] == 170
    assert saved["provider_request_count"] == 2
    assert saved["cache_hit_rate"] == 0.85


@pytest.mark.parametrize("stream", [False, True])
def test_terminal_only_usage_updates_cache_rate_before_tool_task_finishes(root, stream):
    class Provider:
        requests = 0

        def create(self, request):
            self.requests += 1
            return KemoResponse(
                request_id=request.request_id, model=request.model,
                status="requires_action" if self.requests == 1 else "completed",
                output=[call()] if self.requests == 1 else [MessageItem.text("assistant", "done", phase="final_answer")],
                usage={"input_tokens": 100, "cached_input_tokens": 60 if self.requests == 1 else 100,
                       "output_tokens": 2, "total_tokens": 102},
            )

        def stream(self, request):
            response = self.create(request)
            yield ProviderStreamEvent(
                type="response.completed", sequence=0,
                request_id=request.request_id, response_id=response.id, response=response,
            )

    provider = Provider()
    iterator = run(root, provider, stream=stream, tools=[definition(root, lambda **_: {"ok": True})])
    first = next(iterator)
    assert first.type == "usage", first.error
    assert first.metadata["session_cache_hit_rate"] == 0.6
    assert session_statistics(root, "alice", "web", "s1")["usage"]["cache_hit_rate"] == 0.6
    assert provider.requests == 1
    remaining = list(iterator)
    assert remaining[-1].type == "done", remaining[-1].error
    assert remaining[-1].usage["provider_request_count"] == 2
    assert session_statistics(root, "alice", "web", "s1")["usage"]["cache_hit_rate"] == 0.8


def test_stream_failure_retains_usage_already_reported_to_the_user(root):
    from provider.schema import ProviderError

    class Provider:
        def stream(self, request):
            yield ProviderStreamEvent(
                type="usage.updated", sequence=0,
                request_id=request.request_id, response_id="resp_live_usage",
                usage={"input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 2},
            )
            raise ProviderError("stream disconnected", retryable=False)

    events = list(run(root, Provider(), stream=True))
    assert events[0].metadata["session_cache_hit_rate"] == 0.6
    assert events[-1].type == "error"
    saved = session_statistics(root, "alice", "web", "s1")["usage"]
    assert saved["input_tokens"] == 100
    assert saved["cached_input_tokens"] == 60
    assert saved["provider_request_count"] == 1
    assert saved["cache_hit_rate"] == 0.6


def test_subagent_attempt_fills_missing_usage_before_aggregation():
    from provider.protocol.models import KemoRequest
    from run.agents.budgets import provider_attempt

    request = KemoRequest.create(model="other-model", system_prompt="sys", input=[MessageItem.text("user", "hello")])
    context = SimpleNamespace(compression_budget=None, continuation=None)
    state = SimpleNamespace(usage={})

    class Provider:
        def create(self, wire):
            return KemoResponse(
                request_id=wire.request_id, model=wire.model, status="completed",
                output=[MessageItem.text("assistant", "done", phase="final_answer")],
            )

    response = provider_attempt(context, {}, state, Provider(), request)
    assert response.usage.input_tokens == count_request("sys", request.input, [])
    assert response.usage.output_tokens == count_text("done")
    assert response.usage.measurement.mode == "estimated"


def test_summary_request_records_local_compensation_when_gateway_omits_usage(store):
    import json
    from provider.retry import RetryManager
    from run.cards import ProviderSummarizer, compact

    requests = []
    text = json.dumps(summary())

    class Provider:
        def create(self, request):
            requests.append(request)
            return KemoResponse(
                request_id=request.request_id, model=request.model, status="completed",
                output=[MessageItem.text("assistant", text)],
            )

    for _ in range(4):
        add(store)
    result = compact(store, "web", "s1", summarize=ProviderSummarizer(
        Provider(), {}, model="other-model", system_prompt="sys",
        input_limit=100000, retry=RetryManager(max_retries=0),
    ))
    assert len(requests) == 1
    assert result.usage["input_tokens"] == count_request("sys", requests[0].input, requests[0].tools)
    assert result.usage["output_tokens"] == count_text(text)
    assert result.usage["provider_request_count"] == 1
    assert result.usage["estimated"]
