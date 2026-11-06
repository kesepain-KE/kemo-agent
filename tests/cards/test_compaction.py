from dataclasses import replace
import json
from unittest.mock import patch

import pytest

from provider.protocol.models import KemoRequest, KemoResponse, MessageItem, ToolCallItem, ToolResultItem
from provider.retry import RetryManager
from run.cards import (
    CardCapacityError, CardCompactionError, CardStore, DynamicTail, ProviderSummarizer,
    RequestOccupancy, RequestSnapshot, compact, json_text, prepare_request,
    protocol_items, select_batch,
)
from run.cards.compaction import (
    SUMMARY_RESERVE_MAX, SUMMARY_RESERVE_MIN, default_summary_reserve,
)
from tests.cards.test_store import add, store, summary


def test_summary_text_unwraps_markdown_fences_before_parsing():
    """Models fence JSON despite 'Return JSON only'; that must not be fatal."""
    from run.cards.summarize import normalize_summary_text as normalize
    payload = '{"current_work": {}, "success_cases": [], "failure_cases": [], "background": []}'
    for raw in (payload, f"```json\n{payload}\n```", f"```\n{payload}\n```", f"  \n{payload}\n  "):
        assert json.loads(normalize(raw)) == json.loads(payload)
    # An unterminated fence still yields the body; a fence-only body yields "".
    assert normalize(f"```json\n{payload}") == payload
    assert normalize("") == "" and normalize("```json") == ""
    assert normalize(None or "") == ""


def test_summary_failure_reports_the_actual_cause(store):
    """A bare 'not a contract summary' message cost hours of blind debugging."""
    for _ in range(4):
        add(store)

    class Provider:
        def create(self, request):
            return KemoResponse(
                request_id=request.request_id, model=request.model, status="completed",
                output=[MessageItem.text("assistant", "{not json at all")],
            )

    summarize = ProviderSummarizer(
        Provider(), {}, model="test", system_prompt="", input_limit=10000,
        retry=RetryManager(max_retries=0),
    )
    with pytest.raises(CardCompactionError) as caught:
        compact(store, "web", "s1", summarize=summarize)
    message = str(caught.value)
    assert "len=" in message and "head=" in message
    assert "{not json at all" in message


def test_empty_summary_body_is_reported_separately_from_invalid_json(store):
    for _ in range(4):
        add(store)

    class Provider:
        def create(self, request):
            return KemoResponse(
                request_id=request.request_id, model=request.model, status="completed",
                output=[MessageItem.text("assistant", "   ")],
            )

    summarize = ProviderSummarizer(
        Provider(), {}, model="test", system_prompt="", input_limit=10000,
        retry=RetryManager(max_retries=0),
    )
    with pytest.raises(CardCompactionError, match="正文为空"):
        compact(store, "web", "s1", summarize=summarize)


def test_summary_reserve_stays_usable_at_every_history_scale():
    """The retired fixed 256-token reserve produced unsatisfiable 360:1 ratios."""
    # The helper itself never returns below the floor; select_batch clamps the
    # reserve down to whatever a tiny target can actually afford.
    assert default_summary_reserve(0) == 1
    assert default_summary_reserve(1) == SUMMARY_RESERVE_MIN
    assert default_summary_reserve(500) == SUMMARY_RESERVE_MIN
    assert default_summary_reserve(117040) == SUMMARY_RESERVE_MAX
    assert default_summary_reserve(10**9) == SUMMARY_RESERVE_MAX
    # A long session must never be asked to compress hundreds of tokens into
    # a budget so small that the structured summary cannot close.
    assert default_summary_reserve(390136 * 3 // 10) >= SUMMARY_RESERVE_MIN


def test_long_history_batch_admits_a_summary_the_model_can_actually_write(store):
    # 200 cards of real text: the reserve must leave room for a JSON summary.
    for index in range(200):
        add(store, text=f"card {index} " + "filler " * 40)
    snapshot = store.snapshot("web", "s1")
    batch = select_batch(snapshot, .3)
    assert batch.summary_budget >= SUMMARY_RESERVE_MIN
    assert batch.batch_tokens / batch.summary_budget < 100
    assert batch.remaining_tokens + batch.summary_budget == batch.history_target


def test_batch_selection_meets_total_history_target_not_just_selected_prefix(store):
    for _ in range(10):
        add(store)
    snapshot = store.snapshot("web", "s1")
    batch = select_batch(snapshot, .3)
    assert batch.cards == snapshot.cards[:len(batch.cards)]
    assert batch.remaining_tokens + batch.summary_budget == batch.history_target
    assert batch.history_target >= int(snapshot.history_tokens * .3)
    assert snapshot.cards[-1] not in batch.cards
    assert batch.summary_limit >= batch.summary_budget
    assert 1 < len(batch.cards) <= len(snapshot.cards)


def test_model_failure_keeps_old_views_tokens_and_revisions(store):
    for _ in range(4):
        add(store)
    before = store.snapshot("web", "s1")

    def fail(_):
        assert store.snapshot("web", "s1") == before
        raise RuntimeError("unavailable")

    with pytest.raises(RuntimeError):
        compact(store, "web", "s1", summarize=fail)
    assert store.snapshot("web", "s1") == before


def test_invalid_or_oversized_candidate_never_publishes(store):
    for _ in range(4):
        add(store)
    before = store.snapshot("web", "s1")
    huge = {**summary(), "background": ["huge" * 10000]}
    for candidate in ({}, huge):
        with pytest.raises(CardCompactionError):
            compact(store, "web", "s1", summarize=lambda _: candidate)
        assert store.snapshot("web", "s1") == before


def test_long_single_integration_can_compact_all_kinds_including_guidance(store):
    add(store)
    store.append("web", "s1", "integration-1", "user_text",
                 {"text": "LATEST GUIDANCE " * 200}, level="guidance")
    store.append("web", "s1", "integration-1", "agent_text",
                 {"text": "interim " * 200}, level="interim")
    store.append("web", "s1", "integration-1", "tool_call",
                 {"name": "shell", "arguments": {"command": "x" * 2000}},
                 tool_call_id="callid_a")
    store.append("web", "s1", "integration-1", "tool_result", {
        "tool_call_id": "callid_a", "original_call": {"name": "shell", "arguments": {}},
        "execution": {"status": "completed"}, "result": "ok" * 1000,
    }, tool_call_id="callid_a")
    before = store.snapshot("web", "s1")
    result = compact(store, "web", "s1", summarize=lambda batch: summary())
    assert result.after_tokens < result.before_tokens
    assert {c.raw_kind for c in store.raw_page("web", "s1")} == {
        "user_text", "agent_text", "tool_call", "tool_result"}
    assert len(before.cards) == 5


def test_adjacent_intact_tool_pair_is_native_but_detached_result_is_data(store):
    original_call = {"name": "shell", "arguments": {"command": "echo ok"}}
    call = store.append("web", "s1", "i", "tool_call", original_call, tool_call_id="callid_a")
    result = store.append("web", "s1", "i", "tool_result", {
        "original_call": original_call, "execution": {"status": "completed"},
        "execution_id": "exec_a", "result": {"ok": True},
    }, tool_call_id="callid_a")
    intact = protocol_items((call, result))
    assert [type(item) for item in intact] == [ToolCallItem, ToolResultItem]
    KemoRequest.create(model="test", input=intact)
    detached = protocol_items((result,))
    assert len(detached) == 1 and isinstance(detached[0], MessageItem)
    assert "original_call" in detached[0].content[0].text
    assert "exec_a" in detached[0].content[0].text
    KemoRequest.create(model="test", input=detached)
    assert not any(isinstance(item, ToolCallItem) for item in detached)


def test_pending_calls_remain_native_and_keep_the_prefix_stable_when_the_result_arrives(store):
    call = store.append("web", "s1", "i", "tool_call",
                        {"name": "shell", "arguments": {"command": "long-running"}},
                        tool_call_id="callid_pending")
    items = protocol_items((call,))
    assert isinstance(items[0], ToolCallItem)
    KemoRequest.create(model="native", input=items)
    result = store.append("web", "s1", "i", "tool_result", {
        "original_call": {"name": "shell", "arguments": {"command": "long-running"}},
        "execution": {"status": "completed", "execution_live": False}, "result": {"ok": True},
    }, tool_call_id="callid_pending")
    combined = protocol_items((call, result))
    assert combined[0] == items[0]
    assert isinstance(combined[1], ToolResultItem)


def test_dynamic_tail_is_last_virtual_and_does_not_mutate_historical_prefix(store):
    add(store, text="actual user")
    first = RequestSnapshot(store.snapshot("web", "s1"), "FIXED", DynamicTail('{"state":1}'), "[]")
    second = replace(first, dynamic_tail=DynamicTail('{"state":2}'))
    assert first.items()[:-1] == second.items()[:-1]
    assert first.items()[-1] != second.items()[-1]
    assert first.snapshot_id != second.snapshot_id
    assert len(store.raw_page("web", "s1")) == 1


def test_dynamic_tail_preserves_plan_perception_extension_order(store):
    prepared = prepare_request(
        store, "web", "s1", system_prompt="FIXED",
        dynamic_tail=lambda: {
            "task_plan": {"text": "PLAN"},
            "perception": {"text": "SENSE"},
            "extension": {"text": "EXPAND"},
        },
        tools=[], input_limit=10000,
    )
    payload = prepared.snapshot.dynamic_tail.payload_json
    assert payload.index('"task_plan"') < payload.index('"perception"') < payload.index('"extension"')


def test_request_barrier_compacts_before_model_and_freezes_the_tail(store):
    for _ in range(8):
        add(store)
    dynamic_state = {"perception": {"text": "old"}}

    def summarize(batch):
        # Arrival during the maintenance model call is excluded from this tail.
        dynamic_state["perception"]["text"] = "new"
        return summary()

    before = len(store.raw_page("web", "s1"))
    prepared = prepare_request(
        store, "web", "s1", system_prompt="FIXED", dynamic_tail=lambda: dynamic_state,
        tools=[], input_limit=3000, summarize=summarize,
    )
    assert prepared.compaction is not None
    assert prepared.occupancy.total <= 5000 and prepared.wire_tokens <= 5000
    assert '"old"' in prepared.snapshot.dynamic_tail.payload_json
    assert len(store.raw_page("web", "s1")) == before


def test_request_barrier_reports_compaction_start_before_summary_generation(store):
    for _ in range(8):
        add(store)
    events = []

    def summarize(_batch):
        assert len(events) == 1
        assert events[0]["history_tokens_before"] == store.snapshot("web", "s1").history_tokens
        return summary()

    prepared = prepare_request(
        store, "web", "s1", system_prompt="FIXED", dynamic_tail=lambda: {},
        tools=[], input_limit=3000, summarize=summarize,
        on_compaction_start=events.append,
    )
    assert prepared.compaction is not None
    assert len(events) == 1
    assert events[0]["absorbed_cards"] > 0


def test_post_freeze_arrivals_cannot_change_a_request_snapshot(store):
    add(store, text="first")
    prepared = prepare_request(store, "web", "s1", system_prompt="fixed",
                               dynamic_tail=lambda: {}, tools=[], input_limit=10000)
    fingerprint, items = prepared.snapshot.snapshot_id, prepared.snapshot.items()
    add(store, text="second")
    assert prepared.snapshot.snapshot_id == fingerprint and prepared.snapshot.items() == items
    assert prepared.snapshot.history.last_seq == 1
    assert store.snapshot("web", "s1").last_seq == 2


def test_fixed_capacity_failure_does_not_compact_history(store):
    add(store)
    called = []
    with pytest.raises(CardCapacityError, match="固定上下文"):
        prepare_request(
            store, "web", "s1", system_prompt="x" * 5000, dynamic_tail=lambda: {},
            tools=[], input_limit=100, summarize=lambda _: called.append(True),
        )
    assert not called


def test_occupancy_ratio_excludes_fixed_prefix_from_history_target():
    occupancy = RequestOccupancy(180000, 60000, 0, 0, 256000)
    assert occupancy.should_compact()
    assert 60000 * .3 == 18000
    assert RequestOccupancy(180000, 18000, 0, 0, 256000).total == 198000


def test_provider_summarizer_uses_native_readonly_request_and_same_prefix(store):
    for _ in range(4):
        add(store)
    requests = []

    class Provider:
        def create(self, request):
            requests.append(request)
            return KemoResponse(
                request_id=request.request_id, model=request.model, status="completed",
                output=[MessageItem.text("assistant", json.dumps(summary()))],
            )

    summarizer = ProviderSummarizer(
        Provider(), {}, model="same-model", system_prompt="same-system", input_limit=10000,
        retry=RetryManager(max_retries=0),
    )
    result = summarizer(select_batch(store.snapshot("web", "s1")))
    assert result.data == summary()
    assert requests[0].system_prompt == "same-system"
    assert requests[0].tool_choice.mode == "none" and requests[0].stream is False
    assert requests[0].reasoning is None
    assert not (store.root / "agents" / "context_manage").exists()


def test_summary_tool_output_is_never_executed_or_committed(store):
    for _ in range(4):
        add(store)

    class Provider:
        def create(self, request):
            return KemoResponse(
                request_id=request.request_id, model=request.model, status="completed",
                output=[ToolCallItem(id="call_x", call_id="callid_x", name="shell",
                                     arguments={"command": "do not execute"})],
            )

    before = store.snapshot("web", "s1")
    summarize = ProviderSummarizer(
        Provider(), {}, model="test", system_prompt="", input_limit=10000,
        retry=RetryManager(max_retries=0),
    )
    with pytest.raises(CardCompactionError, match="只读"):
        compact(store, "web", "s1", summarize=summarize)
    assert store.snapshot("web", "s1") == before
