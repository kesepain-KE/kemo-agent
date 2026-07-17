"""Tiered user-memory storage, review, retrieval and usage weighting."""

from __future__ import annotations

import json
import math
import os
import re
import threading
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


MEMORY_SCHEMA_VERSION = 1
TIERS = ("seven_days", "one_month", "half_year", "permanent")
DEFAULT_TIERS = {
    "seven_days": {"days": 7, "upgrade_threshold": 3, "next": "one_month"},
    "one_month": {"days": 30, "upgrade_threshold": 10, "next": "half_year"},
    "half_year": {"days": 180, "upgrade_threshold": 60, "next": "permanent"},
    "permanent": {"days": None, "upgrade_threshold": None, "next": None},
}
_WORD_RE = re.compile(r"[A-Za-z0-9_\-]+|[\u4e00-\u9fff]")
_SECRET_RE = re.compile(
    r"(?i)(?:api[_ -]?key|token|password|passwd|secret|cookie|private[_ -]?key|验证码|密码|密钥|令牌)"
    r"\s*(?::|=|：|是|为|is)\s*[^\s,;，；]{4,}|\bsk-[A-Za-z0-9_-]{8,}\b|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
_STORE_LOCKS: dict[tuple[str, str], threading.RLock] = {}
_STORE_LOCKS_GUARD = threading.Lock()


def _store_lock(root: Path, user: str) -> threading.RLock:
    key = (str(root.resolve()).casefold(), user)
    with _STORE_LOCKS_GUARD:
        return _STORE_LOCKS.setdefault(key, threading.RLock())


class MemoryError(RuntimeError):
    pass


class MemoryConfigError(MemoryError):
    pass


@dataclass(frozen=True, slots=True)
class TierRule:
    name: str
    days: int | None
    upgrade_threshold: int | None
    next: str | None


@dataclass(frozen=True, slots=True)
class MemorySelection:
    items: list[dict[str, Any]]
    text: str
    candidate_ids: list[str]
    selected_ids: list[str]
    chars: int


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def local_day(value: datetime | None = None) -> str:
    return (value or utc_now()).astimezone().date().isoformat()


def tier_rules(config: dict[str, Any]) -> dict[str, TierRule]:
    raw = (config.get("memory") or {}).get("tiers", DEFAULT_TIERS)
    if not isinstance(raw, dict):
        raise MemoryConfigError("memory.tiers 必须是对象")
    rules: dict[str, TierRule] = {}
    for name in TIERS:
        item = raw.get(name)
        if not isinstance(item, dict):
            raise MemoryConfigError(f"memory.tiers.{name} 未配置")
        days = item.get("days")
        threshold = item.get("upgrade_threshold")
        next_name = item.get("next")
        if days is not None and (not isinstance(days, int) or isinstance(days, bool) or days <= 0):
            raise MemoryConfigError(f"memory.tiers.{name}.days 必须是正整数或 null")
        if threshold is not None and (
            not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 0
        ):
            raise MemoryConfigError(
                f"memory.tiers.{name}.upgrade_threshold 必须是非负整数或 null"
            )
        if next_name is not None and next_name not in TIERS:
            raise MemoryConfigError(f"memory.tiers.{name}.next 无效")
        rules[name] = TierRule(name, days, threshold, next_name)
    if [rules[name].next for name in TIERS] != ["one_month", "half_year", "permanent", None]:
        raise MemoryConfigError("记忆档位升级路径必须是 seven_days→one_month→half_year→permanent")
    return rules


def _normalise_text(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).strip().split())


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", _normalise_text(value).casefold())


def contains_sensitive_credential(text: str) -> bool:
    return bool(_SECRET_RE.search(text))


def _tokens(text: str) -> set[str]:
    normal = unicodedata.normalize("NFKC", text).casefold()
    words = _WORD_RE.findall(normal)
    result = set(words)
    # Chinese character bigrams improve deterministic retrieval without an
    # embedding dependency.
    chinese = "".join(item for item in words if len(item) == 1 and "\u4e00" <= item <= "\u9fff")
    result.update(chinese[index : index + 2] for index in range(max(0, len(chinese) - 1)))
    return {item for item in result if item}


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class MemoryStore:
    def __init__(self, root: Path, user: str, config: dict[str, Any]) -> None:
        self.root = root.resolve()
        self.user = user
        self.config = config
        self.rules = tier_rules(config)
        self.base = self.root / "users" / user / "improve"
        self._lock = _store_lock(self.root, user)

    def path(self, tier: str) -> Path:
        if tier not in TIERS:
            raise MemoryError(f"未知记忆档位：{tier}")
        return self.base / tier / "data.json"

    def _migrate(self, raw: Any, tier: str, now: datetime) -> list[dict[str, Any]]:
        if isinstance(raw, dict):
            raw = raw.get("items", [])
        if not isinstance(raw, list):
            raise MemoryError(f"记忆文件根节点必须是数组：{self.path(tier)}")
        result = []
        for value in raw:
            if isinstance(value, str):
                value = {"content": value}
            if not isinstance(value, dict):
                continue
            content = _normalise_text(value.get("content") or value.get("text"))
            if not content or contains_sensitive_credential(content):
                continue
            created = parse_time(value.get("created_at")) or now
            entered = parse_time(value.get("tier_entered_at")) or created
            rule = self.rules[tier]
            review = parse_time(value.get("review_at"))
            if review is None and rule.days is not None:
                review = entered + timedelta(days=rule.days)
            result.append(
                {
                    "schema_version": MEMORY_SCHEMA_VERSION,
                    "id": str(value.get("id") or uuid.uuid4().hex),
                    "content": content,
                    "type": str(value.get("type") or "fact"),
                    "keywords": sorted({_normalise_text(item) for item in value.get("keywords", []) if _normalise_text(item)}),
                    "entities": sorted({_normalise_text(item) for item in value.get("entities", []) if _normalise_text(item)}),
                    "source": dict(value.get("source") or {}),
                    "confidence": min(1.0, max(0.0, float(value.get("confidence", 0.5)))),
                    "importance": min(1.0, max(0.0, float(value.get("importance", 0.5)))),
                    "status": str(value.get("status") or "active"),
                    "tier": tier,
                    "tier_weight": max(0, int(value.get("tier_weight", value.get("weight", 0)))),
                    "tier_entered_at": iso(entered),
                    "review_at": iso(review) if review is not None else None,
                    "last_weight_date": value.get("last_weight_date"),
                    "created_at": iso(created),
                    "updated_at": str(value.get("updated_at") or iso(created)),
                    "explicit": bool(value.get("explicit", tier == "permanent")),
                    "version": max(1, int(value.get("version", 1))),
                }
            )
        return result

    def load_tier(self, tier: str, *, now: datetime | None = None) -> list[dict[str, Any]]:
        current = now or utc_now()
        path = self.path(tier)
        try:
            text = path.read_text("utf-8")
            # A zero-byte tier file is the common result of an interrupted
            # first-run bootstrap and is semantically equivalent to no items.
            if not text.strip():
                return []
            raw = json.loads(text)
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError) as exc:
            raise MemoryError(f"记忆文件不可读：{path}（{exc}）") from exc
        return self._migrate(raw, tier, current)

    def load_all(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        current = now or utc_now()
        seen: set[str] = set()
        result = []
        # Higher tier wins if a crash left the same ID in two files.
        for tier in reversed(TIERS):
            for item in self.load_tier(tier, now=current):
                if item["id"] not in seen:
                    seen.add(item["id"])
                    result.append(item)
        return result

    def _write_partition(self, items: Iterable[dict[str, Any]]) -> None:
        partition = {tier: [] for tier in TIERS}
        for item in items:
            partition[item["tier"]].append(item)
        for tier in TIERS:
            partition[tier].sort(key=lambda item: (item["created_at"], item["id"]))
            _atomic_json(self.path(tier), partition[tier])

    def upsert_candidates(
        self,
        candidates: list[dict[str, Any]],
        *,
        source: dict[str, Any],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = now or utc_now()
        with self._lock:
            items = self.load_all(now=current)
            created: list[str] = []
            updated: list[str] = []
            forgotten: list[str] = []
            rejected = 0
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    rejected += 1
                    continue
                action = str(candidate.get("action") or "upsert")
                target = _normalise_text(candidate.get("target"))
                content = _normalise_text(candidate.get("content"))
                if action == "forget":
                    query = target or content
                    removed = self._remove_matches(items, query)
                    forgotten.extend(removed)
                    continue
                if not content or contains_sensitive_credential(content):
                    rejected += 1
                    continue
                explicit = bool(candidate.get("explicit", False))
                tier = "permanent" if explicit else "seven_days"
                match = self._find_duplicate(items, content, candidate.get("keywords") or [])
                if match is not None:
                    match["content"] = content
                    match["type"] = str(candidate.get("type") or match["type"])
                    match["keywords"] = sorted(set(match["keywords"]) | {_normalise_text(item) for item in candidate.get("keywords", []) if _normalise_text(item)})
                    match["entities"] = sorted(set(match["entities"]) | {_normalise_text(item) for item in candidate.get("entities", []) if _normalise_text(item)})
                    match["confidence"] = max(match["confidence"], float(candidate.get("confidence", 0.5)))
                    match["importance"] = max(match["importance"], float(candidate.get("importance", 0.5)))
                    match["source"] = dict(source)
                    match["updated_at"] = iso(current)
                    match["version"] += 1
                    if explicit and match["tier"] != "permanent":
                        match["tier"] = "permanent"
                        match["tier_weight"] = 0
                        match["tier_entered_at"] = iso(current)
                        match["review_at"] = None
                        match["last_weight_date"] = None
                        match["explicit"] = True
                    updated.append(match["id"])
                    continue
                rule = self.rules[tier]
                review = current + timedelta(days=rule.days) if rule.days is not None else None
                item = {
                    "schema_version": MEMORY_SCHEMA_VERSION,
                    "id": uuid.uuid4().hex,
                    "content": content,
                    "type": str(candidate.get("type") or "fact"),
                    "keywords": sorted({_normalise_text(item) for item in candidate.get("keywords", []) if _normalise_text(item)}),
                    "entities": sorted({_normalise_text(item) for item in candidate.get("entities", []) if _normalise_text(item)}),
                    "source": dict(source),
                    "confidence": min(1.0, max(0.0, float(candidate.get("confidence", 0.5)))),
                    "importance": min(1.0, max(0.0, float(candidate.get("importance", 0.5)))),
                    "status": "active",
                    "tier": tier,
                    "tier_weight": 0,
                    "tier_entered_at": iso(current),
                    "review_at": iso(review) if review else None,
                    "last_weight_date": None,
                    "created_at": iso(current),
                    "updated_at": iso(current),
                    "explicit": explicit,
                    "version": 1,
                }
                items.append(item)
                created.append(item["id"])
            self._write_partition(items)
            return {"created": created, "updated": updated, "forgotten": forgotten, "rejected": rejected}

    def _find_duplicate(self, items: list[dict[str, Any]], content: str, keywords: list[Any]) -> dict[str, Any] | None:
        key = _key(content)
        wanted = {_key(item) for item in keywords if _key(item)}
        for item in items:
            if _key(item["content"]) == key:
                return item
            existing = {_key(value) for value in item.get("keywords", []) if _key(value)}
            if wanted and len(wanted & existing) / max(1, len(wanted | existing)) >= 0.8:
                return item
        return None

    def _remove_matches(self, items: list[dict[str, Any]], query: str) -> list[str]:
        needle = _key(query)
        if not needle:
            return []
        removed = [item["id"] for item in items if item["id"] == query or needle in _key(item["content"]) or any(needle in _key(key) for key in item.get("keywords", []))]
        items[:] = [item for item in items if item["id"] not in set(removed)]
        return removed

    def forget(self, query: str) -> list[str]:
        with self._lock:
            items = self.load_all()
            removed = self._remove_matches(items, query)
            if removed:
                self._write_partition(items)
            return removed

    def review_due(self, *, now: datetime | None = None) -> dict[str, list[str]]:
        current = now or utc_now()
        with self._lock:
            items = self.load_all(now=current)
            upgraded: list[str] = []
            deleted: list[str] = []
            kept: list[dict[str, Any]] = []
            for item in items:
                rule = self.rules[item["tier"]]
                due = parse_time(item.get("review_at"))
                if due is None or due > current or rule.next is None:
                    kept.append(item)
                    continue
                if item["tier_weight"] >= int(rule.upgrade_threshold or 0):
                    next_rule = self.rules[rule.next]
                    item["tier"] = rule.next
                    item["tier_weight"] = 0
                    item["tier_entered_at"] = iso(current)
                    item["review_at"] = (
                        iso(current + timedelta(days=next_rule.days))
                        if next_rule.days is not None
                        else None
                    )
                    item["last_weight_date"] = None
                    item["updated_at"] = iso(current)
                    item["version"] += 1
                    kept.append(item)
                    upgraded.append(item["id"])
                else:
                    deleted.append(item["id"])
            if upgraded or deleted:
                self._write_partition(kept)
            return {"upgraded": upgraded, "deleted": deleted}

    def search(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        query_key = _key(query)
        query_tokens = _tokens(query)
        tier_rank = {"seven_days": 1, "one_month": 2, "half_year": 3, "permanent": 4}
        scored = []
        for item in self.load_all():
            haystack = " ".join([item["content"], *item.get("keywords", []), *item.get("entities", [])])
            item_tokens = _tokens(haystack)
            overlap = len(query_tokens & item_tokens)
            substring = 2 if query_key and query_key in _key(haystack) else 0
            if overlap == 0 and substring == 0:
                continue
            relevance = substring + overlap / max(1, math.sqrt(len(query_tokens) * len(item_tokens)))
            score = relevance * 10 + tier_rank[item["tier"]] + item["importance"] + min(item["tier_weight"], 1000) / 1000
            scored.append((score, item))
        scored.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
        return [dict(item, _score=score) for score, item in scored[: max(0, limit)]]

    def select_for_injection(
        self,
        query: str,
        *,
        max_chars: int | None = None,
        max_items: int | None = None,
    ) -> MemorySelection:
        memory_config = self.config.get("memory") or {}
        char_budget = int(max_chars if max_chars is not None else memory_config.get("injection_max_chars", 2000))
        item_budget = int(max_items if max_items is not None else memory_config.get("injection_max_items", 8))
        candidates = self.search(query, limit=max(item_budget * 4, item_budget))
        selected = []
        lines = []
        used = 0
        for item in candidates:
            line = f"- [{item['id']}] ({item['tier']}, weight={item['tier_weight']}) {item['content']}"
            extra = len(line) + (1 if lines else 0)
            if len(selected) >= item_budget or used + extra > char_budget:
                continue
            selected.append(item)
            lines.append(line)
            used += extra
        text = ""
        if lines:
            text = (
                "以下是与当前请求相关的用户记忆，可能过期；当前用户指令和当前事实优先：\n"
                + "\n".join(lines)
            )
        return MemorySelection(
            items=selected,
            text=text,
            candidate_ids=[item["id"] for item in candidates],
            selected_ids=[item["id"] for item in selected],
            chars=len(text),
        )

    def mark_used(self, ids: list[str], *, now: datetime | None = None) -> list[str]:
        if not ids:
            return []
        current = now or utc_now()
        day = local_day(current)
        wanted = set(ids)
        with self._lock:
            items = self.load_all(now=current)
            changed = []
            for item in items:
                if item["id"] in wanted and item.get("last_weight_date") != day:
                    item["tier_weight"] += 1
                    item["last_weight_date"] = day
                    item["updated_at"] = iso(current)
                    item["version"] += 1
                    changed.append(item["id"])
            if changed:
                self._write_partition(items)
            return changed

    def list_items(self) -> list[dict[str, Any]]:
        with self._lock:
            items = self.load_all()
            rank = {tier: index for index, tier in enumerate(TIERS)}
            return sorted(items, key=lambda item: (-rank[item["tier"]], -item["tier_weight"], item["id"]))
