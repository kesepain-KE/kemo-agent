"""Capacity failures reproduced across the actual compression/request chain."""
import base64
import json
from types import SimpleNamespace

import pytest

from provider.protocol.models import ImageContent, KemoResponse, MessageItem, ModelCapabilities
from provider.retry import RetryManager
from provider.schema import ProviderError
from run.cards import (
    CardCapacityError, CardCompactionError, CardMeter, CardStore, ProviderSummarizer,
    compact, estimate_request_tokens, estimate_tokens, prepare_request,
)
from run.context import CompressionBudgetExhausted
from run.engine import iter_request_events
from run.extensions import context_input_budget
from tests.cards.test_store import add, store, summary
from tests.cards.test_runtime import root
from tests.media_input_support import PNG


def reply(request, value=None, *, usage=None):
    return KemoResponse(
        request_id=request.request_id, model=request.model, status="completed",
        output=[MessageItem.text(
            "assistant", json.dumps(summary() if value is None else value),
            phase="final_answer",
        )],
        usage=usage or {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
    )


def test_output_reservation_respects_model_maximum_before_subtracting_context():
    capabilities = SimpleNamespace(
        limits=SimpleNamespace(max_input_tokens=7000, context_window=6000, max_output_tokens=512),
        extensions={},
    )
    budget = context_input_budget(10000, capabilities, output_reserve=8192)
    assert budget["output_reserve"] == 512
    assert budget["effective_token_limit"] == 5488


def test_summary_output_fits_the_same_model_as_the_main_request(store):
    for _ in range(8):
        add(store)
    requests = []

    class Provider:
        def create(self, request):
            requests.append(request)
            assert request.generation.max_output_tokens <= 512
            return reply(request)

    capabilities = SimpleNamespace(
        limits=SimpleNamespace(max_input_tokens=8000, context_window=9000, max_output_tokens=512),
        extensions={},
    )
    summarizer = ProviderSummarizer(
        Provider(), {}, model="test", system_prompt="", input_limit=8000,
        capabilities=capabilities, configured_token_limit=9000,
        retry=RetryManager(max_retries=0),
    )
    result = compact(store, "web", "s1", summarize=summarizer)
    assert result.after_tokens < result.before_tokens
    assert len(requests) == 1


def test_large_prefix_is_summarized_in_bounded_requests_before_one_commit(store):
    for _ in range(16):
        add(store)
    before = store.snapshot("web", "s1")
    raw = store.raw_page("web", "s1")
    requests = []

    class Provider:
        def create(self, request):
            assert store.snapshot("web", "s1") == before
            requests.append(request)
            return reply(request)

    result = compact(store, "web", "s1", summarize=ProviderSummarizer(
        Provider(), {}, model="test", system_prompt="", input_limit=3500,
        retry=RetryManager(max_retries=0),
    ))
    assert 1 < len(requests) <= result.absorbed_count
    assert result.usage["input_tokens"] == 100 * len(requests)
    assert result.usage["output_tokens"] == 20 * len(requests)
    assert result.usage["provider_request_count"] == len(requests)
    assert result.snapshot.view_epoch == before.view_epoch + 1
    assert [card.raw_payload_json for card in raw] == [
        card.raw_payload_json for card in store.raw_page("web", "s1")
    ]


def test_invalid_summary_retries_count_all_received_usage(store):
    for _ in range(4):
        add(store)
    requests = []

    class Provider:
        def create(self, request):
            requests.append(request)
            return reply(request, {} if len(requests) == 1 else summary())

    result = compact(store, "web", "s1", summarize=ProviderSummarizer(
        Provider(), {}, model="test", system_prompt="", input_limit=10000,
        retry=RetryManager(max_retries=1, base_delay=0),
    ))
    assert len(requests) == 2
    assert result.usage["input_tokens"] == 200
    assert result.usage["output_tokens"] == 40
    assert result.usage["provider_request_count"] == 2
    assert requests[0].request_id != requests[1].request_id


def test_transport_retry_reuses_the_exact_summary_request(store):
    for _ in range(4):
        add(store)
    requests = []

    class Provider:
        def create(self, request):
            requests.append(request)
            if len(requests) == 1:
                raise ProviderError("temporary", category="transport_error", retryable=True)
            return reply(request)

    result = compact(store, "web", "s1", summarize=ProviderSummarizer(
        Provider(), {}, model="test", system_prompt="unchanged", input_limit=10000,
        retry=RetryManager(max_retries=1, base_delay=0),
    ))
    assert len(requests) == 2
    assert requests[0].model_dump(mode="json") == requests[1].model_dump(mode="json")
    assert result.usage["provider_request_count"] == 1
    assert result.usage["input_tokens"] == 100


def test_running_summary_response_is_polled_by_original_response_id(store, monkeypatch):
    for _ in range(4):
        add(store)
    created, queries = [], []
    monkeypatch.setattr("run.cards.summarize.time.sleep", lambda _: None)

    class Provider:
        mode = "kemo"

        def create(self, request, *, cancel_event=None):
            created.append(request)
            return KemoResponse(
                id="resp_summary_running", request_id=request.request_id,
                model=request.model, status="incomplete",
                incomplete_details={"reason": "running"},
                usage={"input_tokens": 100},
            )

        def get_response(self, response_id):
            queries.append(response_id)
            completed = reply(created[0])
            return completed.model_copy(update={"id": response_id})

    result = compact(store, "web", "s1", summarize=ProviderSummarizer(
        Provider(), {}, model="test", system_prompt="", input_limit=10000,
        retry=RetryManager(max_retries=1, base_delay=0),
    ))
    assert result.after_tokens < result.before_tokens
    assert len(created) == 1
    assert queries == ["resp_summary_running"]
    assert result.usage["provider_request_count"] == 1
    assert result.usage["input_tokens"] == 100


def test_running_summary_response_that_never_finishes_respects_deadline(store, monkeypatch):
    import time

    clock = [0.0]
    fake_time = SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda delay: clock.__setitem__(0, clock[0] + delay))
    monkeypatch.setattr("run.context.compression_budget.time", fake_time)
    monkeypatch.setattr("run.cards.summarize.time", fake_time)
    monkeypatch.setattr("provider.retry.time", fake_time)
    for _ in range(4):
        add(store)
    before = store.snapshot("web", "s1")
    requests, queries = [], []

    class Provider:
        mode = "kemo"

        def create(self, request, *, cancel_event=None):
            requests.append(request)
            return KemoResponse(
                id="resp_stuck_running", request_id=request.request_id,
                model=request.model, status="incomplete",
                incomplete_details={"reason": "running"},
            )

        def get_response(self, response_id):
            queries.append(response_id)
            return KemoResponse(
                id=response_id, request_id=requests[0].request_id,
                model=requests[0].model, status="incomplete",
                incomplete_details={"reason": "running"},
            )

    with pytest.raises(CompressionBudgetExhausted) as caught:
        compact(store, "web", "s1", summarize=ProviderSummarizer(
            Provider(), {}, model="test", system_prompt="", input_limit=10000,
            timeout=.5, retry=RetryManager(max_retries=0),
        ))
    assert caught.value.reason == "deadline"
    assert len(requests) == 1 and queries
    assert set(queries) == {"resp_stuck_running"}
    assert store.snapshot("web", "s1") == before


def test_output_truncation_retries_only_with_a_larger_supported_budget(store):
    for _ in range(4):
        add(store)
    requests = []
    capabilities = SimpleNamespace(
        limits=SimpleNamespace(max_input_tokens=None, context_window=None,
                               max_output_tokens=4096),
        extensions={},
    )

    class Provider:
        def create(self, request):
            requests.append(request)
            if len(requests) == 1:
                return KemoResponse(
                    request_id=request.request_id, model=request.model,
                    status="incomplete",
                    incomplete_details={
                        "reason": "output_truncated",
                        "details": {"finish_reason": "length"},
                    },
                    usage={"input_tokens": 100, "output_tokens": 256,
                           "total_tokens": 356},
                )
            return reply(request)

    result = compact(store, "web", "s1", summarize=ProviderSummarizer(
        Provider(), {}, model="test", system_prompt="", input_limit=10000,
        capabilities=capabilities,
        retry=RetryManager(max_retries=1, base_delay=0),
    ))
    assert result.after_tokens < result.before_tokens
    assert len(requests) == 2
    assert requests[1].request_id != requests[0].request_id
    assert requests[1].generation.max_output_tokens > requests[0].generation.max_output_tokens
    assert result.usage["provider_request_count"] == 2
    assert result.usage["input_tokens"] == 200
    assert result.usage["output_tokens"] == 276


def test_content_filter_failure_is_diagnostic_and_never_blindly_retried(store):
    for _ in range(4):
        add(store)
    before = store.snapshot("web", "s1")
    raw_before = store.raw_page("web", "s1")
    requests = []

    class Provider:
        def create(self, request):
            requests.append(request)
            return KemoResponse(
                request_id=request.request_id, model=request.model,
                status="incomplete",
                incomplete_details={
                    "reason": "content_filtered",
                    "details": {"finish_reason": "content_filter"},
                },
                usage={"input_tokens": 100, "output_tokens": 5, "total_tokens": 105},
            )

    with pytest.raises(CardCompactionError) as caught:
        compact(store, "web", "s1", summarize=ProviderSummarizer(
            Provider(), {}, model="test", system_prompt="", input_limit=10000,
            retry=RetryManager(max_retries=3, base_delay=0),
        ))
    assert len(requests) == 1
    assert "status=incomplete" in str(caught.value)
    assert "reason=content_filtered" in str(caught.value)
    assert "finish_reason=content_filter" in str(caught.value)
    assert caught.value.response_status == "incomplete"
    assert caught.value.incomplete_reason == "content_filtered"
    assert caught.value.finish_reason == "content_filter"
    assert caught.value.compression_usage["input_tokens"] == 100
    assert store.snapshot("web", "s1") == before
    assert store.raw_page("web", "s1") == raw_before


def test_truncation_without_a_larger_output_ceiling_is_not_retried(store):
    for _ in range(4):
        add(store)
    requests = []

    class Provider:
        def create(self, request):
            requests.append(request)
            return KemoResponse(
                request_id=request.request_id, model=request.model,
                status="incomplete",
                incomplete_details={"reason": "output_truncated"},
            )

    with pytest.raises(CardCompactionError, match="recovery=unavailable"):
        compact(store, "web", "s1", summarize=ProviderSummarizer(
            Provider(), {}, model="test", system_prompt="", input_limit=10000,
            retry=RetryManager(max_retries=2, base_delay=0),
        ))
    assert len(requests) == 1


def test_history_target_also_fits_the_remaining_request_capacity(store):
    for _ in range(20):
        add(store)
    batches = []
    prepared = prepare_request(
        store, "web", "s1", system_prompt="x" * 24000,
        dynamic_tail=lambda: {}, tools=[], input_limit=10000,
        summarize=lambda batch: batches.append(batch) or summary(),
    )
    assert prepared.compaction is not None
    assert batches[0].history_target <= 10000 - estimate_tokens("x" * 24000)
    assert prepared.occupancy.total <= 10000 and prepared.wire_tokens <= 10000


@pytest.mark.parametrize("character", ["中", "\\", '"', "x"])
def test_summary_budget_reserves_its_native_message_envelope(store, character):
    for _ in range(20):
        add(store)
    batches = []

    def summarize(batch):
        batches.append(batch)
        value = summary()
        value["current_work"]["goal"] = ""
        while store.meter.tokens("summary", value) + 2 <= batch.summary_budget:
            value["current_work"]["goal"] += character
        return value

    prepared = prepare_request(
        store, "web", "s1", system_prompt="x" * 12000,
        dynamic_tail=lambda: {}, tools=[], input_limit=5000, summarize=summarize,
    )
    assert len(batches) == 1 and prepared.compaction is not None
    assert prepared.occupancy.total <= 5000 and prepared.wire_tokens <= 5000


def test_unproductive_automatic_compression_does_not_break_a_fitting_request(store):
    add(store, text="short")
    before = store.snapshot("web", "s1")
    calls = []
    prepared = prepare_request(
        store, "web", "s1", system_prompt="x" * 2400,
        dynamic_tail=lambda: {}, tools=[], input_limit=1000,
        summarize=lambda batch: calls.append(batch) or summary(),
    )
    assert prepared.wire_tokens <= 1000
    assert prepared.compaction is None and not calls
    assert store.snapshot("web", "s1") == before


def test_native_base64_is_not_charged_as_plain_text_or_truncated(store):
    add(store, text="inspect")
    data = base64.b64encode(PNG + b"\0" * 65536).decode("ascii")
    image = ImageContent(mime_type="image/png", source={"kind": "inline_base64", "data": data})
    item = MessageItem(id="msg_native_image", role="user", content=[image])
    prepared = prepare_request(
        store, "web", "s1", system_prompt="fixed", dynamic_tail=lambda: {},
        tools=[], input_limit=12000, virtual_items=[item], transport="chat",
    )
    assert prepared.wire_tokens < 12000 and prepared.occupancy.dynamic_tokens < 12000
    sent = prepared.snapshot.items()[-1].content[0]
    assert sent.source.data == data


def test_native_media_usage_does_not_inflate_later_text_only_requests(store):
    add(store, text="inspect")
    image = ImageContent(
        mime_type="image/png",
        source={"kind": "inline_base64", "data": base64.b64encode(PNG).decode("ascii")},
    )
    item = MessageItem(id="msg_calibrated_media", role="user", content=[image])
    prepared = prepare_request(
        store, "web", "s1", system_prompt="fixed " * 60, dynamic_tail=lambda: {},
        tools=[], input_limit=12000, virtual_items=[item], transport="chat",
    )
    predicted = prepared.wire_tokens
    store.observe_usage(prepared.snapshot, request_id="req_media_usage",
                        estimated=predicted, actual=predicted * 10)
    text_request = prepare_request(
        store, "web", "s1", system_prompt="fixed " * 60,
        dynamic_tail=lambda: {}, tools=[], input_limit=2000,
    )
    assert text_request.wire_tokens == estimate_request_tokens(
        "fixed " * 60, text_request.snapshot.items(), [])
    assert store.calibrated_tokens(
        predicted, items=prepared.snapshot.items()) >= predicted * 10


def test_calibration_isolated_by_provider_model_without_changing_tokenizer_key(store):
    store.meter = CardMeter.for_model("gateway", "model-a")
    prepared = prepare_request(
        store, "web", "s1", system_prompt="fixed", dynamic_tail=lambda: {},
        tools=[], input_limit=12000,
    )
    store.observe_usage(prepared.snapshot, request_id="req_model_a",
                        estimated=100, actual=200)

    model_a = CardMeter.for_model("gateway", "model-a")
    model_b = CardMeter.for_model("gateway", "model-b")
    assert model_a.key == model_b.key == store.meter.key
    store.meter = model_b
    assert store.calibrated_tokens(100, items=prepared.snapshot.items()) == 100
    store.meter = model_a
    assert store.calibrated_tokens(100, items=prepared.snapshot.items()) == 200


def test_binary_looking_tool_strings_are_still_charged_as_text():
    from provider.protocol.models import JsonContent, ToolResultItem

    data = base64.b64encode(b"\0" * 65536).decode("ascii")
    result = ToolResultItem(
        id="result_data", call_id="callid_data", name="data",
        content=[JsonContent(data={"source": {"kind": "inline_base64", "data": data}})],
    )
    assert estimate_request_tokens("", [result], []) > 10000


def test_calibrated_fixed_overflow_fails_before_modifying_history(store):
    add(store)
    before = store.snapshot("web", "s1")
    from run.cards.meter import request_calibration_key
    with store.db(write=True) as db:
        db.execute("INSERT INTO card_meter_observations VALUES(?,?,?,?,?,?)",
                   ("calibration", "snapshot", request_calibration_key(
                       store.meter.key, store.meter.calibration_scope, []),
                    100, 1000, "now"))
    calls = []
    with pytest.raises(CardCapacityError):
        prepare_request(
            store, "web", "s1", system_prompt="fixed " * 60,
            dynamic_tail=lambda: {}, tools=[], input_limit=1000,
            summarize=lambda batch: calls.append(batch) or summary(),
        )
    assert not calls and store.snapshot("web", "s1") == before


def test_fragment_failure_never_publishes_the_previous_provisional_summary(store):
    for _ in range(16):
        add(store)
    before = store.snapshot("web", "s1")
    requests = []

    class Provider:
        def create(self, request):
            requests.append(request)
            return reply(request, summary() if len(requests) == 1 else {})

    with pytest.raises(CardCompactionError) as caught:
        compact(store, "web", "s1", summarize=ProviderSummarizer(
            Provider(), {}, model="test", system_prompt="", input_limit=3500,
            retry=RetryManager(max_retries=0),
        ))
    assert len(requests) == 2
    assert caught.value.compression_usage["provider_request_count"] == 2
    assert store.snapshot("web", "s1") == before


def test_fragments_share_one_retry_allowance(store):
    for _ in range(16):
        add(store)
    before = store.snapshot("web", "s1")
    requests = []

    class Provider:
        def create(self, request):
            requests.append(request)
            return reply(request, summary() if len(requests) == 2 else {})

    with pytest.raises(CardCompactionError) as caught:
        compact(store, "web", "s1", summarize=ProviderSummarizer(
            Provider(), {}, model="test", system_prompt="", input_limit=3500,
            retry=RetryManager(max_retries=1, base_delay=0),
        ))
    assert len(requests) == 3
    assert caught.value.compression_usage["provider_request_count"] == 3
    assert store.snapshot("web", "s1") == before


def test_fragments_share_one_deadline_and_reject_late_results(store, monkeypatch):
    import time

    clock = [0.0]
    fake_time = SimpleNamespace(monotonic=lambda: clock[0], sleep=time.sleep)
    monkeypatch.setattr("run.context.compression_budget.time", fake_time)
    monkeypatch.setattr("run.cards.summarize.time", fake_time)
    monkeypatch.setattr("provider.retry.time", fake_time)
    for _ in range(16):
        add(store)
    before = store.snapshot("web", "s1")
    requests = []

    class Provider:
        def create(self, request):
            requests.append(request)
            clock[0] += .6
            return reply(request)

    with pytest.raises(CompressionBudgetExhausted):
        compact(store, "web", "s1", summarize=ProviderSummarizer(
            Provider(), {}, model="test", system_prompt="", input_limit=3500,
            timeout=1, retry=RetryManager(max_retries=0),
        ))
    assert len(requests) == 2
    assert store.snapshot("web", "s1") == before


def test_deadline_during_request_preparation_reports_compression_budget(store, monkeypatch):
    import time

    clock = [0.0]
    fake_time = SimpleNamespace(monotonic=lambda: clock[0], sleep=time.sleep)
    for module in ("run.context.compression_budget", "run.cards.summarize", "provider.retry"):
        monkeypatch.setattr(f"{module}.time", fake_time)
    for _ in range(4):
        add(store)
    before = store.snapshot("web", "s1")
    requests = []

    class Provider:
        def create(self, request):
            requests.append(request)
            return reply(request)

    def calibrate(tokens):
        clock[0] = 2
        return tokens

    with pytest.raises(CompressionBudgetExhausted) as caught:
        compact(store, "web", "s1", summarize=ProviderSummarizer(
            Provider(), {}, model="test", system_prompt="", input_limit=10000,
            timeout=1, calibrate=calibrate, retry=RetryManager(max_retries=0),
        ))
    assert caught.value.reason == "deadline" and not requests
    assert store.snapshot("web", "s1") == before


def test_new_arrivals_that_overflow_capacity_roll_back_the_whole_view_change(store):
    for _ in range(10):
        add(store)
    before = store.snapshot("web", "s1")

    def summarize(batch):
        add(store, text="late " * 10000)
        return summary()

    with pytest.raises(CardCapacityError):
        prepare_request(
            store, "web", "s1", system_prompt="", dynamic_tail=lambda: {},
            tools=[], input_limit=5000, summarize=summarize, force_compact=True,
        )
    after = store.snapshot("web", "s1")
    assert after.cards[:-1] == before.cards
    assert after.view_epoch == before.view_epoch and after.last_seq == before.last_seq + 1


def test_commit_capacity_failure_retains_all_received_summary_usage(store):
    for _ in range(10):
        add(store)
    before = store.snapshot("web", "s1")

    class Provider:
        def create(self, request):
            add(store, text="late " * 10000)
            return reply(request)

    with pytest.raises(CardCapacityError) as caught:
        prepare_request(
            store, "web", "s1", system_prompt="", dynamic_tail=lambda: {},
            tools=[], input_limit=5000, summarize=ProviderSummarizer(
                Provider(), {}, model="test", system_prompt="", input_limit=10000,
                retry=RetryManager(max_retries=0),
            ),
            force_compact=True,
        )
    assert caught.value.compression_usage["provider_request_count"] == 1
    assert caught.value.compression_usage["input_tokens"] == 100
    after = store.snapshot("web", "s1")
    assert after.cards[:-1] == before.cards and after.view_epoch == before.view_epoch


def test_single_oversized_card_splits_only_readonly_input_and_preserves_raw(store):
    add(store, text="original arguments " * 10000)
    before = store.snapshot("web", "s1")
    requests = []

    class Provider:
        def create(self, request):
            requests.append(request)
            assert store.snapshot("web", "s1") == before
            assert estimate_request_tokens(request.system_prompt, request.input, request.tools) <= 3500
            return reply(request)

    result = compact(store, "web", "s1", summarize=ProviderSummarizer(
        Provider(), {}, model="test", system_prompt="", input_limit=3500,
        retry=RetryManager(max_retries=0),
    ))
    assert len(requests) > 1 and result.snapshot.view_epoch == before.view_epoch + 1
    parts = [json.loads(item.content[0].text.split("\n", 1)[1])
             for request in requests for item in request.input
             if item.type == "message" and item.content[0].text.startswith("[CardStream compression_fragment:")]
    assert "".join(part["data"] for part in parts) == before.cards[0].view_payload_json
    assert parts[0]["offset"] == 0 and parts[-1]["end"] == parts[-1]["total"]
    assert store.raw_page("web", "s1")[0].raw_payload_json == before.cards[0].raw_payload_json


def test_main_runtime_uses_the_effective_output_reservation(root, monkeypatch):
    path = root / "config/global_config.yaml"
    config = json.loads(path.read_text("utf-8"))
    config["agents"].update(token_limit=10000, output_reserve=8192)
    path.write_text(json.dumps(config), "utf-8")
    requests = []

    class Provider:
        def capabilities(self, model, **kwargs):
            return ModelCapabilities.model_validate({
                "protocol_version": "2.0", "model": model, "provider_id": "fake",
                "provider_model": model, "input_modalities": ["text"],
                "limits": {"max_input_tokens": 7000, "context_window": 6000,
                           "max_output_tokens": 512},
            })

        def create(self, request):
            requests.append(request)
            return reply(request)

    events = list(iter_request_events(
        {"user": "alice", "source": "web", "session_id": "capacity", "prompt": "work"},
        root=root, provider_factory=lambda _: Provider(),
    ))
    assert events[-1].type == "done", events[-1].error
    assert len(requests) == 1 and requests[0].generation.max_output_tokens == 512
    assert estimate_request_tokens(requests[0].system_prompt, requests[0].input, requests[0].tools) <= 5488
    from run.context.service import context_status
    status = context_status(
        {"user": "alice", "source": "web", "session_id": "capacity"}, root=root)
    assert status["context"]["effective_token_limit"] == 5488
    assert status["context"]["capability_unknown"] is False
    monkeypatch.setenv("KEMO_API_KEY", "different-fake-identity")
    isolated = context_status(
        {"user": "alice", "source": "web", "session_id": "capacity"}, root=root)
    assert isolated["context"]["capability_unknown"] is True
    assert isolated["context"]["effective_token_limit"] == 1808
    assert len(requests) == 1, "reading diagnostics must not make model requests"


def test_failed_compaction_still_records_its_received_usage_in_the_run(root):
    path = root / "config/global_config.yaml"
    config = json.loads(path.read_text("utf-8"))
    config["agents"].update(token_limit=3000, output_reserve=64)
    path.write_text(json.dumps(config), "utf-8")
    store = CardStore(root, "alice")
    store.ensure_session("web", "s1")
    for _ in range(4):
        add(store)

    class Provider:
        def create(self, request):
            return reply(request, {})

    events = list(iter_request_events(
        {"user": "alice", "source": "web", "session_id": "s1", "prompt": "work",
         "compress": True},
        root=root, provider_factory=lambda _: Provider(),
    ))
    assert events[-1].type == "error"
    with store.db() as db:
        record = json.loads(db.execute(
            "SELECT record_json FROM card_integrations ORDER BY created_at DESC LIMIT 1"
        ).fetchone()[0])
    assert record["usage"]["input_tokens"] == 100
    assert record["usage"]["provider_request_count"] == 1
    assert record["context"]["compression_input_tokens"] == 100
