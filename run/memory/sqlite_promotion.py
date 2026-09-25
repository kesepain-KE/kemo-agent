"""Internal mixin for the SQLite memory store."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import json
import math
from pathlib import Path
import sqlite3
from typing import Any

from run.memory.store import connection, database_path
from run.memory.sqlite_support import (
    content_hash as _hash,
    entry_from_row as _entry_from_row,
    filename_key as _filename_key,
    memory_api as _memory_api,
    row_meta as _row_meta,
)

class PromotionStoreMixin:
    def _promote_row(
        self,
        database: sqlite3.Connection,
        row: sqlite3.Row,
        target_tier: str,
        current: datetime,
        *,
        merged_content: str | None = None,
        target_filename: str | None = None,
        memory_type: str | None = None,
        require_explicit_type: bool = False,
    ) -> None:
        api = _memory_api()
        source_tier = str(row["tier"])
        if source_tier == "permanent" or target_tier not in api.TIERS:
            raise api.MemoryError(f"无效记忆晋升：{source_tier}→{target_tier}")
        target_name = api.normalize_memory_filename(target_filename or row["filename"])
        target = self._row_by_filename(database, target_name)
        content = str(row["content"])
        if merged_content is not None:
            content = api._normalise_text(merged_content)
            if not content or api.contains_sensitive_credential(content):
                raise api.MemoryError("融合后的记忆内容无效或包含疑似敏感凭据")
        content, _ = api.validate_memory_fragment_size(
            content,
            memory_type,
            operation="融合后的记忆" if merged_content is not None else "晋升记忆",
            require_explicit_type=require_explicit_type,
        )
        if target is not None and int(target["id"]) != int(row["id"]):
            if merged_content is None or str(target["tier"]) != target_tier:
                raise api.MemoryError(f"晋升目标已存在同名记忆：{target_name}")
            target_meta = self._new_meta(
                target_tier,
                current,
                source_meta=_row_meta(target),
                content_changed=True,
            )
            database.execute(
                """
                UPDATE memory_fragments SET content=?, content_hash=?,
                    weight=0, last_weight_date=NULL, tier_entered_at=?,
                    expires_at=?, content_updated_at=?, revision=revision+1
                WHERE id=?
                """,
                (
                    content,
                    _hash(content),
                    target_meta["tier_entered_at"],
                    target_meta["expires_at"],
                    api.iso(current),
                    int(target["id"]),
                ),
            )
            database.execute(
                "DELETE FROM memory_weight_events WHERE fragment_id=?",
                (int(target["id"]),),
            )
            database.execute(
                "DELETE FROM memory_fragments WHERE id=?", (int(row["id"]),)
            )
            return
        if target_tier == "permanent":
            meta = {
                "tier_entered_at": api.iso(current),
                "expires_at": None,
            }
        else:
            meta = self._new_meta(
                target_tier,
                current,
                source_meta=_row_meta(row),
                content_changed=merged_content is not None,
            )
        database.execute(
            """
            UPDATE memory_fragments SET filename=?, filename_key=?, tier=?,
                content=?, content_hash=?, weight=0, last_weight_date=NULL,
                tier_entered_at=?, expires_at=?, content_updated_at=?,
                revision=revision+1 WHERE id=?
            """,
            (
                target_name,
                _filename_key(target_name),
                target_tier,
                content,
                _hash(content),
                meta["tier_entered_at"],
                meta["expires_at"],
                api.iso(current)
                if merged_content is not None
                else row["content_updated_at"],
                int(row["id"]),
            ),
        )
        database.execute(
            "DELETE FROM memory_weight_events WHERE fragment_id=?", (int(row["id"]),)
        )

    def _promote_location(
        self,
        location: Any,
        target_tier: str,
        current: datetime,
        *,
        merged_content: str | None = None,
        target_filename: str | None = None,
        memory_type: str | None = None,
        require_explicit_type: bool = False,
    ) -> None:
        api = _memory_api()
        with self._lock, connection(self.root, self.user, write=True) as database:
            row = self._row_by_filename(database, location.filename, tier=location.tier)
            if row is None:
                raise api.MemoryError(f"晋升来源不存在：{location.filename}")
            self._promote_row(
                database,
                row,
                target_tier,
                current,
                merged_content=merged_content,
                target_filename=target_filename,
                memory_type=memory_type,
                require_explicit_type=require_explicit_type,
            )

    def _split_promote_location(
        self,
        location: Any,
        target_tier: str,
        current: datetime,
        split_into: list[dict[str, Any]],
        *,
        memory_type: str | None = None,
        require_explicit_type: bool = False,
    ) -> list[str]:
        """Atomically replace one promoted fragment with several child rows.

        A split is a structural rewrite of the same memory rather than a new
        lifecycle.  Child rows therefore inherit the source expiry and tier
        entry timestamps, start at weight zero, and receive no weight events.
        Every child is validated before the source row is removed; any later
        SQLite or validation failure rolls the whole write transaction back.
        """
        api = _memory_api()
        if target_tier not in api.TIERS:
            raise api.MemoryError(f"未知记忆档位：{target_tier}")
        if not isinstance(split_into, list) or not split_into:
            raise api.MemoryError("拆分晋升至少需要一个子碎片")

        with self._lock, connection(self.root, self.user, write=True) as database:
            row = self._row_by_filename(
                database, location.filename, tier=location.tier
            )
            if row is None:
                raise api.MemoryError(f"晋升来源不存在：{location.filename}")
            if str(row["tier"]) == "permanent":
                raise api.MemoryError("永久记忆不能继续晋升")

            source_id = int(row["id"])
            reserved_keys = {
                str(item["filename_key"])
                for item in database.execute(
                    "SELECT filename_key FROM memory_fragments WHERE id!=?",
                    (source_id,),
                ).fetchall()
            }
            prepared: list[tuple[str, str]] = []

            def unique_name(raw_name: Any) -> str:
                requested = api.normalize_memory_filename(raw_name)
                stem = requested[:-3]
                candidate = requested
                index = 2
                while _filename_key(candidate) in reserved_keys:
                    suffix = f"-{index}"
                    candidate = api.normalize_memory_filename(
                        f"{stem[: max(1, api.FILENAME_MAX_CHARS - len(suffix))]}{suffix}"
                    )
                    index += 1
                reserved_keys.add(_filename_key(candidate))
                return candidate

            for child in split_into:
                if not isinstance(child, dict):
                    raise api.MemoryError("拆分晋升的子碎片必须是对象")
                filename = unique_name(child.get("filename"))
                content = api._normalise_text(child.get("content"))
                if not content:
                    raise api.MemoryError("拆分晋升的子碎片内容不能为空")
                if api.contains_sensitive_credential(content):
                    raise api.MemoryError("拆分晋升的子碎片包含疑似敏感凭据")
                content, _ = api.validate_memory_fragment_size(
                    content,
                    child.get("memory_type", memory_type),
                    operation="拆分晋升的子碎片",
                    require_explicit_type=require_explicit_type,
                )
                prepared.append((filename, content))

            # Delete only after every child has passed validation.  The
            # foreign-key cascade also removes the source's old weight events.
            database.execute("DELETE FROM memory_fragments WHERE id=?", (source_id,))
            for filename, content in prepared:
                database.execute(
                    """
                    INSERT INTO memory_fragments(
                        filename, filename_key, tier, content, content_hash,
                        weight, created_at, content_updated_at, last_used_at,
                        last_weight_date, tier_entered_at, expires_at
                    ) VALUES(?, ?, ?, ?, ?, 0, ?, ?, ?, NULL, ?, ?)
                    """,
                    (
                        filename,
                        _filename_key(filename),
                        target_tier,
                        content,
                        _hash(content),
                        row["created_at"],
                        api.iso(current),
                        row["last_used_at"],
                        row["tier_entered_at"],
                        row["expires_at"],
                    ),
                )
            return [filename for filename, _content in prepared]
