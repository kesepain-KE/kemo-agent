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

from run.config.users import user_dir


HISTORY_DB_FILENAME = "history.sqlite3"
HISTORY_SCHEMA_VERSION = 3
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


def _configure(connection: sqlite3.Connection, *, initialize: bool = False) -> None:
    connection.row_factory = sqlite3.Row
    if initialize:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")


def _migrate_archive_text_to_messages(connection: sqlite3.Connection) -> None:
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


_MESSAGE_INSERT_SQL = """
    INSERT INTO history_messages(
        window_name, message_index, source, session_id,
        round_number, role, content_text, message_json,
        created_at, updated_at
    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _message_rows(
    window_name: str,
    source: str,
    session_id: str,
    created_at: str,
    updated_at: str,
    messages: list[Any],
) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    fallback_round = 0
    for index, raw in enumerate(messages):
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        if role == "user":
            fallback_round += 1
        rows.append(
            (
                window_name,
                index,
                source,
                session_id,
                _round_number(raw, fallback_round),
                role,
                _content_text(raw.get("content")),
                _json(raw),
                created_at,
                updated_at,
            )
        )
    return rows


def _sync_archive_messages(
    database: sqlite3.Connection,
    *,
    window_name: str,
    source: str,
    session_id: str,
    created_at: str,
    updated_at: str,
    messages: list[Any],
) -> None:
    candidates = _message_rows(
        window_name,
        source,
        session_id,
        created_at,
        updated_at,
        messages,
    )
    existing = database.execute(
        "SELECT message_index, source, session_id, message_json "
        "FROM history_messages WHERE window_name=? ORDER BY message_index",
        (window_name,),
    ).fetchall()
    prefix_matches = len(existing) <= len(candidates)
    if prefix_matches:
        for row, candidate in zip(existing, candidates):
            if (
                int(row["message_index"]) != int(candidate[1])
                or str(row["source"]) != source
                or str(row["session_id"]) != session_id
                or str(row["message_json"]) != str(candidate[7])
            ):
                prefix_matches = False
                break
    if not prefix_matches:
        database.execute(
            "DELETE FROM history_messages WHERE window_name=?", (window_name,)
        )
        database.executemany(_MESSAGE_INSERT_SQL, candidates)
        return
    if len(existing) < len(candidates):
        database.executemany(_MESSAGE_INSERT_SQL, candidates[len(existing) :])


_ROUND_INSERT_SQL = """
    INSERT INTO history_rounds(
        window_kind, window_name, round_number,
        think_json, tool_json, items_json, metric_json
    ) VALUES(?, ?, ?, ?, ?, ?, ?)
"""


def _safe_round_number(value: Any) -> int:
    if not isinstance(value, dict):
        return 0
    metadata = value.get("metadata")
    raw = metadata.get("round") if isinstance(metadata, dict) else value.get("round")
    try:
        return max(0, int(raw or 0))
    except (TypeError, ValueError):
        return 0


def _partition_reference(value: Any, *, default_schema: int) -> dict[str, Any]:
    schema = value.get("schema_version") if isinstance(value, dict) else default_schema
    try:
        rendered_schema = max(1, int(schema or default_schema))
    except (TypeError, ValueError):
        rendered_schema = default_schema
    return {"schema_version": rendered_schema, "storage": "history_rounds"}


def _partition_schema(value: Any, default_schema: int) -> int:
    raw = value.get("schema_version") if isinstance(value, dict) else default_schema
    try:
        return max(1, int(raw or default_schema))
    except (TypeError, ValueError):
        return default_schema


def _window_round_rows(
    window_kind: str,
    window_name: str,
    *,
    think: dict[str, Any],
    tool: dict[str, Any],
    items: dict[str, Any],
    metrics: Any,
) -> list[tuple[Any, ...]]:
    thinks = {
        number: value
        for value in think.get("rounds", [])
        if isinstance(value, dict) and (number := _safe_round_number(value)) > 0
    }
    tools = {
        number: value
        for value in tool.get("rounds", [])
        if isinstance(value, dict) and (number := _safe_round_number(value)) > 0
    }
    metrics_by_round = {
        number: value
        for value in (metrics if isinstance(metrics, list) else [])
        if isinstance(value, dict)
        if (number := _safe_round_number(value)) > 0
    }
    items_by_round: dict[int, list[dict[str, Any]]] = {}
    for value in items.get("items", []) if isinstance(items.get("items"), list) else []:
        if not isinstance(value, dict):
            continue
        number = _safe_round_number(value)
        items_by_round.setdefault(number, []).append(value)
    round_numbers = sorted(
        set(thinks) | set(tools) | set(metrics_by_round) | set(items_by_round)
    )
    return [
        (
            window_kind,
            window_name,
            number,
            _json(thinks[number]) if number in thinks else "",
            _json(tools[number]) if number in tools else "",
            _json(items_by_round.get(number, [])),
            _json(metrics_by_round[number]) if number in metrics_by_round else "",
        )
        for number in round_numbers
    ]


def _sync_window_rounds(
    database: sqlite3.Connection,
    *,
    window_kind: str,
    window_name: str,
    think: dict[str, Any],
    tool: dict[str, Any],
    items: dict[str, Any],
    metrics: Any,
) -> None:
    candidates = _window_round_rows(
        window_kind,
        window_name,
        think=think,
        tool=tool,
        items=items,
        metrics=metrics,
    )
    existing = database.execute(
        """
        SELECT round_number, think_json, tool_json, items_json, metric_json
        FROM history_rounds WHERE window_kind=? AND window_name=?
        ORDER BY round_number
        """,
        (window_kind, window_name),
    ).fetchall()
    prefix_matches = len(existing) <= len(candidates)
    if prefix_matches:
        for row, candidate in zip(existing, candidates):
            if (
                int(row["round_number"]) != int(candidate[2])
                or str(row["think_json"]) != str(candidate[3])
                or str(row["tool_json"]) != str(candidate[4])
                or str(row["items_json"]) != str(candidate[5])
                or str(row["metric_json"]) != str(candidate[6])
            ):
                prefix_matches = False
                break
    if not prefix_matches:
        database.execute(
            "DELETE FROM history_rounds WHERE window_kind=? AND window_name=?",
            (window_kind, window_name),
        )
        database.executemany(_ROUND_INSERT_SQL, candidates)
        return
    if len(existing) < len(candidates):
        database.executemany(_ROUND_INSERT_SQL, candidates[len(existing) :])


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


def _save_window_on_connection(
    database: sqlite3.Connection,
    *,
    kind: str,
    name: str,
    window: dict[str, Any],
    summary_cache: dict[str, Any] | None | object = _SUMMARY_UNSET,
) -> dict[str, Any]:
    data = copy.deepcopy(window.get("data") or {})
    text = copy.deepcopy(window.get("text") or {})
    think = copy.deepcopy(window.get("think") or {})
    tool = copy.deepcopy(window.get("tool") or {})
    items = copy.deepcopy(window.get("items") or {})
    existing = database.execute(
        "SELECT title FROM history_windows WHERE window_kind=? AND window_name=?",
        (kind, name),
    ).fetchone()
    if existing is not None:
        data["title"] = str(existing["title"] or "")
    data["complete"] = True
    source = str(data.get("source") or "")
    session_id = str(data.get("session_id") or "")
    created_at = str(data.get("created_at") or data.get("updated_at") or "")
    updated_at = str(data.get("updated_at") or "")
    messages = text.get("messages", []) if isinstance(text, dict) else []
    if kind == "archive":
        _sync_archive_messages(
            database,
            window_name=name,
            source=source,
            session_id=session_id,
            created_at=created_at,
            updated_at=updated_at,
            messages=messages if isinstance(messages, list) else [],
        )
        stored_text = {
            "schema_version": max(1, int(text.get("schema_version") or 1)),
            "storage": "history_messages",
        }
    else:
        stored_text = text
    _sync_window_rounds(
        database,
        window_kind=kind,
        window_name=name,
        think=think if isinstance(think, dict) else {},
        tool=tool if isinstance(tool, dict) else {},
        items=items if isinstance(items, dict) else {},
        metrics=data.get("round_metrics"),
    )
    stored_data = copy.deepcopy(data)
    stored_data.pop("round_metrics", None)
    stored_data["round_metrics_storage"] = "history_rounds"
    stored_think = _partition_reference(think, default_schema=1)
    stored_tool = _partition_reference(tool, default_schema=1)
    stored_items = _partition_reference(items, default_schema=2)
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
            source,
            session_id,
            str(data.get("title") or ""),
            created_at,
            updated_at,
            max(0, int(data.get("rounds") or 0)),
            _json(stored_data),
            _json(stored_text),
            _json(stored_think),
            _json(stored_tool),
            _json(stored_items),
        ),
    )
    if kind == "runtime" and summary_cache is not _SUMMARY_UNSET:
        _store_context_summary(
            database,
            window_name=name,
            source=source,
            session_id=session_id,
            cache=summary_cache if isinstance(summary_cache, dict) else None,
        )
    return data


def _write_registry_state(
    database: sqlite3.Connection,
    *,
    record: dict[str, Any] | None,
    active_updates: dict[str, dict[str, str] | None] | None,
    updated_at: str,
) -> None:
    if record is not None:
        _upsert_session_row(database, record)
    for active_key, binding in (active_updates or {}).items():
        if binding is None:
            database.execute(
                "DELETE FROM history_active_sessions WHERE active_key=?",
                (str(active_key),),
            )
        else:
            source = str(binding.get("source") or "")
            session_id = str(binding.get("session_id") or "")
            if source and session_id:
                database.execute(
                    """
                    INSERT INTO history_active_sessions(active_key, source, session_id)
                    VALUES(?, ?, ?)
                    ON CONFLICT(active_key) DO UPDATE SET
                        source=excluded.source, session_id=excluded.session_id
                    """,
                    (str(active_key), source, session_id),
                )
    if record is not None or active_updates:
        database.execute(
            """
            INSERT INTO history_meta(key, value) VALUES('registry_revision', '1')
            ON CONFLICT(key) DO UPDATE SET
                value=CAST(COALESCE(NULLIF(history_meta.value, ''), '0') AS INTEGER) + 1
            """
        )
        database.execute(
            "INSERT OR REPLACE INTO history_meta(key, value) VALUES('registry_updated_at', ?)",
            (updated_at,),
        )


def save_window_bundle(
    entries: list[tuple[Path, dict[str, Any], dict[str, Any] | None | object]],
    *,
    session_record: dict[str, Any] | None = None,
    active_updates: dict[str, dict[str, str] | None] | None = None,
    updated_at: str = "",
) -> list[dict[str, Any]]:
    if not entries:
        return []
    locations = [window_location(directory) for directory, _, _ in entries]
    root, user = locations[0][0], locations[0][1]
    if any(location[0] != root or location[1] != user for location in locations[1:]):
        raise ValueError("同一历史事务只能写入一个用户的窗口")
    stored: list[dict[str, Any]] = []
    with connection(root, user, write=True) as database:
        for (_, window, summary_cache), (_, _, kind, name) in zip(entries, locations):
            stored.append(
                _save_window_on_connection(
                    database,
                    kind=kind,
                    name=name,
                    window=window,
                    summary_cache=summary_cache,
                )
            )
        _write_registry_state(
            database,
            record=copy.deepcopy(session_record) if session_record is not None else None,
            active_updates=active_updates,
            updated_at=str(updated_at or (session_record or {}).get("updated_at") or ""),
        )
    return stored


def save_window(
    directory: Path,
    window: dict[str, Any],
    *,
    summary_cache: dict[str, Any] | None | object = _SUMMARY_UNSET,
) -> dict[str, Any]:
    return save_window_bundle([(directory, window, summary_cache)])[0]


def patch_window_data(
    directory: Path,
    data: dict[str, Any],
    *,
    session_record: dict[str, Any] | None = None,
    updated_at: str = "",
    merge_updates: dict[str, Any] | None = None,
    merge_removals: Iterable[str] = (),
    session_record_factory: Callable[
        [dict[str, Any], dict[str, Any] | None], dict[str, Any] | None
    ] | None = None,
) -> dict[str, Any]:
    """Update window/session metadata without rewriting transcript partitions.

    ``merge_updates`` is used by small asynchronous metadata transitions.  It
    reads the current ``data_json`` while holding the SQLite write transaction
    and overlays only the requested keys, so a caller's stale in-memory window
    cannot roll back a newer conversation round.  A record factory receives
    that merged data and the current registry row, allowing the registry to be
    updated from the same transaction as the window row.
    """

    root, user, kind, name = window_location(directory)
    with connection(root, user, write=True) as database:
        existing = database.execute(
            "SELECT source, session_id, title, data_json "
            "FROM history_windows WHERE window_kind=? AND window_name=?",
            (kind, name),
        ).fetchone()
        if existing is None:
            raise FileNotFoundError(f"历史窗口不存在：{directory}")

        if merge_updates is None:
            rendered = copy.deepcopy(data)
        else:
            current_data = _object(existing["data_json"], {})
            rendered = copy.deepcopy(current_data) if isinstance(current_data, dict) else {}
            for key, value in merge_updates.items():
                rendered[str(key)] = copy.deepcopy(value)
            for key in merge_removals:
                rendered.pop(str(key), None)
        rendered["complete"] = True
        rendered.setdefault("source", str(existing["source"] or ""))
        rendered.setdefault("session_id", str(existing["session_id"] or ""))
        rendered["title"] = str(existing["title"] or rendered.get("title") or "")
        stored_rendered = copy.deepcopy(rendered)
        stored_rendered.pop("round_metrics", None)
        stored_rendered["round_metrics_storage"] = "history_rounds"
        database.execute(
            """
            UPDATE history_windows SET
                source=?, session_id=?, title=?, created_at=?, updated_at=?,
                rounds=?, data_json=?
            WHERE window_kind=? AND window_name=?
            """,
            (
                str(rendered.get("source") or ""),
                str(rendered.get("session_id") or ""),
                str(rendered.get("title") or ""),
                str(rendered.get("created_at") or rendered.get("updated_at") or ""),
                str(rendered.get("updated_at") or ""),
                max(0, int(rendered.get("rounds") or 0)),
                _json(stored_rendered),
                kind,
                name,
            ),
        )

        resolved_record = session_record
        if session_record_factory is not None:
            record_row = database.execute(
                "SELECT record_json FROM history_sessions WHERE source=? AND session_id=?",
                (
                    str(rendered.get("source") or ""),
                    str(rendered.get("session_id") or ""),
                ),
            ).fetchone()
            previous_record = (
                _object(record_row["record_json"], {})
                if record_row is not None
                else None
            )
            if not isinstance(previous_record, dict):
                previous_record = None
            resolved_record = session_record_factory(rendered, previous_record)
        _write_registry_state(
            database,
            record=copy.deepcopy(resolved_record) if resolved_record is not None else None,
            active_updates=None,
            updated_at=str(updated_at or rendered.get("updated_at") or ""),
        )
    return rendered


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
        return (
            database.execute(
                "SELECT 1 FROM history_context_summaries WHERE window_name=?",
                (name,),
            ).fetchone()
            is not None
        )


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
        message_rows = (
            database.execute(
                "SELECT message_json FROM history_messages "
                "WHERE window_name=? ORDER BY message_index",
                (name,),
            ).fetchall()
            if row is not None and kind == "archive"
            else []
        )
        round_rows = (
            database.execute(
                """
                SELECT round_number, think_json, tool_json, items_json, metric_json
                FROM history_rounds WHERE window_kind=? AND window_name=?
                ORDER BY round_number
                """,
                (kind, name),
            ).fetchall()
            if row is not None
            else []
        )
    if row is None:
        return None
    text = _object(row["text_json"], {"schema_version": 1, "messages": []})
    if kind == "archive":
        text = {
            "schema_version": max(
                1, int(text.get("schema_version") or 1)
            )
            if isinstance(text, dict)
            else 1,
            "messages": [
                message
                for value in message_rows
                if isinstance((message := _object(value["message_json"], {})), dict)
            ],
        }
    data = _object(row["data_json"], {})
    think = _object(row["think_json"], {"schema_version": 1, "rounds": []})
    tool = _object(row["tool_json"], {"schema_version": 1, "rounds": []})
    items = _object(row["items_json"], {"schema_version": 2, "items": []})
    round_storage = bool(
        round_rows
        or (isinstance(data, dict) and data.get("round_metrics_storage") == "history_rounds")
        or (isinstance(think, dict) and think.get("storage") == "history_rounds")
    )
    if round_storage:
        think = {
            "schema_version": _partition_schema(think, 1),
            "rounds": [
                value
                for round_row in round_rows
                if round_row["think_json"]
                and isinstance((value := _object(round_row["think_json"], {})), dict)
            ],
        }
        tool = {
            "schema_version": _partition_schema(tool, 1),
            "rounds": [
                value
                for round_row in round_rows
                if round_row["tool_json"]
                and isinstance((value := _object(round_row["tool_json"], {})), dict)
            ],
        }
        restored_items: list[dict[str, Any]] = []
        metrics: list[dict[str, Any]] = []
        for round_row in round_rows:
            item_values = _object(round_row["items_json"], [])
            if isinstance(item_values, list):
                restored_items.extend(
                    value for value in item_values if isinstance(value, dict)
                )
            if round_row["metric_json"]:
                metric = _object(round_row["metric_json"], {})
                if isinstance(metric, dict):
                    metrics.append(metric)
        items = {
            "schema_version": _partition_schema(items, 2),
            "items": restored_items,
        }
        if isinstance(data, dict):
            data.pop("round_metrics_storage", None)
            data["round_metrics"] = metrics
    return {
        "data": data,
        "text": text,
        "think": think,
        "tool": tool,
        "items": items,
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
        database.execute(
            "DELETE FROM history_rounds WHERE window_kind=? AND window_name=?",
            (kind, name),
        )
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
        windows = database.execute(
            "SELECT window_kind, window_name FROM history_windows "
            "WHERE source=? AND session_id=?",
            (source, session_id),
        ).fetchall()
        database.executemany(
            "DELETE FROM history_rounds WHERE window_kind=? AND window_name=?",
            [(row["window_kind"], row["window_name"]) for row in windows],
        )
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
        windows = database.execute(
            "SELECT window_kind, window_name FROM history_windows WHERE source=?",
            (source,),
        ).fetchall()
        database.executemany(
            "DELETE FROM history_rounds WHERE window_kind=? AND window_name=?",
            [(row["window_kind"], row["window_name"]) for row in windows],
        )
        database.execute(
            "DELETE FROM history_context_summaries WHERE source=?", (source,)
        )
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


def claim_registry_record(
    root: Path,
    user: str,
    *,
    status_column: str,
    statuses: Iterable[str],
    predicate: Callable[[dict[str, Any]], bool],
    mutator: Callable[[dict[str, Any]], dict[str, Any]],
    updated_at: str,
) -> dict[str, Any] | None:
    """Atomically select and mutate one background-job registry row.

    Candidate filtering stays on the indexed status column, while the less
    frequently queried lease metadata remains in ``record_json``.  This keeps
    idle workers away from complete registry/window reconciliation.
    """

    if status_column not in {"memory_status", "summary_status"}:
        raise ValueError(f"不支持的历史任务状态列：{status_column}")
    normalized_statuses = sorted({str(value) for value in statuses if str(value)})
    if not normalized_statuses:
        return None
    placeholders = ",".join("?" for _ in normalized_statuses)
    sql = (
        f"SELECT record_json FROM history_sessions "
        f"WHERE lifecycle != 'deleted' AND {status_column} IN ({placeholders}) "
        "ORDER BY updated_at, source, session_id"
    )

    def eligible_records(database: sqlite3.Connection) -> Iterator[dict[str, Any]]:
        for row in database.execute(sql, normalized_statuses).fetchall():
            record = _object(row["record_json"], {})
            if isinstance(record, dict) and predicate(record):
                yield record

    # The overwhelmingly common scheduler pass has no eligible job.  Keep that
    # path on a query-only connection and acquire SQLite's write lease only
    # after a candidate exists.  The predicate is evaluated again under the
    # write transaction so concurrent workers remain exclusive.
    with connection(root, user) as database:
        if next(eligible_records(database), None) is None:
            return None
    with connection(root, user, write=True) as database:
        for record in eligible_records(database):
            rendered = mutator(copy.deepcopy(record))
            if not isinstance(rendered, dict):
                raise ValueError("历史任务领取 mutator 必须返回对象")
            _upsert_session_row(database, rendered)
            database.execute(
                """
                INSERT INTO history_meta(key, value) VALUES('registry_revision', '1')
                ON CONFLICT(key) DO UPDATE SET
                    value=CAST(COALESCE(NULLIF(history_meta.value, ''), '0') AS INTEGER) + 1
                """
            )
            database.execute(
                "INSERT OR REPLACE INTO history_meta(key, value) "
                "VALUES('registry_updated_at', ?)",
                (str(updated_at or rendered.get("updated_at") or ""),),
            )
            return copy.deepcopy(rendered)
    return None


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
        cursor_source = ""
        parsed_cursor = _object(before_updated_at, None)
        if (
            isinstance(parsed_cursor, list)
            and len(parsed_cursor) in {2, 3}
            and all(isinstance(item, str) for item in parsed_cursor)
        ):
            cursor_updated_at, cursor_session_id = parsed_cursor[:2]
            if len(parsed_cursor) == 3:
                cursor_source = parsed_cursor[2]
        if cursor_session_id and cursor_source:
            clauses.append(
                """(
                    updated_at < ? OR (
                        updated_at = ? AND (
                            session_id < ? OR (session_id = ? AND source < ?)
                        )
                    )
                )"""
            )
            params.extend(
                (
                    cursor_updated_at,
                    cursor_updated_at,
                    cursor_session_id,
                    cursor_session_id,
                    cursor_source,
                )
            )
        elif cursor_session_id:
            clauses.append("(updated_at < ? OR (updated_at = ? AND session_id < ?))")
            params.extend((cursor_updated_at, cursor_updated_at, cursor_session_id))
        else:
            # Backward compatibility for clients that used a timestamp cursor.
            clauses.append("updated_at < ?")
            params.append(cursor_updated_at)
    requested = None if limit is None else max(1, int(limit))
    sql = "SELECT record_json FROM history_sessions WHERE " + " AND ".join(clauses)
    sql += " ORDER BY updated_at DESC, session_id DESC, source DESC"
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
    source = str(record.get("source") or "")
    return (
        _json([updated_at, session_id, source])
        if updated_at and session_id and source
        else _json([updated_at, session_id])
        if updated_at and session_id
        else ""
    )


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
