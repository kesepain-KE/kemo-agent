"""Regression fixtures never open a real user's database."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest

from run.history import commit_window, empty_window, load_window
from run.history import store_core as history
from run.history.schema_contract import REQUIRED_COLUMNS
from run.infra.sqlite_migrations import SchemaMigrationError, backup_database, schema_needs_repair
from tests.persistence.test_sqlite_migration_safety import (
    db_path, open_store, snapshot, test_history_legacy_archive_body_and_rounds_survive_migration as legacy_fixture,
)


def populated(root):
    (root / "users/alice").mkdir(parents=True)
    directory = root / "users/alice/history/archive"
    window = empty_window("alice", "web", "session")
    window["data"]["rounds"] = 1
    window["text"]["messages"] = [{"role": "user", "content": "keep正文"}]
    window["think"]["rounds"] = [{"round": 1, "content": "keep thought"}]
    commit_window(directory, window)
    return db_path(root, "history"), directory


def contents(path):
    with closing(sqlite3.connect(path)) as db:
        return {
            table: db.execute(f"SELECT * FROM {table} ORDER BY 1,2").fetchall()
            for table in ("history_messages", "history_rounds")
        }


@pytest.mark.parametrize("version", (1, 2))
def test_supported_historical_layout_still_upgrades(tmp_path, version):
    legacy_fixture(tmp_path, version)


@pytest.mark.parametrize("versioned", [True, False])
def test_genuine_historical_ddl_with_or_without_version_still_upgrades(tmp_path, versioned):
    path = db_path(tmp_path, "history")
    path.parent.mkdir(parents=True)
    historical = Path(__file__).parents[1] / "fixtures/history/schema-v1.sql"
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(historical.read_text("utf-8"))
        db.execute("INSERT INTO history_windows VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "old", "archive", "web", "legacy", "original", "2026-01-01", "2026-01-01", 1,
            json.dumps({"complete": True, "rounds": 1, "source": "web", "session_id": "legacy"}),
            json.dumps({"schema_version": 1, "messages": [{"role": "user", "content": "historical正文"}]}),
            json.dumps({"schema_version": 1, "rounds": [{"round": 1, "content": "historical thought"}]}),
            json.dumps({"schema_version": 1, "rounds": []}),
            json.dumps({"schema_version": 2, "items": []}),
        ))
        if not versioned:
            db.execute("DELETE FROM history_meta WHERE key='schema_version'")
    before = snapshot(path)[0]
    open_store(tmp_path, "history")
    restored = load_window(path.parent / "old")
    assert restored["text"]["messages"][0]["content"] == "historical正文"
    assert restored["think"]["rounds"][0]["content"] == "historical thought"
    backup = next(path.parent.glob("*.bak.*"))
    assert snapshot(backup)[0] == before


@pytest.mark.parametrize("column,index", [
    ("memory_status", "idx_history_sessions_memory"),
    ("summary_status", "idx_history_sessions_summary"),
])
def test_current_history_missing_known_column_backs_up_before_repair(tmp_path, column, index):
    path, _ = populated(tmp_path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(f"DROP INDEX {index}")
        db.execute(f"ALTER TABLE history_sessions DROP COLUMN {column}")
    before = snapshot(path)[0]
    open_store(tmp_path, "history")
    backup = next(path.parent.glob(path.name + f".bak.v{history.HISTORY_SCHEMA_VERSION}.*"))
    assert snapshot(backup)[0] == before
    with closing(sqlite3.connect(path)) as db:
        assert not schema_needs_repair(db, REQUIRED_COLUMNS)
        assert db.execute(f"SELECT {column} FROM history_sessions").fetchone()[0] in {"unknown", "none"}
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (index,)).fetchone()
        assert schema_needs_repair(db, {"history_sessions": ("imaginary_column",)})


def test_missing_identity_or_content_column_is_not_fabricated(tmp_path):
    path, _ = populated(tmp_path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("ALTER TABLE history_sessions DROP COLUMN record_json")
    before = snapshot(path)[0]
    with pytest.raises(SchemaMigrationError, match="拒绝猜测恢复"):
        open_store(tmp_path, "history")
    assert snapshot(path)[0] == before
    assert list(path.parent.glob(path.name + f".bak.v{history.HISTORY_SCHEMA_VERSION}.*"))
    assert history._ready_key(path) not in history._READY_DATABASES


def test_backup_failure_blocks_even_known_additive_column_repair(tmp_path, monkeypatch):
    path, _ = populated(tmp_path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DROP INDEX idx_history_sessions_memory")
        db.execute("ALTER TABLE history_sessions DROP COLUMN memory_status")
    before = snapshot(path)[0]
    def fail(*args, **kwargs):
        raise SchemaMigrationError("backup unavailable")
    monkeypatch.setattr("run.infra.sqlite_migrations.backup_database", fail)
    with pytest.raises(SchemaMigrationError, match="backup unavailable"):
        open_store(tmp_path, "history")
    assert snapshot(path)[0] == before
    assert history._ready_key(path) not in history._READY_DATABASES


def test_added_state_projection_preserves_authoritative_record_state(tmp_path):
    path, _ = populated(tmp_path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE history_sessions SET record_json=json_set(record_json,'$.memory_status','completed')")
        db.execute("DROP INDEX idx_history_sessions_memory")
        db.execute("ALTER TABLE history_sessions DROP COLUMN memory_status")
    open_store(tmp_path, "history")
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT memory_status FROM history_sessions").fetchone()[0] == "completed"


@pytest.mark.parametrize("stamp", (None, "0", "1", "2"))
def test_compact_partitions_survive_missing_or_regressed_version(tmp_path, stamp):
    path, directory = populated(tmp_path)
    before = contents(path)
    with closing(sqlite3.connect(path)) as db, db:
        if stamp is None:
            db.execute("DELETE FROM history_meta WHERE key='schema_version'")
        else:
            db.execute("UPDATE history_meta SET value=? WHERE key='schema_version'", (stamp,))
    open_store(tmp_path, "history")
    assert contents(path) == before
    assert load_window(directory)["text"]["messages"][0]["content"] == "keep正文"
    assert load_window(directory)["think"]["rounds"][0]["content"] == "keep thought"
    # Reopening a repaired database is itself idempotent.
    backups = list(path.parent.glob("*.bak.*"))
    open_store(tmp_path, "history")
    assert contents(path) == before
    assert list(path.parent.glob("*.bak.*")) == backups


@pytest.mark.parametrize("damage", ("invalid-json", "unknown-object", "conflict", "partial-rounds", "missing-table"))
def test_ambiguous_content_is_rejected_without_deleting_rows(tmp_path, damage):
    path, _ = populated(tmp_path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DELETE FROM history_meta WHERE key='schema_version'")
        if damage == "invalid-json":
            db.execute("UPDATE history_windows SET text_json='{'")
        elif damage == "unknown-object":
            db.execute("UPDATE history_windows SET text_json='{}'")
        elif damage == "conflict":
            db.execute("UPDATE history_windows SET text_json=?", (
                json.dumps({"messages": [{"role": "user", "content": "different"}]}),))
        elif damage == "partial-rounds":
            db.execute("UPDATE history_windows SET think_json=?", (json.dumps({"rounds": []}),))
        else:
            db.execute("DROP TABLE history_messages")
    before = snapshot(path)[0]
    with pytest.raises(SchemaMigrationError):
        open_store(tmp_path, "history")
    assert snapshot(path)[0] == before
    assert list(path.parent.glob(path.name + ".bak.v0.*"))


def test_current_version_missing_authoritative_table_is_not_replaced_with_empty_table(tmp_path):
    path, _ = populated(tmp_path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DROP TABLE history_messages")
    before = snapshot(path)[0]
    with pytest.raises(SchemaMigrationError, match="存储引用缺失"):
        open_store(tmp_path, "history")
    assert snapshot(path)[0] == before


def test_backed_up_history_can_be_restored_and_opened_by_business_reader(tmp_path):
    source_root, restore_root = tmp_path / "source", tmp_path / "restored"
    path, _ = populated(source_root)
    backup = tmp_path / "saved.sqlite3"
    backup_database(path, backup)
    target = db_path(restore_root, "history")
    target.parent.mkdir(parents=True)
    with closing(sqlite3.connect(backup)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
    open_store(restore_root, "history")
    restored = load_window(target.parent / "archive")
    assert restored["text"]["messages"][0]["content"] == "keep正文"
    assert restored["think"]["rounds"][0]["content"] == "keep thought"
    assert contents(target) == contents(path)
