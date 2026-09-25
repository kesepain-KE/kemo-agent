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

class ImportantStoreMixin:
    def _important_view_snapshot(
        self, *, now: datetime | None = None,
    ) -> tuple[dict[str, Any], list[sqlite3.Row]]:
        api = _memory_api()
        current = now or api.utc_now()
        with connection(self.root, self.user) as database:
            # Count and source rows must describe the same read-only snapshot.
            database.execute("BEGIN")
            count_row = database.execute(
                "SELECT value FROM memory_meta WHERE key='important_view_count'"
            ).fetchone()
            rows = database.execute(
                """
                SELECT fragment.id, fragment.filename, fragment.tier,
                       fragment.content_hash, fragment.expires_at,
                       source.content_hash AS expected_hash
                FROM memory_important_sources AS source
                LEFT JOIN memory_fragments AS fragment ON fragment.id=source.fragment_id
                ORDER BY fragment.filename_key
                """
            ).fetchall()
        if count_row is None:
            # Preserve legacy/manual files, but never claim verified provenance.
            return {
                "status": "untracked", "is_current": True,
                "reason_codes": ["sources_untracked"],
                "reason": "未记录来源，暂无法校验有效性。",
            }, rows
        reasons: dict[str, str] = {}
        try:
            expected_count = int(count_row["value"])
            if expected_count < 0:
                raise ValueError
        except (TypeError, ValueError):
            expected_count = len(rows)
            reasons["invalid_metadata"] = "来源记录异常"
        if len(rows) > expected_count:
            reasons["invalid_metadata"] = "来源记录异常"
        if len(rows) < expected_count or any(row["id"] is None for row in rows):
            reasons["source_missing"] = "来源记忆已删除或清理"
        for row in rows:
            if row["id"] is None:
                continue
            if str(row["tier"]) == "permanent":
                reasons["source_promoted"] = "来源已转为长期记忆"
            elif (expires_at := api.parse_time(row["expires_at"])) is None:
                reasons["invalid_metadata"] = "来源记录异常"
            elif expires_at <= current:
                reasons["source_expired"] = "来源记忆已到期"
            if str(row["content_hash"]) != str(row["expected_hash"]):
                reasons["source_changed"] = "来源内容已更新"
        return {
            "status": "invalid" if reasons else "valid",
            "is_current": not reasons,
            "reason_codes": list(reasons),
            "reason": "；".join(reasons.values()) + "。" if reasons else "来源校验通过。",
        }, rows

    def important_view_status(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Explain eligibility without exposing source content or changing weights."""
        status, _ = self._important_view_snapshot(now=now)
        return status

    def load_important_view_sources(self) -> frozenset[str]:
        status, rows = self._important_view_snapshot()
        if status["status"] != "valid":
            return frozenset()
        return frozenset(str(row["filename"]) for row in rows)

    def important_view_is_current(self) -> bool:
        return bool(self.important_view_status()["is_current"])

    def set_important_view_sources(
        self,
        filenames: list[str],
        *,
        now: datetime | None = None,
    ) -> list[str]:
        api = _memory_api()
        current = now or api.utc_now()
        normalized = list(
            dict.fromkeys(api.normalize_memory_filename(name) for name in filenames)
        )
        with self._lock, connection(self.root, self.user, write=True) as database:
            rows: list[sqlite3.Row] = []
            for filename in normalized:
                row = self._row_by_filename(database, filename)
                if row is None or str(row["tier"]) not in api.TEMPORARY_TIERS:
                    raise api.MemoryError(
                        f"临时重要记忆来源不是有效临时碎片：{filename}"
                    )
                rows.append(row)
            database.execute("DELETE FROM memory_important_sources")
            database.executemany(
                """
                INSERT INTO memory_important_sources(
                    fragment_id, content_hash, updated_at
                ) VALUES(?, ?, ?)
                """,
                [
                    (int(row["id"]), str(row["content_hash"]), api.iso(current))
                    for row in rows
                ],
            )
            database.execute(
                "INSERT OR REPLACE INTO memory_meta(key, value) VALUES('important_view_count', ?)",
                (str(len(rows)),),
            )
        return normalized

    def reconcile_important_memory(
        self,
        featured_names: list[str],
        actions: list[dict[str, Any]],
        *,
        now: datetime | None = None,
    ) -> None:
        api = _memory_api()
        current = now or api.utc_now()
        with self._lock, connection(self.root, self.user, write=True) as database:
            source_ids: set[int] = set()
            normalized_featured: list[sqlite3.Row] = []
            for name in featured_names:
                row = self._row_by_filename(database, name)
                if row is None or str(row["tier"]) not in api.TEMPORARY_TIERS:
                    raise api.MemoryError(f"临时重要记忆来源不存在：{name}")
                normalized_featured.append(row)
            for action in actions:
                source = self._row_by_filename(
                    database,
                    str(action["filename"]),
                    tier=str(action["tier"]),
                )
                target = self._row_by_filename(
                    database,
                    str(action["permanent_filename"]),
                    tier="permanent",
                )
                if source is None or target is None:
                    raise api.MemoryError("永久记忆协调来源或目标不存在")
                if int(source["id"]) in source_ids:
                    raise api.MemoryError("永久记忆协调来源重复")
                source_ids.add(int(source["id"]))
                if action["action"] == "merge_permanent":
                    content = str(action.get("content") or "").strip()
                    if not content or api.contains_sensitive_credential(content):
                        raise api.MemoryError("永久记忆融合内容无效")
                    database.execute(
                        """
                        UPDATE memory_fragments SET content=?, content_hash=?,
                            content_updated_at=?, revision=revision+1 WHERE id=?
                        """,
                        (content, _hash(content), api.iso(current), int(target["id"])),
                    )
                database.execute(
                    "DELETE FROM memory_fragments WHERE id=?", (int(source["id"]),)
                )
            surviving = [
                row for row in normalized_featured if int(row["id"]) not in source_ids
            ]
            database.execute("DELETE FROM memory_important_sources")
            database.executemany(
                """
                INSERT INTO memory_important_sources(
                    fragment_id, content_hash, updated_at
                ) VALUES(?, ?, ?)
                """,
                [
                    (int(row["id"]), str(row["content_hash"]), api.iso(current))
                    for row in surviving
                ],
            )
            database.execute(
                "INSERT OR REPLACE INTO memory_meta(key, value) VALUES('important_view_count', ?)",
                (str(len(surviving)),),
            )

    def integrity_issues(self) -> list[str]:
        api = _memory_api()
        issues: list[str] = []
        with connection(self.root, self.user) as database:
            meta = database.execute(
                "SELECT value FROM memory_meta WHERE key='schema_version'"
            ).fetchone()
            if meta is None:
                issues.append("missing_schema_version")
            elif str(meta["value"]) != str(api.MEMORY_SCHEMA_VERSION):
                issues.append(f"unsupported_schema_version:{meta['value']}")
            rows = database.execute(
                """
                SELECT filename, tier FROM memory_fragments
                WHERE content='' OR (tier='permanent' AND expires_at IS NOT NULL)
                   OR (tier!='permanent' AND expires_at IS NULL)
                """
            ).fetchall()
        issues.extend(f"invalid_row:{row['tier']}/{row['filename']}" for row in rows)
        return issues
