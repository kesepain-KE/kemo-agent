"""SQLite storage primitives for conversation history.

The database is the only durable history store.  ``Path`` values exposed by
``run.history`` remain logical window identifiers so the runtime does not need
to know whether a window lives in a directory or a table.
"""

from __future__ import annotations

from contextlib import contextmanager
import copy
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Callable, Iterable, Iterator

from run.config import user_dir


HISTORY_DB_FILENAME = "history.sqlite3"
HISTORY_SCHEMA_VERSION = 5
_SUMMARY_UNSET = object()
_READY_DATABASES: set[str] = set()
_READY_DATABASES_LOCK = threading.Lock()


def database_path(root: Path, user: str) -> Path:
    return user_dir(user, root) / "history" / HISTORY_DB_FILENAME


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _object(value: Any, default: Any) -> Any:
    if not isinstance(value, str):
        return copy.deepcopy(default)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return copy.deepcopy(default)
    return parsed


def _session_is_deleted(
    database: sqlite3.Connection, *, source: str, session_id: str
) -> bool:
    row = database.execute(
        "SELECT 1 FROM history_deleted_sessions WHERE source=? AND session_id=?",
        (str(source), str(session_id)),
    ).fetchone()
    return row is not None


def _session_generation(
    database: sqlite3.Connection, *, source: str, session_id: str
) -> str:
    row = database.execute(
        "SELECT record_json FROM history_sessions WHERE source=? AND session_id=?",
        (str(source), str(session_id)),
    ).fetchone()
    if row is None:
        return ""
    record = _object(row["record_json"], {})
    return str(record.get("session_generation") or "").strip() if isinstance(record, dict) else ""


def _deleted_window_exists(
    database: sqlite3.Connection, *, kind: str, name: str
) -> bool:
    row = database.execute(
        "SELECT 1 FROM history_deleted_windows WHERE window_kind=? AND window_name=?",
        (str(kind), str(name)),
    ).fetchone()
    return row is not None


def _remember_deleted_window(
    database: sqlite3.Connection,
    *,
    kind: str,
    name: str,
    source: str,
    session_id: str,
    session_generation: str = "",
) -> None:
    database.execute(
        """
        INSERT INTO history_deleted_windows(
            window_kind, window_name, source, session_id,
            session_generation, deleted_at
        ) VALUES(?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(window_kind, window_name) DO UPDATE SET
            source=excluded.source,
            session_id=excluded.session_id,
            session_generation=excluded.session_generation,
            deleted_at=excluded.deleted_at
        """,
        (
            str(kind),
            str(name),
            str(source),
            str(session_id),
            str(session_generation or ""),
        ),
    )


def clear_session_delete_fence(
    root: Path, user: str, source: str, session_id: str
) -> None:
    """Allow an explicit new reservation to reuse a deleted logical id."""

    with connection(root, user, write=True) as database:
        database.execute(
            "DELETE FROM history_deleted_sessions WHERE source=? AND session_id=?",
            (str(source), str(session_id)),
        )


def _configure(connection: sqlite3.Connection, *, initialize: bool = False) -> None:
    connection.row_factory = sqlite3.Row
    if initialize:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")


def _migrate_archive_text_to_messages(connection: sqlite3.Connection) -> None:
    from run.history.message_store import _MESSAGE_INSERT_SQL, _message_rows

    """Make archive message rows authoritative and remove the duplicate text blob."""

    rows = connection.execute(
        "SELECT window_name, source, session_id, created_at, updated_at, text_json "
        "FROM history_windows WHERE window_kind='archive'"
    ).fetchall()
    for row in rows:
        text = _object(row["text_json"], {"schema_version": 1, "messages": []})
        messages = text.get("messages") if isinstance(text, dict) else []
        candidates = _message_rows(
            str(row["window_name"]),
            str(row["source"]),
            str(row["session_id"]),
            str(row["created_at"]),
            str(row["updated_at"]),
            messages if isinstance(messages, list) else [],
        )
        existing = int(
            connection.execute(
                "SELECT COUNT(*) FROM history_messages WHERE window_name=?",
                (str(row["window_name"]),),
            ).fetchone()[0]
        )
        if existing != len(candidates):
            connection.execute(
                "DELETE FROM history_messages WHERE window_name=?",
                (str(row["window_name"]),),
            )
            connection.executemany(
                _MESSAGE_INSERT_SQL,
                candidates,
            )
        compact_text = {
            "schema_version": max(1, int(text.get("schema_version") or 1))
            if isinstance(text, dict)
            else 1,
            "storage": "history_messages",
        }
        connection.execute(
            "UPDATE history_windows SET text_json=? "
            "WHERE window_kind='archive' AND window_name=?",
            (_json(compact_text), str(row["window_name"])),
        )


def _migrate_window_partitions_to_rounds(connection: sqlite3.Connection) -> None:
    from run.history.window_store import _partition_reference, _sync_window_rounds

    rows = connection.execute(
        "SELECT window_name, window_kind, data_json, think_json, tool_json, items_json "
        "FROM history_windows"
    ).fetchall()
    for row in rows:
        data = _object(row["data_json"], {})
        think = _object(row["think_json"], {"schema_version": 1, "rounds": []})
        tool = _object(row["tool_json"], {"schema_version": 1, "rounds": []})
        items = _object(row["items_json"], {"schema_version": 2, "items": []})
        _sync_window_rounds(
            connection,
            window_kind=str(row["window_kind"]),
            window_name=str(row["window_name"]),
            think=think if isinstance(think, dict) else {},
            tool=tool if isinstance(tool, dict) else {},
            items=items if isinstance(items, dict) else {},
            metrics=data.get("round_metrics") if isinstance(data, dict) else [],
        )
        if isinstance(data, dict):
            data.pop("round_metrics", None)
            data["round_metrics_storage"] = "history_rounds"
        connection.execute(
            """
            UPDATE history_windows SET data_json=?, think_json=?, tool_json=?, items_json=?
            WHERE window_kind=? AND window_name=?
            """,
            (
                _json(data),
                _json(_partition_reference(think, default_schema=1)),
                _json(_partition_reference(tool, default_schema=1)),
                _json(_partition_reference(items, default_schema=2)),
                str(row["window_kind"]),
                str(row["window_name"]),
            ),
        )


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS history_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS history_windows (
            window_name TEXT NOT NULL,
            window_kind TEXT NOT NULL CHECK (window_kind IN ('archive', 'runtime')),
            source TEXT NOT NULL,
            session_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            rounds INTEGER NOT NULL DEFAULT 0,
            data_json TEXT NOT NULL,
            text_json TEXT NOT NULL,
            think_json TEXT NOT NULL,
            tool_json TEXT NOT NULL,
            items_json TEXT NOT NULL,
            PRIMARY KEY (window_kind, window_name)
        );
        CREATE INDEX IF NOT EXISTS idx_history_windows_session
            ON history_windows(source, session_id, window_kind, updated_at DESC);

        CREATE TABLE IF NOT EXISTS history_sessions (
            source TEXT NOT NULL,
            session_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            archive_window TEXT NOT NULL DEFAULT '',
            lifecycle TEXT NOT NULL DEFAULT 'open',
            run_state TEXT NOT NULL DEFAULT 'idle',
            title TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            rounds INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            memory_status TEXT NOT NULL DEFAULT 'unknown',
            summary_status TEXT NOT NULL DEFAULT 'none',
            record_json TEXT NOT NULL,
            PRIMARY KEY (source, session_id)
        );
        CREATE INDEX IF NOT EXISTS idx_history_sessions_list
            ON history_sessions(source, lifecycle, updated_at DESC, session_id DESC);
        CREATE INDEX IF NOT EXISTS idx_history_sessions_memory
            ON history_sessions(memory_status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_history_sessions_summary
            ON history_sessions(summary_status, updated_at);

        CREATE TABLE IF NOT EXISTS history_active_sessions (
            active_key TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            session_id TEXT NOT NULL
        );

        -- A deleted session must remain fenced off long enough to reject
        -- terminal commits that were already in flight when the user deleted
        -- it.  The row is cleared only by an explicit new reservation for the
        -- same logical id; ordinary registry upserts cannot remove it.
        CREATE TABLE IF NOT EXISTS history_deleted_sessions (
            source TEXT NOT NULL,
            session_id TEXT NOT NULL,
            deleted_at TEXT NOT NULL,
            PRIMARY KEY (source, session_id)
        );

        -- Keep physical window tombstones so a late writer cannot recreate a
        -- deleted window after the same logical session id is reserved again.
        -- New physical windows for an existing session are still allowed; only
        -- a previously deleted window name is fenced.
        CREATE TABLE IF NOT EXISTS history_deleted_windows (
            window_kind TEXT NOT NULL CHECK (window_kind IN ('archive', 'runtime')),
            window_name TEXT NOT NULL,
            source TEXT NOT NULL,
            session_id TEXT NOT NULL,
            session_generation TEXT NOT NULL DEFAULT '',
            deleted_at TEXT NOT NULL,
            PRIMARY KEY (window_kind, window_name)
        );
        CREATE INDEX IF NOT EXISTS idx_history_deleted_windows_session
            ON history_deleted_windows(source, session_id, deleted_at DESC);

        CREATE TABLE IF NOT EXISTS history_messages (
            window_name TEXT NOT NULL,
            message_index INTEGER NOT NULL,
            source TEXT NOT NULL,
            session_id TEXT NOT NULL,
            round_number INTEGER NOT NULL DEFAULT 0,
            role TEXT NOT NULL,
            content_text TEXT NOT NULL,
            message_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (window_name, message_index)
        );
        CREATE INDEX IF NOT EXISTS idx_history_messages_session
            ON history_messages(source, session_id, message_index);
        CREATE INDEX IF NOT EXISTS idx_history_messages_role
            ON history_messages(role, updated_at DESC);

        CREATE TABLE IF NOT EXISTS history_rounds (
            window_kind TEXT NOT NULL CHECK (window_kind IN ('archive', 'runtime')),
            window_name TEXT NOT NULL,
            round_number INTEGER NOT NULL,
            think_json TEXT NOT NULL DEFAULT '',
            tool_json TEXT NOT NULL DEFAULT '',
            items_json TEXT NOT NULL DEFAULT '[]',
            metric_json TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (window_kind, window_name, round_number)
        );
        CREATE INDEX IF NOT EXISTS idx_history_rounds_window
            ON history_rounds(window_kind, window_name, round_number);

        CREATE TABLE IF NOT EXISTS history_context_summaries (
            window_name TEXT PRIMARY KEY,
            source TEXT NOT NULL DEFAULT '',
            session_id TEXT NOT NULL DEFAULT '',
            schema_version INTEGER NOT NULL,
            source_hash TEXT NOT NULL DEFAULT '',
            previous_source_hash TEXT,
            covered_through_round INTEGER NOT NULL DEFAULT 0,
            covered_rounds_json TEXT NOT NULL DEFAULT '[]',
            summary_json TEXT NOT NULL,
            memory_extractions_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_history_context_summary_session
            ON history_context_summaries(source, session_id);

        CREATE TABLE IF NOT EXISTS message_processed_messages (
            dedupe_key TEXT PRIMARY KEY,
            platform TEXT NOT NULL DEFAULT '',
            message_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL CHECK (status IN ('processing', 'completed', 'failed')),
            claimed_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '',
            session_id TEXT NOT NULL DEFAULT '',
            error_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_message_processed_status_time
            ON message_processed_messages(status, updated_at);
        """
    )
    current_row = connection.execute(
        "SELECT value FROM history_meta WHERE key='schema_version'"
    ).fetchone()
    try:
        current_version = int(current_row[0]) if current_row is not None else 0
    except (TypeError, ValueError):
        current_version = 0
    if current_version < 2:
        _migrate_archive_text_to_messages(connection)
    if current_version < 3:
        _migrate_window_partitions_to_rounds(connection)
    connection.execute(
        "INSERT OR REPLACE INTO history_meta(key, value) VALUES('schema_version', ?)",
        (str(HISTORY_SCHEMA_VERSION),),
    )


def _ready_key(path: Path) -> str:
    return str(path.resolve()).casefold()


def _ensure_database(path: Path) -> None:
    """Initialize/migrate once per process instead of on every read query."""

    key = _ready_key(path)
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
    root: Path, user: str, *, write: bool = False
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
