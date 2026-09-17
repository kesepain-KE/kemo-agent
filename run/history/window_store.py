"""Atomic history-window persistence and archive/runtime partition storage."""

from __future__ import annotations

import copy
from pathlib import Path
import sqlite3
from typing import Any, Callable, Iterable

from run.config import user_dir
from run.history.store_core import (
    _deleted_window_exists,
    _json,
    _object,
    _remember_deleted_window,
    _session_generation,
    _session_is_deleted,
    connection,
)
from run.history.registry_store import _upsert_session_row
from run.history.message_store import _ROUND_INSERT_SQL, _sync_archive_messages

_SUMMARY_UNSET = object()


def window_location(directory: Path) -> tuple[Path, str, str, str]:
    """Return ``(root, user, kind, window_name)`` for a logical window path."""

    resolved = directory.resolve()
    kind = "runtime" if resolved.parent.name == "temp" else "archive"
    history_dir = resolved.parent.parent if kind == "runtime" else resolved.parent
    if history_dir.name != "history" or history_dir.parent.parent.name != "users":
        raise ValueError(f"历史窗口路径无效：{directory}")
    user = history_dir.parent.name
    root = history_dir.parent.parent.parent
    return root, user, kind, resolved.name


def window_path(
    root: Path, user: str, window_name: str, *, kind: str = "archive"
) -> Path:
    base = user_dir(user, root) / "history"
    return (base / "temp" / window_name) if kind == "runtime" else (base / window_name)


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


def _save_window_on_connection(
    database: sqlite3.Connection,
    *,
    kind: str,
    name: str,
    window: dict[str, Any],
    summary_cache: dict[str, Any] | None | object = _SUMMARY_UNSET,
) -> dict[str, Any]:
    data = copy.deepcopy(window.get("data") or {})
    text = copy.deepcopy(window.get("text") or {})
    think = copy.deepcopy(window.get("think") or {})
    tool = copy.deepcopy(window.get("tool") or {})
    items = copy.deepcopy(window.get("items") or {})
    source = str(data.get("source") or "")
    session_id = str(data.get("session_id") or "")
    incoming_generation = str(
        window.get("_session_generation") or data.get("session_generation") or ""
    ).strip()
    if incoming_generation:
        data["session_generation"] = incoming_generation
    if _deleted_window_exists(database, kind=kind, name=name):
        # Physical names are generated by the framework and are not reused.
        # Refuse every later write to a tombstoned name, including a stale
        # writer that no longer carries the generation field.
        return data
    if source and session_id and _session_is_deleted(
        database, source=source, session_id=session_id
    ):
        # A run may finish after its conversation was deleted.  Do not recreate
        # either history window; returning the caller's in-memory data keeps
        # the terminal event path deterministic without persisting stale data.
        return data
    current_generation = _session_generation(
        database,
        source=source,
        session_id=session_id,
    ) if source and session_id else ""
    if (
        current_generation
        and incoming_generation
        and incoming_generation != current_generation
    ):
        # A writer from a previous reservation must never be rebound to the
        # current logical session.  Physical-window tombstones protect already
        # committed windows; this generation check also fences a window that
        # was only prepared in memory when the old session was deleted.
        return data
    if current_generation and not incoming_generation:
        data["session_generation"] = current_generation
        incoming_generation = current_generation
    existing = database.execute(
        "SELECT title FROM history_windows WHERE window_kind=? AND window_name=?",
        (kind, name),
    ).fetchone()
    if existing is not None:
        data["title"] = str(existing["title"] or "")
    data["complete"] = True
    created_at = str(data.get("created_at") or data.get("updated_at") or "")
    updated_at = str(data.get("updated_at") or "")
    messages = text.get("messages", []) if isinstance(text, dict) else []
    if kind == "archive":
        _sync_archive_messages(
            database,
            window_name=name,
            source=source,
            session_id=session_id,
            created_at=created_at,
            updated_at=updated_at,
            messages=messages if isinstance(messages, list) else [],
        )
        stored_text = {
            "schema_version": max(1, int(text.get("schema_version") or 1)),
            "storage": "history_messages",
        }
    else:
        stored_text = text
    _sync_window_rounds(
        database,
        window_kind=kind,
        window_name=name,
        think=think if isinstance(think, dict) else {},
        tool=tool if isinstance(tool, dict) else {},
        items=items if isinstance(items, dict) else {},
        metrics=data.get("round_metrics"),
    )
    stored_data = copy.deepcopy(data)
    if kind == "archive":
        # session_generation is an internal concurrency marker.  Keep the
        # historical archive payload contract unchanged while retaining the
        # marker in the returned in-memory data and registry record.
        stored_data.pop("session_generation", None)
    stored_data.pop("round_metrics", None)
    stored_data["round_metrics_storage"] = "history_rounds"
    stored_think = _partition_reference(think, default_schema=1)
    stored_tool = _partition_reference(tool, default_schema=1)
    stored_items = _partition_reference(items, default_schema=2)
    database.execute(
        """
        INSERT INTO history_windows(
            window_name, window_kind, source, session_id, title,
            created_at, updated_at, rounds, data_json, text_json,
            think_json, tool_json, items_json
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(window_kind, window_name) DO UPDATE SET
            source=excluded.source,
            session_id=excluded.session_id,
            title=excluded.title,
            created_at=excluded.created_at,
            updated_at=excluded.updated_at,
            rounds=excluded.rounds,
            data_json=excluded.data_json,
            text_json=excluded.text_json,
            think_json=excluded.think_json,
            tool_json=excluded.tool_json,
            items_json=excluded.items_json
        """,
        (
            name,
            kind,
            source,
            session_id,
            str(data.get("title") or ""),
            created_at,
            updated_at,
            max(0, int(data.get("rounds") or 0)),
            _json(stored_data),
            _json(stored_text),
            _json(stored_think),
            _json(stored_tool),
            _json(stored_items),
        ),
    )
    if kind == "runtime" and summary_cache is not _SUMMARY_UNSET:
        _store_context_summary(
            database,
            window_name=name,
            source=source,
            session_id=session_id,
            cache=summary_cache if isinstance(summary_cache, dict) else None,
        )
    return data


def _write_registry_state(
    database: sqlite3.Connection,
    *,
    record: dict[str, Any] | None,
    active_updates: dict[str, dict[str, str] | None] | None,
    conditional_active_updates: dict[str, dict[str, str] | None] | None = None,
    updated_at: str,
) -> None:
    stale_record = False
    if record is not None:
        record_source = str(record.get("source") or "")
        record_session_id = str(record.get("session_id") or "")
        if record_source and record_session_id and _session_is_deleted(
            database,
            source=record_source,
            session_id=record_session_id,
        ):
            # A late terminal commit must not recreate a deleted session row or
            # its active pointer.
            record = None
        else:
            incoming_generation = str(
                record.get("session_generation") or ""
            ).strip()
            current_generation = _session_generation(
                database,
                source=record_source,
                session_id=record_session_id,
            )
            if (
                incoming_generation
                and current_generation
                and incoming_generation != current_generation
            ):
                # Do not let a stale terminal commit replace the current
                # registry row or move its active binding backwards.
                record = None
                stale_record = True
    if stale_record:
        active_updates = None
        conditional_active_updates = None
    if record is not None:
        _upsert_session_row(database, record)
    def apply_active_update(
        active_key: str,
        binding: dict[str, str] | None,
        *,
        conditional: bool,
    ) -> None:
        current = database.execute(
            "SELECT source, session_id FROM history_active_sessions WHERE active_key=?",
            (str(active_key),),
        ).fetchone()
        if conditional and binding is not None and current is not None:
            expected = (str(binding.get("source") or ""), str(binding.get("session_id") or ""))
            existing = (str(current["source"]), str(current["session_id"]))
            if existing != expected:
                # A newer session was explicitly selected while this older
                # run was finishing.  Do not let the late terminal commit
                # move the active pointer backwards.
                return
        if binding is None:
            if not conditional:
                database.execute(
                    "DELETE FROM history_active_sessions WHERE active_key=?",
                    (str(active_key),),
                )
            return
        source = str(binding.get("source") or "")
        session_id = str(binding.get("session_id") or "")
        if source and session_id and _session_is_deleted(
            database, source=source, session_id=session_id
        ):
            return
        if source and session_id:
            database.execute(
                """
                INSERT INTO history_active_sessions(active_key, source, session_id)
                VALUES(?, ?, ?)
                ON CONFLICT(active_key) DO UPDATE SET
                    source=excluded.source, session_id=excluded.session_id
                """,
                (str(active_key), source, session_id),
            )

    for active_key, binding in (active_updates or {}).items():
        apply_active_update(active_key, binding, conditional=False)
    for active_key, binding in (conditional_active_updates or {}).items():
        apply_active_update(active_key, binding, conditional=True)
    if record is not None or active_updates or conditional_active_updates:
        database.execute(
            """
            INSERT INTO history_meta(key, value) VALUES('registry_revision', '1')
            ON CONFLICT(key) DO UPDATE SET
                value=CAST(COALESCE(NULLIF(history_meta.value, ''), '0') AS INTEGER) + 1
            """
        )
        database.execute(
            "INSERT OR REPLACE INTO history_meta(key, value) VALUES('registry_updated_at', ?)",
            (updated_at,),
        )


def save_window_bundle(
    entries: list[tuple[Path, dict[str, Any], dict[str, Any] | None | object]],
    *,
    session_record: dict[str, Any] | None = None,
    active_updates: dict[str, dict[str, str] | None] | None = None,
    conditional_active_updates: dict[str, dict[str, str] | None] | None = None,
    context_summary_updates: list[
        tuple[str, str, str, dict[str, Any] | None]
    ] | None = None,
    updated_at: str = "",
) -> list[dict[str, Any]]:
    if not entries:
        return []
    locations = [window_location(directory) for directory, _, _ in entries]
    root, user = locations[0][0], locations[0][1]
    if any(location[0] != root or location[1] != user for location in locations[1:]):
        raise ValueError("同一历史事务只能写入一个用户的窗口")
    stored: list[dict[str, Any]] = []
    with connection(root, user, write=True) as database:
        for (_, window, summary_cache), (_, _, kind, name) in zip(entries, locations):
            stored.append(
                _save_window_on_connection(
                    database,
                    kind=kind,
                    name=name,
                    window=window,
                    summary_cache=summary_cache,
                )
            )
        for window_name, source, session_id, cache in context_summary_updates or []:
            _store_context_summary(
                database,
                window_name=str(window_name),
                source=str(source),
                session_id=str(session_id),
                cache=copy.deepcopy(cache) if isinstance(cache, dict) else None,
            )
        _write_registry_state(
            database,
            record=copy.deepcopy(session_record) if session_record is not None else None,
            active_updates=active_updates,
            conditional_active_updates=conditional_active_updates,
            updated_at=str(updated_at or (session_record or {}).get("updated_at") or ""),
        )
    return stored


def save_window(
    directory: Path,
    window: dict[str, Any],
    *,
    summary_cache: dict[str, Any] | None | object = _SUMMARY_UNSET,
) -> dict[str, Any]:
    return save_window_bundle([(directory, window, summary_cache)])[0]


def patch_window_data(
    directory: Path,
    data: dict[str, Any],
    *,
    session_record: dict[str, Any] | None = None,
    updated_at: str = "",
    merge_updates: dict[str, Any] | None = None,
    merge_removals: Iterable[str] = (),
    session_record_factory: Callable[
        [dict[str, Any], dict[str, Any] | None], dict[str, Any] | None
    ] | None = None,
) -> dict[str, Any]:
    """Update window/session metadata without rewriting transcript partitions.

    ``merge_updates`` is used by small asynchronous metadata transitions.  It
    reads the current ``data_json`` while holding the SQLite write transaction
    and overlays only the requested keys, so a caller's stale in-memory window
    cannot roll back a newer conversation round.  A record factory receives
    that merged data and the current registry row, allowing the registry to be
    updated from the same transaction as the window row.
    """

    root, user, kind, name = window_location(directory)
    with connection(root, user, write=True) as database:
        existing = database.execute(
            "SELECT source, session_id, title, data_json "
            "FROM history_windows WHERE window_kind=? AND window_name=?",
            (kind, name),
        ).fetchone()
        if existing is None:
            raise FileNotFoundError(f"历史窗口不存在：{directory}")

        if merge_updates is None:
            rendered = copy.deepcopy(data)
        else:
            current_data = _object(existing["data_json"], {})
            rendered = copy.deepcopy(current_data) if isinstance(current_data, dict) else {}
            for key, value in merge_updates.items():
                rendered[str(key)] = copy.deepcopy(value)
            for key in merge_removals:
                rendered.pop(str(key), None)
        rendered["complete"] = True
        rendered.setdefault("source", str(existing["source"] or ""))
        rendered.setdefault("session_id", str(existing["session_id"] or ""))
        rendered["title"] = str(existing["title"] or rendered.get("title") or "")
        stored_rendered = copy.deepcopy(rendered)
        stored_rendered.pop("round_metrics", None)
        stored_rendered["round_metrics_storage"] = "history_rounds"
        database.execute(
            """
            UPDATE history_windows SET
                source=?, session_id=?, title=?, created_at=?, updated_at=?,
                rounds=?, data_json=?
            WHERE window_kind=? AND window_name=?
            """,
            (
                str(rendered.get("source") or ""),
                str(rendered.get("session_id") or ""),
                str(rendered.get("title") or ""),
                str(rendered.get("created_at") or rendered.get("updated_at") or ""),
                str(rendered.get("updated_at") or ""),
                max(0, int(rendered.get("rounds") or 0)),
                _json(stored_rendered),
                kind,
                name,
            ),
        )

        resolved_record = session_record
        if session_record_factory is not None:
            record_row = database.execute(
                "SELECT record_json FROM history_sessions WHERE source=? AND session_id=?",
                (
                    str(rendered.get("source") or ""),
                    str(rendered.get("session_id") or ""),
                ),
            ).fetchone()
            previous_record = (
                _object(record_row["record_json"], {})
                if record_row is not None
                else None
            )
            if not isinstance(previous_record, dict):
                previous_record = None
            resolved_record = session_record_factory(rendered, previous_record)
        _write_registry_state(
            database,
            record=copy.deepcopy(resolved_record) if resolved_record is not None else None,
            active_updates=None,
            updated_at=str(updated_at or rendered.get("updated_at") or ""),
        )
    return rendered


def load_window(
    directory: Path,
    *,
    tail_rounds: int | None = None,
) -> dict[str, Any] | None:
    root, user, kind, name = window_location(directory)
    with connection(root, user) as database:
        row = database.execute(
            """
            SELECT rounds, data_json, text_json, think_json, tool_json, items_json
            FROM history_windows WHERE window_kind=? AND window_name=?
            """,
            (kind, name),
        ).fetchone()
        lower_round = 0
        if row is not None and tail_rounds is not None:
            lower_round = max(1, int(row["rounds"] or 0) - max(1, int(tail_rounds)) + 1)
        message_rows = (
            database.execute(
                "SELECT message_json FROM history_messages "
                "WHERE window_name=? AND round_number>=? ORDER BY message_index",
                (name, lower_round),
            ).fetchall()
            if row is not None and kind == "archive" and tail_rounds is not None
            else database.execute(
                "SELECT message_json FROM history_messages "
                "WHERE window_name=? ORDER BY message_index",
                (name,),
            ).fetchall()
            if row is not None and kind == "archive"
            else []
        )
        round_rows = (
            database.execute(
                """
                SELECT round_number, think_json, tool_json, items_json, metric_json
                FROM history_rounds WHERE window_kind=? AND window_name=?
                    AND round_number>=?
                ORDER BY round_number
                """,
                (kind, name, lower_round),
            ).fetchall()
            if row is not None and tail_rounds is not None
            else database.execute(
                """
                SELECT round_number, think_json, tool_json, items_json, metric_json
                FROM history_rounds WHERE window_kind=? AND window_name=?
                ORDER BY round_number
                """,
                (kind, name),
            ).fetchall()
            if row is not None
            else []
        )
    if row is None:
        return None
    text = _object(row["text_json"], {"schema_version": 1, "messages": []})
    if kind == "archive":
        text = {
            "schema_version": max(
                1, int(text.get("schema_version") or 1)
            )
            if isinstance(text, dict)
            else 1,
            "messages": [
                message
                for value in message_rows
                if isinstance((message := _object(value["message_json"], {})), dict)
            ],
        }
    data = _object(row["data_json"], {})
    think = _object(row["think_json"], {"schema_version": 1, "rounds": []})
    tool = _object(row["tool_json"], {"schema_version": 1, "rounds": []})
    items = _object(row["items_json"], {"schema_version": 2, "items": []})
    round_storage = bool(
        round_rows
        or (isinstance(data, dict) and data.get("round_metrics_storage") == "history_rounds")
        or (isinstance(think, dict) and think.get("storage") == "history_rounds")
    )
    if round_storage:
        think = {
            "schema_version": _partition_schema(think, 1),
            "rounds": [
                value
                for round_row in round_rows
                if round_row["think_json"]
                and isinstance((value := _object(round_row["think_json"], {})), dict)
            ],
        }
        tool = {
            "schema_version": _partition_schema(tool, 1),
            "rounds": [
                value
                for round_row in round_rows
                if round_row["tool_json"]
                and isinstance((value := _object(round_row["tool_json"], {})), dict)
            ],
        }
        restored_items: list[dict[str, Any]] = []
        metrics: list[dict[str, Any]] = []
        for round_row in round_rows:
            item_values = _object(round_row["items_json"], [])
            if isinstance(item_values, list):
                restored_items.extend(
                    value for value in item_values if isinstance(value, dict)
                )
            if round_row["metric_json"]:
                metric = _object(round_row["metric_json"], {})
                if isinstance(metric, dict):
                    metrics.append(metric)
        items = {
            "schema_version": _partition_schema(items, 2),
            "items": restored_items,
        }
        if isinstance(data, dict):
            data.pop("round_metrics_storage", None)
            data["round_metrics"] = metrics
    return {
        "data": data,
        "text": text,
        "think": think,
        "tool": tool,
        "items": items,
    }


def window_exists(directory: Path) -> bool:
    root, user, kind, name = window_location(directory)
    with connection(root, user) as database:
        row = database.execute(
            "SELECT 1 FROM history_windows WHERE window_kind=? AND window_name=?",
            (kind, name),
        ).fetchone()
    return row is not None


def delete_window(directory: Path) -> bool:
    root, user, kind, name = window_location(directory)
    with connection(root, user, write=True) as database:
        row = database.execute(
            """
            SELECT source, session_id, data_json
            FROM history_windows WHERE window_kind=? AND window_name=?
            """,
            (kind, name),
        ).fetchone()
        if row is not None and kind != "runtime":
            stored_data = _object(row["data_json"], {})
            _remember_deleted_window(
                database,
                kind=kind,
                name=name,
                source=str(row["source"] or ""),
                session_id=str(row["session_id"] or ""),
                session_generation=(
                    str(stored_data.get("session_generation") or "")
                    if isinstance(stored_data, dict)
                    else ""
                ),
            )
        database.execute(
            "DELETE FROM history_rounds WHERE window_kind=? AND window_name=?",
            (kind, name),
        )
        if kind == "archive":
            database.execute(
                "DELETE FROM history_messages WHERE window_name=?", (name,)
            )
            database.execute(
                "DELETE FROM history_context_summaries WHERE window_name=?",
                (name,),
            )
        result = database.execute(
            "DELETE FROM history_windows WHERE window_kind=? AND window_name=?",
            (kind, name),
        )
    from run.history import runtime_cache

    runtime_cache.drop(window_path(root, user, name, kind="runtime"))
    return result.rowcount > 0


def list_windows(
    root: Path, user: str, *, source: str | None = None
) -> list[dict[str, Any]]:
    sql = "SELECT window_name, source, session_id, data_json FROM history_windows WHERE window_kind='archive'"
    params: list[Any] = []
    if source is not None:
        sql += " AND source=?"
        params.append(source)
    sql += " ORDER BY updated_at DESC, window_name DESC"
    with connection(root, user) as database:
        rows = database.execute(sql, params).fetchall()
    return [
        {
            "window_name": str(row["window_name"]),
            "source": str(row["source"]),
            "session_id": str(row["session_id"]),
            "data": _object(row["data_json"], {}),
        }
        for row in rows
    ]


def find_window_name(root: Path, user: str, source: str, session_id: str) -> str | None:
    with connection(root, user) as database:
        row = database.execute(
            """
            SELECT window_name FROM history_windows
            WHERE window_kind='archive' AND source=? AND session_id=?
            ORDER BY updated_at DESC LIMIT 1
            """,
            (source, session_id),
        ).fetchone()
    return str(row["window_name"]) if row is not None else None


def rename_windows(
    root: Path, user: str, source: str, session_id: str, title: str
) -> int:
    with connection(root, user, write=True) as database:
        rows = database.execute(
            "SELECT window_kind, window_name, data_json FROM history_windows WHERE source=? AND session_id=?",
            (source, session_id),
        ).fetchall()
        for row in rows:
            data = _object(row["data_json"], {})
            data["title"] = title
            database.execute(
                """
                UPDATE history_windows SET title=?, data_json=?
                WHERE window_kind=? AND window_name=?
                """,
                (title, _json(data), row["window_kind"], row["window_name"]),
            )
    from run.history import runtime_cache

    runtime_cache.drop_session(root, user, source, session_id)
    return len(rows)


def delete_session_windows(root: Path, user: str, source: str, session_id: str) -> int:
    with connection(root, user, write=True) as database:
        return _delete_session_windows(database, source, session_id)


def _delete_session_windows(database: sqlite3.Connection, source: str, session_id: str) -> int:
    """Shared transaction body for manual deletion and lifecycle cleanup."""
    if source == 'web':
        database.execute('DELETE FROM history_web_leases WHERE session_id=?', (session_id,))
    database.execute(
        """
        INSERT INTO history_deleted_sessions(source, session_id, deleted_at)
        VALUES(?, ?, datetime('now'))
        ON CONFLICT(source, session_id) DO UPDATE SET
            deleted_at=excluded.deleted_at
        """,
        (str(source), str(session_id)),
    )
    windows = database.execute(
        "SELECT window_kind, window_name, data_json FROM history_windows "
        "WHERE source=? AND session_id=?",
        (source, session_id),
    ).fetchall()
    for row in windows:
        stored_data = _object(row["data_json"], {})
        _remember_deleted_window(
            database,
            kind=str(row["window_kind"]),
            name=str(row["window_name"]),
            source=source,
            session_id=session_id,
            session_generation=(
                str(stored_data.get("session_generation") or "")
                if isinstance(stored_data, dict)
                else ""
            ),
        )
    database.executemany(
        "DELETE FROM history_rounds WHERE window_kind=? AND window_name=?",
        [(row["window_kind"], row["window_name"]) for row in windows],
    )
    database.execute(
        "DELETE FROM history_context_summaries WHERE source=? AND session_id=?",
        (source, session_id),
    )
    database.execute(
        "DELETE FROM history_messages WHERE source=? AND session_id=?",
        (source, session_id),
    )
    result = database.execute(
        "DELETE FROM history_windows WHERE source=? AND session_id=?",
        (source, session_id),
    )
    from run.history import runtime_cache

    database_file = Path(
        str(database.execute("PRAGMA database_list").fetchone()["file"])
    ).resolve()
    runtime_cache.drop_session(
        database_file.parents[3], database_file.parent.parent.name, source, session_id
    )
    return result.rowcount


def delete_source_windows(root: Path, user: str, source: str) -> tuple[int, int]:
    with connection(root, user, write=True) as database:
        session_rows = database.execute(
            "SELECT DISTINCT session_id FROM history_windows WHERE source=?",
            (str(source),),
        ).fetchall()
        database.executemany(
            """
            INSERT INTO history_deleted_sessions(source, session_id, deleted_at)
            VALUES(?, ?, datetime('now'))
            ON CONFLICT(source, session_id) DO UPDATE SET
                deleted_at=excluded.deleted_at
            """,
            [(str(source), str(row["session_id"])) for row in session_rows],
        )
        session_count = int(
            database.execute(
                "SELECT COUNT(DISTINCT session_id) FROM history_windows WHERE source=?",
                (source,),
            ).fetchone()[0]
        )
        windows = database.execute(
            "SELECT window_kind, window_name, session_id, data_json "
            "FROM history_windows WHERE source=?",
            (source,),
        ).fetchall()
        for row in windows:
            stored_data = _object(row["data_json"], {})
            _remember_deleted_window(
                database,
                kind=str(row["window_kind"]),
                name=str(row["window_name"]),
                source=source,
                session_id=str(row["session_id"] or ""),
                session_generation=(
                    str(stored_data.get("session_generation") or "")
                    if isinstance(stored_data, dict)
                    else ""
                ),
            )
        database.executemany(
            "DELETE FROM history_rounds WHERE window_kind=? AND window_name=?",
            [(row["window_kind"], row["window_name"]) for row in windows],
        )
        database.execute(
            "DELETE FROM history_context_summaries WHERE source=?", (source,)
        )
        database.execute("DELETE FROM history_messages WHERE source=?", (source,))
        result = database.execute(
            "DELETE FROM history_windows WHERE source=?", (source,)
        )
    from run.history import runtime_cache

    runtime_cache.drop_source(root, user, source)
    return session_count, result.rowcount
