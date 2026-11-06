"""Bounded RAW queries, scoped opaque identities and immutable seq snapshots."""
from __future__ import annotations

import base64
import hashlib
import json

from run.cards import CardStore, card_read_connection as archive_database, session_key

TIMEZONE = "Asia/Shanghai"
DATE_BASIS = "session_created_at"
VISIBLE = "s.lifecycle!='deleted' AND s.last_seq>0"
TEXT_CARDS = "c.raw_kind IN ('user_text','agent_text')"
CONTENT = "coalesce(json_extract(c.raw_payload,'$.text'),'')"
ROLE = "CASE c.raw_kind WHEN 'user_text' THEN 'user' WHEN 'agent_text' THEN 'assistant' ELSE 'tool' END"


def serialized_size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def encode_token(value):
    return base64.urlsafe_b64encode(json.dumps(value, ensure_ascii=False,
        separators=(",", ":")).encode()).decode("ascii").rstrip("=")


def decode_token(value, prefix):
    try:
        if not isinstance(value, str) or len(value) > 8192:
            raise ValueError
        parsed = json.loads(base64.b64decode(value + "=" * (-len(value) % 4),
                                            altchars=b"-_", validate=True))
        if not isinstance(parsed, list) or not parsed or parsed[0] != prefix:
            raise ValueError
        return parsed
    except (ValueError, UnicodeError):
        raise ValueError(f"{prefix} 标识或游标无效") from None


def user_scope(root, user):
    path = CardStore(root, user).path
    return hashlib.sha256(str(path).casefold().encode()).hexdigest()[:24]


def archive_identity(root, user, token):
    parts = decode_token(token, "card-archive-v1")
    if (len(parts) != 5 or parts[1] != user_scope(root, user)
            or not all(isinstance(value, str) and value for value in parts[2:])):
        raise ValueError("归档标识不属于当前用户或身份无效")
    return parts[2], parts[3], parts[4]


def metadata_entry(root, user, row):
    record = json.loads(row["record_json"])
    return {
        "archive_id": encode_token(["card-archive-v1", user_scope(root, user),
                                   row["source"], row["session_id"], row["generation"]]),
        "source": row["source"], "session_id": row["session_id"],
        "title": str(record.get("title") or "")[:200],
        "summary": str(record.get("summary") or "")[:500],
        "summary_truncated": len(str(record.get("summary") or "")) > 500,
        "created_at": row["created_at"], "updated_at": row["updated_at"],
        "last_seq": row["last_seq"], "engine": "cardstream",
    }


def raw_search_rows(root, user, *, source="", session_id="", since="", until=""):
    """Stream original conversation text without tool payloads."""
    clauses, args = [VISIBLE, TEXT_CARDS], []
    for column, value in (("source", source), ("session_id", session_id)):
        if value:
            clauses.append(f"s.{column}=?")
            args.append(value)
    if since:
        clauses.append("date(c.created_at,'+8 hours')>=?")
        args.append(since)
    if until:
        clauses.append("date(c.created_at,'+8 hours')<=?")
        args.append(until)
    with archive_database(root, user) as db:
        if db is None:
            return
        rows = db.execute(f"SELECT s.source,s.session_id,c.card_id,c.integration_id,c.seq,"
                          f"c.raw_kind,c.created_at,{ROLE} AS role,{CONTENT} AS content "
                          "FROM context_cards c JOIN card_sessions s USING(session_key) "
                          f"WHERE {' AND '.join(clauses)} ORDER BY c.created_at DESC,c.card_id DESC", args)
        for row in rows:
            yield dict(row)


def raw_text_context(root, user, *, source, session_id, seq, count, max_chars):
    """Read the nearest text messages, counting past intervening tool cards."""
    with archive_database(root, user) as db:
        if db is None:
            return []
        select = f"SELECT c.seq,c.raw_kind,substr({CONTENT},1,?) AS content "
        where = f"FROM context_cards c WHERE c.session_key=? AND {TEXT_CARDS} AND c.seq"
        key = session_key(source, session_id)
        before = db.execute(select + where + "<? ORDER BY c.seq DESC LIMIT ?",
                            (max_chars, key, seq, count)).fetchall()
        after = db.execute(select + where + ">=? ORDER BY c.seq LIMIT ?",
                           (max_chars, key, seq, count + 1)).fetchall()
        return [dict(row) for row in before[::-1] + after]
