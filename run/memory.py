"""File-backed tiered user memory with lightweight lifecycle indexes."""

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
from typing import Any


MEMORY_SCHEMA_VERSION = 2
TIERS = ("seven_days", "one_month", "half_year", "permanent")
TEMPORARY_TIERS = TIERS[:-1]
FILENAME_MAX_CHARS = 50
DEFAULT_TIERS = {
    "seven_days": {"days": 7, "upgrade_threshold": 3, "next": "one_month"},
    "one_month": {"days": 30, "upgrade_threshold": 10, "next": "half_year"},
    "half_year": {"days": 180, "upgrade_threshold": 60, "next": None},
}
_WORD_RE = re.compile(r"[A-Za-z0-9_\-]+|[\u4e00-\u9fff]")
_INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f\r\n]')
_SECRET_RE = re.compile(
    r"(?i)(?:api[_ -]?key|token|password|passwd|secret|cookie|private[_ -]?key|验证码|密码|密钥|令牌)"
    r"\s*(?::|=|：|是|为|is)\s*[^\s,;，；]{4,}|\bsk-[A-Za-z0-9_-]{8,}\b|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
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
class MemoryLocation:
    tier: str
    filename: str
    path: Path
    indexed: bool


@dataclass(frozen=True, slots=True)
class TierPromptSelection:
    tier: str
    items: tuple[dict[str, Any], ...]
    text: str
    selected_ids: tuple[str, ...]
    original_chars: int
    injected_chars: int
    original_items: int
    injected_items: int
    truncated: bool
    source_files: tuple[Path, ...]


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
    unknown = sorted(set(raw) - set(TEMPORARY_TIERS))
    if unknown:
        raise MemoryConfigError(
            "memory.tiers 只声明临时层，包含未知项：" + ", ".join(unknown)
        )
    rules: dict[str, TierRule] = {}
    for name in TEMPORARY_TIERS:
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
        if next_name is not None and next_name not in TEMPORARY_TIERS:
            raise MemoryConfigError(f"memory.tiers.{name}.next 无效")
        effective_next = "permanent" if name == "half_year" and next_name is None else next_name
        rules[name] = TierRule(name, days, threshold, effective_next)
    rules["permanent"] = TierRule("permanent", None, None, None)
    if [rules[name].next for name in TEMPORARY_TIERS] != [
        "one_month",
        "half_year",
        "permanent",
    ]:
        raise MemoryConfigError("记忆档位升级路径必须是 seven_days→one_month→half_year→permanent")
    return rules


def _normalise_text(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", _normalise_text(value).casefold())


def contains_sensitive_credential(text: str) -> bool:
    return bool(_SECRET_RE.search(text))


def _tokens(text: str) -> set[str]:
    normal = unicodedata.normalize("NFKC", text).casefold()
    words = _WORD_RE.findall(normal)
    result = set(words)
    chinese = "".join(item for item in words if len(item) == 1 and "\u4e00" <= item <= "\u9fff")
    result.update(chinese[index : index + 2] for index in range(max(0, len(chinese) - 1)))
    return {item for item in result if item}


def normalize_memory_filename(value: Any) -> str:
    title = unicodedata.normalize("NFKC", str(value or "")).strip()
    if title.casefold().endswith(".md"):
        title = title[:-3]
    title = _INVALID_FILENAME_RE.sub("", title)
    title = " ".join(title.split()).strip(" .")[:FILENAME_MAX_CHARS].strip(" .")
    if not title:
        raise MemoryError("记忆文件名不能为空")
    if title.casefold() in _WINDOWS_RESERVED:
        title = f"_{title}"[:FILENAME_MAX_CHARS]
    return f"{title}.md"


def _filename_from_content(content: str) -> str:
    return normalize_memory_filename(content)


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


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(value.rstrip())
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _empty_index() -> dict[str, Any]:
    return {"schema_version": MEMORY_SCHEMA_VERSION, "files": {}}


class MemoryStore:
    def __init__(self, root: Path, user: str, config: dict[str, Any]) -> None:
        self.root = root.resolve()
        self.user = user
        self.config = config
        self.rules = tier_rules(config)
        self.base = self.root / "users" / user / "improve"
        self._lock = _store_lock(self.root, user)

    def tier_dir(self, tier: str) -> Path:
        if tier not in TIERS:
            raise MemoryError(f"未知记忆档位：{tier}")
        return self.base / tier

    def path(self, tier: str) -> Path:
        directory = self.tier_dir(tier)
        return directory if tier == "permanent" else directory / "data.json"

    def fragment_path(self, tier: str, filename: str) -> Path:
        return self.tier_dir(tier) / normalize_memory_filename(filename)

    def load_index(self, tier: str) -> dict[str, dict[str, Any]]:
        if tier not in TEMPORARY_TIERS:
            raise MemoryError("永久记忆没有 data.json 索引")
        path = self.path(tier)
        try:
            text = path.read_text("utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise MemoryError(f"记忆索引不可读：{path}（{exc}）") from exc
        if not text.strip():
            return {}
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MemoryError(f"记忆索引不是有效 JSON：{path}（{exc}）") from exc
        if isinstance(raw, list):
            raise MemoryError(f"检测到旧版记忆数组，请先执行 v2 迁移：{path}")
        if not isinstance(raw, dict) or raw.get("schema_version") != MEMORY_SCHEMA_VERSION:
            raise MemoryError(f"记忆索引 schema_version 必须是 {MEMORY_SCHEMA_VERSION}：{path}")
        files = raw.get("files")
        if not isinstance(files, dict):
            raise MemoryError(f"记忆索引 files 必须是对象：{path}")
        result: dict[str, dict[str, Any]] = {}
        for raw_filename, raw_meta in files.items():
            filename = normalize_memory_filename(raw_filename)
            if filename != raw_filename or not isinstance(raw_meta, dict):
                raise MemoryError(f"记忆索引条目无效：{path}#{raw_filename}")
            weight = raw_meta.get("weight", 0)
            if isinstance(weight, bool) or not isinstance(weight, int) or weight < 0:
                raise MemoryError(f"记忆权重必须是非负整数：{path}#{filename}")
            updated_at = parse_time(raw_meta.get("updated_at"))
            expires_at = parse_time(raw_meta.get("expires_at"))
            if updated_at is None or expires_at is None:
                raise MemoryError(f"记忆索引时间字段无效：{path}#{filename}")
            last_weight_date = raw_meta.get("last_weight_date")
            if last_weight_date is not None and not isinstance(last_weight_date, str):
                raise MemoryError(f"last_weight_date 必须是字符串或 null：{path}#{filename}")
            result[filename] = {
                "weight": weight,
                "updated_at": iso(updated_at),
                "last_weight_date": last_weight_date,
                "expires_at": iso(expires_at),
            }
        return result

    def write_index(self, tier: str, files: dict[str, dict[str, Any]]) -> None:
        if tier not in TEMPORARY_TIERS:
            raise MemoryError("永久记忆不能写入 data.json 索引")
        ordered = {name: files[name] for name in sorted(files, key=str.casefold)}
        _atomic_json(self.path(tier), {"schema_version": MEMORY_SCHEMA_VERSION, "files": ordered})

    def _locations(self, filename: str) -> list[MemoryLocation]:
        normalized = normalize_memory_filename(filename)
        result: list[MemoryLocation] = []
        for tier in TEMPORARY_TIERS:
            matches = [
                existing
                for existing in self.load_index(tier)
                if existing.casefold() == normalized.casefold()
            ]
            result.extend(
                MemoryLocation(tier, existing, self.fragment_path(tier, existing), True)
                for existing in matches
            )
        permanent_dir = self.tier_dir("permanent")
        if permanent_dir.is_dir():
            result.extend(
                MemoryLocation("permanent", path.name, path, False)
                for path in permanent_dir.glob("*.md")
                if path.is_file() and path.name.casefold() == normalized.casefold()
            )
        return result

    def locate(self, filename: str) -> MemoryLocation | None:
        locations = self._locations(filename)
        if len(locations) > 1:
            tiers = ", ".join(location.tier for location in locations)
            raise MemoryError(f"记忆文件名跨层重复：{normalize_memory_filename(filename)}（{tiers}）")
        return locations[0] if locations else None

    def _entry(self, location: MemoryLocation, meta: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            content = location.path.read_text("utf-8").strip()
        except FileNotFoundError as exc:
            raise MemoryError(f"记忆索引指向不存在的文件：{location.path}") from exc
        except OSError as exc:
            raise MemoryError(f"记忆文件不可读：{location.path}（{exc}）") from exc
        if location.tier == "permanent":
            updated_at = iso(datetime.fromtimestamp(location.path.stat().st_mtime, timezone.utc))
            return {
                "filename": location.filename,
                "content": content,
                "tier": location.tier,
                "weight": 0,
                "updated_at": updated_at,
                "last_weight_date": None,
                "expires_at": None,
            }
        current_meta = meta or self.load_index(location.tier).get(location.filename)
        if current_meta is None:
            raise MemoryError(f"临时记忆缺少索引：{location.path}")
        return {
            "filename": location.filename,
            "content": content,
            "tier": location.tier,
            **current_meta,
        }

    def load_tier(self, tier: str, *, now: datetime | None = None) -> list[dict[str, Any]]:
        del now
        if tier == "permanent":
            directory = self.tier_dir(tier)
            if not directory.is_dir():
                return []
            return [
                self._entry(MemoryLocation(tier, path.name, path, False))
                for path in sorted(directory.glob("*.md"), key=lambda item: item.name.casefold())
                if path.is_file()
            ]
        index = self.load_index(tier)
        return [
            self._entry(MemoryLocation(tier, filename, self.fragment_path(tier, filename), True), meta)
            for filename, meta in index.items()
        ]

    def load_all(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        return [item for tier in TIERS for item in self.load_tier(tier, now=now)]

    def list_file_references(self) -> list[dict[str, Any]]:
        """Return filename-only metadata without reading Markdown bodies."""

        references: list[dict[str, Any]] = []
        for tier in TEMPORARY_TIERS:
            references.extend(
                {
                    "filename": filename,
                    "tier": tier,
                    "weight": int(meta["weight"]),
                    "updated_at": meta["updated_at"],
                }
                for filename, meta in self.load_index(tier).items()
            )
        permanent_dir = self.tier_dir("permanent")
        if permanent_dir.is_dir():
            references.extend(
                {
                    "filename": path.name,
                    "tier": "permanent",
                    "weight": 0,
                    "updated_at": iso(datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)),
                }
                for path in permanent_dir.glob("*.md")
                if path.is_file()
            )
        return sorted(
            references,
            key=lambda item: (
                -(parse_time(item["updated_at"]) or datetime.min.replace(tzinfo=timezone.utc)).timestamp(),
                item["filename"].casefold(),
            ),
        )

    def _new_meta(self, tier: str, current: datetime) -> dict[str, Any]:
        rule = self.rules[tier]
        if rule.days is None:
            raise MemoryError(f"永久层不应创建生命周期索引：{tier}")
        return {
            "weight": 0,
            "updated_at": iso(current),
            "last_weight_date": None,
            "expires_at": iso(current + timedelta(days=rule.days)),
        }

    def _touch_temporary(self, location: MemoryLocation, current: datetime) -> bool:
        index = self.load_index(location.tier)
        meta = index.get(location.filename)
        if meta is None:
            raise MemoryError(f"临时记忆缺少索引：{location.path}")
        day = local_day(current)
        weighted = meta.get("last_weight_date") != day
        if weighted:
            meta["weight"] = int(meta.get("weight", 0)) + 1
            meta["last_weight_date"] = day
        meta["updated_at"] = iso(current)
        index[location.filename] = meta
        self.write_index(location.tier, index)
        return weighted

    def _delete_location(self, location: MemoryLocation) -> None:
        if location.indexed:
            index = self.load_index(location.tier)
            index.pop(location.filename, None)
            self.write_index(location.tier, index)
        try:
            location.path.unlink()
        except FileNotFoundError:
            return

    def _promote_location(
        self,
        location: MemoryLocation,
        target_tier: str,
        current: datetime,
        *,
        merged_content: str | None = None,
        target_filename: str | None = None,
    ) -> None:
        if location.tier == "permanent" or target_tier not in TIERS:
            raise MemoryError(f"无效记忆晋升：{location.tier}→{target_tier}")
        target_name = normalize_memory_filename(target_filename or location.filename)
        target_path = self.fragment_path(target_tier, target_name)
        conflicting_locations = [
            item
            for item in self._locations(target_name)
            if item.path != location.path and item.tier != target_tier
        ]
        if conflicting_locations:
            tiers = ", ".join(item.tier for item in conflicting_locations)
            raise MemoryError(f"晋升目标文件名已存在于其他层级：{target_name}（{tiers}）")
        if merged_content is not None:
            content = _normalise_text(merged_content)
            if not content:
                raise MemoryError("融合后的记忆内容不能为空")
            if contains_sensitive_credential(content):
                raise MemoryError("融合后的记忆包含疑似敏感凭据")
            if not location.path.is_file():
                raise MemoryError(f"晋升来源不存在：{location.path}")

            source_index = self.load_index(location.tier)
            if location.filename not in source_index:
                raise MemoryError(f"晋升来源缺少索引：{location.path}")
            source_meta = dict(source_index[location.filename])
            target_index = (
                self.load_index(target_tier) if target_tier != "permanent" else None
            )
            previous_target_index = (
                {key: dict(value) for key, value in target_index.items()}
                if target_index is not None
                else None
            )
            previous_target = (
                target_path.read_text("utf-8") if target_path.is_file() else None
            )
            try:
                _atomic_text(target_path, content)
                source_index.pop(location.filename)
                self.write_index(location.tier, source_index)
                if target_index is not None:
                    target_index[target_name] = self._new_meta(
                        target_tier,
                        current,
                    )
                    self.write_index(target_tier, target_index)
                location.path.unlink()
            except Exception:
                if previous_target is None:
                    target_path.unlink(missing_ok=True)
                else:
                    _atomic_text(target_path, previous_target)
                rollback_source = self.load_index(location.tier)
                rollback_source[location.filename] = source_meta
                self.write_index(location.tier, rollback_source)
                if previous_target_index is not None:
                    self.write_index(target_tier, previous_target_index)
                raise
            return
        if target_path.exists():
            raise MemoryError(f"晋升目标已存在同名记忆：{target_path}")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(location.path, target_path)
        source_index = self.load_index(location.tier)
        source_meta = source_index.pop(location.filename, None)
        if source_meta is None:
            os.replace(target_path, location.path)
            raise MemoryError(f"晋升来源缺少索引：{location.path}")
        try:
            if target_tier != "permanent":
                target_index = self.load_index(target_tier)
                target_index[target_name] = self._new_meta(target_tier, current)
                self.write_index(target_tier, target_index)
            self.write_index(location.tier, source_index)
        except Exception:
            if target_tier != "permanent":
                try:
                    rollback_index = self.load_index(target_tier)
                    rollback_index.pop(target_name, None)
                    self.write_index(target_tier, rollback_index)
                except Exception:
                    pass
            if target_path.exists() and not location.path.exists():
                location.path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target_path, location.path)
            raise

    def upsert_candidates(
        self,
        candidates: list[dict[str, Any]],
        *,
        source: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        del source
        current = now or utc_now()
        created: list[str] = []
        updated: list[str] = []
        forgotten: list[str] = []
        rejected = 0
        with self._lock:
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    rejected += 1
                    continue
                action = str(candidate.get("action") or "upsert").strip().casefold()
                raw_filename = candidate.get("filename") or candidate.get("target")
                content = _normalise_text(candidate.get("content"))
                if action == "forget":
                    if raw_filename is None:
                        raw_filename = content
                    try:
                        removed = self.forget(str(raw_filename))
                    except MemoryError:
                        rejected += 1
                    else:
                        forgotten.extend(removed)
                    continue
                if action != "upsert" or not content or contains_sensitive_credential(content):
                    rejected += 1
                    continue
                try:
                    filename = normalize_memory_filename(raw_filename or content)
                except MemoryError:
                    rejected += 1
                    continue
                explicit = bool(candidate.get("explicit", False))
                location = self.locate(filename)
                if location is None:
                    tier = "permanent" if explicit else "seven_days"
                    path = self.fragment_path(tier, filename)
                    _atomic_text(path, content)
                    if tier != "permanent":
                        try:
                            index = self.load_index(tier)
                            index[filename] = self._new_meta(tier, current)
                            self.write_index(tier, index)
                        except Exception:
                            path.unlink(missing_ok=True)
                            raise
                    created.append(filename)
                    continue
                old_content = location.path.read_text("utf-8").strip()
                changed = old_content != content
                if explicit and location.tier != "permanent":
                    if changed:
                        _atomic_text(location.path, content)
                    self._promote_location(location, "permanent", current)
                    updated.append(filename)
                    continue
                if changed:
                    _atomic_text(location.path, content)
                if location.tier != "permanent":
                    self._touch_temporary(location, current)
                updated.append(filename)
        return {"created": created, "updated": updated, "forgotten": forgotten, "rejected": rejected}

    def forget(self, query: str) -> list[str]:
        with self._lock:
            try:
                filename = normalize_memory_filename(query)
            except MemoryError:
                return []
            location = self.locate(filename)
            if location is None:
                return []
            self._delete_location(location)
            return [filename]

    def review_due(self, *, now: datetime | None = None) -> dict[str, list[str]]:
        """Compatibility API; automatic lifecycle review is owned by cron.review_due."""
        current = now or utc_now()
        upgraded: list[str] = []
        deleted: list[str] = []
        with self._lock:
            for tier in TEMPORARY_TIERS:
                for filename, meta in list(self.load_index(tier).items()):
                    due = parse_time(meta.get("expires_at"))
                    if due is None or due > current:
                        continue
                    location = MemoryLocation(tier, filename, self.fragment_path(tier, filename), True)
                    rule = self.rules[tier]
                    if int(meta.get("weight", 0)) >= int(rule.upgrade_threshold or 0):
                        if rule.next is None:
                            raise MemoryConfigError(f"临时记忆层缺少晋升目标：{tier}")
                        self._promote_location(location, rule.next, current)
                        upgraded.append(filename)
                    else:
                        self._delete_location(location)
                        deleted.append(filename)
            return {"upgraded": upgraded, "deleted": deleted}

    def search(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        query_key = _key(query)
        query_tokens = _tokens(query)
        tier_rank = {"seven_days": 1, "one_month": 2, "half_year": 3, "permanent": 4}
        references: list[tuple[MemoryLocation, dict[str, Any] | None]] = []
        for tier in TEMPORARY_TIERS:
            references.extend(
                (MemoryLocation(tier, filename, self.fragment_path(tier, filename), True), meta)
                for filename, meta in self.load_index(tier).items()
            )
        permanent_dir = self.tier_dir("permanent")
        if permanent_dir.is_dir():
            references.extend(
                (MemoryLocation("permanent", path.name, path, False), None)
                for path in permanent_dir.glob("*.md")
                if path.is_file()
            )
        scored: list[tuple[float, MemoryLocation, dict[str, Any] | None]] = []
        for location, meta in references:
            title = Path(location.filename).stem
            title_key = _key(title)
            title_tokens = _tokens(title)
            overlap = len(query_tokens & title_tokens)
            substring = 2 if query_key and (query_key in title_key or title_key in query_key) else 0
            if overlap == 0 and substring == 0:
                continue
            relevance = substring + overlap / max(1, math.sqrt(len(query_tokens) * len(title_tokens)))
            weight = int((meta or {}).get("weight", 0))
            score = relevance * 10 + tier_rank[location.tier] + min(weight, 1000) / 1000
            scored.append((score, location, meta))
        scored.sort(key=lambda pair: (-pair[0], pair[1].filename.casefold()))
        return [
            dict(self._entry(location, meta), _score=score)
            for score, location, meta in scored[: max(0, limit)]
        ]

    def select_tier_for_prompt(
        self,
        tier: str,
        *,
        max_files: int | None,
        mode: str = "full",
    ) -> TierPromptSelection:
        if tier not in TIERS:
            raise MemoryError(f"未知记忆档位：{tier}")
        if mode != "full":
            raise MemoryConfigError(f"{tier} 记忆注入模式暂不支持：{mode}")
        if max_files is not None and (
            isinstance(max_files, bool) or not isinstance(max_files, int) or max_files < 0
        ):
            raise MemoryConfigError(f"{tier} 记忆文件上限必须是非负整数或 null")

        if tier == "permanent":
            items = self.load_tier(tier)
            selected = sorted(items, key=lambda item: item["filename"].casefold())
            original_items = len(items)
            all_paths = [self.fragment_path(tier, item["filename"]) for item in items]
        else:
            index = self.load_index(tier)
            ordered = sorted(
                index.items(),
                key=lambda pair: (
                    -int(pair[1]["weight"]),
                    -(parse_time(pair[1]["updated_at"]) or datetime.min.replace(tzinfo=timezone.utc)).timestamp(),
                    pair[0].casefold(),
                ),
            )
            if max_files is not None:
                ordered = ordered[:max_files]
            selected = [
                self._entry(
                    MemoryLocation(tier, filename, self.fragment_path(tier, filename), True),
                    meta,
                )
                for filename, meta in ordered
            ]
            original_items = len(index)
            all_paths = [self.fragment_path(tier, filename) for filename in index]

        def line(item: dict[str, Any]) -> str:
            if tier == "permanent":
                return f"- [{item['filename']}] {item['content']}"
            return f"- [{item['filename']}] (weight={item['weight']}) {item['content']}"

        text = "\n".join(line(item) for item in selected)
        source_files = tuple(self.fragment_path(tier, item["filename"]) for item in selected)
        original_size = sum(path.stat().st_size for path in all_paths if path.is_file())
        return TierPromptSelection(
            tier=tier,
            items=tuple(selected),
            text=text,
            selected_ids=tuple(item["filename"] for item in selected),
            original_chars=original_size,
            injected_chars=len(text),
            original_items=original_items,
            injected_items=len(selected),
            truncated=len(selected) < original_items,
            source_files=source_files,
        )

    def mark_used(self, filenames: list[str], *, now: datetime | None = None) -> list[str]:
        if not filenames:
            return []
        current = now or utc_now()
        changed: list[str] = []
        with self._lock:
            for raw_filename in dict.fromkeys(filenames):
                try:
                    location = self.locate(raw_filename)
                except MemoryError:
                    continue
                if location is None or location.tier == "permanent":
                    continue
                if self._touch_temporary(location, current):
                    changed.append(location.filename)
        return changed

    def list_items(self) -> list[dict[str, Any]]:
        with self._lock:
            items = self.load_all()
            rank = {tier: index for index, tier in enumerate(TIERS)}
            return sorted(
                items,
                key=lambda item: (-rank[item["tier"]], -int(item["weight"]), item["filename"].casefold()),
            )

    def integrity_issues(self) -> list[str]:
        issues: list[str] = []
        seen: dict[str, str] = {}
        for tier in TEMPORARY_TIERS:
            index = self.load_index(tier)
            indexed = set(index)
            present = {path.name for path in self.tier_dir(tier).glob("*.md") if path.is_file()}
            for filename in sorted(indexed - present, key=str.casefold):
                issues.append(f"missing_file:{tier}/{filename}")
            for filename in sorted(present - indexed, key=str.casefold):
                issues.append(f"orphan_file:{tier}/{filename}")
            for filename in sorted(indexed & present, key=str.casefold):
                key = filename.casefold()
                if key in seen:
                    issues.append(f"duplicate_filename:{seen[key]}/{filename}:{tier}/{filename}")
                else:
                    seen[key] = tier
        for path in sorted(self.tier_dir("permanent").glob("*.md"), key=lambda item: item.name.casefold()):
            key = path.name.casefold()
            if key in seen:
                issues.append(f"duplicate_filename:{seen[key]}/{path.name}:permanent/{path.name}")
            else:
                seen[key] = "permanent"
        if (self.tier_dir("permanent") / "data.json").exists():
            issues.append("unexpected_index:permanent/data.json")
        return issues
