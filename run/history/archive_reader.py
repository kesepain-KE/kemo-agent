"""Lossless RAW paging and bounded text/tool previews around text cards."""
import codecs
import json
from contextlib import closing
from itertools import zip_longest

from run.cards import session_key
from run.history.archive_query_common import (
    CONTENT, ROLE, TEXT_CARDS, DATE_BASIS, TIMEZONE, VISIBLE, archive_database, archive_identity,
    decode_token, encode_token, metadata_entry, serialized_size, user_scope,
)
from run.tools import MAX_TOOL_RESULT_CHARS


_TOOL_PROBE_TOO_LARGE = "本次结果超过十万字符上限，请降低 before/after 或设置正数 max_tool_chars"


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
                             f"coalesce(substr(CAST({CONTENT} AS BLOB),?,?), x'') AS chunk FROM context_cards c "
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


def _preview_message(row, room):
    content = codecs.getincrementaldecoder("utf-8")().decode(row["chunk"], final=False)
    entry = {name: row[name] for name in ("card_id", "integration_id", "seq", "raw_kind", "raw_level", "role")}
    entry.update(created_at=row["card_created_at"], content=content, partial=len(content.encode()) < row["size"])
    if serialized_size(entry) <= room:
        return entry

    entry["partial"] = True
    lo, hi = 0, len(content) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        entry["content"] = content[:mid]
        if serialized_size(entry) <= room:
            lo = mid
        else:
            hi = mid - 1
    if lo:
        return {**entry, "content": content[:lo]}
    return None


def _tool_preview_rows(db, key, start, end, cap):
    # substr and length below operate on TEXT (characters); raw_card_bytes alone counts BLOB bytes.
    # JSON spelling preserves scalar types and escapes NUL before SQLite's TEXT slicing.
    yield from db.execute("""
        WITH cards AS (
            SELECT c.card_id,c.seq,c.raw_kind,c.tool_call_id,c.raw_payload,
                   CASE WHEN c.raw_kind='tool_call' THEN '$.arguments'
                        WHEN json_type(c.raw_payload,'$.result.result') IS NOT NULL THEN '$.result.result'
                        ELSE '$.result' END AS preview_path
            FROM context_cards c
            WHERE c.session_key=? AND c.seq BETWEEN ? AND ? AND c.raw_kind IN ('tool_call','tool_result')
        ), fields AS (
            SELECT cards.*, json_type(raw_payload,preview_path) AS value_type,
                   json_extract(raw_payload,preview_path) AS value
            FROM cards
        ), previews AS (
            SELECT card_id,seq,raw_kind,tool_call_id,
                   json_extract(raw_payload,CASE raw_kind WHEN 'tool_call' THEN '$.name'
                                                          ELSE '$.original_call.name' END) AS tool_name,
                   CASE WHEN raw_kind='tool_result' THEN json_type(raw_payload,'$.result.ok') END AS ok_type,
                   CASE WHEN raw_kind='tool_result'
                        THEN json_extract(raw_payload,'$.execution.status') END AS execution_status,
                   CASE WHEN raw_kind='tool_result'
                        THEN json_type(raw_payload,'$.execution.execution_live') END AS live_type,
                   CASE WHEN raw_kind='tool_result' THEN length(CAST(raw_payload AS BLOB)) END AS raw_card_bytes,
                   CASE WHEN value_type IN ('object','array') THEN value
                        WHEN raw_kind='tool_result' AND value_type='text' AND instr(value,char(0))=0 THEN value
                        ELSE coalesce(raw_payload -> preview_path,'') END AS preview_text
            FROM fields
        )
        SELECT card_id,seq,raw_kind,tool_call_id,tool_name,ok_type,execution_status,live_type,raw_card_bytes,
               substr(preview_text,1,?) AS preview, length(preview_text)>? AS truncated
        FROM previews ORDER BY seq
    """, (key, start, end, cap, cap))


def _tool_preview(row):
    entry = {name: row[name] for name in ("card_id", "seq", "tool_name", "tool_call_id")}
    entry["kind"] = row["raw_kind"]
    if row["raw_kind"] == "tool_call":
        entry.update(arguments_preview=row["preview"], arguments_truncated=bool(row["truncated"]))
    else:
        boolean = {"true": True, "false": False}
        entry.update(ok=boolean.get(row["ok_type"]), execution_status=row["execution_status"],
                     execution_live=boolean.get(row["live_type"]), raw_card_bytes=row["raw_card_bytes"],
                     result_preview=row["preview"], result_truncated=bool(row["truncated"]))
    return entry


def _append_tool_previews(messages, rows, used, page_char_limit, full):
    chars = 0
    for row in rows:
        if full and row["truncated"]:
            raise ValueError(_TOOL_PROBE_TOO_LARGE)
        entry = _tool_preview(row)
        if full:
            # Stop when the tool entries alone cannot fit the framework's JSON character limit.
            chars += len(json.dumps(entry, ensure_ascii=False)) + 2
            if chars > MAX_TOOL_RESULT_CHARS:
                raise ValueError(_TOOL_PROBE_TOO_LARGE)
        size = serialized_size(entry) + 1
        if not full and used + size > page_char_limit:
            return used, True
        messages.append(entry)
        used += size
    return used, False


def read_archive_anchor(root, user, *, anchor_card_id, archive_id="", before=5, after=5, page_char_limit=80000,
                        include_tools=False, max_tool_chars=2000):
    """Read a bounded preview around a visible RAW text card in one snapshot."""
    for field, value in (("before", before), ("after", after)):
        if type(value) is not int or not 0 <= value <= 20:
            raise ValueError(f"{field} 必须是 0～20 的整数")
    if type(page_char_limit) is not int or not 1000 <= page_char_limit <= 90_000:
        raise ValueError("page_char_limit 必须是 1000～90000 的整数")
    if type(include_tools) is not bool:
        raise ValueError("include_tools 必须是布尔值")
    if type(max_tool_chars) is not int or not (max_tool_chars == 0 or 200 <= max_tool_chars <= 20_000):
        raise ValueError("max_tool_chars 必须是 0 或 200～20000 的整数")

    body = f"CAST({CONTENT} AS BLOB)"
    columns = ("c.card_id,c.integration_id,c.seq,c.raw_kind,c.raw_level,c.created_at AS card_created_at,"
               f"{ROLE} AS role,length({body}) AS size,coalesce(substr({body},1,?), x'') AS chunk")
    with archive_database(root, user) as db:
        if db is None:
            raise ValueError("锚点正文不存在或不可见")
        anchor = db.execute(
            f"SELECT s.*,{columns} FROM context_cards c JOIN card_sessions s USING(session_key) "
            f"WHERE c.card_id=? AND {VISIBLE} AND {TEXT_CARDS}", (page_char_limit, anchor_card_id),
        ).fetchone()
        if anchor is None:
            raise ValueError("锚点正文不存在或不可见")
        if archive_id and archive_identity(root, user, archive_id) != (
            anchor["source"], anchor["session_id"], anchor["generation"]
        ):
            raise ValueError("archive_id 与锚点不属于同一归档代际")
        archive = metadata_entry(root, user, anchor)
        where = f"FROM context_cards c WHERE c.session_key=? AND {TEXT_CARDS} AND c.seq"
        args = (page_char_limit, anchor["session_key"], anchor["seq"])
        left = db.execute(f"SELECT {columns} {where}<? ORDER BY c.seq DESC LIMIT ?", (*args, before)).fetchall()
        right = db.execute(f"SELECT {columns} {where}>? ORDER BY c.seq ASC LIMIT ?", (*args, after)).fetchall()
        if include_tools:
            start = left[-1]["seq"] if left else anchor["seq"]
            end = right[-1]["seq"] if right else anchor["seq"]
            cap = max_tool_chars or MAX_TOOL_RESULT_CHARS
            with closing(_tool_preview_rows(db, anchor["session_key"], start, end, cap)) as tools:
                return _anchor_preview(archive, anchor, left, right, page_char_limit, tools=tools,
                                       index_bound=anchor["seq"] - start, full=max_tool_chars == 0)
    return _anchor_preview(archive, anchor, left, right, page_char_limit)


def _anchor_preview(archive, anchor, left, right, page_char_limit, *, tools=None, index_bound=None, full=False):
    messages = []
    result = {"action": "read", "mode": "anchor", "archive": archive, "messages": messages,
              "anchor": {"card_id": anchor["card_id"], "seq": anchor["seq"],
                         "index": len(left) if index_bound is None else index_bound},
              "page_char_limit": page_char_limit, "page_limited_by_chars": False, "window_clipped": False}
    # The final index cannot get wider, and false is longer than true in JSON.
    used = serialized_size({"ok": True, **result})
    main = _preview_message(anchor, page_char_limit - used)
    if main is None:
        raise ValueError("page_char_limit 太小，无法容纳锚点正文")
    messages.append(main)
    used += serialized_size(main)

    tools_clipped = False
    if tools is not None and (full or not main["partial"]):
        used, tools_clipped = _append_tool_previews(messages, tools, used, page_char_limit, full)
    neighbors = 0
    if not main["partial"] and not tools_clipped and used < page_char_limit:
        for row in (row for pair in zip_longest(left, right) for row in pair if row is not None):
            entry = _preview_message(row, page_char_limit - used - 1)
            if entry is None:
                break
            messages.append(entry)
            used += serialized_size(entry) + 1
            neighbors += 1
            if entry["partial"]:
                break
    messages.sort(key=lambda item: item["seq"])
    result["anchor"]["index"] = sum(item["seq"] < anchor["seq"] for item in messages)
    result["window_clipped"] = tools_clipped or neighbors < len(left) + len(right)
    result["page_limited_by_chars"] = result["window_clipped"] or any(item.get("partial", False) for item in messages)
    if full:
        rendered = {"ok": True, **result}
        if len(json.dumps(rendered, ensure_ascii=False)) > MAX_TOOL_RESULT_CHARS:
            raise ValueError(_TOOL_PROBE_TOO_LARGE)
        if used > page_char_limit and serialized_size(rendered) > page_char_limit:
            raise ValueError("完整工具卡预览超过 page_char_limit，请提高页预算、降低 before/after 或设置正数 max_tool_chars")
    return result
