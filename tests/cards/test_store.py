from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import json
import sqlite3
from unittest.mock import patch

import pytest

from run.cards import (
    CardConflictError, CardMeter, CardSessionClosedError, CardStore,
    CompactionBatch, effective_cards,
)
from run.infra.fulltext import load_simple


@pytest.fixture
def store(tmp_path):
    (tmp_path / "users" / "alice").mkdir(parents=True)
    result = CardStore(tmp_path, "alice")
    result.ensure_session("web", "s1")
    return result


def add(store, text="x" * 2000, **kwargs):
    return store.append("web", "s1", "integration-1", "user_text",
                        {"text": text}, level="initial", **kwargs)


def summary():
    return {"current_work": {"goal": "continue"}, "success_cases": [],
            "failure_cases": [], "background": []}


def batch(store, count=None):
    snapshot = store.snapshot("web", "s1")
    return CompactionBatch(snapshot, snapshot.cards[:count], snapshot.history_tokens // 3)


def test_raw_immutability_is_enforced_by_sqlite_not_just_python(store):
    card = add(store)
    with store.db(write=True) as db:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("UPDATE context_cards SET raw_payload='{}' WHERE card_id=?", (card.card_id,))
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            db.execute("DELETE FROM context_cards WHERE card_id=?", (card.card_id,))
    assert store.raw_page("web", "s1")[0].raw_payload == card.raw_payload


def test_append_commits_raw_view_and_token_increment_together(store):
    card = add(store)
    snapshot = store.snapshot("web", "s1")
    assert snapshot.history_tokens == card.raw_tokens == card.view_tokens
    assert snapshot.last_seq == 1 and card.view_revision == 1 and card.token_active
    assert len(snapshot.cards) == 1
    assert store.path == store.root / "users/alice/history/cards.sqlite3"


def test_payload_reads_do_not_mutate_frozen_snapshots(store):
    card = add(store)
    card.raw_payload["text"] = "changed"
    card.view_payload["text"] = "changed"
    assert card.raw_payload["text"] == "x" * 2000
    assert card.view_payload == card.raw_payload


def test_event_replay_is_idempotent_but_conflicting_reuse_is_rejected(store):
    first = add(store, event_key="event-1")
    assert add(store, event_key="event-1") == first
    assert store.snapshot("web", "s1").last_seq == 1
    with pytest.raises(CardConflictError, match="event key"):
        add(store, text="different fact", event_key="event-1")


def test_concurrent_append_has_monotonic_gap_free_session_order(store):
    with ThreadPoolExecutor(max_workers=4) as executor:
        cards = list(executor.map(lambda n: add(store, text=str(n)), range(30)))
    assert sorted(c.seq for c in cards) == list(range(1, 31))
    assert store.snapshot("web", "s1").history_tokens == sum(c.view_tokens for c in cards)


def test_session_source_and_user_isolation(store):
    add(store)
    store.ensure_session("app", "s1")
    other = store.append("app", "s1", "i", "user_text", {"text": "APP"})
    assert store.snapshot("web", "s1").cards[0].raw_payload["text"] != "APP"
    assert store.snapshot("app", "s1").cards == (other,)
    (store.root / "users" / "bob").mkdir()
    bob = CardStore(store.root, "bob")
    bob.ensure_session("web", "s1")
    assert not bob.snapshot("web", "s1").cards


def test_compaction_keeps_raw_and_hosts_one_summary_on_earliest_row(store):
    for _ in range(5):
        add(store)
    original = [(c.card_id, c.raw_payload_json, c.raw_tokens) for c in store.raw_page("web", "s1")]
    snapshot = store.replace_prefix(batch(store, 4), summary())
    assert len(snapshot.cards) == 2
    assert snapshot.cards[0].view_kind == "summary"
    assert snapshot.cards[0].raw_kind == "user_text"
    assert snapshot.cards[0].compression_stage == 1
    assert snapshot.history_tokens <= sum(c.raw_tokens for c in store.raw_page("web", "s1")) // 3
    assert [(c.card_id, c.raw_payload_json, c.raw_tokens) for c in store.raw_page("web", "s1")] == original
    add(store)
    second = store.replace_prefix(batch(store), summary())
    assert len(second.cards) == 1
    assert second.cards[0].card_id == original[0][0]
    assert second.cards[0].compression_stage == 2
    assert len(store.raw_page("web", "s1")) == 6


def test_failed_transaction_rolls_back_all_view_and_meter_changes(store):
    for _ in range(4):
        add(store)
    before = store.snapshot("web", "s1")

    def fail():
        raise sqlite3.OperationalError("simulated commit failure")

    with pytest.raises(sqlite3.OperationalError):
        store.replace_prefix(batch(store), summary(), before_commit=fail)
    assert store.snapshot("web", "s1") == before
    assert all(c.token_active for c in store.raw_page("web", "s1"))


def test_stale_compaction_cannot_overwrite_a_newer_view(store):
    for _ in range(4):
        add(store)
    old_batch = batch(store)
    store.replace_prefix(old_batch, summary())
    current = store.snapshot("web", "s1")
    with pytest.raises(CardConflictError, match="view changed"):
        store.replace_prefix(old_batch, summary())
    assert store.snapshot("web", "s1") == current


def test_append_during_summary_generation_is_retained_for_the_next_snapshot(store):
    for _ in range(4):
        add(store)
    old_batch = batch(store)
    latest = add(store, text="arrived after the snapshot")
    result = store.replace_prefix(old_batch, summary())
    assert result.last_seq == 5 and result.cards[-1] == latest
    assert result.history_tokens == sum(c.view_tokens for c in result.cards)


@pytest.mark.parametrize("bad", [{}, {"current_work": []}, {"current_work": {}, "success_cases": [],
                                                            "failure_cases": [], "background": []}, {"checkpoint": "  "}])
def test_invalid_summary_never_changes_effective_history(store, bad):
    for _ in range(3):
        add(store)
    before = store.snapshot("web", "s1")
    with pytest.raises(ValueError):
        store.replace_prefix(batch(store), bad)
    assert store.snapshot("web", "s1") == before


def test_history_search_reads_raw_after_compaction(store):
    add(store, text="original-only-evidence " * 300)
    add(store)
    store.replace_prefix(batch(store), summary())
    assert len(store.raw_page("web", "s1", query="original-only-evidence")) == 1
    assert "original-only-evidence" not in json.dumps(store.snapshot("web", "s1").cards[0].view_payload)


def test_memory_database_and_cache_overlap_does_not_double_count(store):
    original = add(store)
    memory_newer = replace(original, token_active=False, view_revision=original.view_revision + 1)
    assert effective_cards((original,), (original,)) == (original,)
    assert effective_cards((original,), (memory_newer,)) == ()
    assert effective_cards((memory_newer,), (original,)) == ()
    with pytest.raises(CardConflictError):
        effective_cards((original,), (replace(original, view_tokens=1),))


def test_remeter_only_updates_view_not_raw(store):
    original = add(store)
    alternate = CardStore(store.root, "alice", meter=CardMeter(key="alternate", counter=lambda _: 7))
    result = alternate.remeter("web", "s1")
    assert result.history_tokens == 15
    assert result.cards[0].raw_tokens == original.raw_tokens
    assert result.cards[0].raw_payload == original.raw_payload
    assert result.cards[0].view_revision > original.view_revision


def test_closed_or_deleted_sessions_reject_late_append(store):
    add(store)
    store.close("web", "s1")
    with pytest.raises(CardSessionClosedError):
        add(store)
    with pytest.raises(CardSessionClosedError):
        store.ensure_session("web", "s1")


def test_new_engine_never_imports_or_deletes_legacy_history(store):
    legacy = store.root / "users" / "alice" / "history"
    legacy.mkdir(exist_ok=True)
    old = legacy / "history.sqlite3"
    old.write_bytes(b"old-user-data-must-remain-unchanged")
    add(store)
    assert old.read_bytes() == b"old-user-data-must-remain-unchanged"


def test_unknown_future_schema_is_rejected_without_mutation(store):
    with store.db(write=True) as db:
        db.execute("PRAGMA user_version=999")
    before = store.path.read_bytes()
    from run.infra import SchemaMigrationError
    with pytest.raises(SchemaMigrationError):
        store.snapshot("web", "s1")
    assert store.path.read_bytes() == before


def test_schema_upgrade_preserves_raw_tools_and_corrects_response_terminal_status(store):
    from run.cards import IntegrationStore
    from run.cards.schema import SCHEMA, SCHEMA_VERSION
    integration = IntegrationStore(store, "web", "s1", "schema-upgrade")
    integration.start()
    execution, _ = integration.register_tool("call", {"name": "work", "arguments": {"value": "original"}})
    integration.finish("cancelled", metadata={"status": "partial"})
    before = store.raw_page("web", "s1")
    # Reconstruct the old CHECK on this temporary test database only.
    with sqlite3.connect(store.path) as db:
        load_simple(db)
        db.execute("PRAGMA foreign_keys=OFF")
        start = SCHEMA.index("CREATE TABLE IF NOT EXISTS card_integrations")
        end = SCHEMA.index("CREATE INDEX IF NOT EXISTS idx_integrations_session")
        old = SCHEMA[start:end].replace("card_integrations (", "old_integrations (", 1)
        old = old.replace("'completed','limited','partial','failed'", "'completed','failed'")
        db.execute(old)
        db.execute("INSERT INTO old_integrations SELECT * FROM card_integrations")
        db.execute("DROP TABLE card_integrations")
        db.execute("ALTER TABLE old_integrations RENAME TO card_integrations")
        db.execute("PRAGMA user_version=3")
    with store.db() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert db.execute("SELECT status FROM card_integrations").fetchone()[0] == "partial"
        assert db.execute("SELECT execution_id FROM card_tool_executions").fetchone()[0] == execution["execution_id"]
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert store.raw_page("web", "s1") == before
    backups = list(store.path.parent.glob("cards.sqlite3.bak.v3.*"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3


def test_schema_five_upgrade_indexes_absorption_and_preserves_compressed_history(store):
    from run.cards.schema import SCHEMA_VERSION

    for index in range(4):
        add(store, f"retained message {index} " + "x" * 2000)
    store.replace_prefix(batch(store), summary())
    before = store.snapshot("web", "s1")
    raw = store.raw_page("web", "s1")
    with closing(sqlite3.connect(store.path)) as db, db:
        db.execute("DROP INDEX IF EXISTS idx_cards_absorbed")
        db.execute("PRAGMA user_version=5")

    with store.db() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        plan = db.execute(
            "EXPLAIN QUERY PLAN SELECT rowid FROM context_cards WHERE absorbed_by=?",
            (raw[0].card_id,),
        ).fetchall()
        assert any("idx_cards_absorbed" in row[3] for row in plan)
        assert db.execute(
            "SELECT count(*) FROM card_text_fts WHERE card_text_fts MATCH 'retained'"
        ).fetchone()[0] == 4
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert store.snapshot("web", "s1") == before
    assert store.raw_page("web", "s1") == raw
    backups = list(store.path.parent.glob("cards.sqlite3.bak.v5.*"))
    assert len(backups) == 1
    with closing(sqlite3.connect(backups[0])) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 5
        assert db.execute(
            "SELECT count(*) FROM sqlite_master WHERE name='idx_cards_absorbed'"
        ).fetchone()[0] == 0
    with store.db():
        pass
    assert list(store.path.parent.glob("cards.sqlite3.bak.v5.*")) == backups
