"""Session metadata and maintenance claims over immutable CardStream RAW."""
from __future__ import annotations

import pytest

from run.cards import CardSessionClosedError, CardStore, IntegrationStore
from run.history import (
    claim_pending_memory, claim_pending_summary, close_session, delete_session,
    defer_memory_claim, find_record, finish_memory_claim, finish_summary_claim,
    get_active, get_or_reserve_active, list_sessions, mutate_registry_record,
    queue_summary, reserve_session, retry_summary, session_page_cursor, update_title,
)
from run.history.session_listing import query_session_records
from tests.card_history_support import seed_history


@pytest.fixture
def root(tmp_path):
    (tmp_path / "users/alice").mkdir(parents=True)
    return tmp_path


def seed(root, session="s1", *, source="web", count=1, metadata=None):
    return seed_history(root, "alice", source, session, [
        message for number in range(count) for message in (
            {"role": "user", "content": f"question {number}"},
            {"role": "assistant", "content": f"answer {number}"},
        )
    ], metadata=metadata)


def test_history_uses_cards_database_without_window_directories(root):
    history = seed(root)
    assert history.store.path == root / "users/alice/history/cards.sqlite3"
    assert history.store.path.is_file()
    assert not (root / "users/alice/history/history.sqlite3").exists()
    assert [card.raw_kind for card in history.cards] == ["user_text", "agent_text"]


def test_repeated_reads_are_query_only_and_do_not_change_metadata(root):
    history = seed(root)
    before = find_record(root, "alice", "web", "s1")
    with history.store.db() as db:
        assert db.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(Exception, match="readonly"):
            db.execute("UPDATE card_sessions SET updated_at='modified'")
    for _ in range(3):
        assert query_session_records(root, "alice", source="web")[0][0] == before
    assert find_record(root, "alice", "web", "s1") == before


@pytest.mark.parametrize("identities", [
    [("web", f"same-{index}") for index in range(5)],
    [(source, "same") for source in ("web", "cli", "message:telegram")],
])
def test_keyset_cursor_does_not_skip_equal_timestamps_or_cross_source_identities(root, identities):
    stamp = "2026-08-02T00:00:00+00:00"
    for source, identity in identities:
        reserve_session(root, "alice", source, identity)
        mutate_registry_record(root, "alice", source, identity, lambda value: value, updated_at=stamp)
    collected, cursor = [], ""
    while True:
        page, more = query_session_records(root, "alice", limit=1, before_updated_at=cursor)
        collected.extend((row["source"], row["session_id"]) for row in page)
        if not more:
            break
        cursor = session_page_cursor(page[-1])
    assert collected == sorted(identities, key=lambda value: (value[1], value[0]), reverse=True)
    assert len(set(collected)) == len(identities)


def test_same_session_id_is_isolated_by_user_and_source(root):
    (root / "users/bob").mkdir()
    for user, source in (("alice", "web"), ("alice", "app"), ("bob", "web")):
        seed_history(root, user, source, "same", [{"role": "user", "content": f"{user}/{source}"}])
    for user, source in (("alice", "web"), ("alice", "app"), ("bob", "web")):
        card = CardStore(root, user).raw_page(source, "same")[0]
        assert card.raw_payload["text"] == f"{user}/{source}"


def test_reserved_identity_can_close_and_hide_without_raw(root):
    reserve_session(root, "alice", "web", "empty", active_key="browser")
    found_record = get_active(root, "alice", "browser")
    assert found_record is not None
    assert found_record["session_id"] == "empty"
    found_record = close_session(root, "alice", "web", "empty")
    assert found_record is not None
    assert found_record["lifecycle"] == "closed"
    assert get_active(root, "alice", "browser") is None
    assert delete_session(root, "alice", "web", "empty") == 1
    assert find_record(root, "alice", "web", "empty") is None
    found_record = CardStore(root, "alice").session("web", "empty")
    assert found_record is not None
    assert found_record["lifecycle"] == "deleted"


def test_deleted_history_is_erased_and_identity_cannot_be_reused_by_late_writers(root):
    history = seed(root)
    delete_session(root, "alice", "web", "s1")
    with pytest.raises(CardSessionClosedError):
        reserve_session(root, "alice", "web", "s1")
    with pytest.raises(CardSessionClosedError):
        history.store.append("web", "s1", "late", "user_text", {"text": "late"})
    new, created = get_or_reserve_active(root, "alice", "web", "browser", preferred_session_id="s1")
    assert created and new["session_id"] != "s1"
    assert history.cards == []


def test_active_bindings_do_not_collide_across_sources(root):
    reserve_session(root, "alice", "web", "same", active_key="browser")
    reserve_session(root, "alice", "message:telegram", "same", active_key="telegram")
    close_session(root, "alice", "web", "same")
    assert get_active(root, "alice", "browser") is None
    found_record = get_active(root, "alice", "telegram")
    assert found_record is not None
    assert found_record["source"] == "message:telegram"


def test_memory_claim_is_exclusive_and_advances_only_its_contiguous_raw_range(root):
    seed(root, count=3)
    first = claim_pending_memory(root, "alice", input_tokens=1)
    assert (first["memory_claim_start_seq"], first["memory_claim_end_seq"]) == (1, 2)
    assert claim_pending_memory(root, "alice") is None
    assert finish_memory_claim(root, "alice", "web", "s1", claim_id="wrong") is None
    done = finish_memory_claim(root, "alice", "web", "s1", claim_id=first["memory_claim_id"])
    assert done["memory_processed_seq"] == 2 and done["memory_status"] == "pending"
    second = claim_pending_memory(root, "alice", input_tokens=1)
    assert (second["memory_claim_start_seq"], second["memory_claim_end_seq"]) == (3, 4)
    done = finish_memory_claim(root, "alice", "web", "s1", claim_id=second["memory_claim_id"])
    assert done["memory_processed_seq"] == 4 and done["memory_status"] == "pending"
    third = claim_pending_memory(root, "alice", input_tokens=1)
    assert (third["memory_claim_start_seq"], third["memory_claim_end_seq"]) == (5, 6)
    done = finish_memory_claim(root, "alice", "web", "s1", claim_id=third["memory_claim_id"])
    assert done["memory_processed_seq"] == 6 and done["memory_status"] == "completed"
    assert claim_pending_memory(root, "alice") is None


def test_memory_defer_persists_backoff_without_advancing_or_consuming_attempt(root):
    seed(root, metadata={"memory_attempt_count": 2})
    claim = claim_pending_memory(root, "alice")
    deferred = defer_memory_claim(root, "alice", "web", "s1",
                                 claim_id=claim["memory_claim_id"], memory_queue_reason="execution_capacity")
    assert deferred["memory_status"] == "queued"
    assert deferred["memory_attempt_count"] == 2
    assert deferred["memory_processed_seq"] == 0
    assert deferred["memory_retry_at"]
    assert claim_pending_memory(root, "alice") is None
    mutate_registry_record(root, "alice", "web", "s1", lambda value: {**value, "memory_retry_at": ""})
    assert claim_pending_memory(root, "alice") is not None


def test_new_raw_after_completed_memory_is_claimable_again(root):
    history = seed(root)
    claim = claim_pending_memory(root, "alice")
    finish_memory_claim(root, "alice", "web", "s1", claim_id=claim["memory_claim_id"])
    history.store.append("web", "s1", "next", "user_text", {"text": "new fact"}, level="initial")
    claim = claim_pending_memory(root, "alice", statuses={"pending"})
    assert (claim["memory_claim_start_seq"], claim["memory_claim_end_seq"]) == (3, 3)


def test_explicit_memory_queue_drains_to_target_and_leaves_new_tail_pending(root):
    from run.history import queue_memory_extraction
    (root / "config").mkdir()
    (root / "config/global_config.yaml").write_text('{"memory":{"extraction_mode":"compression_only"}}')
    history = seed(root, count=3)
    queue_memory_extraction(root, "alice", "web", "s1", target_seq=4)
    first = claim_pending_memory(root, "alice", input_tokens=1, statuses={"queued"})
    done = finish_memory_claim(root, "alice", "web", "s1", claim_id=first["memory_claim_id"])
    assert done["memory_status"] == "queued" and done["memory_processed_seq"] == 2
    second = claim_pending_memory(root, "alice", input_tokens=1, statuses={"queued"})
    done = finish_memory_claim(root, "alice", "web", "s1", claim_id=second["memory_claim_id"])
    assert done["memory_processed_seq"] == 4 and done["memory_status"] == "pending"
    assert "memory_target_seq" not in done
    assert claim_pending_memory(root, "alice", statuses={"queued"}) is None
    assert len(history.cards) == 6


def test_memory_is_not_claimed_until_integration_and_live_execution_exit(root):
    history = seed(root)
    integration = IntegrationStore(history.store, "web", "s1", "active")
    integration.start()
    execution, _ = integration.register_tool("call_live", {"name": "work", "arguments": {}})
    assert claim_pending_memory(root, "alice") is None
    integration.finish("cancelled")
    assert claim_pending_memory(root, "alice") is None
    integration.tool_result(execution["execution_id"], {"ok": True}, status="completed", duration_ms=1)
    assert claim_pending_memory(root, "alice") is not None


def test_summary_claim_is_exclusive_idempotent_and_keeps_raw(root):
    history = seed(root)
    close_session(root, "alice", "web", "s1")
    found_record = queue_summary(root, "alice", "web", "s1")
    assert found_record is not None
    assert found_record["status"] == "queued"
    claim = claim_pending_summary(root, "alice")
    assert claim_pending_summary(root, "alice") is None
    done = finish_summary_claim(root, "alice", "web", "s1", claim_id=claim["summary_claim_id"],
                                title="Title", summary="Synopsis")
    assert done["summary_completed_seq"] == 2
    assert done["summary_status"] == "completed"
    assert done["title_source"] == "summary"
    found_record = queue_summary(root, "alice", "web", "s1")
    assert found_record is not None
    assert found_record["status"] == "completed"
    assert claim_pending_summary(root, "alice") is None
    assert len(history.cards) == 2


def test_manual_title_wins_over_background_summary(root):
    seed(root)
    queue_summary(root, "alice", "web", "s1")
    claim = claim_pending_summary(root, "alice")
    update_title(root, "alice", "web", "s1", "Manual")
    done = finish_summary_claim(root, "alice", "web", "s1", claim_id=claim["summary_claim_id"],
                                title="Generated", summary="Synopsis")
    assert done["title"] == "Manual" and done["title_source"] == "manual"
    assert done["summary"] == "Synopsis"


def test_summary_failure_is_visible_and_manual_retry_resets_exhaustion(root):
    seed(root)
    queue_summary(root, "alice", "web", "s1")
    for attempt in range(1, 6):
        claim = claim_pending_summary(root, "alice")
        assert claim is not None
        failed = finish_summary_claim(root, "alice", "web", "s1", claim_id=claim["summary_claim_id"],
                                      error={"message": f"failure {attempt}"})
        assert failed["summary_attempt_count"] == attempt
        listed = list_sessions(root, "alice", "web")[0]
        assert listed["summary_last_error"] == {"message": f"failure {attempt}"}
        mutate_registry_record(root, "alice", "web", "s1", lambda value: {**value, "summary_retry_at": ""})
    assert claim_pending_summary(root, "alice") is None
    found_record = retry_summary(root, "alice", "web", "s1")
    assert found_record is not None
    assert found_record["summary_status"] == "queued"
    claim = claim_pending_summary(root, "alice")
    assert claim["summary_attempt_count"] == 1
    done = finish_summary_claim(root, "alice", "web", "s1", claim_id=claim["summary_claim_id"],
                                title="Recovered", summary="Ready")
    assert done["summary_status"] == "completed" and done["summary_last_error"] is None
