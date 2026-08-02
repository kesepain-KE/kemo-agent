"""SQLite-backed implementation of the tiered memory engine."""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Any

from run.memory_store import connection, database_path


def _memory_api() -> Any:
    # Imported lazily because run.memory installs this implementation after
    # defining the shared validation helpers and public data classes.
    from run import memory

    return memory


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _filename_key(filename: str) -> str:
    return filename.casefold()


class SqliteMemoryStore:
    def __init__(self, root: Path, user: str, config: dict[str, Any]) -> None:
        api = _memory_api()
        self.root = root.resolve()
        self.user = user
        self.config = config
        self.rules = api.tier_rules(config)
        self._lock = api._store_lock(self.root, user)

    def database_path(self) -> Path:
        return database_path(self.root, self.user)

    @staticmethod
    def _meta(row: sqlite3.Row) -> dict[str, Any]:
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

    @classmethod
    def _entry_from_row(cls, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "filename": str(row["filename"]),
            "content": str(row["content"]),
            "tier": str(row["tier"]),
            **cls._meta(row),
        }

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
            return self._entry_from_row(row)

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
        return self._entry_from_row(row) if row is not None else None

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
        return [self._entry_from_row(row) for row in rows]

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
        return [self._entry_from_row(row) for row in rows]

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
    ) -> bool:
        api = _memory_api()
        if str(row["tier"]) == "permanent":
            return False
        day = api.local_day(current)
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
            assignments.extend(("weight=weight+1", "last_weight_date=?"))
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

    def _promote_row(
        self,
        database: sqlite3.Connection,
        row: sqlite3.Row,
        target_tier: str,
        current: datetime,
        *,
        merged_content: str | None = None,
        target_filename: str | None = None,
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
        if target is not None and int(target["id"]) != int(row["id"]):
            if merged_content is None or str(target["tier"]) != target_tier:
                raise api.MemoryError(f"晋升目标已存在同名记忆：{target_name}")
            target_meta = self._new_meta(
                target_tier,
                current,
                source_meta=self._meta(target),
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
                source_meta=self._meta(row),
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
            )

    def _load_operation(
        self, database: sqlite3.Connection, operation_id: str
    ) -> dict[str, Any] | None:
        row = database.execute(
            "SELECT result_json FROM memory_operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            parsed = json.loads(str(row["result_json"]))
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _write_operation_result(
        self,
        database: sqlite3.Connection,
        operation_id: str,
        result: dict[str, Any],
        current: datetime,
    ) -> None:
        api = _memory_api()
        database.execute(
            """
            INSERT OR REPLACE INTO memory_operations(
                operation_id, completed_at, result_json
            ) VALUES(?, ?, ?)
            """,
            (
                operation_id,
                api.iso(current),
                json.dumps(result, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        limit = int(api.MEMORY_OPERATION_HISTORY_LIMIT)
        database.execute(
            """
            DELETE FROM memory_operations WHERE operation_id IN (
                SELECT operation_id FROM memory_operations
                ORDER BY completed_at DESC LIMIT -1 OFFSET ?
            )
            """,
            (limit,),
        )

    def upsert_candidates(
        self,
        candidates: list[dict[str, Any]],
        *,
        source: dict[str, Any] | None = None,
        operation_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        del source
        api = _memory_api()
        current = now or api.utc_now()
        created: list[str] = []
        updated: list[str] = []
        skipped_permanent: list[str] = []
        forgotten: list[str] = []
        rejected = 0
        normalized_operation_id = str(operation_id or "").strip()
        if len(normalized_operation_id) > 256:
            raise api.MemoryError("记忆批次 operation_id 不能超过 256 个字符")
        with self._lock, connection(self.root, self.user, write=True) as database:
            if normalized_operation_id:
                previous = self._load_operation(database, normalized_operation_id)
                if previous is not None:
                    return {
                        **previous,
                        "operation_id": normalized_operation_id,
                        "replayed": True,
                    }
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    rejected += 1
                    continue
                action = str(candidate.get("action") or "upsert").strip().casefold()
                raw_filename = candidate.get("filename") or candidate.get("target")
                content = api._normalise_text(candidate.get("content"))
                if action == "forget":
                    if raw_filename is None:
                        raw_filename = content
                    try:
                        filename = api.normalize_memory_filename(raw_filename)
                    except api.MemoryError:
                        rejected += 1
                        continue
                    row = self._row_by_filename(database, filename)
                    if row is not None:
                        forgotten.append(str(row["filename"]))
                        database.execute(
                            "DELETE FROM memory_fragments WHERE id=?", (int(row["id"]),)
                        )
                    continue
                if (
                    action != "upsert"
                    or not content
                    or api.contains_sensitive_credential(content)
                ):
                    rejected += 1
                    continue
                try:
                    filename = api.normalize_memory_filename(raw_filename or content)
                except api.MemoryError:
                    rejected += 1
                    continue
                explicit = bool(candidate.get("explicit", False))
                row = self._row_by_filename(database, filename)
                if row is None:
                    tier = "permanent" if explicit else "seven_days"
                    inserted = self._insert_fragment(
                        database, tier, filename, content, current
                    )
                    created.append(str(inserted["filename"]))
                    continue
                tier = str(row["tier"])
                changed = str(row["content"]) != content
                if tier == "permanent" and not explicit:
                    skipped_permanent.append(filename)
                    continue
                if changed:
                    database.execute(
                        """
                        UPDATE memory_fragments SET content=?, content_hash=?,
                            content_updated_at=?, revision=revision+1 WHERE id=?
                        """,
                        (content, _hash(content), api.iso(current), int(row["id"])),
                    )
                if explicit and tier != "permanent":
                    refreshed = database.execute(
                        "SELECT * FROM memory_fragments WHERE id=?", (int(row["id"]),)
                    ).fetchone()
                    if changed:
                        self._touch_row(
                            database,
                            refreshed,
                            current,
                            reason="content_update",
                        )
                        refreshed = database.execute(
                            "SELECT * FROM memory_fragments WHERE id=?",
                            (int(row["id"]),),
                        ).fetchone()
                    self._promote_row(database, refreshed, "permanent", current)
                elif tier != "permanent":
                    refreshed = database.execute(
                        "SELECT * FROM memory_fragments WHERE id=?", (int(row["id"]),)
                    ).fetchone()
                    self._touch_row(
                        database,
                        refreshed,
                        current,
                        reason="content_update" if changed else "reference",
                    )
                updated.append(filename)
            result = {
                "created": created,
                "updated": updated,
                "skipped_permanent": skipped_permanent,
                "forgotten": forgotten,
                "rejected": rejected,
            }
            if normalized_operation_id:
                self._write_operation_result(
                    database, normalized_operation_id, result, current
                )
                return {
                    **result,
                    "operation_id": normalized_operation_id,
                    "replayed": False,
                }
            return result

    def forget(self, query: str) -> list[str]:
        api = _memory_api()
        try:
            filename = api.normalize_memory_filename(query)
        except api.MemoryError:
            return []
        with self._lock, connection(self.root, self.user, write=True) as database:
            row = self._row_by_filename(database, filename)
            if row is None:
                return []
            database.execute(
                "DELETE FROM memory_fragments WHERE id=?", (int(row["id"]),)
            )
            return [str(row["filename"])]

    def review_due(self, *, now: datetime | None = None) -> dict[str, list[str]]:
        api = _memory_api()
        current = now or api.utc_now()
        upgraded: list[str] = []
        deleted: list[str] = []
        with self._lock, connection(self.root, self.user, write=True) as database:
            rows = database.execute(
                """
                SELECT * FROM memory_fragments
                WHERE tier != 'permanent' AND expires_at <= ?
                ORDER BY expires_at, filename_key
                """,
                (api.iso(current),),
            ).fetchall()
            for row in rows:
                tier = str(row["tier"])
                filename = str(row["filename"])
                rule = self.rules[tier]
                if int(row["weight"]) >= int(rule.upgrade_threshold or 0):
                    if rule.next is None:
                        raise api.MemoryConfigError(f"临时记忆层缺少晋升目标：{tier}")
                    self._promote_row(database, row, rule.next, current)
                    upgraded.append(filename)
                else:
                    database.execute(
                        "DELETE FROM memory_fragments WHERE id=?", (int(row["id"]),)
                    )
                    deleted.append(filename)
        return {"upgraded": upgraded, "deleted": deleted}

    def search(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        api = _memory_api()
        query_key = api._key(query)
        query_tokens = api._tokens(query)
        tier_rank = {"seven_days": 1, "one_month": 2, "half_year": 3, "permanent": 4}
        with connection(self.root, self.user) as database:
            rows = database.execute("SELECT * FROM memory_fragments").fetchall()
        scored: list[tuple[float, sqlite3.Row]] = []
        for row in rows:
            title = Path(str(row["filename"])).stem
            title_key = api._key(title)
            title_tokens = api._tokens(title)
            overlap = len(query_tokens & title_tokens)
            substring = (
                2
                if query_key and (query_key in title_key or title_key in query_key)
                else 0
            )
            if overlap == 0 and substring == 0:
                continue
            relevance = substring + overlap / max(
                1, math.sqrt(len(query_tokens) * len(title_tokens))
            )
            score = (
                relevance * 10
                + tier_rank[str(row["tier"])]
                + min(int(row["weight"]), 1000) / 1000
            )
            scored.append((score, row))
        scored.sort(key=lambda pair: (-pair[0], str(pair[1]["filename"]).casefold()))
        return [
            dict(self._entry_from_row(row), _score=score)
            for score, row in scored[: max(0, limit)]
        ]

    def select_tier_for_prompt(
        self,
        tier: str,
        *,
        max_files: int | None,
        mode: str = "full",
    ) -> Any:
        api = _memory_api()
        if tier not in api.TIERS:
            raise api.MemoryError(f"未知记忆档位：{tier}")
        if mode != "full":
            raise api.MemoryConfigError(f"{tier} 记忆注入模式暂不支持：{mode}")
        if max_files is not None and (
            isinstance(max_files, bool)
            or not isinstance(max_files, int)
            or max_files < 0
        ):
            raise api.MemoryConfigError(f"{tier} 记忆文件上限必须是非负整数或 null")
        featured = (
            self.load_important_view_sources() if tier != "permanent" else frozenset()
        )
        with connection(self.root, self.user) as database:
            rows = database.execute(
                """
                SELECT * FROM memory_fragments WHERE tier=?
                ORDER BY weight DESC, filename_key
                """,
                (tier,),
            ).fetchall()
        eligible = [row for row in rows if str(row["filename"]) not in featured]
        selected_rows = (
            eligible
            if tier == "permanent" or max_files is None
            else eligible[:max_files]
        )
        selected = [self._entry_from_row(row) for row in selected_rows]

        def line(item: dict[str, Any]) -> str:
            if tier == "permanent":
                return f"- [{item['filename']}] {item['content']}"
            return f"- [{item['filename']}] (weight={item['weight']}) {item['content']}"

        text = "\n".join(line(item) for item in selected)
        return api.TierPromptSelection(
            tier=tier,
            items=tuple(selected),
            text=text,
            selected_ids=tuple(str(item["filename"]) for item in selected),
            original_chars=sum(len(str(row["content"])) for row in eligible),
            injected_chars=len(text),
            original_items=len(eligible),
            injected_items=len(selected),
            truncated=len(selected) < len(eligible),
            source_files=(self.database_path(),) if selected else (),
            integrity_warnings=(),
        )

    def mark_used(
        self, filenames: list[str], *, now: datetime | None = None
    ) -> list[str]:
        if not filenames:
            return []
        api = _memory_api()
        current = now or api.utc_now()
        changed: list[str] = []
        with self._lock, connection(self.root, self.user, write=True) as database:
            for raw_filename in dict.fromkeys(filenames):
                try:
                    row = self._row_by_filename(database, raw_filename)
                except api.MemoryError:
                    continue
                if row is None or str(row["tier"]) == "permanent":
                    continue
                if self._touch_row(database, row, current, reason="history_reference"):
                    changed.append(str(row["filename"]))
        return changed

    def list_items(self) -> list[dict[str, Any]]:
        return self.load_all()

    def load_important_view_sources(self) -> frozenset[str]:
        with connection(self.root, self.user) as database:
            count_row = database.execute(
                "SELECT value FROM memory_meta WHERE key='important_view_count'"
            ).fetchone()
            rows = database.execute(
                """
                SELECT fragment.filename, fragment.tier, fragment.content_hash,
                       source.content_hash AS expected_hash
                FROM memory_important_sources AS source
                JOIN memory_fragments AS fragment ON fragment.id=source.fragment_id
                ORDER BY fragment.filename_key
                """
            ).fetchall()
        expected_count = int(count_row["value"]) if count_row is not None else 0
        if len(rows) != expected_count:
            return frozenset()
        if any(
            str(row["tier"]) == "permanent"
            or str(row["content_hash"]) != str(row["expected_hash"])
            for row in rows
        ):
            return frozenset()
        return frozenset(str(row["filename"]) for row in rows)

    def important_view_is_current(self) -> bool:
        with connection(self.root, self.user) as database:
            count_row = database.execute(
                "SELECT value FROM memory_meta WHERE key='important_view_count'"
            ).fetchone()
            mismatch = database.execute(
                """
                SELECT COUNT(*) FROM memory_important_sources AS source
                LEFT JOIN memory_fragments AS fragment ON fragment.id=source.fragment_id
                WHERE fragment.id IS NULL OR fragment.tier='permanent'
                   OR fragment.content_hash != source.content_hash
                """
            ).fetchone()[0]
            actual = database.execute(
                "SELECT COUNT(*) FROM memory_important_sources"
            ).fetchone()[0]
        if count_row is None:
            return True
        return int(actual) == int(count_row["value"]) and int(mismatch) == 0

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
