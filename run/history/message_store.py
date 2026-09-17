"""Archive message row projection and synchronization."""

from __future__ import annotations

from typing import Any
import sqlite3

from run.history.store_core import _json, _object


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for block in value:
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if isinstance(text, str) and text:
            parts.append(text)
    return "\n".join(parts)


def _round_number(message: dict[str, Any], fallback: int) -> int:
    metadata = message.get("metadata")
    if isinstance(metadata, dict):
        try:
            return max(0, int(metadata.get("round") or 0))
        except (TypeError, ValueError):
            pass
    return fallback


_MESSAGE_INSERT_SQL = """
    INSERT INTO history_messages(
        window_name, message_index, source, session_id,
        round_number, role, content_text, message_json,
        created_at, updated_at
    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _message_rows(
    window_name: str,
    source: str,
    session_id: str,
    created_at: str,
    updated_at: str,
    messages: list[Any],
) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    fallback_round = 0
    for index, raw in enumerate(messages):
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        if role == "user":
            fallback_round += 1
        rows.append(
            (
                window_name,
                index,
                source,
                session_id,
                _round_number(raw, fallback_round),
                role,
                _content_text(raw.get("content")),
                _json(raw),
                created_at,
                updated_at,
            )
        )
    return rows


def _message_signature(raw: Any) -> str:
    parsed = _object(str(raw or ""), None)
    if not isinstance(parsed, dict):
        return str(raw or "")
    rendered = dict(parsed)
    metadata = rendered.get("metadata")
    if isinstance(metadata, dict) and "round" in metadata:
        rendered["metadata"] = {**metadata, "round": 0}
    return _json(rendered)


def _shifted_message_overlap(
    existing: list[sqlite3.Row], candidates: list[tuple[Any, ...]]
) -> int:
    maximum = min(len(existing), len(candidates))
    for overlap in range(maximum, 0, -1):
        if all(
            str(row["source"]) == str(candidate[2])
            and str(row["session_id"]) == str(candidate[3])
            and _message_signature(row["message_json"])
            == _message_signature(candidate[7])
            for row, candidate in zip(existing[-overlap:], candidates[:overlap])
        ):
            return overlap
    return 0


def _rewrite_shifted_messages(
    database: sqlite3.Connection,
    *,
    window_name: str,
    existing: list[sqlite3.Row],
    candidates: list[tuple[Any, ...]],
) -> bool:
    overlap = _shifted_message_overlap(existing, candidates)
    if not candidates or overlap * 5 < len(candidates) * 4:
        return False
    retained = list(zip(existing[-overlap:], candidates[:overlap]))
    for index, (row, _candidate) in enumerate(retained, start=1):
        database.execute(
            "UPDATE history_messages SET message_index=? "
            "WHERE window_name=? AND message_index=?",
            (-index, window_name, int(row["message_index"])),
        )
    database.execute(
        "DELETE FROM history_messages WHERE window_name=? AND message_index>=0",
        (window_name,),
    )
    for index, (_row, candidate) in enumerate(retained, start=1):
        database.execute(
            """
            UPDATE history_messages SET message_index=?, source=?, session_id=?,
                round_number=?, role=?, content_text=?, message_json=?,
                created_at=?, updated_at=?
            WHERE window_name=? AND message_index=?
            """,
            (
                int(candidate[1]),
                candidate[2],
                candidate[3],
                candidate[4],
                candidate[5],
                candidate[6],
                candidate[7],
                candidate[8],
                candidate[9],
                window_name,
                -index,
            ),
        )
    if overlap < len(candidates):
        database.executemany(_MESSAGE_INSERT_SQL, candidates[overlap:])
    return True


def _sync_archive_messages(
    database: sqlite3.Connection,
    *,
    window_name: str,
    source: str,
    session_id: str,
    created_at: str,
    updated_at: str,
    messages: list[Any],
) -> None:
    candidates = _message_rows(
        window_name,
        source,
        session_id,
        created_at,
        updated_at,
        messages,
    )
    existing = database.execute(
        "SELECT message_index, source, session_id, message_json "
        "FROM history_messages WHERE window_name=? ORDER BY message_index",
        (window_name,),
    ).fetchall()
    prefix_matches = len(existing) <= len(candidates)
    if prefix_matches:
        for row, candidate in zip(existing, candidates):
            if (
                int(row["message_index"]) != int(candidate[1])
                or str(row["source"]) != source
                or str(row["session_id"]) != session_id
                or str(row["message_json"]) != str(candidate[7])
            ):
                prefix_matches = False
                break
    if not prefix_matches:
        if _rewrite_shifted_messages(
            database,
            window_name=window_name,
            existing=existing,
            candidates=candidates,
        ):
            return
        database.execute(
            "DELETE FROM history_messages WHERE window_name=?", (window_name,)
        )
        database.executemany(_MESSAGE_INSERT_SQL, candidates)
        return
    if len(existing) < len(candidates):
        database.executemany(_MESSAGE_INSERT_SQL, candidates[len(existing) :])


_ROUND_INSERT_SQL = """
    INSERT INTO history_rounds(
        window_kind, window_name, round_number,
        think_json, tool_json, items_json, metric_json
    ) VALUES(?, ?, ?, ?, ?, ?, ?)
"""
