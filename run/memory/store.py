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

from run.config.users import user_dir


MEMORY_DB_FILENAME = "memory.sqlite3"
MEMORY_DB_SCHEMA_VERSION = 1
_READY_DATABASES: set[str] = set()
_READY_DATABASES_LOCK = threading.Lock()


def database_path(root: Path, user: str) -> Path:
    return user_dir(user, root) / "improve" / MEMORY_DB_FILENAME


def _configure(database: sqlite3.Connection, *, initialize: bool = False) -> None:
    database.row_factory = sqlite3.Row
    if initialize:
        database.execute("PRAGMA journal_mode=WAL")
        database.execute("PRAGMA synchronous=NORMAL")
    database.execute("PRAGMA foreign_keys=ON")
    database.execute("PRAGMA busy_timeout=5000")


def _ensure_schema(database: sqlite3.Connection) -> None:
    database.executescript(
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

        CREATE TABLE IF NOT EXISTS memory_weight_events (
            fragment_id INTEGER NOT NULL,
            evidence_date TEXT NOT NULL,
            reason TEXT NOT NULL,
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
    database.execute(
        "INSERT OR IGNORE INTO memory_meta(key, value) VALUES('schema_version', ?)",
        (str(MEMORY_DB_SCHEMA_VERSION),),
    )
    stored = database.execute(
        "SELECT value FROM memory_meta WHERE key='schema_version'"
    ).fetchone()
    if stored is None or str(stored["value"]) != str(MEMORY_DB_SCHEMA_VERSION):
        value = "missing" if stored is None else str(stored["value"])
        raise RuntimeError(
            f"不支持的记忆数据库 schema：{value}，当前需要 {MEMORY_DB_SCHEMA_VERSION}"
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
            _configure(database, initialize=True)
            _ensure_schema(database)
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
