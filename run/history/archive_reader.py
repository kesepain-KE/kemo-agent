"""Bounded, lossless visible-message reading for one committed physical archive."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from run.history.archive_query_common import (
    DATE_BASIS, FROM_ARCHIVES, METADATA, TIMEZONE, VISIBLE,
    archive_database, archive_identity, decode_token, encode_token,
    metadata_entry, serialized_size, user_scope,
)

_MESSAGES = """window_name=? AND source=? AND session_id=?
    AND role IN ('user', 'assistant')"""


def read_archive(
    root: Path, user: str, *, archive_id: str, cursor: str,
    role: str, limit: int, page_char_limit: int,
) -> dict[str, Any]:
    identity = archive_identity(root, user, archive_id)
    with archive_database(root, user) as database:
        row = None if database is None else database.execute(
            f"""SELECT {METADATA} {FROM_ARCHIVES} WHERE {VISIBLE}
                AND w.window_name=? AND w.source=? AND w.session_id=?""", identity,
        ).fetchone()
        if database is None or row is None:
            raise ValueError("归档不存在、已删除或尚未提交；请重新查询归档目录")
        where, params = _MESSAGES, list(identity)
        if role != "any":
            where += " AND role=?"
            params.append(role)
        stats = database.execute(
            f"""SELECT COUNT(*), MAX(message_index),
                COALESCE(SUM(length(CAST(content_text AS BLOB))), 0)
                FROM history_messages WHERE {where}""", params,
        ).fetchone()
        revision = hashlib.sha256(json.dumps(
            [list(row), list(stats)], ensure_ascii=False
        ).encode("utf-8")).hexdigest()
        scope = [user_scope(root, user), archive_id, revision, role]
        index, byte_offset = 0, 0
        if cursor:
            parsed = decode_token(cursor, "archive-read-v1")
            if (len(parsed) != 7 or parsed[1:5] != scope
                    or any(isinstance(n, bool) or not isinstance(n, int) or n < 0
                           for n in parsed[5:])):
                raise ValueError("cursor 不属于此归档/用户/角色，或归档已更新；请从头读取")
            index, byte_offset = parsed[5:]
            if byte_offset:
                position = database.execute(
                    f"""SELECT length(CAST(content_text AS BLOB))
                        FROM history_messages WHERE {where} AND message_index=?""",
                    [*params, index],
                ).fetchone()
                if position is None or byte_offset >= position[0]:
                    raise ValueError("cursor 的正文位置无效")
            elif database.execute(
                f"SELECT 1 FROM history_messages WHERE {where} AND message_index>=? LIMIT 1",
                [*params, index],
            ).fetchone() is None:
                raise ValueError("cursor 已越过归档末尾；请重新查询")
        result: dict[str, Any] = {
            "action": "read", "archive": metadata_entry(root, user, row),
            "messages": [], "total_messages": stats[0],
            "has_more": False, "next_cursor": None, "role": role,
            "page_char_limit": page_char_limit, "page_limited_by_chars": False,
            "offset_unit": "utf8_bytes", "date_basis": DATE_BASIS, "timezone": TIMEZONE,
            "message_time_note": "当前历史消息表没有独立可信的逐消息时间列，本次不返回发言时间",
        }
        messages: list[dict[str, Any]] = []
        used = 0
        while len(messages) < limit:
            # Fetch one bounded byte slice; never select message_json or a full body.
            message = database.execute(
                f"""SELECT message_index, round_number, role,
                    length(CAST(content_text AS BLOB)) AS content_bytes,
                    substr(CAST(content_text AS BLOB), ?, ?) AS chunk
                    FROM history_messages WHERE {where} AND message_index>=?
                    ORDER BY message_index LIMIT 1""",
                [byte_offset + 1, page_char_limit * 4 + 4, *params, index],
            ).fetchone()
            if message is None:
                if byte_offset:
                    raise ValueError("cursor 的消息位置无效")
                break
            message_index, total_bytes = message["message_index"], message["content_bytes"]
            if byte_offset and (message_index != index or byte_offset >= total_bytes):
                raise ValueError("cursor 的正文偏移无效")
            raw = bytes(message["chunk"])
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                if exc.reason != "unexpected end of data" or exc.end != len(raw):
                    raise ValueError("cursor 不在 UTF-8 字符边界") from None
                content = raw[:exc.start].decode("utf-8")
            entry = {
                "message_index": message_index, "round_number": message["round_number"],
                "role": message["role"], "content": "",
                "content_offset": byte_offset, "next_content_offset": byte_offset,
                "total_content_bytes": total_bytes, "message_complete": False,
            }

            def candidate(count: int) -> dict[str, Any]:
                text = content[:count]
                end = byte_offset + len(text.encode("utf-8"))
                return {**entry, "content": text, "next_content_offset": end,
                        "message_complete": end == total_bytes}

            # JSON escape expansion (quotes/newlines/NUL) counts toward the budget too.
            low, high = 0, len(content)
            while low < high:
                middle = (low + high + 1) // 2
                if serialized_size(candidate(middle)) <= page_char_limit - used:
                    low = middle
                else:
                    high = middle - 1
            selected = candidate(low)
            if ((content and low == 0)
                    or serialized_size(selected) > page_char_limit - used):
                if not messages:
                    raise ValueError("消息元数据超过 page_char_limit，请增大字符预算")
                result["page_limited_by_chars"] = True
                break
            messages.append(selected)
            used += serialized_size(selected)
            if not selected["message_complete"]:
                index, byte_offset = message_index, selected["next_content_offset"]
                result["page_limited_by_chars"] = True
                break
            index, byte_offset = message_index + 1, 0
        has_more = byte_offset > 0 or database.execute(
            f"SELECT 1 FROM history_messages WHERE {where} AND message_index>=? LIMIT 1",
            [*params, index],
        ).fetchone() is not None
        result["messages"] = messages
        result["has_more"] = has_more
        if has_more:
            result["next_cursor"] = encode_token(["archive-read-v1", *scope, index, byte_offset])
        return result
