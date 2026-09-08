"""Logical session registry persistence and paginated queries."""

from __future__ import annotations

import copy
from pathlib import Path
import sqlite3
from typing import Any, Callable, Iterable, Iterator

from run.history.store_core import (
    _json,
    _object,
    _session_generation,
    _session_is_deleted,
    connection,
)


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
    incoming_generation = str(record.get("session_generation") or "").strip()
    current_generation = _session_generation(
        database,
        source=source,
        session_id=session_id,
    )
    if (
        incoming_generation
        and current_generation
        and incoming_generation != current_generation
    ):
        return
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
    allow_deleted_reuse: bool = False,
) -> dict[str, Any]:
    """Atomically upsert one session and optional active bindings."""

    rendered = copy.deepcopy(record)
    with connection(root, user, write=True) as database:
        source = str(rendered.get("source") or "")
        session_id = str(rendered.get("session_id") or "")
        if source and session_id and _session_is_deleted(
            database, source=source, session_id=session_id
        ):
            if not allow_deleted_reuse:
                raise RuntimeError(f"历史会话已删除，拒绝重新写入：{source}/{session_id}")
            database.execute(
                "DELETE FROM history_deleted_sessions WHERE source=? AND session_id=?",
                (source, session_id),
            )
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
            if _session_is_deleted(
                database, source=source, session_id=session_id
            ):
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
            if _session_is_deleted(
                database, source=source, session_id=session_id
            ):
                # A stale index snapshot must not resurrect a session that was
                # deleted by another worker.
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
                if _session_is_deleted(
                    database, source=source, session_id=session_id
                ):
                    continue
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
