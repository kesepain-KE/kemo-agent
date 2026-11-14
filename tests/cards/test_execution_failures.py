"""Faults and execution races must not turn into partial commits or replays."""
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from provider.adapters.compat import kemo_request_to_chat
from provider.factory import provider_semaphore_status
from provider.protocol.enums import ResponseStatus
from provider.protocol.models import JsonContent, KemoRequest, KemoResponse, MessageItem, ToolCallItem, ToolResultItem
from provider.request_pool import FairRequestPool
from provider.retry import RetryManager
from run.cards import (
    CardConflictError, IntegrationStore, ProviderSummarizer, compact, protocol_items,
)
from run.context import CompressionBudgetExhausted
from run.extensions import context_input_budget
from run.tools import ToolRuntime
from tests.cards.test_store import add, store, summary


def test_tool_registration_rolls_back_metadata_and_raw_together(store):
    integration = IntegrationStore(store, "web", "s1", "i")
    integration.start()
    before = store.snapshot("web", "s1")
    with patch.object(store, "append", side_effect=OSError("disk failure")):
        with pytest.raises(OSError):
            integration.register_tool("callid_a", {"name": "write", "arguments": {"data": "must not run"}})
    assert store.snapshot("web", "s1") == before
    with store.db() as db:
        assert db.execute("SELECT count(*) FROM card_tool_executions").fetchone()[0] == 0


def test_result_after_the_frozen_request_is_not_acknowledged_by_that_response(store):
    integration = IntegrationStore(store, "web", "s1", "i")
    integration.start()
    execution, _ = integration.register_tool("callid_a", {"name": "work", "arguments": {}})
    frozen = store.snapshot("web", "s1")
    integration.request_started()
    result = integration.tool_result(execution["execution_id"], {"ok": True}, status="completed", duration_ms=1)
    integration.request_finished(accepted_seq=frozen.last_seq)
    assert result.seq > frozen.last_seq
    assert integration.pending() == (0, 1)
    with pytest.raises(CardConflictError):
        integration.finish("completed")


def test_summary_deadline_retains_old_views_and_the_slot_until_real_exit(store):
    add(store)
    add(store)
    before = store.snapshot("web", "s1")
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    config = {"provider_runtime": {"max_concurrent_requests": 1}}

    class Provider:
        def create(self, request):
            entered.set()
            try:
                assert release.wait(3)
                import json
                return KemoResponse(request_id=request.request_id, model=request.model,
                                    status=ResponseStatus.COMPLETED,
                                    output=[MessageItem.text("assistant", json.dumps(summary()))])
            finally:
                exited.set()

    summarizer = ProviderSummarizer(
        Provider(), config, model="fake", system_prompt="", input_limit=10000,
        timeout=.05, retry=RetryManager(max_retries=0),
    )
    try:
        with pytest.raises(CompressionBudgetExhausted) as error:
            compact(store, "web", "s1", summarize=summarizer)
        assert entered.is_set()
        assert error.value.still_running
        assert provider_semaphore_status()["active_requests"] == 1
        assert store.snapshot("web", "s1") == before
    finally:
        release.set()
    assert exited.wait(2)
    deadline = time.monotonic() + 2
    while provider_semaphore_status()["active_requests"] and time.monotonic() < deadline:
        time.sleep(.01)
    assert provider_semaphore_status()["active_requests"] == 0
    assert store.snapshot("web", "s1") == before


@pytest.mark.parametrize(("window", "input_limit", "configured", "reserve", "expected", "expected_window"), [
    (10000, 8000, 20000, 3000, 8000, 10000),
    (10000, 8000, 20000, 1000, 8000, 10000),
    (2000000, 1500000, 1000000, 10000, 1500000, 2000000),
    (2000000, None, 1000000, 10000, 1990000, 2000000),
    (128000, 96000, 1000000, 10000, 96000, 128000),
    (None, 2000000, 1000000, 10000, 2000000, 1000000),
    (None, 128000, 1000000, 10000, 128000, 1000000),
    (None, None, 20000, 1000, 19000, 20000),
])
def test_input_declaration_precedes_window_and_configuration(
    window, input_limit, configured, reserve, expected, expected_window,
):
    capabilities = SimpleNamespace(
        limits=SimpleNamespace(max_input_tokens=input_limit, context_window=window), extensions={},
    ) if window or input_limit else None
    capacity = context_input_budget(configured, capabilities, output_reserve=reserve)
    assert capacity["effective_token_limit"] == expected
    assert capacity["context_window_tokens"] == expected_window
    assert capacity["context_window_source"] == ("model" if window else "configuration")


def test_provider_pool_prefers_main_then_system_without_preemption():
    pool = FairRequestPool(1)
    low = pool.register(("worker",), 2)
    system = pool.register(("compression",), 1)
    main = pool.register(("main",), 0)
    assert pool.wait(main, 0)
    assert pool.status()["waiting_estimate"] == 2
    pool.release(("main",))
    assert pool.wait(system, 0)
    pool.release(("compression",))
    assert pool.wait(low, 0)
    pool.release(("worker",))


def test_aged_worker_is_not_starved_by_a_new_main_request():
    pool = FairRequestPool(1)
    with patch("provider.request_pool.time.monotonic", return_value=1):
        low = pool.register(("worker",), 2)
    with patch("provider.request_pool.time.monotonic", return_value=6):
        main = pool.register(("main",), 0)
        assert pool.wait(low, 0)
        pool.release(("worker",))
        assert pool.wait(main, 0)
        pool.release(("main",))


def test_pending_native_tool_only_degrades_inside_the_chat_adapter(store):
    tool_call = store.append("web", "s1", "i", "tool_call",
                            {"name": "work", "arguments": {"full": "arguments"}},
                            tool_call_id="callid_a")
    guidance = store.append("web", "s1", "i", "user_text", {"text": "new direction"}, level="guidance")
    result = store.append("web", "s1", "i", "tool_result", {
        "original_call": {"name": "work", "arguments": {"full": "arguments"}},
        "execution": {"status": "completed"}, "result": {"ok": True},
    }, tool_call_id="callid_a")
    native = protocol_items((tool_call, guidance, result))
    assert isinstance(native[0], ToolCallItem)
    request = KemoRequest.create(model="fake", input=native)
    chat = kemo_request_to_chat(request)
    assert [message["role"] for message in chat.messages] == ["user", "user", "user"]
    assert "compatibility data" in chat.messages[0]["content"]
    assert '"full": "arguments"' in chat.messages[0]["content"]
    assert "tool_result" in chat.messages[-1]["content"]
    assert isinstance(request.input[0], ToolCallItem), "adapter must not mutate native history"


def test_blocked_tool_receipt_keeps_native_linkage_and_raw_argument_diagnostics(store):
    integration = IntegrationStore(store, "web", "s1", "i")
    integration.start()
    call = ToolCallItem(
        id="call_a", call_id="callid_a", name="shell",
        arguments={"command": "echo ok"}, arguments_raw='{"command": "echo ok"}',
    )
    ToolRuntime(integration).record(
        call, {"ok": False, "execution": {"executed": False},
               "error": {"code": "duplicate_blocked"}},
        status="duplicate_blocked",
    )
    cards = store.snapshot("web", "s1").cards
    items = protocol_items(cards)
    assert len(items) == 2
    assert isinstance(items[0], ToolCallItem)
    assert isinstance(items[1], ToolResultItem)
    assert items[0].call_id == items[1].call_id
    assert items[1].is_error is True
    assert cards[1].raw_payload["original_call"]["arguments_raw"] == call.arguments_raw
    assert isinstance(items[1].content[0], JsonContent)
    assert items[1].content[0].data == cards[1].raw_payload
    assert protocol_items(store.snapshot("web", "s1").cards) == items


@pytest.mark.parametrize("response_closed", [False, True])
def test_dead_owner_retires_incomplete_cards_and_recovers_only_once(store, response_closed):
    integration = IntegrationStore(store, "web", "s1", "lost")
    integration.start()
    execution, _ = integration.register_tool("callid_effect", {"name": "write", "arguments": {"data": "once"}})
    if response_closed:
        integration.finish("cancelled")
    with store.db(write=True) as db:
        db.execute("UPDATE card_integrations SET owner_pid=987654321,owner_token='other' WHERE integration_id='lost'")
        before = dict(db.execute("SELECT * FROM card_integrations WHERE integration_id='lost'").fetchone())
    following = IntegrationStore(store, "web", "s1", "following")
    with patch("run.infra.process_snapshot", return_value={"alive": False}), patch(
            "run.infra.process_identity_matches", return_value=False):
        assert following.recover_interrupted() == ["lost"]
        seq = store.snapshot("web", "s1").last_seq
        assert following.recover_interrupted() == []
        assert store.snapshot("web", "s1").last_seq == seq
        following.start()
    with store.db() as db:
        tool = dict(db.execute("SELECT * FROM card_tool_executions WHERE execution_id=?",
                              (execution["execution_id"],)).fetchone())
        row = dict(db.execute("SELECT * FROM card_integrations WHERE integration_id='lost'").fetchone())
    assert tool["execution_live"] and tool["status"] == "interrupted_unknown"
    assert not tool["finished_at"]
    assert row["response_closed"] and not json_value(row)["recovery"]["replay_permitted"]
    assert json_value(row)["recovery"]["tools_detached"] is True
    snapshot = store.snapshot("web", "s1")
    assert [card.view_kind for card in snapshot.cards] == ["tool_result"]
    items = protocol_items(snapshot.cards)
    assert len(items) == 1 and isinstance(items[0], MessageItem)
    assert "interrupted_unknown" in str(items[0])
    assert snapshot.history_tokens == sum(card.view_tokens for card in snapshot.cards)
    assert store.raw_page("web", "s1")[0].raw_kind == "tool_call"
    if response_closed:
        assert row["updated_at"] == before["updated_at"] and row["status"] == "cancelled"
    notice = store.raw_page("web", "s1")[-1]
    assert notice.raw_payload["original_call"] == {"name": "write", "arguments": {"data": "once"}}


def json_value(row):
    import json
    return json.loads(row["record_json"])


def test_dead_owner_without_live_execution_allows_a_fresh_integration(store):
    integration = IntegrationStore(store, "web", "s1", "lost")
    integration.start()
    with store.db(write=True) as db:
        db.execute("UPDATE card_integrations SET owner_pid=987654321,owner_token='other' WHERE integration_id='lost'")
    with patch("run.infra.process_snapshot", return_value={"alive": False}), patch(
            "run.infra.process_identity_matches", return_value=False):
        IntegrationStore(store, "web", "s1", "following").start()
    with store.db() as db:
        assert db.execute("SELECT status FROM card_integrations WHERE integration_id='lost'").fetchone()[0] == "failed"


@pytest.mark.parametrize("model,expires,restored", [
    ("fake", "2099-01-01T00:00:00+00:00", True),
    ("other-model", "2099-01-01T00:00:00+00:00", False),
    ("fake", "2000-01-01T00:00:00+00:00", False),
    ("fake", "2099-01-01T00:00:00", False),
])
def test_provider_continuation_restores_only_matching_live_opaque_state(store, model, expires, restored):
    from provider.protocol.models import ReasoningItem
    card = store.append("web", "s1", "i", "agent_text", {
        "text": "visible", "reasoning": "never send plaintext reasoning",
        "provider_model": model, "provider_state": [{
            "type": "reasoning", "id": "rs_old", "content": "never send either",
            "provider_state": {"kind": "opaque", "data": "signed-data", "provider": "fake",
                               "model": model, "expires_at": expires},
        }],
    })
    items = protocol_items((card,), model="fake")
    state = [item for item in items if isinstance(item, ReasoningItem)]
    assert bool(state) == restored
    assert all(item.content is None and item.summary is None for item in state)
    assert "never send" not in str(items)


@pytest.mark.parametrize("owner_matches", [True, None])
def test_live_or_unconfirmed_owner_does_not_release_session(store, owner_matches):
    integration = IntegrationStore(store, "web", "s1", "still-owned")
    integration.start()
    integration.register_tool("call-effect", {"name": "write", "arguments": {}})
    with store.db(write=True) as db:
        db.execute("UPDATE card_integrations SET owner_pid=987654321,owner_token='other'")
    before = store.snapshot("web", "s1")
    following = IntegrationStore(store, "web", "s1", "following")
    with patch("run.infra.process_identity_matches", return_value=owner_matches):
        assert following.recover_interrupted() == []
        with pytest.raises(CardConflictError, match="未知副作用"):
            following.start()
    assert store.snapshot("web", "s1") == before


def test_legacy_unknown_execution_is_retired_without_duplicating_recovery_notice(store):
    integration = IntegrationStore(store, "web", "s1", "legacy")
    integration.start()
    completed, _ = integration.register_tool("call-known", {"name": "read", "arguments": {}})
    integration.tool_result(completed["execution_id"], {"ok": True, "text": "known result"},
                            status="completed", duration_ms=1)
    original = {"name": "write", "arguments": {"data": "once"}}
    execution, _ = integration.register_tool("call-unknown", original)
    integration.publish_running_notice(execution["execution_id"])
    integration.finish("cancelled", final_payload={"text": "saved progress"})
    store.append("web", "s1", integration.id, "tool_result", {
        "tool_call_id": execution["tool_call_id"], "execution_id": execution["execution_id"],
        "original_call": original,
        "execution": {"status": "interrupted_unknown", "execution_live": True},
        "result": {"ok": False, "error": {"code": "owner_lost",
                   "message": "宿主已退出；副作用与隔离执行状态未知，不自动重放"}},
    }, tool_call_id=execution["tool_call_id"], event_key=f"{execution['execution_id']}:owner_lost")
    with store.db(write=True) as db:
        db.execute("UPDATE card_tool_executions SET status='interrupted_unknown' WHERE execution_id=?",
                   (execution["execution_id"],))
        row = db.execute("SELECT * FROM card_integrations WHERE integration_id=?", (integration.id,)).fetchone()
        record = json_value(row)
        record["recovery"] = {"reason": "owner_lost", "unknown_executions": 1, "replay_permitted": False}
        import json
        db.execute("UPDATE card_integrations SET owner_pid=987654321,owner_token='other',record_json=? "
                   "WHERE integration_id=?", (json.dumps(record), integration.id))
    before = store.snapshot("web", "s1")
    raw = store.raw_page("web", "s1")
    following = IntegrationStore(store, "web", "s1", "following")
    with patch("run.infra.process_identity_matches", return_value=False):
        assert following.recover_interrupted() == ["legacy"]
        following.start()
        assert following.recover_interrupted() == []
    after = store.snapshot("web", "s1")
    assert [(card.seq, card.raw_payload) for card in raw] == [
        (card.seq, card.raw_payload) for card in store.raw_page("web", "s1")]
    assert before.last_seq == after.last_seq
    assert after.view_epoch == before.view_epoch + 1
    assert after.history_tokens < before.history_tokens
    assert [card.view_kind for card in after.cards] == [
        "tool_call", "tool_result", "agent_text", "tool_result"]
    assert after.cards[1].raw_payload["result"]["text"] == "known result"
    assert after.cards[2].raw_payload["text"] == "saved progress"
    assert all(card.tool_call_id != "call-unknown" or card.view_kind != "tool_call"
               for card in after.cards)


def test_interrupted_tool_cleanup_rolls_back_with_the_recovery_notice(store):
    integration = IntegrationStore(store, "web", "s1", "lost")
    integration.start()
    for number in range(2):
        integration.register_tool(f"call-{number}", {"name": "write", "arguments": {"n": number}})
    with store.db(write=True) as db:
        db.execute("UPDATE card_integrations SET owner_pid=987654321,owner_token='other'")
    before = store.snapshot("web", "s1")
    append, calls = store.append, []
    def fail_second_notice(*args, **kwargs):
        calls.append(args)
        if len(calls) == 2:
            raise OSError("disk failure")
        return append(*args, **kwargs)
    following = IntegrationStore(store, "web", "s1", "following")
    with patch("run.infra.process_identity_matches", return_value=False), patch.object(
            store, "append", side_effect=fail_second_notice):
        with pytest.raises(OSError, match="disk failure"):
            following.recover_interrupted()
    assert store.snapshot("web", "s1") == before
    with store.db() as db:
        assert db.execute("SELECT response_closed FROM card_integrations").fetchone()[0] == 0
        assert [row[0] for row in db.execute("SELECT status FROM card_tool_executions")] == ["running", "running"]
    with patch("run.infra.process_identity_matches", return_value=False):
        following.start()
    assert len(store.snapshot("web", "s1").cards) == 2
