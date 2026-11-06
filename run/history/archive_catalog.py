"""Session/date catalog over CardStream metadata, without loading RAW bodies."""
from run.history.archive_query_common import (
    DATE_BASIS, TIMEZONE, VISIBLE, archive_database, metadata_entry, serialized_size,
)


def query_archives(root, user, *, action, date="", since="", until="", source="",
                   session_id="", limit=50, offset=0, page_char_limit=80000):
    clauses, args = [VISIBLE], []
    for column, value in (("source", source), ("session_id", session_id)):
        if value:
            clauses.append(f"s.{column}=?")
            args.append(value)
    day = "date(s.created_at,'+8 hours')"
    for op, value in (("=", date), (">=", since), ("<=", until)):
        if value:
            clauses.append(f"{day}{op}?")
            args.append(value)
    where = " AND ".join(clauses)
    key = "dates" if action == "dates" else "archives"
    result = {"action": action, key: [], "total": 0, "offset": offset, "next_offset": None,
              "has_more": False, "page_char_limit": page_char_limit, "page_limited_by_chars": False,
              "date_basis": DATE_BASIS, "timezone": TIMEZONE,
              "filters": {"source": source or None, "session_id": session_id or None,
                          "date": date or None, "since": since or None, "until": until or None}}
    with archive_database(root, user) as db:
        if db is None:
            return result
        if action == "dates":
            base = f"SELECT {day} AS date,count(*) AS archive_count FROM card_sessions s WHERE {where} GROUP BY {day}"
            result["total"] = db.execute(f"SELECT count(*) FROM ({base})", args).fetchone()[0]
            entries = [dict(row) for row in db.execute(base + " ORDER BY date DESC LIMIT ? OFFSET ?",
                                                      (*args, min(100, limit), offset))]
        else:
            result["total"] = db.execute(f"SELECT count(*) FROM card_sessions s WHERE {where}", args).fetchone()[0]
            entries = [metadata_entry(root, user, row) for row in db.execute(
                f"SELECT s.* FROM card_sessions s WHERE {where} ORDER BY s.updated_at DESC,s.session_key DESC "
                "LIMIT ? OFFSET ?", (*args, min(100, limit), offset))]
        for entry in entries:
            candidate = {**result, key: [*result[key], entry]}
            if serialized_size(candidate) + 128 > page_char_limit:
                if not result[key]:
                    raise ValueError("单项元数据超过 page_char_limit，请增加预算")
                result["page_limited_by_chars"] = True
                break
            result[key].append(entry)
    end = offset + len(result[key])
    result.update(has_more=end < result["total"], next_offset=end if end < result["total"] else None)
    return result
