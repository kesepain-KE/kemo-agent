"""Logical session registry persistence and paginated queries."""

from __future__ import annotations

# lifecycle='deleted' remains a supported legacy record value. Public deletion
# now hard-deletes rows; read-side filters still protect imported/old records.

import copy
from pathlib import Path
import sqlite3
from typing import Any, Callable, Iterable, Iterator

from run.history.session_listing import query_session_records, session_page_cursor

from run.history.store_core import (
    _json,
    _object,
    _session_generation,
    _session_is_deleted,
    connection,
)


class RegistryConflictError(RuntimeError):
    """A read/modify/write snapshot no longer owns the row it changes."""


class RegistrySnapshot(dict):
    def __init__(self, values=()):
        super().__init__(values)
        self.baseline = copy.deepcopy(dict(self))


def read_registry(
    root: Path, user: str
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, str]]]:
    with connection(root, user) as database:
        database.execute("BEGIN")
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
    return RegistrySnapshot(sessions), RegistrySnapshot(active)


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
        raise RegistryConflictError("历史会话代际冲突，拒绝陈旧写入")
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


def mutate_registry_record(
    root: Path,
    user: str,
    source: str,
    session_id: str,
    mutator: Callable[[dict[str, Any]], dict[str, Any] | None],
    *,
    updated_at: str,
    clear_active: bool = False,
) -> dict[str, Any] | None:
    """Change one existing session atomically, without reading the whole registry."""

    with connection(root, user, write=True) as database:
        row = database.execute(
            "SELECT record_json FROM history_sessions WHERE source=? AND session_id=?",
            (source, session_id),
        ).fetchone()
        if row is None or _session_is_deleted(database, source=source, session_id=session_id):
            return None
        previous = _object(row["record_json"], {})
        rendered = mutator(copy.deepcopy(previous))
        if rendered is None:
            return copy.deepcopy(previous)
        if (rendered.get("source"), rendered.get("session_id")) != (source, session_id):
            raise ValueError("历史会话更新不能改变 source/session_id")
        _upsert_session_row(database, rendered)
        if clear_active:
            database.execute(
                "DELETE FROM history_active_sessions WHERE source=? AND session_id=?",
                (source, session_id),
            )
        database.execute(
            "INSERT INTO history_meta(key, value) VALUES('registry_revision', '1') "
            "ON CONFLICT(key) DO UPDATE SET "
            "value=CAST(COALESCE(NULLIF(history_meta.value, ''), '0') AS INTEGER) + 1"
        )
        database.execute(
            "INSERT OR REPLACE INTO history_meta(key, value) VALUES('registry_updated_at', ?)",
            (updated_at,),
        )
        return copy.deepcopy(rendered)


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
    updated_at: str = "",
) -> int:
    # Snapshots carry their original rows. Only changed keys are written and
    # deletions are explicit (a key removed from that snapshot), never a diff
    # against rows added by other connections after the read.
    before = getattr(sessions, "baseline", {})
    active_before = getattr(active, "baseline", {})
    with connection(root, user, write=True) as database:
        for key in set(before) | set(sessions):
            old, record = before.get(key), sessions.get(key)
            if old == record:
                continue
            identity = record if isinstance(record, dict) else old
            if not isinstance(identity, dict):
                continue
            source, session_id = str(identity.get("source") or ""), str(identity.get("session_id") or "")
            if not source or not session_id:
                raise ValueError("历史会话记录缺少身份")
            current = database.execute("SELECT record_json FROM history_sessions WHERE source=? AND session_id=?",
                                       (source, session_id)).fetchone()
            actual = _object(current["record_json"], {}) if current is not None else None
            if isinstance(sessions, RegistrySnapshot) and actual != old:
                raise RegistryConflictError(f"历史会话并发写入冲突：{source}/{session_id}")
            if record is None:
                database.execute("DELETE FROM history_sessions WHERE source=? AND session_id=?", (source, session_id))
                database.execute("DELETE FROM history_active_sessions WHERE source=? AND session_id=?", (source, session_id))
            elif _session_is_deleted(database, source=source, session_id=session_id):
                raise RegistryConflictError("历史会话已删除，拒绝陈旧快照")
            else:
                _upsert_session_row(database, record)
        for key in set(active_before) | set(active):
            old, binding = active_before.get(key), active.get(key)
            if old == binding:
                continue
            current = database.execute("SELECT source, session_id FROM history_active_sessions WHERE active_key=?", (key,)).fetchone()
            actual = dict(current) if current is not None else None
            if isinstance(active, RegistrySnapshot) and actual != old:
                raise RegistryConflictError("活跃会话绑定并发写入冲突")
            if binding is None:
                database.execute("DELETE FROM history_active_sessions WHERE active_key=?", (key,))
            elif isinstance(binding, dict):
                source, session_id = str(binding.get("source") or ""), str(binding.get("session_id") or "")
                if not _session_is_deleted(database, source=source, session_id=session_id):
                    database.execute("INSERT INTO history_active_sessions VALUES(?,?,?) ON CONFLICT(active_key) DO UPDATE SET source=excluded.source,session_id=excluded.session_id",
                                     (key, source, session_id))
        database.execute("INSERT INTO history_meta(key,value) VALUES('registry_revision','1') ON CONFLICT(key) DO UPDATE SET value=CAST(history_meta.value AS INTEGER)+1")
        database.execute("INSERT OR REPLACE INTO history_meta(key,value) VALUES('registry_updated_at',?)", (updated_at,))
        actual_revision = int(database.execute("SELECT value FROM history_meta WHERE key='registry_revision'").fetchone()[0])
    for snapshot in (sessions, active):
        if isinstance(snapshot, RegistrySnapshot):
            snapshot.baseline = copy.deepcopy(dict(snapshot))
    return actual_revision


def message_windows(
    root: Path,
    user: str,
    *,
    source: str = "",
    session_id: str = "",
) -> list[dict[str, Any]]:
    """Return committed archive messages grouped by window for rich search.

    The structured ``history_messages.content_text`` column is authoritative
    for searchable user-visible text.  ``message_json`` may contain a
    multimodal content array, so callers must not have to reimplement text
    extraction or lose those messages.  Optional identity filters keep plugin
    searches from loading unrelated sessions.
    """

    clauses = [
        "window.window_kind='archive'",
        "NOT EXISTS ("
        "SELECT 1 FROM history_deleted_sessions AS deleted "
        "WHERE deleted.source=window.source "
        "AND deleted.session_id=window.session_id)",
    ]
    params: list[Any] = []
    if source:
        clauses.append("window.source=?")
        params.append(source)
    if session_id:
        clauses.append("window.session_id=?")
        params.append(session_id)
    where = " AND ".join(clauses)

    with connection(root, user) as database:
        windows = database.execute(
            f"""
            SELECT window.window_name, window.source, window.session_id,
                   window.data_json
            FROM history_windows AS window
            WHERE {where}
            ORDER BY window.updated_at DESC, window.window_name DESC
            """,
            params,
        ).fetchall()
        messages = database.execute(
            f"""
            SELECT message.window_name, message.message_index, message.role,
                   message.content_text, message.message_json
            FROM history_messages AS message
            JOIN history_windows AS window
              ON window.window_kind='archive'
             AND window.window_name=message.window_name
            WHERE {where}
            ORDER BY message.window_name, message.message_index
            """,
            params,
        ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in messages:
        message = _object(row["message_json"], {})
        if isinstance(message, dict):
            message = dict(message)
            message["role"] = str(row["role"])
            message["content"] = str(row["content_text"])
            message["_history_message_index"] = int(row["message_index"])
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
