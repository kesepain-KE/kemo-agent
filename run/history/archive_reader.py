"""Lossless bounded reading of immutable RAW cards with snapshot-scoped cursors."""
import codecs
from run.cards import session_key
from run.history.archive_query_common import (
    CONTENT, ROLE, TEXT_CARDS, DATE_BASIS, TIMEZONE, archive_database, archive_identity,
    decode_token, encode_token, metadata_entry, serialized_size, user_scope,
)


def read_archive(root, user, *, archive_id, cursor="", role="any", limit=50, page_char_limit=80000):
    source, identity, generation = archive_identity(root, user, archive_id)
    key, scope = session_key(source, identity), user_scope(root, user)
    with archive_database(root, user) as db:
        if db is None:
            raise ValueError("CardStream 会话不存在或已经删除")
        session = db.execute(
            "SELECT * FROM card_sessions WHERE session_key=? AND generation=? AND lifecycle!='deleted'",
            (key, generation)).fetchone()
        if session is None:
            raise ValueError("CardStream 会话不存在或已经删除")
        through, next_seq, offset = session["last_seq"], 1, 0
        if cursor:
            parts = decode_token(cursor, "card-read-v1")
            if (len(parts) != 8 or parts[1:5] != [scope, archive_id, generation, role]
                    or any(type(value) is not int or value < 0 for value in parts[5:])):
                raise ValueError("读取游标不属于当前会话/用户/角色")
            through, next_seq, offset = parts[5:]
            if through > session["last_seq"] or next_seq < 1:
                raise ValueError("读取游标范围无效")
        where, args = f"c.session_key=? AND c.seq>=? AND c.seq<=? AND {TEXT_CARDS}", [key, next_seq, through]
        if role != "any":
            where += f" AND {ROLE}=?"
            args.append(role)
        total = db.execute(f"SELECT count(*) FROM context_cards c WHERE c.session_key=? AND c.seq<=? AND {TEXT_CARDS} "
                           + (f"AND {ROLE}=?" if role != "any" else ""),
                           (key, through, role) if role != "any" else (key, through)).fetchone()[0]
        result = {"action": "read", "archive": metadata_entry(root, user, session), "messages": [],
                  "total_messages": total, "has_more": False, "next_cursor": None, "role": role,
                  "through_seq": through, "page_char_limit": page_char_limit,
                  "page_limited_by_chars": False, "offset_unit": "utf8_bytes",
                  "date_basis": DATE_BASIS, "timezone": TIMEZONE}
        for _ in range(max(1, min(100, limit))):
            args[1] = next_seq
            room = page_char_limit - serialized_size(result) - 1024
            if room < 32:
                result["page_limited_by_chars"] = True
                break
            row = db.execute(f"SELECT c.card_id,c.integration_id,c.seq,c.raw_kind,c.raw_level,c.created_at,"
                             f"{ROLE} AS role,length(CAST({CONTENT} AS BLOB)) AS size,"
                             f"substr(CAST({CONTENT} AS BLOB),?,?) AS chunk FROM context_cards c "
                             f"WHERE {where} ORDER BY c.seq LIMIT 1", (offset + 1, room, *args)).fetchone()
            if row is None:
                break
            if offset and row["seq"] != next_seq or offset > row["size"]:
                raise ValueError("读取游标正文位置无效")
            try:
                content = codecs.getincrementaldecoder("utf-8")().decode(bytes(row["chunk"]), final=False)
            except UnicodeDecodeError:
                raise ValueError("读取游标不位于 UTF-8 字符边界") from None
            entry = {name: row[name] for name in ("card_id", "integration_id", "seq", "raw_kind",
                                                  "raw_level", "created_at", "role")}
            # Escape-heavy strings must also fit after JSON serialization.
            while content and serialized_size({**result, "messages": [*result["messages"],
                      {**entry, "content": content}]}) + 640 > page_char_limit:
                content = content[:max(1, len(content) // 2)] if len(content) > 1 else ""
            if not content and row["size"] > offset:
                if not result["messages"]:
                    raise ValueError("page_char_limit 太小，无法容纳单个 UTF-8 字符")
                result["page_limited_by_chars"] = True
                break
            consumed = len(content.encode())
            partial = offset + consumed < row["size"]
            entry.update(content=content, byte_offset=offset, partial=partial)
            result["messages"].append(entry)
            next_seq, offset = (row["seq"], offset + consumed) if partial else (row["seq"] + 1, 0)
            if partial:
                result["page_limited_by_chars"] = True
                break
        args[1] = next_seq
        more = offset > 0 or db.execute(f"SELECT 1 FROM context_cards c WHERE {where} LIMIT 1", args).fetchone() is not None
        result["has_more"] = more
        if more:
            result["next_cursor"] = encode_token(["card-read-v1", scope, archive_id, generation,
                                                  role, through, next_seq, offset])
        return result
