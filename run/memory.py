"""File-backed tiered user memory with lightweight lifecycle indexes."""

from __future__ import annotations

import hashlib
import json
import logging
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
from zoneinfo import ZoneInfo


MEMORY_SCHEMA_VERSION = 3
LEGACY_MEMORY_SCHEMA_VERSION = 2
TIERS = ("seven_days", "one_month", "half_year", "permanent")
TEMPORARY_TIERS = TIERS[:-1]
MEMORY_EXTRACTION_MODES = frozenset(
    {"disabled", "compression_only", "background", "on_commit"}
)
DEFAULT_EXTRACTION_BATCH_ROUNDS = 5
DEFAULT_EXTRACTION_MAX_CANDIDATES_PER_BATCH = 10
MAX_EXTRACTION_BATCH_ROUNDS = 20
MAX_EXTRACTION_CANDIDATES_PER_BATCH = 40
MEMORY_OPERATION_SCHEMA_VERSION = 1
MEMORY_OPERATION_HISTORY_LIMIT = 512
IMPORTANT_VIEW_SCHEMA_VERSION = 1
IMPORTANT_VIEW_INDEX_FILENAME = "important_view.json"
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
LOGGER = logging.getLogger(__name__)
BEIJING = ZoneInfo("Asia/Shanghai")


def _store_lock(root: Path, user: str) -> threading.RLock:
    key = (str(root.resolve()).casefold(), user)
    with _STORE_LOCKS_GUARD:
        return _STORE_LOCKS.setdefault(key, threading.RLock())


class MemoryError(RuntimeError):
    pass


class MemoryConfigError(MemoryError):
    pass


class MemoryIntegrityError(MemoryError):
    def __init__(self, issue: str, message: str) -> None:
        super().__init__(message)
        self.issue = issue


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
    integrity_warnings: tuple[str, ...] = ()


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
    return (value or utc_now()).astimezone(BEIJING).date().isoformat()


def memory_extraction_mode(config: dict[str, Any]) -> str:
    """Resolve the explicit extraction policy, including the legacy boolean."""

    raw_memory = config.get("memory") or {}
    if not isinstance(raw_memory, dict):
        raise MemoryConfigError("memory 必须是对象")
    raw_mode = raw_memory.get("extraction_mode")
    if raw_mode is None:
        # The legacy boolean described synchronous timing, but users naturally
        # interpreted false as disabling per-round extraction.  Preserve true
        # as on_commit and map false/absent to the safe compression boundary.
        return (
            "on_commit"
            if raw_memory.get("auto_extract_on_commit") is True
            else "compression_only"
        )
    mode = str(raw_mode).strip().casefold()
    if mode not in MEMORY_EXTRACTION_MODES:
        allowed = ", ".join(sorted(MEMORY_EXTRACTION_MODES))
        raise MemoryConfigError(f"memory.extraction_mode 只允许 {allowed}")
    return mode


def memory_extraction_batch_rounds(config: dict[str, Any]) -> int:
    """Return the bounded number of contiguous rounds analyzed per model run."""

    raw_memory = config.get("memory") or {}
    if not isinstance(raw_memory, dict):
        raise MemoryConfigError("memory 必须是对象")
    raw_value = raw_memory.get(
        "extraction_batch_rounds", DEFAULT_EXTRACTION_BATCH_ROUNDS
    )
    if isinstance(raw_value, bool):
        raise MemoryConfigError("memory.extraction_batch_rounds 必须是正整数")
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise MemoryConfigError("memory.extraction_batch_rounds 必须是正整数") from exc
    if value < 1:
        raise MemoryConfigError("memory.extraction_batch_rounds 必须是正整数")
    return min(value, MAX_EXTRACTION_BATCH_ROUNDS)


def memory_extraction_candidate_limit(
    config: dict[str, Any],
    round_count: int,
) -> int:
    """Return the batch candidate cap while retaining the two-per-round policy."""

    raw_memory = config.get("memory") or {}
    if not isinstance(raw_memory, dict):
        raise MemoryConfigError("memory 必须是对象")
    raw_value = raw_memory.get(
        "extraction_max_candidates_per_batch",
        DEFAULT_EXTRACTION_MAX_CANDIDATES_PER_BATCH,
    )
    if isinstance(raw_value, bool):
        raise MemoryConfigError(
            "memory.extraction_max_candidates_per_batch 必须是正整数"
        )
    try:
        configured = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise MemoryConfigError(
            "memory.extraction_max_candidates_per_batch 必须是正整数"
        ) from exc
    if configured < 1:
        raise MemoryConfigError(
            "memory.extraction_max_candidates_per_batch 必须是正整数"
        )
    bounded = min(configured, MAX_EXTRACTION_CANDIDATES_PER_BATCH)
    return min(bounded, max(1, int(round_count)) * 2)


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

    def _operation_path(self) -> Path:
        return self.base / ".memory_operations.json"

    def important_view_path(self) -> Path:
        return self.base / IMPORTANT_VIEW_INDEX_FILENAME

    def load_important_view_sources(self) -> frozenset[str]:
        """Return temporary fragment identities mirrored by the hot-memory view.

        The view index is derived cache metadata.  A missing or malformed file must
        never make the authoritative memory tiers unavailable, so prompt assembly
        falls back to injecting all temporary fragments.  Source validation is
        deliberately all-or-nothing: returning a valid subset while the hot view is
        stale would exclude that subset from both the hot view and the regular
        temporary-memory prompt.
        """

        path = self.important_view_path()
        try:
            raw = json.loads(path.read_text("utf-8"))
        except FileNotFoundError:
            return frozenset()
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning(
                "忽略不可读的临时重要记忆来源索引：user=%s path=%s error=%s",
                self.user,
                path,
                exc,
            )
            return frozenset()
        if not isinstance(raw, dict) or raw.get("schema_version") != IMPORTANT_VIEW_SCHEMA_VERSION:
            LOGGER.warning("忽略版本无效的临时重要记忆来源索引：%s", path)
            return frozenset()
        sources = raw.get("sources")
        if not isinstance(sources, list):
            LOGGER.warning("忽略 sources 无效的临时重要记忆来源索引：%s", path)
            return frozenset()
        normalized: set[str] = set()
        for source in sources:
            try:
                if isinstance(source, dict):
                    filename = normalize_memory_filename(source.get("filename"))
                    expected_hash = str(source.get("sha256") or "").strip().casefold()
                else:
                    filename = normalize_memory_filename(source)
                    expected_hash = ""
                location = self.locate(filename)
                if location is None or location.tier not in TEMPORARY_TIERS:
                    LOGGER.warning("临时重要记忆来源已失效，回退全部临时记忆：%s", source)
                    return frozenset()
                if expected_hash:
                    actual_hash = hashlib.sha256(location.path.read_bytes()).hexdigest()
                    if actual_hash != expected_hash:
                        LOGGER.warning("临时重要记忆来源内容已变化，回退全部临时记忆：%s", source)
                        return frozenset()
                if location.filename in normalized:
                    LOGGER.warning("临时重要记忆来源重复，回退全部临时记忆：%s", source)
                    return frozenset()
                normalized.add(location.filename)
            except (MemoryError, OSError):
                LOGGER.warning("临时重要记忆来源无效，回退全部临时记忆：%s", source)
                return frozenset()
        return frozenset(normalized)

    def important_view_is_current(self) -> bool:
        """Return false when a derived source changed or left temporary storage."""

        path = self.important_view_path()
        try:
            raw = json.loads(path.read_text("utf-8"))
        except FileNotFoundError:
            # Preserve pre-index installations until the first periodic rebuild.
            return True
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(raw, dict) or raw.get("schema_version") != IMPORTANT_VIEW_SCHEMA_VERSION:
            return False
        sources = raw.get("sources")
        if not isinstance(sources, list):
            return False
        return len(self.load_important_view_sources()) == len(sources)

    def set_important_view_sources(
        self,
        filenames: list[str],
        *,
        now: datetime | None = None,
    ) -> list[str]:
        """Replace the hot-view source index after validating live temp fragments."""

        current = now or utc_now()
        normalized = list(
            dict.fromkeys(normalize_memory_filename(filename) for filename in filenames)
        )
        with self._lock:
            sources: list[dict[str, str]] = []
            for filename in normalized:
                location = self.locate(filename)
                if location is None or location.tier not in TEMPORARY_TIERS:
                    raise MemoryError(f"临时重要记忆来源不是有效临时碎片：{filename}")
                sources.append(
                    {
                        "filename": location.filename,
                        "sha256": hashlib.sha256(location.path.read_bytes()).hexdigest(),
                    }
                )
            _atomic_json(
                self.important_view_path(),
                {
                    "schema_version": IMPORTANT_VIEW_SCHEMA_VERSION,
                    "updated_at": iso(current),
                    "sources": sources,
                },
            )
        return normalized

    def _load_operations(self) -> dict[str, dict[str, Any]]:
        path = self._operation_path()
        try:
            raw = json.loads(path.read_text("utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            raise MemoryError(f"记忆批次操作日志不可读：{path}（{exc}）") from exc
        if not isinstance(raw, dict) or raw.get("schema_version") != MEMORY_OPERATION_SCHEMA_VERSION:
            raise MemoryError(f"记忆批次操作日志版本无效：{path}")
        operations = raw.get("operations")
        if not isinstance(operations, dict):
            raise MemoryError(f"记忆批次操作日志 operations 必须是对象：{path}")
        return {
            str(key): dict(value)
            for key, value in operations.items()
            if isinstance(key, str) and isinstance(value, dict)
        }

    def _write_operation_result(
        self,
        operation_id: str,
        result: dict[str, Any],
        current: datetime,
    ) -> None:
        operations = self._load_operations()
        operations[operation_id] = {
            "completed_at": iso(current),
            "result": dict(result),
        }
        ordered = sorted(
            operations.items(),
            key=lambda item: str(item[1].get("completed_at") or ""),
            reverse=True,
        )[:MEMORY_OPERATION_HISTORY_LIMIT]
        _atomic_json(
            self._operation_path(),
            {
                "schema_version": MEMORY_OPERATION_SCHEMA_VERSION,
                "operations": dict(ordered),
            },
        )

    def tier_dir(self, tier: str) -> Path:
        if tier not in TIERS:
            raise MemoryError(f"未知记忆档位：{tier}")
        return self.base / tier

    def path(self, tier: str) -> Path:
        directory = self.tier_dir(tier)
        return directory if tier == "permanent" else directory / "data.json"

    def fragment_path(self, tier: str, filename: str) -> Path:
        return self.tier_dir(tier) / normalize_memory_filename(filename)

    def _normalise_temporary_meta(
        self,
        tier: str,
        filename: str,
        raw_meta: dict[str, Any],
    ) -> dict[str, Any]:
        weight = raw_meta.get("weight", 0)
        if isinstance(weight, bool) or not isinstance(weight, int) or weight < 0:
            raise MemoryError(
                f"记忆权重必须是非负整数：{self.path(tier)}#{filename}"
            )
        legacy_updated = parse_time(raw_meta.get("updated_at"))
        expires_at = parse_time(raw_meta.get("expires_at"))
        if legacy_updated is None or expires_at is None:
            raise MemoryError(
                f"记忆索引时间字段无效：{self.path(tier)}#{filename}"
            )
        last_weight_date = raw_meta.get("last_weight_date")
        if last_weight_date is not None and not isinstance(last_weight_date, str):
            raise MemoryError(
                f"last_weight_date 必须是字符串或 null：{self.path(tier)}#{filename}"
            )

        rule = self.rules[tier]
        tier_entered_at = parse_time(raw_meta.get("tier_entered_at"))
        if tier_entered_at is None:
            tier_entered_at = expires_at - timedelta(days=int(rule.days or 0))
        fragment = self.fragment_path(tier, filename)
        try:
            file_modified_at = datetime.fromtimestamp(
                fragment.stat().st_mtime,
                timezone.utc,
            )
        except OSError:
            file_modified_at = legacy_updated
        content_updated_at = (
            parse_time(raw_meta.get("content_updated_at")) or file_modified_at
        )
        created_at = parse_time(raw_meta.get("created_at")) or min(
            tier_entered_at,
            file_modified_at,
            legacy_updated,
        )
        last_used_at = parse_time(raw_meta.get("last_used_at"))
        if last_used_at is None and last_weight_date is not None:
            last_used_at = legacy_updated
        return {
            "weight": weight,
            "created_at": iso(created_at),
            "content_updated_at": iso(content_updated_at),
            # Retain updated_at as a compatibility alias.  From schema v3 it
            # means content update time and is never changed merely by use.
            "updated_at": iso(content_updated_at),
            "last_used_at": iso(last_used_at) if last_used_at is not None else None,
            "last_weight_date": last_weight_date,
            "tier_entered_at": iso(tier_entered_at),
            "expires_at": iso(expires_at),
        }

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
            raise MemoryError(f"检测到旧版记忆数组，请先执行文件记忆迁移：{path}")
        schema_version = raw.get("schema_version") if isinstance(raw, dict) else None
        if schema_version not in {LEGACY_MEMORY_SCHEMA_VERSION, MEMORY_SCHEMA_VERSION}:
            raise MemoryError(
                f"记忆索引 schema_version 必须是 {LEGACY_MEMORY_SCHEMA_VERSION} 或 "
                f"{MEMORY_SCHEMA_VERSION}：{path}"
            )
        files = raw.get("files")
        if not isinstance(files, dict):
            raise MemoryError(f"记忆索引 files 必须是对象：{path}")
        result: dict[str, dict[str, Any]] = {}
        for raw_filename, raw_meta in files.items():
            filename = normalize_memory_filename(raw_filename)
            if filename != raw_filename or not isinstance(raw_meta, dict):
                raise MemoryError(f"记忆索引条目无效：{path}#{raw_filename}")
            result[filename] = self._normalise_temporary_meta(
                tier,
                filename,
                raw_meta,
            )
        if schema_version == LEGACY_MEMORY_SCHEMA_VERSION:
            self.write_index(tier, result)
        return result

    def write_index(self, tier: str, files: dict[str, dict[str, Any]]) -> None:
        if tier not in TEMPORARY_TIERS:
            raise MemoryError("永久记忆不能写入 data.json 索引")
        ordered = {
            name: self._normalise_temporary_meta(tier, name, files[name])
            for name in sorted(files, key=str.casefold)
        }
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

    def locate_in_tier(self, tier: str, filename: str) -> MemoryLocation | None:
        """Locate one fragment by its composite identity without scanning other tiers.

        Cross-tier duplicate filenames are an invalid storage state for normal writes, but
        management and repair operations must still be able to address each copy exactly.
        """

        normalized = normalize_memory_filename(filename)
        directory = self.tier_dir(tier)
        if tier in TEMPORARY_TIERS:
            matches = [
                existing
                for existing in self.load_index(tier)
                if existing.casefold() == normalized.casefold()
            ]
            if len(matches) > 1:
                raise MemoryError(f"记忆文件名在层内重复：{tier}/{normalized}")
            if not matches:
                return None
            existing = matches[0]
            return MemoryLocation(tier, existing, self.fragment_path(tier, existing), True)
        if not directory.is_dir():
            return None
        matches = [
            path
            for path in directory.glob("*.md")
            if path.is_file() and path.name.casefold() == normalized.casefold()
        ]
        if len(matches) > 1:
            raise MemoryError(f"记忆文件名在层内重复：{tier}/{normalized}")
        if not matches:
            return None
        path = matches[0]
        return MemoryLocation(tier, path.name, path, False)

    def _entry(self, location: MemoryLocation, meta: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            content = location.path.read_text("utf-8").strip()
        except FileNotFoundError as exc:
            issue = f"missing_file:{location.tier}/{location.filename}"
            raise MemoryIntegrityError(
                issue,
                f"记忆索引指向不存在的文件：{location.path}",
            ) from exc
        except OSError as exc:
            raise MemoryError(f"记忆文件不可读：{location.path}（{exc}）") from exc
        if location.tier == "permanent":
            updated_at = iso(datetime.fromtimestamp(location.path.stat().st_mtime, timezone.utc))
            return {
                "filename": location.filename,
                "content": content,
                "tier": location.tier,
                "weight": 0,
                "created_at": updated_at,
                "content_updated_at": updated_at,
                "updated_at": updated_at,
                "last_used_at": None,
                "last_weight_date": None,
                "tier_entered_at": updated_at,
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

    def _entry_or_warning(
        self,
        location: MemoryLocation,
        meta: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        try:
            return self._entry(location, meta), None
        except MemoryIntegrityError as exc:
            LOGGER.warning(
                "跳过缺失正文的记忆索引：user=%s issue=%s path=%s",
                self.user,
                exc.issue,
                location.path,
            )
            return None, exc.issue

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
        entries: list[dict[str, Any]] = []
        for filename, meta in index.items():
            entry, _ = self._entry_or_warning(
                MemoryLocation(tier, filename, self.fragment_path(tier, filename), True),
                meta,
            )
            if entry is not None:
                entries.append(entry)
        return entries

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

    def _new_meta(
        self,
        tier: str,
        current: datetime,
        *,
        source_meta: dict[str, Any] | None = None,
        content_changed: bool = False,
    ) -> dict[str, Any]:
        rule = self.rules[tier]
        if rule.days is None:
            raise MemoryError(f"永久层不应创建生命周期索引：{tier}")
        source = source_meta or {}
        created_at = parse_time(source.get("created_at")) or current
        content_updated_at = (
            current
            if content_changed
            else parse_time(
                source.get("content_updated_at") or source.get("updated_at")
            )
            or current
        )
        last_used_at = parse_time(source.get("last_used_at"))
        return {
            "weight": 0,
            "created_at": iso(created_at),
            "content_updated_at": iso(content_updated_at),
            "updated_at": iso(content_updated_at),
            "last_used_at": (
                iso(last_used_at) if last_used_at is not None else None
            ),
            "last_weight_date": None,
            "tier_entered_at": iso(current),
            "expires_at": iso(current + timedelta(days=rule.days)),
        }

    def _touch_temporary(
        self,
        location: MemoryLocation,
        current: datetime,
        *,
        content_changed: bool = False,
    ) -> bool:
        index = self.load_index(location.tier)
        meta = index.get(location.filename)
        if meta is None:
            raise MemoryError(f"临时记忆缺少索引：{location.path}")
        day = local_day(current)
        weighted = meta.get("last_weight_date") != day
        if weighted:
            meta["weight"] = int(meta.get("weight", 0)) + 1
            meta["last_weight_date"] = day
        if content_changed:
            meta["content_updated_at"] = iso(current)
            meta["updated_at"] = iso(current)
        meta["last_used_at"] = iso(current)
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
                        source_meta=source_meta,
                        content_changed=True,
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
                target_index[target_name] = self._new_meta(
                    target_tier,
                    current,
                    source_meta=source_meta,
                )
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
        operation_id: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        del source
        current = now or utc_now()
        created: list[str] = []
        updated: list[str] = []
        skipped_permanent: list[str] = []
        forgotten: list[str] = []
        rejected = 0
        with self._lock:
            normalized_operation_id = str(operation_id or "").strip()
            if len(normalized_operation_id) > 256:
                raise MemoryError("记忆批次 operation_id 不能超过 256 个字符")
            if normalized_operation_id:
                previous = self._load_operations().get(normalized_operation_id)
                if isinstance(previous, dict) and isinstance(previous.get("result"), dict):
                    return {
                        **dict(previous["result"]),
                        "operation_id": normalized_operation_id,
                        "replayed": True,
                    }
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
                if location.tier == "permanent" and not explicit:
                    skipped_permanent.append(filename)
                    continue
                if explicit and location.tier != "permanent":
                    if changed:
                        _atomic_text(location.path, content)
                        self._touch_temporary(
                            location,
                            current,
                            content_changed=True,
                        )
                    self._promote_location(location, "permanent", current)
                    updated.append(filename)
                    continue
                if changed:
                    _atomic_text(location.path, content)
                if location.tier != "permanent":
                    self._touch_temporary(
                        location,
                        current,
                        content_changed=changed,
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
                self._write_operation_result(normalized_operation_id, result, current)
                return {
                    **result,
                    "operation_id": normalized_operation_id,
                    "replayed": False,
                }
            return result

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
            featured_sources = self.load_important_view_sources()
            ordered = sorted(
                (
                    (filename, meta)
                    for filename, meta in index.items()
                    if filename not in featured_sources
                ),
                key=lambda pair: (
                    -int(pair[1]["weight"]),
                    pair[0].casefold(),
                ),
            )
            selected = []
            integrity_warnings: list[str] = []
            if max_files != 0:
                for filename, meta in ordered:
                    entry, warning = self._entry_or_warning(
                        MemoryLocation(
                            tier,
                            filename,
                            self.fragment_path(tier, filename),
                            True,
                        ),
                        meta,
                    )
                    if warning is not None:
                        integrity_warnings.append(warning)
                        continue
                    if entry is not None:
                        selected.append(entry)
                    if max_files is not None and len(selected) >= max_files:
                        break
            eligible_names = [
                filename for filename in index if filename not in featured_sources
            ]
            original_items = len(eligible_names)
            all_paths = [self.fragment_path(tier, filename) for filename in eligible_names]

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
            integrity_warnings=tuple(integrity_warnings) if tier != "permanent" else (),
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
