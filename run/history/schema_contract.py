"""Known history columns and deliberately limited, additive repair policy."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from run.infra import SchemaMigrationError, add_missing_columns


REQUIRED_COLUMNS = {
    "history_meta": ("key", "value"),
    "history_windows": (
        "window_name", "window_kind", "source", "session_id", "title", "created_at",
        "updated_at", "rounds", "data_json", "text_json", "think_json", "tool_json", "items_json",
    ),
    "history_sessions": (
        "source", "session_id", "conversation_id", "archive_window", "lifecycle", "run_state",
        "title", "summary", "rounds", "updated_at", "memory_status", "summary_status", "record_json",
    ),
    "history_session_marks": ("source", "session_id", "pinned_at", "favorite"),
    "history_active_sessions": ("active_key", "source", "session_id"),
    "history_web_leases": ("session_id", "client_id", "expires_at", "sequence", "released"),
    "history_deleted_sessions": ("source", "session_id", "deleted_at"),
    "history_deleted_windows": (
        "window_kind", "window_name", "source", "session_id", "session_generation", "deleted_at",
    ),
    "history_messages": (
        "window_name", "message_index", "source", "session_id", "round_number", "role",
        "content_text", "message_json", "created_at", "updated_at",
    ),
    "history_rounds": (
        "window_kind", "window_name", "round_number", "think_json", "tool_json", "items_json", "metric_json",
    ),
    "history_context_summaries": (
        "window_name", "source", "session_id", "schema_version", "source_hash",
        "previous_source_hash", "covered_through_round", "covered_rounds_json",
        "summary_json", "memory_extractions_json", "created_at", "updated_at",
    ),
    "message_processed_messages": (
        "dedupe_key", "platform", "message_id", "status", "claimed_at", "updated_at",
        "source", "session_id", "error_json",
    ),
}

# Only columns whose absence has a known non-destructive default are repaired.
# Identity, primary keys, timestamps and content cannot be invented.
ADDITIVE_COLUMNS = {
    "history_web_leases": {
        "sequence": "INTEGER NOT NULL DEFAULT 0",
        "released": "INTEGER NOT NULL DEFAULT 0 CHECK (released IN (0, 1))",
    },
    "history_sessions": {
        "memory_status": "TEXT NOT NULL DEFAULT 'unknown'",
        "summary_status": "TEXT NOT NULL DEFAULT 'none'",
    },
    "history_session_marks": {
        "pinned_at": "TEXT NOT NULL DEFAULT ''",
        "favorite": "INTEGER NOT NULL DEFAULT 0 CHECK (favorite IN (0, 1))",
    },
    "history_deleted_windows": {"session_generation": "TEXT NOT NULL DEFAULT ''"},
    "history_messages": {"round_number": "INTEGER NOT NULL DEFAULT 0"},
    "history_context_summaries": {
        "source_hash": "TEXT NOT NULL DEFAULT ''",
        "previous_source_hash": "TEXT",
        "covered_through_round": "INTEGER NOT NULL DEFAULT 0",
        "covered_rounds_json": "TEXT NOT NULL DEFAULT '[]'",
        "memory_extractions_json": "TEXT NOT NULL DEFAULT '[]'",
    },
    "message_processed_messages": {
        "platform": "TEXT NOT NULL DEFAULT ''",
        "message_id": "TEXT NOT NULL DEFAULT ''",
        "source": "TEXT NOT NULL DEFAULT ''",
        "session_id": "TEXT NOT NULL DEFAULT ''",
        "error_json": "TEXT",
    },
}


def repair_existing_columns(database: sqlite3.Connection) -> None:
    """Called inside the migration transaction, after pre-DDL backup."""
    for table, expected in REQUIRED_COLUMNS.items():
        actual = {str(row[1]) for row in database.execute(f'PRAGMA table_info("{table}")')}
        if not actual:
            continue  # A whole missing table is created by the canonical DDL.
        safe = ADDITIVE_COLUMNS.get(table, {})
        unsafe = set(expected) - actual - set(safe)
        if unsafe:
            raise SchemaMigrationError(
                f"history 结构不完整，拒绝猜测恢复 {table} 缺失列：{', '.join(sorted(unsafe))}"
            )
        add_missing_columns(database, table, safe)
        if table == "history_sessions":
            for column in set(safe) - actual:
                # These columns are query projections of record_json. Preserve
                # known queue/completion state instead of silently resetting it.
                database.execute(
                    f'UPDATE "{table}" SET "{column}" = CASE WHEN json_valid(record_json) '
                    f"THEN CASE WHEN json_type(record_json, '$.{column}')='text' "
                    f"THEN json_extract(record_json, '$.{column}') ELSE \"{column}\" END "
                    f'ELSE "{column}" END'
                )


def migration_object(raw: str, *, label: str) -> dict[str, Any]:
    """Corruption is not an empty partition and must not erase durable rows."""
    try:
        value = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise SchemaMigrationError(f"history 迁移遇到非法 JSON：{label}") from exc
    if not isinstance(value, dict):
        raise SchemaMigrationError(f"history 迁移要求对象：{label}")
    return value


def check_storage_references(database: sqlite3.Connection) -> None:
    """Do not create an empty replacement for a missing authoritative table."""
    tables = {str(row[0]) for row in database.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    if "history_windows" not in tables:
        return
    for row in database.execute(
        "SELECT window_name, text_json, think_json, tool_json, items_json FROM history_windows"
    ):
        for field in ("text_json", "think_json", "tool_json", "items_json"):
            value = migration_object(row[field], label=f"{row['window_name']}.{field}")
            storage = value.get("storage")
            expected = "history_messages" if field == "text_json" else "history_rounds"
            if storage is not None and (storage != expected or storage not in tables):
                raise SchemaMigrationError(
                    f"history 存储引用缺失或未知：{row['window_name']}.{field} -> {storage}"
                )
