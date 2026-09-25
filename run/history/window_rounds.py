"""Round partition synchronization for history windows."""

from __future__ import annotations

import copy
from pathlib import Path
import sqlite3
from typing import Any

from run.history.store_core import _json, _object
from run.history.message_store import _ROUND_INSERT_SQL

_SUMMARY_UNSET = object()

def _safe_round_number(value: Any) -> int:
    if not isinstance(value, dict):
        return 0
    metadata = value.get("metadata")
    raw = metadata.get("round") if isinstance(metadata, dict) else value.get("round")
    try:
        return max(0, int(raw or 0))
    except (TypeError, ValueError):
        return 0


def _partition_reference(value: Any, *, default_schema: int) -> dict[str, Any]:
    schema = value.get("schema_version") if isinstance(value, dict) else default_schema
    try:
        rendered_schema = max(1, int(schema or default_schema))
    except (TypeError, ValueError):
        rendered_schema = default_schema
    return {"schema_version": rendered_schema, "storage": "history_rounds"}


def _partition_schema(value: Any, default_schema: int) -> int:
    raw = value.get("schema_version") if isinstance(value, dict) else default_schema
    try:
        return max(1, int(raw or default_schema))
    except (TypeError, ValueError):
        return default_schema


def _window_round_rows(
    window_kind: str,
    window_name: str,
    *,
    think: dict[str, Any],
    tool: dict[str, Any],
    items: dict[str, Any],
    metrics: Any,
) -> list[tuple[Any, ...]]:
    thinks = {
        number: value
        for value in think.get("rounds", [])
        if isinstance(value, dict) and (number := _safe_round_number(value)) > 0
    }
    tools = {
        number: value
        for value in tool.get("rounds", [])
        if isinstance(value, dict) and (number := _safe_round_number(value)) > 0
    }
    metrics_by_round = {
        number: value
        for value in (metrics if isinstance(metrics, list) else [])
        if isinstance(value, dict)
        if (number := _safe_round_number(value)) > 0
    }
    items_by_round: dict[int, list[dict[str, Any]]] = {}
    for value in items.get("items", []) if isinstance(items.get("items"), list) else []:
        if not isinstance(value, dict):
            continue
        number = _safe_round_number(value)
        items_by_round.setdefault(number, []).append(value)
    round_numbers = sorted(
        set(thinks) | set(tools) | set(metrics_by_round) | set(items_by_round)
    )
    return [
        (
            window_kind,
            window_name,
            number,
            _json(thinks[number]) if number in thinks else "",
            _json(tools[number]) if number in tools else "",
            _json(items_by_round.get(number, [])),
            _json(metrics_by_round[number]) if number in metrics_by_round else "",
        )
        for number in round_numbers
    ]


def _scrub_local_rounds(value: Any) -> Any:
    """Remove workspace-local round numbering for shifted-row comparison."""

    if isinstance(value, dict):
        return {
            str(key): (0 if str(key) == "round" else _scrub_local_rounds(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_scrub_local_rounds(item) for item in value]
    return value


def _round_payload_signature(values: Iterable[Any]) -> tuple[str, ...]:
    rendered: list[str] = []
    for raw in values:
        text = str(raw or "")
        if not text:
            rendered.append("")
            continue
        parsed = _object(text, None)
        rendered.append(
            _json(_scrub_local_rounds(parsed)) if parsed is not None else text
        )
    return tuple(rendered)


def _shifted_round_overlap(
    existing: list[sqlite3.Row], candidates: list[tuple[Any, ...]]
) -> int:
    maximum = min(len(existing), len(candidates))
    for overlap in range(maximum, 0, -1):
        if all(
            _round_payload_signature(
                (row["think_json"], row["tool_json"], row["items_json"], row["metric_json"])
            )
            == _round_payload_signature(candidate[3:7])
            for row, candidate in zip(existing[-overlap:], candidates[:overlap])
        ):
            return overlap
    return 0


def _rewrite_shifted_rounds(
    database: sqlite3.Connection,
    *,
    window_kind: str,
    window_name: str,
    existing: list[sqlite3.Row],
    candidates: list[tuple[Any, ...]],
) -> bool:
    """Reuse a shifted workspace suffix instead of delete/reinsert-all.

    Runtime trimming renumbers the retained tail to local rounds ``1..N``.
    Move the retained rows through temporary negative keys, then update their
    local round metadata and append only the genuinely new suffix.  The caller
    already owns the SQLite transaction, so any failure rolls back atomically.
    """

    overlap = _shifted_round_overlap(existing, candidates)
    if not candidates or overlap * 5 < len(candidates) * 4:
        return False
    retained = list(zip(existing[-overlap:], candidates[:overlap]))
    for index, (row, _candidate) in enumerate(retained, start=1):
        database.execute(
            "UPDATE history_rounds SET round_number=? "
            "WHERE window_kind=? AND window_name=? AND round_number=?",
            (-index, window_kind, window_name, int(row["round_number"])),
        )
    database.execute(
        "DELETE FROM history_rounds WHERE window_kind=? AND window_name=? AND round_number>=0",
        (window_kind, window_name),
    )
    for index, (_row, candidate) in enumerate(retained, start=1):
        database.execute(
            """
            UPDATE history_rounds SET round_number=?, think_json=?, tool_json=?,
                items_json=?, metric_json=?
            WHERE window_kind=? AND window_name=? AND round_number=?
            """,
            (
                int(candidate[2]),
                candidate[3],
                candidate[4],
                candidate[5],
                candidate[6],
                window_kind,
                window_name,
                -index,
            ),
        )
    if overlap < len(candidates):
        database.executemany(_ROUND_INSERT_SQL, candidates[overlap:])
    return True


def _sync_window_rounds(
    database: sqlite3.Connection,
    *,
    window_kind: str,
    window_name: str,
    think: dict[str, Any],
    tool: dict[str, Any],
    items: dict[str, Any],
    metrics: Any,
) -> None:
    candidates = _window_round_rows(
        window_kind,
        window_name,
        think=think,
        tool=tool,
        items=items,
        metrics=metrics,
    )
    existing = database.execute(
        """
        SELECT round_number, think_json, tool_json, items_json, metric_json
        FROM history_rounds WHERE window_kind=? AND window_name=?
        ORDER BY round_number
        """,
        (window_kind, window_name),
    ).fetchall()
    prefix_matches = len(existing) <= len(candidates)
    if prefix_matches:
        for row, candidate in zip(existing, candidates):
            if (
                int(row["round_number"]) != int(candidate[2])
                or str(row["think_json"]) != str(candidate[3])
                or str(row["tool_json"]) != str(candidate[4])
                or str(row["items_json"]) != str(candidate[5])
                or str(row["metric_json"]) != str(candidate[6])
            ):
                prefix_matches = False
                break
    if not prefix_matches:
        if _rewrite_shifted_rounds(
            database,
            window_kind=window_kind,
            window_name=window_name,
            existing=existing,
            candidates=candidates,
        ):
            return
        database.execute(
            "DELETE FROM history_rounds WHERE window_kind=? AND window_name=?",
            (window_kind, window_name),
        )
        database.executemany(_ROUND_INSERT_SQL, candidates)
        return
    if len(existing) < len(candidates):
        database.executemany(_ROUND_INSERT_SQL, candidates[len(existing) :])


def _store_context_summary(
    database: sqlite3.Connection,
    *,
    window_name: str,
    source: str,
    session_id: str,
    cache: dict[str, Any] | None,
) -> None:
    if cache is None:
        database.execute(
            "DELETE FROM history_context_summaries WHERE window_name=?",
            (window_name,),
        )
        return
    now = str(cache.get("created_at") or "")
    database.execute(
        """
        INSERT INTO history_context_summaries(
            window_name, source, session_id, schema_version, source_hash,
            previous_source_hash, covered_through_round, covered_rounds_json,
            summary_json, memory_extractions_json, created_at, updated_at
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(window_name) DO UPDATE SET
            source=excluded.source,
            session_id=excluded.session_id,
            schema_version=excluded.schema_version,
            source_hash=excluded.source_hash,
            previous_source_hash=excluded.previous_source_hash,
            covered_through_round=excluded.covered_through_round,
            covered_rounds_json=excluded.covered_rounds_json,
            summary_json=excluded.summary_json,
            memory_extractions_json=excluded.memory_extractions_json,
            created_at=excluded.created_at,
            updated_at=excluded.updated_at
        """,
        (
            window_name,
            source,
            session_id,
            max(0, int(cache.get("schema_version") or 0)),
            str(cache.get("source_hash") or ""),
            cache.get("previous_source_hash"),
            max(0, int(cache.get("covered_through_round") or 0)),
            _json(cache.get("covered_rounds") or []),
            _json(cache.get("summary") or {}),
            _json(cache.get("memory_extractions") or []),
            now,
            now,
        ),
    )
