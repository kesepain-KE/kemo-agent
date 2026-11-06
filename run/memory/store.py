"""SQLite primitives for per-user memory fragments.

The database is the only authoritative fragment store.  Human-readable hot
memory remains a derived view and can be rebuilt from these tables.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import threading
from typing import Iterator

from run.config import user_dir
from run.infra import execute_schema, load_simple, schema_migration


MEMORY_DB_FILENAME = "memory.sqlite3"
MEMORY_DB_SCHEMA_VERSION = 4
_READY_DATABASES: set[str] = set()
_READY_DATABASES_LOCK = threading.Lock()


def database_path(root: Path, user: str) -> Path:
    return user_dir(user, root) / "improve" / MEMORY_DB_FILENAME


def _configure(database: sqlite3.Connection, *, initialize: bool = False) -> None:
    database.row_factory = sqlite3.Row
    load_simple(database)
    if initialize:
        database.execute("PRAGMA journal_mode=WAL")
        database.execute("PRAGMA synchronous=NORMAL")
    database.execute("PRAGMA foreign_keys=ON")
    database.execute("PRAGMA busy_timeout=5000")


def _ensure_schema(database: sqlite3.Connection) -> None:
    required: dict[str, tuple[str, ...]] = {name: () for name in (
        "memory_meta", "memory_fragments", "memory_operations", "memory_skill_sources",
        "memory_generated_skills", "memory_important_sources",
    )}
    required["memory_weight_events"] = ("source",)
    required["memory_fragments"] = ("domain",)
    required["memory_text_fts"] = ("filename", "content")
    required["memory_work_items"] = ("lease_token", "lease_until")
    with schema_migration(
        database, target=MEMORY_DB_SCHEMA_VERSION,
        meta_table="memory_meta", required=required,
    ) as previous:
        _initialize_schema(database)
        if previous < 4:
            database.execute("INSERT INTO memory_text_fts(memory_text_fts) VALUES('rebuild')")


def _initialize_schema(database: sqlite3.Connection) -> None:
    execute_schema(database,
        """
        CREATE TABLE IF NOT EXISTS memory_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS memory_fragments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            filename_key TEXT NOT NULL UNIQUE,
            tier TEXT NOT NULL CHECK (
                tier IN ('seven_days', 'one_month', 'half_year', 'permanent')
            ),
            content TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            domain TEXT NOT NULL DEFAULT 'fact'
                CHECK (domain IN ('fact', 'relation', 'state')),
            weight INTEGER NOT NULL DEFAULT 0 CHECK (weight >= 0),
            created_at TEXT NOT NULL,
            content_updated_at TEXT NOT NULL,
            last_used_at TEXT,
            last_weight_date TEXT,
            tier_entered_at TEXT NOT NULL,
            expires_at TEXT,
            revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1)
        );
        CREATE INDEX IF NOT EXISTS idx_memory_fragments_tier_weight
            ON memory_fragments(tier, weight DESC, filename_key);
        CREATE INDEX IF NOT EXISTS idx_memory_fragments_expiry
            ON memory_fragments(tier, expires_at);
        CREATE INDEX IF NOT EXISTS idx_memory_fragments_updated
            ON memory_fragments(content_updated_at DESC);

        CREATE VIRTUAL TABLE IF NOT EXISTS memory_text_fts USING fts5(
            filename, content, content='memory_fragments', content_rowid='id',
            tokenize='simple 0'
        );
        CREATE TRIGGER IF NOT EXISTS memory_text_fts_insert AFTER INSERT ON memory_fragments BEGIN
            INSERT INTO memory_text_fts(rowid,filename,content)
            VALUES(new.id,new.filename,new.content);
        END;
        CREATE TRIGGER IF NOT EXISTS memory_text_fts_delete AFTER DELETE ON memory_fragments BEGIN
            INSERT INTO memory_text_fts(memory_text_fts,rowid,filename,content)
            VALUES('delete',old.id,old.filename,old.content);
        END;
        CREATE TRIGGER IF NOT EXISTS memory_text_fts_update
        AFTER UPDATE OF filename,content ON memory_fragments BEGIN
            INSERT INTO memory_text_fts(memory_text_fts,rowid,filename,content)
            VALUES('delete',old.id,old.filename,old.content);
            INSERT INTO memory_text_fts(rowid,filename,content)
            VALUES(new.id,new.filename,new.content);
        END;

        CREATE TABLE IF NOT EXISTS memory_weight_events (
            fragment_id INTEGER NOT NULL,
            evidence_date TEXT NOT NULL,
            reason TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'historical_migration',
            created_at TEXT NOT NULL,
            PRIMARY KEY (fragment_id, evidence_date),
            FOREIGN KEY (fragment_id) REFERENCES memory_fragments(id)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS memory_operations (
            operation_id TEXT PRIMARY KEY,
            completed_at TEXT NOT NULL,
            result_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS memory_work_items (
            item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_type TEXT NOT NULL CHECK (work_type IN ('candidate', 'archive', 'extraction', 'skill')),
            work_key TEXT NOT NULL UNIQUE,
            payload_json TEXT NOT NULL,
            source_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'completed')),
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            lease_token TEXT,
            lease_until TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_memory_work_items_status
            ON memory_work_items(work_type, status, item_id);
        CREATE TABLE IF NOT EXISTS memory_skill_sources (
            skill_name TEXT NOT NULL,
            fragment_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            PRIMARY KEY(skill_name, fragment_id)
        );
        CREATE TABLE IF NOT EXISTS memory_generated_skills (
            skill_name TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'active',
            reason TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_memory_operations_completed
            ON memory_operations(completed_at DESC);

        CREATE TABLE IF NOT EXISTS memory_important_sources (
            fragment_id INTEGER PRIMARY KEY,
            content_hash TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (fragment_id) REFERENCES memory_fragments(id)
                ON DELETE CASCADE
        );
        """
    )
    # The migration guard already serializes introspection and ALTER across processes.
    fragment_columns = {str(r["name"]) for r in database.execute(
        "PRAGMA table_info(memory_fragments)").fetchall()}
    if "domain" not in fragment_columns:
        database.execute(
            "ALTER TABLE memory_fragments ADD COLUMN domain TEXT NOT NULL DEFAULT 'fact' "
            "CHECK (domain IN ('fact', 'relation', 'state'))"
        )
    work_columns = {str(r["name"]) for r in database.execute(
        "PRAGMA table_info(memory_work_items)").fetchall()}
    for column in ("lease_token", "lease_until"):
        if column not in work_columns:
            database.execute(f"ALTER TABLE memory_work_items ADD COLUMN {column} TEXT")
    columns = {
        str(row["name"])
        for row in database.execute("PRAGMA table_info(memory_weight_events)").fetchall()
    }
    if "source" not in columns:
        database.execute(
            "ALTER TABLE memory_weight_events ADD COLUMN source TEXT NOT NULL DEFAULT 'historical_migration'"
        )


def _ensure_database(path: Path) -> None:
    key = str(path.resolve()).casefold()
    with _READY_DATABASES_LOCK:
        if key in _READY_DATABASES and path.is_file():
            return
        _READY_DATABASES.discard(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        database = sqlite3.connect(path, timeout=5.0)
        try:
            _configure(database)
            _ensure_schema(database)
            _configure(database, initialize=True)
            database.commit()
        finally:
            database.close()
        _READY_DATABASES.add(key)


@contextmanager
def connection(
    root: Path,
    user: str,
    *,
    write: bool = False,
) -> Iterator[sqlite3.Connection]:
    path = database_path(root, user)
    _ensure_database(path)
    database = sqlite3.connect(path, timeout=5.0)
    try:
        _configure(database)
        if write:
            database.execute("BEGIN IMMEDIATE")
        else:
            database.execute("PRAGMA query_only=ON")
        yield database
        if write:
            database.commit()
    except BaseException:
        if write:
            database.rollback()
        raise
    finally:
        database.close()
