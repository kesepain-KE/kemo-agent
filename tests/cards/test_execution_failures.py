"""Faults and execution races must not turn into partial commits or replays."""
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from provider.adapters.compat import kemo_request_to_chat
from provider.factory import provider_semaphore_status
from provider.protocol.enums import ResponseStatus
from provider.protocol.models import KemoRequest, KemoResponse, MessageItem, ToolCallItem
from provider.request_pool import FairRequestPool
from provider.retry import RetryManager
from run.cards import (
    CardConflictError, IntegrationStore, ProviderSummarizer, compact, protocol_items,
)
from run.context import CompressionBudgetExhausted
from run.extensions import context_input_budget
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


def test_input_limit_and_shared_context_reserve_are_not_double_subtracted():
    capabilities = SimpleNamespace(limits=SimpleNamespace(max_input_tokens=8000, context_window=10000),
                                   extensions={})
    assert context_input_budget(20000, capabilities, output_reserve=3000)["effective_token_limit"] == 7000
    assert context_input_budget(20000, capabilities, output_reserve=1000)["effective_token_limit"] == 8000
    assert context_input_budget(20000, None, output_reserve=1000)["effective_token_limit"] == 19000


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


@pytest.mark.parametrize("response_closed", [False, True])
def test_dead_owner_keeps_unknown_side_effect_fenced_and_recovers_only_once(store, response_closed):
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
        with pytest.raises(CardConflictError, match="未知副作用"):
            following.start()
    with store.db() as db:
        tool = dict(db.execute("SELECT * FROM card_tool_executions WHERE execution_id=?",
                              (execution["execution_id"],)).fetchone())
        row = dict(db.execute("SELECT * FROM card_integrations WHERE integration_id='lost'").fetchone())
    assert tool["execution_live"] and tool["status"] == "interrupted_unknown"
    assert not tool["finished_at"]
    assert row["response_closed"] and not json_value(row)["recovery"]["replay_permitted"]
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
