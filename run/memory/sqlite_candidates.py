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

class CandidateStoreMixin:
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

    def _weight_evidence(self, database, row, candidate, source, current, *, created=False):
        """Use only dates supplied by the history host, never model date fields."""
        api = _memory_api()
        if row["tier"] == "permanent":
            return [], [], []
        if "evidence_dates" not in source:
            if created:
                return [], [], []
            added = self._touch_row(database, row, current, reason="reference")
            return ([api.local_day(current)] if added else [],
                    [] if added else [api.local_day(current)], [])
        mapping = source["evidence_dates"]
        valid_dates = set()
        missing_date = False
        for number in candidate.get("evidence_rounds", []):
            day = mapping.get(str(number)) if isinstance(mapping, dict) else None
            try:
                if isinstance(day, str) and date.fromisoformat(day).isoformat() == day and day <= api.local_day(current):
                    valid_dates.add(day)
                else:
                    missing_date = True
            except ValueError:
                missing_date = True
        dates = sorted(valid_dates)
        if not dates:
            return [], [], ["missing_evidence_date"]
        baseline = database.execute(
            "SELECT evidence_date FROM memory_weight_events WHERE fragment_id=? AND reason='creation'",
            (row["id"],),
        ).fetchone()
        if created:
            # Initial observation creates the fragment at weight zero. Reserve
            # its day so replay/another batch cannot turn creation into +1.
            database.execute(
                "INSERT OR IGNORE INTO memory_weight_events VALUES(?, ?, 'creation', ?)",
                (row["id"], dates[0], api.iso(current)),
            )
            floor = dates[0]
        else:
            floor = (baseline[0] if baseline else
                     api.local_day(api.parse_time(row["tier_entered_at"]) or current))
        weighted, locked = [], []
        skipped = ["missing_evidence_date"] if missing_date else []
        for day in dates:
            if day < floor or day > api.local_day(current):
                skipped.append("outside_tier_evidence_window")
                continue
            if self._touch_row(database, row, current, reason="history_evidence", evidence_date=day):
                weighted.append(day)
            else:
                locked.append(day)
        return weighted, locked, skipped

    def upsert_candidates(
        self,
        candidates: list[dict[str, Any]],
        *,
        source: dict[str, Any] | None = None,
        operation_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        source = source or {}
        api = _memory_api()
        current = now or api.utc_now()
        created: list[str] = []
        updated: list[str] = []
        skipped_permanent: list[str] = []
        forgotten: list[str] = []
        rejected = 0
        weighted: list[str] = []
        daily_locked: list[str] = []
        content_updated: list[str] = []
        weight_events: list[dict[str, Any]] = []
        weight_skipped: list[dict[str, Any]] = []
        rejection_reasons: list[dict[str, Any]] = []
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
                ref = candidate.get("memory_ref")
                if ref:
                    try:
                        ref_tier, ref_name = str(ref).split(":", 1)
                        referenced = self._row_by_filename(database, ref_name, tier=ref_tier)
                        if referenced is None or (raw_filename and api.normalize_memory_filename(raw_filename) != ref_name):
                            raise ValueError("stale_memory_ref")
                        expected = candidate.get("expected_content_hash")
                        if expected and expected != referenced["content_hash"]:
                            raise ValueError("stale_memory_content")
                    except (ValueError, api.MemoryError) as exc:
                        rejected += 1
                        rejection_reasons.append({"memory_ref": ref, "reason": str(exc)})
                        continue
                    raw_filename = ref_name
                    if action == "reinforce":
                        content = str(referenced["content"])
                elif action in {"reinforce", "revise"}:
                    rejected += 1
                    rejection_reasons.append({"reason": "missing_memory_ref"})
                    continue
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
                    action not in {"upsert", "create", "reinforce", "revise"}
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
                explicit = candidate.get("explicit") is True
                row = self._row_by_filename(database, filename)
                if action == "create" and row is not None:
                    rejected += 1
                    rejection_reasons.append({"filename": filename, "reason": "create_conflict"})
                    continue
                if row is None:
                    tier = "permanent" if explicit else "seven_days"
                    inserted = self._insert_fragment(
                        database, tier, filename, content, current
                    )
                    created.append(str(inserted["filename"]))
                    added, locked, skipped = self._weight_evidence(
                        database, inserted, candidate, source, current, created=True)
                    if added:
                        weighted.append(filename)
                    if locked:
                        daily_locked.append(filename)
                    weight_events.extend({"filename": filename, "evidence_date": d} for d in added)
                    weight_skipped.extend({"filename": filename, "reason": r} for r in sorted(set(skipped)))
                    continue
                tier = str(row["tier"])
                changed = action != "reinforce" and str(row["content"]) != content
                if tier == "permanent" and not explicit:
                    skipped_permanent.append(filename)
                    continue
                if changed:
                    content_updated.append(filename)
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
                    added, locked, skipped = self._weight_evidence(
                        database, refreshed, candidate, source, current)
                    if added:
                        weighted.append(filename)
                    if locked:
                        daily_locked.append(filename)
                    weight_events.extend({"filename": filename, "evidence_date": d} for d in added)
                    weight_skipped.extend({"filename": filename, "reason": r} for r in sorted(set(skipped)))
                updated.append(filename)
            result = {
                "created": created,
                "updated": updated,
                "skipped_permanent": skipped_permanent,
                "forgotten": forgotten,
                "rejected": rejected,
                "weighted": sorted(set(weighted)),
                "daily_locked": sorted(set(daily_locked)),
                "content_updated": sorted(set(content_updated)),
                "weight_events": weight_events,
                "weight_skipped": weight_skipped,
                "rejection_reasons": rejection_reasons,
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
            dict(_entry_from_row(row), _score=score)
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
        with connection(self.root, self.user) as database:
            rows = database.execute(
                """
                SELECT * FROM memory_fragments WHERE tier=?
                ORDER BY weight DESC, filename_key
                """,
                (tier,),
            ).fetchall()
        selected_rows = (
            rows
            if tier == "permanent" or max_files is None
            else rows[:max_files]
        )
        selected = [_entry_from_row(row) for row in selected_rows]

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
            original_chars=sum(len(str(row["content"])) for row in rows),
            injected_chars=len(text),
            original_items=len(rows),
            injected_items=len(selected),
            truncated=len(selected) < len(rows),
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

    def weight_status(self, tier: str, filename: str, *, now: datetime | None = None) -> dict[str, Any]:
        """Today's evidence lock is not necessarily an actual +1 (creation is zero)."""
        api = _memory_api()
        day = api.local_day(now or api.utc_now())
        with connection(self.root, self.user) as database:
            row = self._row_by_filename(database, filename, tier=tier)
            event = database.execute(
                "SELECT reason FROM memory_weight_events WHERE fragment_id=? AND evidence_date=?",
                (row["id"], day),
            ).fetchone() if row is not None else None
        return {"weighted_today": event is not None and event["reason"] != "creation",
                "weight_locked_today": event is not None, "weight_day": day,
                "weight_timezone": "Asia/Shanghai"}

    def weight_activity(self, start: datetime, end: datetime) -> dict[str, dict[str, Any]]:
        """Actual increments applied in a processing interval, excluding creation."""
        api = _memory_api()
        with connection(self.root, self.user) as database:
            rows = database.execute(
                """SELECT f.filename, MAX(e.created_at) AS applied_at, COUNT(*) AS increments
                   FROM memory_weight_events e JOIN memory_fragments f ON f.id=e.fragment_id
                   WHERE e.reason != 'creation' AND e.created_at >= ? AND e.created_at < ?
                   GROUP BY f.id""", (api.iso(start), api.iso(end)),
            ).fetchall()
        return {row["filename"]: {"applied_at": row["applied_at"], "increments": row["increments"]}
                for row in rows}
