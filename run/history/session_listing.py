"""Pin-aware session keyset pagination over CardStream metadata and RAW."""
from __future__ import annotations

import json
from pathlib import Path

from run.cards import CardStore, json_text
from run.history.registry_store import session_record

PIN = "coalesce(json_extract(s.record_json,'$.pinned_at'),'')"


def query_session_records(root: Path, user: str, *, source: str | None = None,
                          query: str = "", limit: int | None = None,
                          before_updated_at: str = "", archive_date: str = "",
                          favorites_only: bool = False) -> tuple[list[dict], bool]:
    store = CardStore(root, user)
    if not store.path.is_file():
        return [], False
    clauses, args = ["s.lifecycle!='deleted'"], []
    if source is not None:
        clauses.append("s.source=?")
        args.append(source)
    if favorites_only:
        clauses.append("json_extract(s.record_json,'$.favorite')=1")
    if query.strip():
        clauses.append("(instr(lower(s.record_json),lower(?))>0 OR instr(lower(s.session_id),lower(?))>0 "
                       "OR EXISTS(SELECT 1 FROM context_cards c WHERE c.session_key=s.session_key "
                       "AND instr(lower(c.raw_payload),lower(?))>0))")
        args.extend([query.strip()] * 3)
    if archive_date:
        clauses.append("date(s.updated_at,'+8 hours')=?")
        args.append(archive_date)
    if before_updated_at:
        try:
            cursor = json.loads(before_updated_at)
        except ValueError:
            cursor = None
        if not (isinstance(cursor, list) and len(cursor) == 5 and cursor[0] == "cards-v1"
                and all(isinstance(value, str) for value in cursor)):
            raise ValueError("Invalid CardStream session cursor")
        clauses.append(f"({PIN},s.updated_at,s.session_id,s.source)<(?,?,?,?)")
        args.extend(cursor[1:])
    requested = None if limit is None else max(1, min(500, int(limit)))
    sql = (f"SELECT s.* FROM card_sessions s WHERE {' AND '.join(clauses)} "
           f"ORDER BY {PIN} DESC,s.updated_at DESC,s.session_id DESC,s.source DESC")
    if requested is not None:
        sql += " LIMIT ?"
        args.append(requested + 1)
    with store.db() as db:
        rows = db.execute(sql, args).fetchall()
        selected = rows if requested is None else rows[:requested]
        return [session_record(db, row) for row in selected], requested is not None and len(rows) > requested


def session_page_cursor(record: dict) -> str:
    return json_text(["cards-v1", str(record.get("pinned_at") or ""), record["updated_at"],
                      record["session_id"], record["source"]])
