"""Archive message row projection and synchronization."""

from __future__ import annotations

from typing import Any
import sqlite3

from run.history.store_core import _json


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
