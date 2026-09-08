"""Context-summary persistence for runtime history windows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from run.history.store_core import _deleted_window_exists, _object, connection
from run.history.window_store import _store_context_summary, window_location


def _summary_window(runtime_path: Path) -> tuple[Path, str, str]:
    root, user, kind, name = window_location(runtime_path)
    if kind != "runtime":
        raise ValueError(f"上下文摘要必须关联运行时窗口：{runtime_path}")
    return root, user, name


def read_context_summary(runtime_path: Path) -> dict[str, Any] | None:
    root, user, name = _summary_window(runtime_path)
    with connection(root, user) as database:
        row = database.execute(
            """
            SELECT schema_version, source_hash, previous_source_hash,
                   covered_through_round, covered_rounds_json, summary_json,
                   memory_extractions_json, created_at
            FROM history_context_summaries
            WHERE window_name=?
              AND EXISTS (
                  SELECT 1 FROM history_windows
                  WHERE window_kind='runtime' AND window_name=?
              )
            """,
            (name, name),
        ).fetchone()
    if row is None:
        return None
    return {
        "schema_version": int(row["schema_version"]),
        "source_hash": str(row["source_hash"] or ""),
        "previous_source_hash": row["previous_source_hash"],
        "covered_through_round": int(row["covered_through_round"] or 0),
        "covered_rounds": _object(row["covered_rounds_json"], []),
        "summary": _object(row["summary_json"], {}),
        "memory_extractions": _object(row["memory_extractions_json"], []),
        "created_at": str(row["created_at"] or ""),
    }


def write_context_summary(runtime_path: Path, cache: dict[str, Any] | None) -> None:
    root, user, name = _summary_window(runtime_path)
    with connection(root, user, write=True) as database:
        window = database.execute(
            """
            SELECT source, session_id FROM history_windows
            WHERE window_kind='runtime' AND window_name=?
            LIMIT 1
            """,
            (name,),
        ).fetchone()
        if window is None or _deleted_window_exists(
            database, kind="runtime", name=name
        ):
            # A summary worker can finish after its runtime window was deleted.
            # Do not create an orphan cache that could be observed by a later
            # session using the same logical conversation id.
            return
        _store_context_summary(
            database,
            window_name=name,
            source=str(window["source"] if window is not None else ""),
            session_id=str(window["session_id"] if window is not None else ""),
            cache=cache,
        )


def context_summary_exists(runtime_path: Path) -> bool:
    root, user, name = _summary_window(runtime_path)
    with connection(root, user) as database:
        return (
            database.execute(
                """
                SELECT 1 FROM history_context_summaries
                WHERE window_name=?
                  AND EXISTS (
                      SELECT 1 FROM history_windows
                      WHERE window_kind='runtime' AND window_name=?
                  )
                """,
                (name, name),
            ).fetchone()
            is not None
        )
