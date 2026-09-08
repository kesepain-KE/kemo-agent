"""Stateless helpers shared by the SQLite memory store."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any


def memory_api() -> Any:
    # Imported lazily because run.memory installs the implementation after
    # defining the shared validation helpers and public data classes.
    from run import memory

    return memory


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def filename_key(filename: str) -> str:
    return filename.casefold()


def row_meta(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "weight": int(row["weight"]),
        "created_at": str(row["created_at"]),
        "content_updated_at": str(row["content_updated_at"]),
        "updated_at": str(row["content_updated_at"]),
        "last_used_at": row["last_used_at"],
        "last_weight_date": row["last_weight_date"],
        "tier_entered_at": str(row["tier_entered_at"]),
        "expires_at": row["expires_at"],
    }


def entry_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "filename": str(row["filename"]),
        "content": str(row["content"]),
        "tier": str(row["tier"]),
        **row_meta(row),
    }
