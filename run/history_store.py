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
from typing import Any, Iterator

from run.users import user_dir


HISTORY_DB_FILENAME = "history.sqlite3"
HISTORY_SCHEMA_VERSION = 1
_SUMMARY_UNSET = object()


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


def _configure(connection: sqlite3.Connection) -> None:
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")


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
    connection.execute(
        "INSERT OR REPLACE INTO history_meta(key, value) VALUES('schema_version', ?)",
        (str(HISTORY_SCHEMA_VERSION),),
    )


@contextmanager
def connection(
    root: Path, user: str, *, write: bool = False
) -> Iterator[sqlite3.Connection]:
    path = database_path(root, user)
    path.parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(path, timeout=5.0)
    try:
        _configure(database)
        _ensure_schema(database)
        database.commit()
        if write:
            database.execute("BEGIN IMMEDIATE")
        yield database
        if write:
            database.commit()
    except BaseException:
        if write:
            database.rollback()
        raise
    finally:
        database.close()


def window_location(directory: Path) -> tuple[Path, str, str, str]:
    """Return ``(root, user, kind, window_name)`` for a logical window path."""

    resolved = directory.resolve()
    kind = "runtime" if resolved.parent.name == "temp" else "archive"
    history_dir = resolved.parent.parent if kind == "runtime" else resolved.parent
    if history_dir.name != "history" or history_dir.parent.parent.name != "users":
        raise ValueError(f"历史窗口路径无效：{directory}")
    user = history_dir.parent.name
    root = history_dir.parent.parent.parent
    return root, user, kind, resolved.name


def window_path(
    root: Path, user: str, window_name: str, *, kind: str = "archive"
) -> Path:
    base = user_dir(user, root) / "history"
    return (base / "temp" / window_name) if kind == "runtime" else (base / window_name)


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for block in value:
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if isinstance(text, str) and text:
            parts.append(text)
    return "\n".join(parts)


def _round_number(message: dict[str, Any], fallback: int) -> int:
    metadata = message.get("metadata")
    if isinstance(metadata, dict):
        try:
            return max(0, int(metadata.get("round") or 0))
        except (TypeError, ValueError):
            pass
    return fallback


def _store_context_summary(
    database: sqlite3.Connection,
    *,
    window_name: str,
    source: str,
    session_id: str,
    cache: dict[str, Any] | None,
) -> None:
    if cache is None:
        database.execute(
            "DELETE FROM history_context_summaries WHERE window_name=?",
            (window_name,),
        )
        return
    now = str(cache.get("created_at") or "")
    database.execute(
        """
        INSERT INTO history_context_summaries(
            window_name, source, session_id, schema_version, source_hash,
            previous_source_hash, covered_through_round, covered_rounds_json,
            summary_json, memory_extractions_json, created_at, updated_at
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(window_name) DO UPDATE SET
            source=excluded.source,
            session_id=excluded.session_id,
            schema_version=excluded.schema_version,
            source_hash=excluded.source_hash,
            previous_source_hash=excluded.previous_source_hash,
            covered_through_round=excluded.covered_through_round,
            covered_rounds_json=excluded.covered_rounds_json,
            summary_json=excluded.summary_json,
            memory_extractions_json=excluded.memory_extractions_json,
            created_at=excluded.created_at,
            updated_at=excluded.updated_at
        """,
        (
            window_name,
            source,
            session_id,
            max(0, int(cache.get("schema_version") or 0)),
            str(cache.get("source_hash") or ""),
            cache.get("previous_source_hash"),
            max(0, int(cache.get("covered_through_round") or 0)),
            _json(cache.get("covered_rounds") or []),
            _json(cache.get("summary") or {}),
            _json(cache.get("memory_extractions") or []),
            now,
            now,
        ),
    )


def save_window(
    directory: Path,
    window: dict[str, Any],
    *,
    summary_cache: dict[str, Any] | None | object = _SUMMARY_UNSET,
) -> dict[str, Any]:
    root, user, kind, name = window_location(directory)
    data = copy.deepcopy(window.get("data") or {})
    text = copy.deepcopy(window.get("text") or {})
    think = copy.deepcopy(window.get("think") or {})
    tool = copy.deepcopy(window.get("tool") or {})
    items = copy.deepcopy(window.get("items") or {})
    with connection(root, user, write=True) as database:
        existing = database.execute(
            "SELECT title FROM history_windows WHERE window_kind=? AND window_name=?",
            (kind, name),
        ).fetchone()
        if existing is not None:
            data["title"] = str(existing["title"] or "")
        data["complete"] = True
        database.execute(
            """
            INSERT INTO history_windows(
                window_name, window_kind, source, session_id, title,
                created_at, updated_at, rounds, data_json, text_json,
                think_json, tool_json, items_json
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(window_kind, window_name) DO UPDATE SET
                source=excluded.source,
                session_id=excluded.session_id,
                title=excluded.title,
                created_at=excluded.created_at,
                updated_at=excluded.updated_at,
                rounds=excluded.rounds,
                data_json=excluded.data_json,
                text_json=excluded.text_json,
                think_json=excluded.think_json,
                tool_json=excluded.tool_json,
                items_json=excluded.items_json
            """,
            (
                name,
                kind,
                str(data.get("source") or ""),
                str(data.get("session_id") or ""),
                str(data.get("title") or ""),
                str(data.get("created_at") or data.get("updated_at") or ""),
                str(data.get("updated_at") or ""),
                max(0, int(data.get("rounds") or 0)),
                _json(data),
                _json(text),
                _json(think),
                _json(tool),
                _json(items),
            ),
        )
        if kind == "archive":
            database.execute(
                "DELETE FROM history_messages WHERE window_name=?", (name,)
            )
            fallback_round = 0
            for index, raw in enumerate(
                text.get("messages", []) if isinstance(text, dict) else []
            ):
                if not isinstance(raw, dict):
                    continue
                role = str(raw.get("role") or "")
                if role not in {"user", "assistant"}:
                    continue
                if role == "user":
                    fallback_round += 1
                database.execute(
                    """
                    INSERT INTO history_messages(
                        window_name, message_index, source, session_id,
                        round_number, role, content_text, message_json,
                        created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        index,
                        str(data.get("source") or ""),
                        str(data.get("session_id") or ""),
                        _round_number(raw, fallback_round),
                        role,
                        _content_text(raw.get("content")),
                        _json(raw),
                        str(data.get("created_at") or ""),
                        str(data.get("updated_at") or ""),
                    ),
                )
        if kind == "runtime" and summary_cache is not _SUMMARY_UNSET:
            _store_context_summary(
                database,
                window_name=name,
                source=str(data.get("source") or ""),
                session_id=str(data.get("session_id") or ""),
                cache=summary_cache if isinstance(summary_cache, dict) else None,
            )
    return data


def _summary_window(runtime_path: Path) -> tuple[Path, str, str]:
    root, user, kind, name = window_location(runtime_path)
    if kind != "runtime":
        raise ValueError(f"上下文摘要必须关联运行时窗口：{runtime_path}")
    return root, user, name


def read_context_summary(runtime_path: Path) -> dict[str, Any] | None:
    root, user, name = _summary_window(runtime_path)
    with connection(root, user) as database:
        row = database.execute(
            """
            SELECT schema_version, source_hash, previous_source_hash,
                   covered_through_round, covered_rounds_json, summary_json,
                   memory_extractions_json, created_at
            FROM history_context_summaries WHERE window_name=?
            """,
            (name,),
        ).fetchone()
    if row is None:
        return None
    return {
        "schema_version": int(row["schema_version"]),
        "source_hash": str(row["source_hash"] or ""),
        "previous_source_hash": row["previous_source_hash"],
        "covered_through_round": int(row["covered_through_round"] or 0),
        "covered_rounds": _object(row["covered_rounds_json"], []),
        "summary": _object(row["summary_json"], {}),
        "memory_extractions": _object(row["memory_extractions_json"], []),
        "created_at": str(row["created_at"] or ""),
    }


def write_context_summary(runtime_path: Path, cache: dict[str, Any] | None) -> None:
    root, user, name = _summary_window(runtime_path)
    with connection(root, user, write=True) as database:
        window = database.execute(
            """
            SELECT source, session_id FROM history_windows
            WHERE window_name=?
            ORDER BY CASE window_kind WHEN 'runtime' THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (name,),
        ).fetchone()
        _store_context_summary(
            database,
            window_name=name,
            source=str(window["source"] if window is not None else ""),
            session_id=str(window["session_id"] if window is not None else ""),
            cache=cache,
        )


def context_summary_exists(runtime_path: Path) -> bool:
    root, user, name = _summary_window(runtime_path)
    with connection(root, user) as database:
        return database.execute(
            "SELECT 1 FROM history_context_summaries WHERE window_name=?",
            (name,),
        ).fetchone() is not None


def load_window(directory: Path) -> dict[str, Any] | None:
    root, user, kind, name = window_location(directory)
    with connection(root, user) as database:
        row = database.execute(
            """
            SELECT data_json, text_json, think_json, tool_json, items_json
            FROM history_windows WHERE window_kind=? AND window_name=?
            """,
            (kind, name),
        ).fetchone()
    if row is None:
        return None
    return {
        "data": _object(row["data_json"], {}),
        "text": _object(row["text_json"], {"schema_version": 1, "messages": []}),
        "think": _object(row["think_json"], {"schema_version": 1, "rounds": []}),
        "tool": _object(row["tool_json"], {"schema_version": 1, "rounds": []}),
        "items": _object(row["items_json"], {"schema_version": 2, "items": []}),
    }


def window_exists(directory: Path) -> bool:
    root, user, kind, name = window_location(directory)
    with connection(root, user) as database:
        row = database.execute(
            "SELECT 1 FROM history_windows WHERE window_kind=? AND window_name=?",
            (kind, name),
        ).fetchone()
    return row is not None


def delete_window(directory: Path) -> bool:
    root, user, kind, name = window_location(directory)
    with connection(root, user, write=True) as database:
        if kind == "archive":
            database.execute(
                "DELETE FROM history_messages WHERE window_name=?", (name,)
            )
        else:
            database.execute(
                "DELETE FROM history_context_summaries WHERE window_name=?",
                (name,),
            )
        result = database.execute(
            "DELETE FROM history_windows WHERE window_kind=? AND window_name=?",
            (kind, name),
        )
    return result.rowcount > 0


def list_windows(
    root: Path, user: str, *, source: str | None = None
) -> list[dict[str, Any]]:
    sql = "SELECT window_name, source, session_id, data_json FROM history_windows WHERE window_kind='archive'"
    params: list[Any] = []
    if source is not None:
        sql += " AND source=?"
        params.append(source)
    sql += " ORDER BY updated_at DESC, window_name DESC"
    with connection(root, user) as database:
        rows = database.execute(sql, params).fetchall()
    return [
        {
            "window_name": str(row["window_name"]),
            "source": str(row["source"]),
            "session_id": str(row["session_id"]),
            "data": _object(row["data_json"], {}),
        }
        for row in rows
    ]


def find_window_name(root: Path, user: str, source: str, session_id: str) -> str | None:
    with connection(root, user) as database:
        row = database.execute(
            """
            SELECT window_name FROM history_windows
            WHERE window_kind='archive' AND source=? AND session_id=?
            ORDER BY updated_at DESC LIMIT 1
            """,
            (source, session_id),
        ).fetchone()
    return str(row["window_name"]) if row is not None else None


def rename_windows(
    root: Path, user: str, source: str, session_id: str, title: str
) -> int:
    with connection(root, user, write=True) as database:
        rows = database.execute(
            "SELECT window_kind, window_name, data_json FROM history_windows WHERE source=? AND session_id=?",
            (source, session_id),
        ).fetchall()
        for row in rows:
            data = _object(row["data_json"], {})
            data["title"] = title
            database.execute(
                """
                UPDATE history_windows SET title=?, data_json=?
                WHERE window_kind=? AND window_name=?
                """,
                (title, _json(data), row["window_kind"], row["window_name"]),
            )
    return len(rows)


def delete_session_windows(root: Path, user: str, source: str, session_id: str) -> int:
    with connection(root, user, write=True) as database:
        database.execute(
            "DELETE FROM history_context_summaries WHERE source=? AND session_id=?",
            (source, session_id),
        )
        database.execute(
            "DELETE FROM history_messages WHERE source=? AND session_id=?",
            (source, session_id),
        )
        result = database.execute(
            "DELETE FROM history_windows WHERE source=? AND session_id=?",
            (source, session_id),
        )
    return result.rowcount


def delete_source_windows(root: Path, user: str, source: str) -> tuple[int, int]:
    with connection(root, user, write=True) as database:
        session_count = int(
            database.execute(
                "SELECT COUNT(DISTINCT session_id) FROM history_windows WHERE source=?",
                (source,),
            ).fetchone()[0]
        )
        database.execute("DELETE FROM history_context_summaries WHERE source=?", (source,))
        database.execute("DELETE FROM history_messages WHERE source=?", (source,))
        result = database.execute(
            "DELETE FROM history_windows WHERE source=?", (source,)
        )
    return session_count, result.rowcount


def read_registry(
    root: Path, user: str
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, str]]]:
    with connection(root, user) as database:
        session_rows = database.execute(
            "SELECT record_json FROM history_sessions"
        ).fetchall()
        active_rows = database.execute(
            "SELECT active_key, source, session_id FROM history_active_sessions"
        ).fetchall()
    sessions: dict[str, dict[str, Any]] = {}
    for row in session_rows:
        record = _object(row["record_json"], {})
        if not isinstance(record, dict):
            continue
        source = str(record.get("source") or "")
        session_id = str(record.get("session_id") or "")
        if source and session_id:
            sessions[f"{source}\x1f{session_id}"] = record
    active = {
        str(row["active_key"]): {
            "source": str(row["source"]),
            "session_id": str(row["session_id"]),
        }
        for row in active_rows
    }
    return sessions, active


def read_registry_metadata(root: Path, user: str) -> dict[str, str]:
    with connection(root, user) as database:
        rows = database.execute(
            "SELECT key, value FROM history_meta WHERE key IN ('registry_revision', 'registry_updated_at')"
        ).fetchall()
    return {str(row["key"]): str(row["value"]) for row in rows}


def read_registry_record(
    root: Path, user: str, source: str, session_id: str
) -> dict[str, Any] | None:
    """Read one session record without decoding the complete registry."""

    with connection(root, user) as database:
        row = database.execute(
            "SELECT record_json FROM history_sessions WHERE source=? AND session_id=?",
            (source, session_id),
        ).fetchone()
    if row is None:
        return None
    record = _object(row["record_json"], {})
    return record if isinstance(record, dict) else None


def read_active_binding(
    root: Path, user: str, active_key: str
) -> dict[str, str] | None:
    """Read one active-session binding without loading unrelated sessions."""

    with connection(root, user) as database:
        row = database.execute(
            "SELECT source, session_id FROM history_active_sessions WHERE active_key=?",
            (active_key,),
        ).fetchone()
    if row is None:
        return None
    return {"source": str(row["source"]), "session_id": str(row["session_id"])}


def read_latest_registry_record(
    root: Path, user: str, source: str
) -> dict[str, Any] | None:
    """Read the latest non-deleted session for one source."""

    with connection(root, user) as database:
        row = database.execute(
            """
            SELECT record_json FROM history_sessions
            WHERE source=? AND lifecycle != 'deleted'
            ORDER BY updated_at DESC, session_id DESC LIMIT 1
            """,
            (source,),
        ).fetchone()
    if row is None:
        return None
    record = _object(row["record_json"], {})
    return record if isinstance(record, dict) else None


def _upsert_session_row(database: sqlite3.Connection, record: dict[str, Any]) -> None:
    source = str(record.get("source") or "")
    session_id = str(record.get("session_id") or "")
    if not source or not session_id:
        raise ValueError("历史会话记录缺少 source 或 session_id")
    database.execute(
        """
        INSERT INTO history_sessions(
            source, session_id, conversation_id, archive_window,
            lifecycle, run_state, title, summary, rounds, updated_at,
            memory_status, summary_status, record_json
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, session_id) DO UPDATE SET
            conversation_id=excluded.conversation_id,
            archive_window=excluded.archive_window,
            lifecycle=excluded.lifecycle,
            run_state=excluded.run_state,
            title=excluded.title,
            summary=excluded.summary,
            rounds=excluded.rounds,
            updated_at=excluded.updated_at,
            memory_status=excluded.memory_status,
            summary_status=excluded.summary_status,
            record_json=excluded.record_json
        """,
        (
            source,
            session_id,
            str(record.get("conversation_id") or session_id),
            str(record.get("archive_window") or ""),
            str(record.get("lifecycle") or "open"),
            str(record.get("run_state") or "idle"),
            str(record.get("title") or ""),
            str(record.get("summary") or ""),
            max(0, int(record.get("rounds") or 0)),
            str(record.get("updated_at") or ""),
            str(record.get("memory_status") or "unknown"),
            str(record.get("summary_status") or "none"),
            _json(record),
        ),
    )


def upsert_registry_record(
    root: Path,
    user: str,
    record: dict[str, Any],
    *,
    active_updates: dict[str, dict[str, str] | None] | None = None,
    updated_at: str = "",
) -> dict[str, Any]:
    """Atomically upsert one session and optional active bindings."""

    rendered = copy.deepcopy(record)
    with connection(root, user, write=True) as database:
        _upsert_session_row(database, rendered)
        for active_key, binding in (active_updates or {}).items():
            if binding is None:
                database.execute(
                    "DELETE FROM history_active_sessions WHERE active_key=?",
                    (str(active_key),),
                )
                continue
            source = str(binding.get("source") or "")
            session_id = str(binding.get("session_id") or "")
            if not source or not session_id:
                continue
            database.execute(
                """
                INSERT INTO history_active_sessions(active_key, source, session_id)
                VALUES(?, ?, ?)
                ON CONFLICT(active_key) DO UPDATE SET
                    source=excluded.source,
                    session_id=excluded.session_id
                """,
                (str(active_key), source, session_id),
            )
        database.execute(
            """
            INSERT INTO history_meta(key, value) VALUES('registry_revision', '1')
            ON CONFLICT(key) DO UPDATE SET
                value=CAST(COALESCE(NULLIF(history_meta.value, ''), '0') AS INTEGER) + 1
            """
        )
        database.execute(
            "INSERT OR REPLACE INTO history_meta(key, value) VALUES('registry_updated_at', ?)",
            (str(updated_at or record.get("updated_at") or ""),),
        )
    return rendered


def write_registry(
    root: Path,
    user: str,
    sessions: dict[str, Any],
    active: dict[str, Any],
    *,
    revision: int = 0,
    updated_at: str = "",
) -> None:
    with connection(root, user, write=True) as database:
        existing_rows = database.execute(
            "SELECT source, session_id, record_json FROM history_sessions"
        ).fetchall()
        existing = {
            (str(row["source"]), str(row["session_id"])): str(row["record_json"])
            for row in existing_rows
        }
        current_keys: set[tuple[str, str]] = set()
        for record in sessions.values():
            if not isinstance(record, dict):
                continue
            source = str(record.get("source") or "")
            session_id = str(record.get("session_id") or "")
            if not source or not session_id:
                continue
            key = (source, session_id)
            current_keys.add(key)
            rendered = _json(record)
            if existing.get(key) == rendered:
                continue
            _upsert_session_row(database, record)
        removed = set(existing) - current_keys
        if removed:
            database.executemany(
                "DELETE FROM history_sessions WHERE source=? AND session_id=?",
                sorted(removed),
            )

        existing_active_rows = database.execute(
            "SELECT active_key, source, session_id FROM history_active_sessions"
        ).fetchall()
        existing_active = {
            str(row["active_key"]): (str(row["source"]), str(row["session_id"]))
            for row in existing_active_rows
        }
        current_active: dict[str, tuple[str, str]] = {}
        for active_key, value in active.items():
            if not isinstance(value, dict):
                continue
            source = str(value.get("source") or "")
            session_id = str(value.get("session_id") or "")
            if source and session_id:
                normalized_key = str(active_key)
                current_active[normalized_key] = (source, session_id)
                if existing_active.get(normalized_key) == (source, session_id):
                    continue
                database.execute(
                    """
                    INSERT INTO history_active_sessions(active_key, source, session_id)
                    VALUES(?, ?, ?)
                    ON CONFLICT(active_key) DO UPDATE SET
                        source=excluded.source,
                        session_id=excluded.session_id
                    """,
                    (normalized_key, source, session_id),
                )
        removed_active = set(existing_active) - set(current_active)
        if removed_active:
            database.executemany(
                "DELETE FROM history_active_sessions WHERE active_key=?",
                [(key,) for key in sorted(removed_active)],
            )
        database.execute(
            "INSERT OR REPLACE INTO history_meta(key, value) VALUES('registry_revision', ?)",
            (str(max(0, int(revision))),),
        )
        database.execute(
            "INSERT OR REPLACE INTO history_meta(key, value) VALUES('registry_updated_at', ?)",
            (str(updated_at or ""),),
        )


def query_session_records(
    root: Path,
    user: str,
    *,
    source: str | None = None,
    query: str = "",
    limit: int | None = None,
    before_updated_at: str = "",
) -> tuple[list[dict[str, Any]], bool]:
    clauses = ["lifecycle != 'deleted'"]
    params: list[Any] = []
    if source is not None:
        clauses.append("source=?")
        params.append(source)
    if query.strip():
        clauses.append(
            """(
                lower(title) LIKE ? OR lower(summary) LIKE ? OR lower(session_id) LIKE ?
                OR EXISTS (
                    SELECT 1 FROM history_messages AS message
                    WHERE message.source=history_sessions.source
                      AND message.session_id=history_sessions.session_id
                      AND lower(message.content_text) LIKE ?
                )
            )"""
        )
        needle = f"%{query.strip().casefold()}%"
        params.extend((needle, needle, needle, needle))
    if before_updated_at:
        cursor_updated_at = before_updated_at
        cursor_session_id = ""
        parsed_cursor = _object(before_updated_at, None)
        if (
            isinstance(parsed_cursor, list)
            and len(parsed_cursor) == 2
            and all(isinstance(item, str) for item in parsed_cursor)
        ):
            cursor_updated_at, cursor_session_id = parsed_cursor
        if cursor_session_id:
            clauses.append("(updated_at < ? OR (updated_at = ? AND session_id < ?))")
            params.extend((cursor_updated_at, cursor_updated_at, cursor_session_id))
        else:
            # Backward compatibility for clients that used a timestamp cursor.
            clauses.append("updated_at < ?")
            params.append(cursor_updated_at)
    requested = None if limit is None else max(1, int(limit))
    sql = "SELECT record_json FROM history_sessions WHERE " + " AND ".join(clauses)
    sql += " ORDER BY updated_at DESC, session_id DESC"
    if requested is not None:
        sql += " LIMIT ?"
        params.append(requested + 1)
    with connection(root, user) as database:
        rows = database.execute(sql, params).fetchall()
    has_more = requested is not None and len(rows) > requested
    selected = rows[:requested] if requested is not None else rows
    return [
        record
        for row in selected
        if isinstance((record := _object(row["record_json"], {})), dict)
    ], has_more


def session_page_cursor(record: dict[str, Any]) -> str:
    """Build an opaque, stable cursor for descending session pagination."""

    updated_at = str(record.get("updated_at") or "")
    session_id = str(record.get("session_id") or "")
    return _json([updated_at, session_id]) if updated_at and session_id else ""


def message_windows(root: Path, user: str) -> list[dict[str, Any]]:
    """Return archive messages grouped by window for rich search semantics."""

    with connection(root, user) as database:
        windows = database.execute(
            """
            SELECT window_name, source, session_id, data_json
            FROM history_windows WHERE window_kind='archive'
            ORDER BY updated_at DESC, window_name DESC
            """
        ).fetchall()
        messages = database.execute(
            """
            SELECT window_name, message_index, role, content_text, message_json
            FROM history_messages ORDER BY window_name, message_index
            """
        ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in messages:
        message = _object(row["message_json"], {})
        if isinstance(message, dict):
            grouped.setdefault(str(row["window_name"]), []).append(message)
    return [
        {
            "window_name": str(row["window_name"]),
            "source": str(row["source"]),
            "session_id": str(row["session_id"]),
            "data": _object(row["data_json"], {}),
            "text": {
                "schema_version": 1,
                "messages": grouped.get(str(row["window_name"]), []),
            },
        }
        for row in windows
    ]
