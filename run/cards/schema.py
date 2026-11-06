"""The history is one table; session metadata is not itself historical content."""
from __future__ import annotations

from contextlib import closing, contextmanager
import os
from pathlib import Path
import sqlite3
import threading
from typing import Iterator

from run.config import user_dir
from run.infra import (
    SchemaMigrationError, execute_schema, load_simple, schema_migration, schema_needs_repair,
)


SCHEMA_VERSION = 6
_LOCKS: dict[str, threading.RLock] = {}
_LOCK_GUARD = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS card_sessions (
    session_key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    session_id TEXT NOT NULL,
    generation TEXT NOT NULL,
    lifecycle TEXT NOT NULL DEFAULT 'open'
        CHECK(lifecycle IN ('open','closed','deleted')),
    last_seq INTEGER NOT NULL DEFAULT 0 CHECK(last_seq >= 0),
    view_epoch INTEGER NOT NULL DEFAULT 0 CHECK(view_epoch >= 0),
    active_tokens INTEGER NOT NULL DEFAULT 0 CHECK(active_tokens >= 0),
    meter_key TEXT NOT NULL DEFAULT '',
    memory_processed_seq INTEGER NOT NULL DEFAULT 0 CHECK(memory_processed_seq >= 0),
    record_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(record_json)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source, session_id)
);
CREATE TABLE IF NOT EXISTS context_cards (
    card_id TEXT PRIMARY KEY,
    session_key TEXT NOT NULL REFERENCES card_sessions(session_key),
    integration_id TEXT NOT NULL,
    seq INTEGER NOT NULL CHECK(seq > 0),
    raw_kind TEXT NOT NULL CHECK(raw_kind IN ('user_text','agent_text','tool_call','tool_result')),
    raw_level TEXT,
    raw_payload TEXT NOT NULL CHECK(json_valid(raw_payload)),
    raw_tokens INTEGER NOT NULL CHECK(raw_tokens >= 0),
    view_kind TEXT,
    view_payload TEXT CHECK(view_payload IS NULL OR json_valid(view_payload)),
    view_tokens INTEGER NOT NULL DEFAULT 0 CHECK(view_tokens >= 0),
    token_active INTEGER NOT NULL DEFAULT 0 CHECK(token_active IN (0,1)),
    compression_stage INTEGER NOT NULL DEFAULT 0 CHECK(compression_stage >= 0),
    absorbed_by TEXT REFERENCES context_cards(card_id),
    view_revision INTEGER NOT NULL DEFAULT 0 CHECK(view_revision >= 0),
    tool_call_id TEXT,
    event_key TEXT,
    meter_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(session_key, seq),
    UNIQUE(session_key, event_key),
    CHECK(token_active=0 OR (view_kind IS NOT NULL AND view_payload IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_cards_session ON context_cards(session_key,seq);
CREATE INDEX IF NOT EXISTS idx_cards_active ON context_cards(session_key,token_active,seq);
CREATE INDEX IF NOT EXISTS idx_cards_integration ON context_cards(session_key,integration_id,seq);
CREATE INDEX IF NOT EXISTS idx_cards_call ON context_cards(session_key,tool_call_id,seq);
CREATE INDEX IF NOT EXISTS idx_cards_absorbed ON context_cards(absorbed_by);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_summary ON context_cards(session_key)
    WHERE token_active=1 AND view_kind='summary';
CREATE TRIGGER IF NOT EXISTS cards_raw_immutable
BEFORE UPDATE OF card_id,session_key,integration_id,seq,raw_kind,raw_level,
    raw_payload,raw_tokens,tool_call_id,event_key,created_at ON context_cards
BEGIN SELECT RAISE(ABORT,'CardStream RAW is immutable'); END;
CREATE TRIGGER IF NOT EXISTS cards_raw_no_delete BEFORE DELETE ON context_cards
BEGIN SELECT RAISE(ABORT,'CardStream RAW cannot be deleted by context maintenance'); END;
CREATE VIEW IF NOT EXISTS card_text_content AS
    SELECT rowid, coalesce(json_extract(raw_payload,'$.text'),'') AS text
    FROM context_cards WHERE raw_kind IN ('user_text','agent_text');
CREATE VIRTUAL TABLE IF NOT EXISTS card_text_fts USING fts5(
    text, content='card_text_content', content_rowid='rowid', tokenize='simple 0'
);
CREATE TRIGGER IF NOT EXISTS card_text_fts_insert AFTER INSERT ON context_cards
WHEN new.raw_kind IN ('user_text','agent_text') BEGIN
    INSERT INTO card_text_fts(rowid,text)
    VALUES(new.rowid,coalesce(json_extract(new.raw_payload,'$.text'),''));
END;
CREATE TABLE IF NOT EXISTS card_integrations (
    integration_id TEXT PRIMARY KEY,
    session_key TEXT NOT NULL REFERENCES card_sessions(session_key),
    generation TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK(status IN ('running','waiting','resuming','finalizing','completed','limited','partial','failed','cancelled')),
    acknowledged_seq INTEGER NOT NULL DEFAULT 0,
    model_request_inflight INTEGER NOT NULL DEFAULT 0 CHECK(model_request_inflight IN (0,1)),
    continuation_required INTEGER NOT NULL DEFAULT 1 CHECK(continuation_required IN (0,1)),
    response_closed INTEGER NOT NULL DEFAULT 0 CHECK(response_closed IN (0,1)),
    owner_pid INTEGER NOT NULL,
    owner_token TEXT NOT NULL,
    record_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(record_json)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_integrations_session ON card_integrations(session_key,created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_integration_open ON card_integrations(session_key)
    WHERE response_closed=0;
CREATE TABLE IF NOT EXISTS card_tool_executions (
    execution_id TEXT PRIMARY KEY,
    integration_id TEXT NOT NULL REFERENCES card_integrations(integration_id),
    tool_call_id TEXT NOT NULL,
    original_call TEXT NOT NULL CHECK(json_valid(original_call)),
    status TEXT NOT NULL,
    execution_live INTEGER NOT NULL DEFAULT 1 CHECK(execution_live IN (0,1)),
    last_result_seq INTEGER,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    UNIQUE(integration_id,tool_call_id)
);
CREATE INDEX IF NOT EXISTS idx_executions_integration
    ON card_tool_executions(integration_id,execution_live,last_result_seq);
CREATE TABLE IF NOT EXISTS card_bindings (
    binding_key TEXT PRIMARY KEY,
    session_key TEXT NOT NULL REFERENCES card_sessions(session_key)
);
CREATE TABLE IF NOT EXISTS card_leases (
    session_key TEXT NOT NULL REFERENCES card_sessions(session_key),
    client_id TEXT NOT NULL,
    expires_at REAL NOT NULL,
    sequence INTEGER NOT NULL DEFAULT 0,
    released INTEGER NOT NULL DEFAULT 0 CHECK(released IN (0,1)),
    PRIMARY KEY(session_key,client_id)
);
CREATE TABLE IF NOT EXISTS card_meter_observations (
    request_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    meter_key TEXT NOT NULL,
    estimated_tokens INTEGER NOT NULL CHECK(estimated_tokens>0),
    actual_tokens INTEGER NOT NULL CHECK(actual_tokens>=0),
    observed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_meter_observations
    ON card_meter_observations(meter_key,observed_at);
CREATE TABLE IF NOT EXISTS message_processed_messages (
    dedupe_key TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    message_id TEXT NOT NULL,
    status TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    error_json TEXT
);
"""

REQUIRED = {
    "card_text_fts": ("text",),
    "card_sessions": (
        "session_key", "source", "session_id", "generation", "lifecycle", "last_seq",
        "view_epoch", "active_tokens", "meter_key", "memory_processed_seq",
        "record_json", "created_at", "updated_at"),
    "context_cards": (
        "card_id", "session_key", "integration_id", "seq", "raw_kind", "raw_level",
        "raw_payload", "raw_tokens", "view_kind", "view_payload", "view_tokens",
        "token_active", "compression_stage", "absorbed_by", "view_revision",
        "tool_call_id", "event_key", "meter_key", "created_at"),
    "card_integrations": (
        "integration_id", "session_key", "generation", "status", "acknowledged_seq",
        "model_request_inflight", "continuation_required", "response_closed",
        "owner_pid", "owner_token", "record_json", "created_at", "updated_at"),
    "card_tool_executions": (
        "execution_id", "integration_id", "tool_call_id", "original_call", "status",
        "execution_live", "last_result_seq", "started_at", "finished_at"),
    "card_bindings": ("binding_key", "session_key"),
    "card_leases": ("session_key", "client_id", "expires_at", "sequence", "released"),
    "card_meter_observations": (
        "request_id", "snapshot_id", "meter_key", "estimated_tokens", "actual_tokens", "observed_at"),
    "message_processed_messages": (
        "dedupe_key", "platform", "message_id", "status", "claimed_at", "updated_at",
        "source", "session_id", "error_json"),
}


def _safe_path(root: Path, user: str) -> Path:
    from plugins._creator_security import reject_link_components

    root = root.resolve()
    directory = user_dir(user, root)
    path = directory / "history" / "cards.sqlite3"
    reject_link_components(root, path, label="CardStream 数据库")
    if path.exists() and (not path.is_file() or path.stat().st_nlink > 1):
        raise ValueError("CardStream database must be an ordinary, non-linked user file")
    path.resolve().relative_to(directory.resolve())
    if (directory / "cards.sqlite3").exists():
        raise SchemaMigrationError(
            "CardStream 数据库仍在用户根目录；请先停止全部服务，"
            "通过更新器迁移到 history/cards.sqlite3 后再启动，避免建立空库或使用两份历史"
        )
    return path


@contextmanager
def connection(root: Path, user: str, *, write: bool = False) -> Iterator[sqlite3.Connection]:
    """Short database ownership prevents Windows WAL close/path-check races."""
    identity = os.path.normcase(str(root.resolve() / "users" / user / "history" / "cards.sqlite3"))
    with _LOCK_GUARD:
        lock = _LOCKS.setdefault(identity, threading.RLock())
    with lock:
        path = _safe_path(root, user)
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=5.0)) as db:
            db.row_factory = sqlite3.Row
            load_simple(db)
            db.execute("PRAGMA foreign_keys=ON")
            # No user/legacy database is opened, migrated or consulted here.
            current = db.execute("PRAGMA user_version").fetchone()[0]
            if current != SCHEMA_VERSION:
                # Rebuild the terminal-state CHECK while preserving execution
                # foreign keys. FK validation runs inside the backed-up migration.
                db.execute("PRAGMA foreign_keys=OFF")
                try:
                    with schema_migration(db, target=SCHEMA_VERSION, meta_table=None, required=REQUIRED):
                        definition = db.execute(
                            "SELECT sql FROM sqlite_master WHERE type='table' AND name='card_integrations'",
                        ).fetchone()
                        if definition and "'partial'" not in definition[0]:
                            start = SCHEMA.index("CREATE TABLE IF NOT EXISTS card_integrations")
                            end = SCHEMA.index("CREATE INDEX IF NOT EXISTS idx_integrations_session")
                            execute_schema(db, SCHEMA[start:end].replace(
                                "card_integrations (", "card_integrations_new (", 1,
                            ))
                            db.execute("INSERT INTO card_integrations_new SELECT * FROM card_integrations")
                            db.execute("DROP TABLE card_integrations")
                            db.execute("ALTER TABLE card_integrations_new RENAME TO card_integrations")
                        execute_schema(db, SCHEMA)
                        if current < 5:
                            db.execute("INSERT INTO card_text_fts(card_text_fts) VALUES('rebuild')")
                        db.execute(
                            "UPDATE card_integrations SET status=json_extract(record_json,'$.status') "
                            "WHERE response_closed=1 AND json_extract(record_json,'$.status') IN ('limited','partial')",
                        )
                        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                            raise SchemaMigrationError("CardStream 迁移后外键校验失败")
                finally:
                    db.execute("PRAGMA foreign_keys=ON")
            elif schema_needs_repair(db, REQUIRED):
                raise SchemaMigrationError("CardStream schema 不完整，拒绝盲目修复或反复写入")
            # Established snapshot readers never take BEGIN IMMEDIATE just to
            # run no-op DDL. Migration is an initialization operation only.
            if db.execute("PRAGMA journal_mode").fetchone()[0].casefold() != "wal":
                db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            if not write:
                db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise


@contextmanager
def read_connection(root: Path, user: str):
    """Inspection never creates, migrates or repairs a database."""
    path = _safe_path(root, user)
    if not path.is_file():
        yield None
        return
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise SchemaMigrationError("CardStream schema 需要由运行入口显式初始化或升级")
        load_simple(db)
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        try:
            yield db
        finally:
            db.rollback()
