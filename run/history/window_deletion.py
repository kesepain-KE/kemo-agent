"""Lookup, rename, and deletion operations for history windows."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any

from run.config import user_dir
from run.history.store_core import (
    _json,
    _object,
    _remember_deleted_window,
    connection,
)

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
        row = database.execute(
            """
            SELECT source, session_id, data_json
            FROM history_windows WHERE window_kind=? AND window_name=?
            """,
            (kind, name),
        ).fetchone()
        if row is not None and kind != "runtime":
            stored_data = _object(row["data_json"], {})
            _remember_deleted_window(
                database,
                kind=kind,
                name=name,
                source=str(row["source"] or ""),
                session_id=str(row["session_id"] or ""),
                session_generation=(
                    str(stored_data.get("session_generation") or "")
                    if isinstance(stored_data, dict)
                    else ""
                ),
            )
        database.execute(
            "DELETE FROM history_rounds WHERE window_kind=? AND window_name=?",
            (kind, name),
        )
        if kind == "archive":
            database.execute(
                "DELETE FROM history_messages WHERE window_name=?", (name,)
            )
            database.execute(
                "DELETE FROM history_context_summaries WHERE window_name=?",
                (name,),
            )
        result = database.execute(
            "DELETE FROM history_windows WHERE window_kind=? AND window_name=?",
            (kind, name),
        )
    from run.history import runtime_cache

    runtime_cache.drop(window_path(root, user, name, kind="runtime"))
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
    from run.history import runtime_cache

    runtime_cache.drop_session(root, user, source, session_id)
    return len(rows)


def delete_session_windows(root: Path, user: str, source: str, session_id: str) -> int:
    with connection(root, user, write=True) as database:
        return _delete_session_windows(database, source, session_id)


def _delete_session_windows(database: sqlite3.Connection, source: str, session_id: str) -> int:
    """Shared transaction body for manual deletion and lifecycle cleanup."""
    if source == 'web':
        database.execute('DELETE FROM history_web_leases WHERE session_id=?', (session_id,))
    database.execute(
        """
        INSERT INTO history_deleted_sessions(source, session_id, deleted_at)
        VALUES(?, ?, datetime('now'))
        ON CONFLICT(source, session_id) DO UPDATE SET
            deleted_at=excluded.deleted_at
        """,
        (str(source), str(session_id)),
    )
    windows = database.execute(
        "SELECT window_kind, window_name, data_json FROM history_windows "
        "WHERE source=? AND session_id=?",
        (source, session_id),
    ).fetchall()
    for row in windows:
        stored_data = _object(row["data_json"], {})
        _remember_deleted_window(
            database,
            kind=str(row["window_kind"]),
            name=str(row["window_name"]),
            source=source,
            session_id=session_id,
            session_generation=(
                str(stored_data.get("session_generation") or "")
                if isinstance(stored_data, dict)
                else ""
            ),
        )
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
    from run.history import runtime_cache

    database_file = Path(
        str(database.execute("PRAGMA database_list").fetchone()["file"])
    ).resolve()
    runtime_cache.drop_session(
        database_file.parents[3], database_file.parent.parent.name, source, session_id
    )
    return result.rowcount


def delete_source_windows(root: Path, user: str, source: str) -> tuple[int, int]:
    with connection(root, user, write=True) as database:
        session_rows = database.execute(
            "SELECT DISTINCT session_id FROM history_windows WHERE source=?",
            (str(source),),
        ).fetchall()
        database.executemany(
            """
            INSERT INTO history_deleted_sessions(source, session_id, deleted_at)
            VALUES(?, ?, datetime('now'))
            ON CONFLICT(source, session_id) DO UPDATE SET
                deleted_at=excluded.deleted_at
            """,
            [(str(source), str(row["session_id"])) for row in session_rows],
        )
        session_count = int(
            database.execute(
                "SELECT COUNT(DISTINCT session_id) FROM history_windows WHERE source=?",
                (source,),
            ).fetchone()[0]
        )
        windows = database.execute(
            "SELECT window_kind, window_name, session_id, data_json "
            "FROM history_windows WHERE source=?",
            (source,),
        ).fetchall()
        for row in windows:
            stored_data = _object(row["data_json"], {})
            _remember_deleted_window(
                database,
                kind=str(row["window_kind"]),
                name=str(row["window_name"]),
                source=source,
                session_id=str(row["session_id"] or ""),
                session_generation=(
                    str(stored_data.get("session_generation") or "")
                    if isinstance(stored_data, dict)
                    else ""
                ),
            )
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
    from run.history import runtime_cache

    runtime_cache.drop_source(root, user, source)
    return session_count, result.rowcount
