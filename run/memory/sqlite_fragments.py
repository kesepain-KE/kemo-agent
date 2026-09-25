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

class FragmentStoreMixin:
    def database_path(self) -> Path:
        return database_path(self.root, self.user)

    def _row_by_filename(
        self,
        database: sqlite3.Connection,
        filename: str,
        *,
        tier: str | None = None,
    ) -> sqlite3.Row | None:
        api = _memory_api()
        normalized = api.normalize_memory_filename(filename)
        if tier is None:
            return database.execute(
                "SELECT * FROM memory_fragments WHERE filename_key=?",
                (_filename_key(normalized),),
            ).fetchone()
        if tier not in api.TIERS:
            raise api.MemoryError(f"未知记忆档位：{tier}")
        return database.execute(
            "SELECT * FROM memory_fragments WHERE filename_key=? AND tier=?",
            (_filename_key(normalized), tier),
        ).fetchone()

    def _new_meta(
        self,
        tier: str,
        current: datetime,
        *,
        source_meta: dict[str, Any] | None = None,
        content_changed: bool = False,
    ) -> dict[str, Any]:
        api = _memory_api()
        rule = self.rules[tier]
        if rule.days is None:
            raise api.MemoryError(f"永久层不应创建生命周期索引：{tier}")
        source = source_meta or {}
        created_at = api.parse_time(source.get("created_at")) or current
        content_updated_at = (
            current
            if content_changed
            else api.parse_time(
                source.get("content_updated_at") or source.get("updated_at")
            )
            or current
        )
        last_used_at = api.parse_time(source.get("last_used_at"))
        return {
            "weight": 0,
            "created_at": api.iso(created_at),
            "content_updated_at": api.iso(content_updated_at),
            "updated_at": api.iso(content_updated_at),
            "last_used_at": api.iso(last_used_at) if last_used_at else None,
            "last_weight_date": None,
            "tier_entered_at": api.iso(current),
            "expires_at": api.iso(current + timedelta(days=rule.days)),
        }

    def _insert_fragment(
        self,
        database: sqlite3.Connection,
        tier: str,
        filename: str,
        content: str,
        current: datetime,
        *,
        source_meta: dict[str, Any] | None = None,
    ) -> sqlite3.Row:
        api = _memory_api()
        normalized = api.normalize_memory_filename(filename)
        body = api._normalise_text(content)
        if not body:
            raise api.MemoryError("记忆内容不能为空")
        if api.contains_sensitive_credential(body):
            raise api.MemoryError("记忆内容包含疑似敏感凭据")
        if tier not in api.TIERS:
            raise api.MemoryError(f"未知记忆档位：{tier}")
        if self._row_by_filename(database, normalized) is not None:
            raise FileExistsError(f"同名记忆已存在：{normalized}")
        if tier == "permanent":
            timestamp = api.iso(current)
            meta = {
                "weight": 0,
                "created_at": timestamp,
                "content_updated_at": timestamp,
                "last_used_at": None,
                "last_weight_date": None,
                "tier_entered_at": timestamp,
                "expires_at": None,
            }
        else:
            meta = self._new_meta(tier, current, source_meta=source_meta)
        database.execute(
            """
            INSERT INTO memory_fragments(
                filename, filename_key, tier, content, content_hash, weight,
                created_at, content_updated_at, last_used_at, last_weight_date,
                tier_entered_at, expires_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized,
                _filename_key(normalized),
                tier,
                body,
                _hash(body),
                int(meta["weight"]),
                meta["created_at"],
                meta["content_updated_at"],
                meta["last_used_at"],
                meta["last_weight_date"],
                meta["tier_entered_at"],
                meta["expires_at"],
            ),
        )
        return self._row_by_filename(database, normalized, tier=tier)  # type: ignore[return-value]

    def create_fragment(
        self,
        tier: str,
        filename: str,
        content: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        api = _memory_api()
        current = now or api.utc_now()
        with self._lock, connection(self.root, self.user, write=True) as database:
            row = self._insert_fragment(database, tier, filename, content, current)
            return _entry_from_row(row)

    def locate(self, filename: str) -> Any | None:
        api = _memory_api()
        normalized = api.normalize_memory_filename(filename)
        with connection(self.root, self.user) as database:
            row = self._row_by_filename(database, normalized)
        if row is None:
            return None
        return api.MemoryLocation(str(row["tier"]), str(row["filename"]))

    def locate_in_tier(self, tier: str, filename: str) -> Any | None:
        api = _memory_api()
        normalized = api.normalize_memory_filename(filename)
        with connection(self.root, self.user) as database:
            row = self._row_by_filename(database, normalized, tier=tier)
        if row is None:
            return None
        return api.MemoryLocation(tier, str(row["filename"]))

    def get_entry(self, tier: str, filename: str) -> dict[str, Any] | None:
        with connection(self.root, self.user) as database:
            row = self._row_by_filename(database, filename, tier=tier)
        return _entry_from_row(row) if row is not None else None

    def _entry(
        self, location: Any, meta: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        del meta
        api = _memory_api()
        entry = self.get_entry(location.tier, location.filename)
        if entry is None:
            issue = f"missing_row:{location.tier}/{location.filename}"
            raise api.MemoryIntegrityError(
                issue, f"记忆表中不存在：{location.filename}"
            )
        return entry

    def load_tier(
        self, tier: str, *, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        del now
        api = _memory_api()
        if tier not in api.TIERS:
            raise api.MemoryError(f"未知记忆档位：{tier}")
        with connection(self.root, self.user) as database:
            rows = database.execute(
                "SELECT * FROM memory_fragments WHERE tier=? ORDER BY filename_key",
                (tier,),
            ).fetchall()
        return [_entry_from_row(row) for row in rows]

    def load_all(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        del now
        with connection(self.root, self.user) as database:
            rows = database.execute(
                """
                SELECT * FROM memory_fragments
                ORDER BY CASE tier
                    WHEN 'permanent' THEN 4 WHEN 'half_year' THEN 3
                    WHEN 'one_month' THEN 2 ELSE 1 END DESC,
                    weight DESC, filename_key
                """
            ).fetchall()
        return [_entry_from_row(row) for row in rows]

    def list_file_references(self) -> list[dict[str, Any]]:
        with connection(self.root, self.user) as database:
            rows = database.execute(
                """
                SELECT filename, tier, weight, content_updated_at
                FROM memory_fragments
                ORDER BY content_updated_at DESC, filename_key
                """
            ).fetchall()
        return [
            {
                "filename": str(row["filename"]),
                "tier": str(row["tier"]),
                "weight": int(row["weight"]),
                "updated_at": str(row["content_updated_at"]),
            }
            for row in rows
        ]

    def _touch_row(
        self,
        database: sqlite3.Connection,
        row: sqlite3.Row,
        current: datetime,
        *,
        content_changed: bool = False,
        reason: str = "reference",
        evidence_date: str | None = None,
    ) -> bool:
        api = _memory_api()
        if str(row["tier"]) == "permanent":
            return False
        day = evidence_date or api.local_day(current)
        inserted = (
            database.execute(
                """
            INSERT OR IGNORE INTO memory_weight_events(
                fragment_id, evidence_date, reason, created_at
            ) VALUES(?, ?, ?, ?)
            """,
                (int(row["id"]), day, reason, api.iso(current)),
            ).rowcount
            > 0
        )
        assignments = ["last_used_at=?", "revision=revision+1"]
        params: list[Any] = [api.iso(current)]
        if inserted:
            assignments.extend(("weight=weight+1", "last_weight_date=MAX(COALESCE(last_weight_date, ''), ?)"))
            params.append(day)
        if content_changed:
            assignments.append("content_updated_at=?")
            params.append(api.iso(current))
        params.append(int(row["id"]))
        database.execute(
            f"UPDATE memory_fragments SET {', '.join(assignments)} WHERE id=?",
            params,
        )
        return inserted

    def _touch_temporary(
        self,
        location: Any,
        current: datetime,
        *,
        content_changed: bool = False,
    ) -> bool:
        api = _memory_api()
        with self._lock, connection(self.root, self.user, write=True) as database:
            row = self._row_by_filename(database, location.filename, tier=location.tier)
            if row is None:
                raise api.MemoryError(f"临时记忆不存在：{location.filename}")
            return self._touch_row(
                database,
                row,
                current,
                content_changed=content_changed,
                reason="content_update" if content_changed else "reference",
            )

    def _delete_location(self, location: Any) -> None:
        with self._lock, connection(self.root, self.user, write=True) as database:
            database.execute(
                "DELETE FROM memory_fragments WHERE filename_key=? AND tier=?",
                (_filename_key(location.filename), location.tier),
            )

    def delete_fragment(self, tier: str, filename: str) -> bool:
        with self._lock, connection(self.root, self.user, write=True) as database:
            return (
                database.execute(
                    "DELETE FROM memory_fragments WHERE filename_key=? AND tier=?",
                    (_filename_key(filename), tier),
                ).rowcount
                > 0
            )

    def edit_fragment(
        self,
        tier: str,
        filename: str,
        content: str,
        *,
        new_filename: str | None = None,
        now: datetime | None = None,
    ) -> str:
        api = _memory_api()
        current = now or api.utc_now()
        body = api._normalise_text(content)
        if not body:
            raise api.MemoryError("记忆内容不能为空")
        if api.contains_sensitive_credential(body):
            raise api.MemoryError("记忆内容包含疑似敏感凭据")
        source_name = api.normalize_memory_filename(filename)
        target_name = api.normalize_memory_filename(new_filename or source_name)
        with self._lock, connection(self.root, self.user, write=True) as database:
            row = self._row_by_filename(database, source_name, tier=tier)
            if row is None:
                raise FileNotFoundError(f"记忆不存在：{tier}/{source_name}")
            conflict = self._row_by_filename(database, target_name)
            if conflict is not None and int(conflict["id"]) != int(row["id"]):
                raise FileExistsError(f"目标记忆已存在：{target_name}")
            database.execute(
                """
                UPDATE memory_fragments SET filename=?, filename_key=?, content=?,
                    content_hash=?, content_updated_at=?, revision=revision+1
                WHERE id=?
                """,
                (
                    target_name,
                    _filename_key(target_name),
                    body,
                    _hash(body),
                    api.iso(current),
                    int(row["id"]),
                ),
            )
            if tier != "permanent":
                refreshed = database.execute(
                    "SELECT * FROM memory_fragments WHERE id=?", (int(row["id"]),)
                ).fetchone()
                self._touch_row(
                    database,
                    refreshed,
                    current,
                    reason="content_update",
                )
        return target_name
