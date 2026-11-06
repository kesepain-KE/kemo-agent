"""Recovery exercises the real integration; no tools or RAW are replayed."""
import json

import pytest

from provider.protocol.models import KemoResponse, MessageItem
from provider.retry import RetryManager
from provider.schema import ProviderError
from run.cards import (
    CardCompactionError, CardStore, ProviderSummarizer, compact,
    estimate_request_tokens, prepare_request,
)
from run.conversation.provider_events import is_context_length_exceeded
from tests.cards.test_runtime import call, definition, response, run
from tests.cards.test_runtime import root as runtime_root
from tests.cards.test_store import store as card_store
from tests.cards.test_store import add, summary


@pytest.fixture
def root(tmp_path, monkeypatch):
    return runtime_root.__wrapped__(tmp_path, monkeypatch)


@pytest.fixture
def store(tmp_path):
    return card_store.__wrapped__(tmp_path)


def checkpoint(request, value=None):
    return KemoResponse(request_id=request.request_id, model=request.model, status="completed",
                        output=[MessageItem.text("assistant", json.dumps(value or summary()))])


def test_provider_overflow_compacts_same_integration_without_replaying_tools(root):
    store = CardStore(root, "alice")
    store.ensure_session("web", "s1")
    add(store, text="older context " * 1000)
    requests, effects = [], []

    def work(**kwargs):
        effects.append("once")
        return {"ok": True, "result": "observed evidence " * 1000}

    class Provider:
        main_calls = 0

        def create(self, request):
            requests.append(request)
            if request.metadata.get("capability") != "conversation":
                return checkpoint(request)
            self.main_calls += 1
            if self.main_calls == 1:
                return response(request, text="", calls=[call("work")])
            if self.main_calls == 2:
                raise ProviderError("context_length_exceeded", status_code=400)
            return response(request, text="verified result")

    events = list(run(root, Provider(), tools=[definition(root, work)]))
    assert events[-1].type == "done", events[-1].error
    assert events[-1].metadata["status"] == "completed"
    assert effects == ["once"]
    main_requests = [r for r in requests if r.metadata.get("capability") == "conversation"]
    assert len(main_requests) == 3
    assert len({r.metadata["integration_id"] for r in main_requests}) == 1
    assert len({r.request_id for r in requests}) == len(requests)
    assert estimate_request_tokens(main_requests[-1].system_prompt, main_requests[-1].input,
                                   main_requests[-1].tools) < estimate_request_tokens(
        main_requests[-2].system_prompt, main_requests[-2].input, main_requests[-2].tools)
    assert any(e.type == "context_compression" and e.metadata.get("trigger") == "provider_overflow"
               and e.metadata.get("status") == "ready" for e in events)
    raw = store.raw_page("web", "s1", limit=500)
    assert sum(c.raw_kind == "tool_call" for c in raw) == 1
    assert sum(c.raw_kind == "tool_result" for c in raw) == 1


@pytest.mark.parametrize("status,message", [
    (401, "unauthorized"), (403, "context_length_exceeded"),
    (413, "context_length_exceeded"), (400, "invalid parameter"),
])
def test_non_context_errors_do_not_start_compaction(root, status, message):
    requests = []

    class Provider:
        def create(self, request):
            requests.append(request)
            raise ProviderError(message, status_code=status)

    events = list(run(root, Provider()))
    assert events[-1].type == "error"
    assert len(requests) == 1
    assert not any(e.type == "context_compression" for e in events)
    assert not is_context_length_exceeded(ProviderError(message, status_code=status))


def test_preventative_failure_keeps_fitting_request_but_manual_failure_is_explicit(store):
    for _ in range(6):
        add(store)
    before = store.snapshot("web", "s1")
    baseline = prepare_request(store, "web", "s1", system_prompt="", dynamic_tail=lambda: {},
                               tools=[], input_limit=100000)
    limit = int(max(baseline.occupancy.total, baseline.wire_tokens) * 1.1)

    def fail(batch):
        error = CardCompactionError("candidate unavailable")
        error.compression_usage = {"input_tokens": 7, "output_tokens": 2}
        raise error

    prepared = prepare_request(store, "web", "s1", system_prompt="", dynamic_tail=lambda: {},
                               tools=[], input_limit=limit, summarize=fail)
    assert prepared.compaction is None and prepared.compaction_error is not None
    assert prepared.compaction_error.compression_usage["input_tokens"] == 7
    assert prepared.wire_tokens <= limit
    assert store.snapshot("web", "s1") == before
    with pytest.raises(CardCompactionError, match="candidate unavailable"):
        prepare_request(store, "web", "s1", system_prompt="", dynamic_tail=lambda: {},
                        tools=[], input_limit=limit, summarize=fail, force_compact=True)


def test_useful_checkpoint_can_exceed_soft_ratio_without_exceeding_capacity(store):
    for _ in range(10):
        add(store)
    selected = []

    def summarize(batch):
        selected.append(batch)
        value = {"checkpoint": "useful continuity "}
        while store.meter.tokens("summary", value) <= batch.summary_budget:
            value["checkpoint"] += "useful continuity "
        assert store.meter.tokens("summary", value) <= batch.summary_limit
        return value

    result = compact(store, "web", "s1", summarize=summarize)
    assert selected[0].history_target < result.after_tokens < result.before_tokens


def test_markdown_checkpoint_is_accepted_without_a_four_key_schema(store):
    for _ in range(4):
        add(store)

    class Provider:
        def create(self, request):
            return KemoResponse(request_id=request.request_id, model=request.model, status="completed",
                                output=[MessageItem.text("assistant",
                                    "# Current task\nContinue the verified investigation.\n"
                                    "## Pending\nCheck the final test output.")])

    result = compact(store, "web", "s1", summarize=ProviderSummarizer(
        Provider(), {}, model="test", system_prompt="", input_limit=10000,
        retry=RetryManager(max_retries=0)))
    assert "checkpoint" in result.snapshot.cards[0].view_payload


def test_rejected_candidate_changes_repair_prompt_not_only_request_id(store):
    for _ in range(4):
        add(store)
    requests = []

    class Provider:
        def create(self, request):
            requests.append(request)
            if len(requests) == 1:
                return KemoResponse(request_id=request.request_id, model=request.model, status="completed",
                                    output=[MessageItem.text("assistant", "{}")])
            return checkpoint(request)

    compact(store, "web", "s1", summarize=ProviderSummarizer(
        Provider(), {}, model="test", system_prompt="", input_limit=10000,
        retry=RetryManager(max_retries=1, base_delay=0)))
    assert len(requests) == 2
    assert requests[0].request_id != requests[1].request_id
    assert requests[0].input != requests[1].input
    assert "Repair:" in requests[1].input[-1].content[0].text


def test_summary_provider_overflow_repacks_smaller_readonly_input(store):
    for _ in range(8):
        add(store)
    requests = []

    class Provider:
        def create(self, request):
            requests.append(request)
            if len(requests) == 1:
                raise ProviderError("maximum context length exceeded", status_code=400)
            return checkpoint(request)

    result = compact(store, "web", "s1", summarize=ProviderSummarizer(
        Provider(), {}, model="test", system_prompt="", input_limit=10000,
        retry=RetryManager(max_retries=1, base_delay=0)))
    assert len(requests) >= 2 and result.after_tokens < result.before_tokens
    assert estimate_request_tokens(requests[1].system_prompt, requests[1].input, requests[1].tools) < \
        estimate_request_tokens(requests[0].system_prompt, requests[0].input, requests[0].tools)


def test_late_failure_of_oversized_card_never_publishes_partial_checkpoint(store):
    add(store, text="huge original tool payload " * 4000)
    before = store.snapshot("web", "s1")
    requests = []

    class Provider:
        def create(self, request):
            requests.append(request)
            if len(requests) > 1:
                raise RuntimeError("later fragment failed")
            return checkpoint(request)

    with pytest.raises(RuntimeError, match="later fragment failed"):
        compact(store, "web", "s1", summarize=ProviderSummarizer(
            Provider(), {}, model="test", system_prompt="", input_limit=3500,
            retry=RetryManager(max_retries=0)))
    assert len(requests) == 2
    assert store.snapshot("web", "s1") == before
    assert store.raw_page("web", "s1")[0].raw_payload_json == before.cards[0].raw_payload_json
