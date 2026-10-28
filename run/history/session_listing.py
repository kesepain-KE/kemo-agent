"""SQL-side history filters and stable pin-aware keyset pagination."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from run.history.session_marks import marked_record
from run.history.store_core import _json, _object, connection

PIN = "COALESCE(m.pinned_at, '')"


def query_session_records(
    root: Path, user: str, *, source: str | None = None, query: str = "",
    limit: int | None = None, before_updated_at: str = "", archive_date: str = "",
    favorites_only: bool = False,
) -> tuple[list[dict[str, Any]], bool]:
    clauses = ["s.lifecycle != 'deleted'"]
    params: list[Any] = []
    if source is not None:
        clauses.append("s.source=?")
        params.append(source)
    if favorites_only:
        clauses.append("m.favorite=1")
    if query.strip():
        clauses.append("""(
            lower(s.title) LIKE ? OR lower(s.summary) LIKE ? OR lower(s.session_id) LIKE ?
            OR EXISTS (SELECT 1 FROM history_messages AS message
                WHERE message.source=s.source AND message.session_id=s.session_id
                AND lower(message.content_text) LIKE ?))""")
        needle = f"%{query.strip().casefold()}%"
        params.extend([needle] * 4)
    if archive_date:
        clauses.append("date(s.updated_at, '+8 hours')=?")
        params.append(archive_date)
    if before_updated_at:
        parsed = _object(before_updated_at, None)
        if isinstance(parsed, list) and len(parsed) == 5 and parsed[0] == "marks-v1" and all(
            isinstance(item, str) for item in parsed
        ):
            clauses.append(f"({PIN}, s.updated_at, s.session_id, s.source) < (?, ?, ?, ?)")
            params.extend(parsed[1:])
        else:
            # Old clients used 2/3 string tuples or a bare timestamp.
            if isinstance(parsed, list) and len(parsed) in {2, 3} and all(
                isinstance(item, str) for item in parsed
            ):
                columns = ["s.updated_at", "s.session_id", "s.source"][:len(parsed)]
                clauses.append(f"({', '.join(columns)}) < ({', '.join('?' for _ in parsed)})")
                params.extend(parsed)
            else:
                clauses.append("s.updated_at < ?")
                params.append(before_updated_at)
    requested = None if limit is None else max(1, int(limit))
    sql = f"""SELECT s.record_json, {PIN} AS pinned_at, COALESCE(m.favorite, 0) AS favorite
        FROM history_sessions AS s LEFT JOIN history_session_marks AS m USING(source, session_id)
        WHERE {' AND '.join(clauses)}
        ORDER BY {PIN} DESC, s.updated_at DESC, s.session_id DESC, s.source DESC"""
    if requested is not None:
        sql += " LIMIT ?"
        params.append(requested + 1)
    with connection(root, user) as database:
        rows = database.execute(sql, params).fetchall()
    has_more = requested is not None and len(rows) > requested
    selected = rows[:requested] if requested is not None else rows
    return [marked_record(row) for row in selected], has_more


def session_page_cursor(record: dict[str, Any]) -> str:
    updated_at = str(record.get("updated_at") or "")
    session_id = str(record.get("session_id") or "")
    source = str(record.get("source") or "")
    if not updated_at or not session_id:
        return ""
    return _json(["marks-v1", str(record.get("pinned_at") or ""), updated_at, session_id, source])
