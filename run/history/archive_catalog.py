"""SQL-side physical archive/date directory without loading message bodies."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from run.history.archive_query_common import (
    DATE_BASIS, FROM_ARCHIVES, METADATA, TIMEZONE, VISIBLE,
    archive_database, metadata_entry, serialized_size,
)


def query_archives(
    root: Path, user: str, *, action: str, date: str, since: str, until: str,
    source: str, session_id: str, limit: int, offset: int, page_char_limit: int,
) -> dict[str, Any]:
    clauses, params = [VISIBLE], []
    for column, value in (("source", source), ("session_id", session_id)):
        if value:
            clauses.append(f"w.{column}=?")
            params.append(value)
    day = "archive_day(w.updated_at, w.created_at, w.window_name)"
    for operator, value in (("=", date), (">=", since), ("<=", until)):
        if value:
            clauses.append(f"{day}{operator}?")
            params.append(value)
    where = " AND ".join(clauses)
    is_dates = action == "dates"
    key = "dates" if is_dates else "archives"
    result: dict[str, Any] = {
        "action": action, key: [], "total": 0, "offset": offset,
        "next_offset": None, "has_more": False, "page_char_limit": page_char_limit,
        "page_limited_by_chars": False, "date_basis": DATE_BASIS, "timezone": TIMEZONE,
        "filters": {"source": source or None, "session_id": session_id or None,
                    "date": date or None, "since": since or None, "until": until or None},
    }
    with archive_database(root, user) as database:
        if database is None:
            return result
        if is_dates:
            base = f"SELECT {day} AS date, COUNT(*) AS archive_count {FROM_ARCHIVES} WHERE {where} GROUP BY {day}"
            result["total"] = database.execute(f"SELECT COUNT(*) FROM ({base})", params).fetchone()[0]
            rows = database.execute(
                base + " ORDER BY date DESC LIMIT ? OFFSET ?", [*params, limit, offset]
            ).fetchall()
            entries = [dict(row) for row in rows]
        else:
            result["total"] = database.execute(
                f"SELECT COUNT(*) {FROM_ARCHIVES} WHERE {where}", params
            ).fetchone()[0]
            rows = database.execute(
                f"""SELECT {METADATA} {FROM_ARCHIVES} WHERE {where}
                    ORDER BY archive_time(w.updated_at, w.created_at, w.window_name) DESC,
                        w.window_name DESC LIMIT ? OFFSET ?""",
                [*params, limit, offset],
            ).fetchall()
            entries = [metadata_entry(root, user, row) for row in rows]
        selected: list[dict[str, Any]] = []
        used = 0
        for entry in entries:
            size = serialized_size(entry)
            if used + size > page_char_limit:
                if not selected:
                    raise ValueError("单项归档元数据超过 page_char_limit，请增大字符预算")
                result["page_limited_by_chars"] = True
                break
            selected.append(entry)
            used += size
        result[key] = selected
        result["has_more"] = offset + len(selected) < result["total"]
        result["next_offset"] = offset + len(selected) if result["has_more"] else None
    return result
