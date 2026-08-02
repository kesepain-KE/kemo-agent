"""Tiered user-memory API backed by a per-user SQLite database."""

from __future__ import annotations

import re
import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from run.memory_sqlite import SqliteMemoryStore
from run.memory_store import MEMORY_DB_SCHEMA_VERSION


MEMORY_SCHEMA_VERSION = MEMORY_DB_SCHEMA_VERSION
TIERS = ("seven_days", "one_month", "half_year", "permanent")
TEMPORARY_TIERS = TIERS[:-1]
MEMORY_EXTRACTION_MODES = frozenset(
    {"disabled", "compression_only", "background", "on_commit"}
)
DEFAULT_EXTRACTION_BATCH_ROUNDS = 5
DEFAULT_EXTRACTION_MAX_CANDIDATES_PER_BATCH = 10
MAX_EXTRACTION_BATCH_ROUNDS = 20
MAX_EXTRACTION_CANDIDATES_PER_BATCH = 40
MEMORY_OPERATION_HISTORY_LIMIT = 512
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
    """Database identity for callers that promote a fragment."""

    tier: str
    filename: str


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
    raw_memory = config.get("memory") or {}
    if not isinstance(raw_memory, dict):
        raise MemoryConfigError("memory 必须是对象")
    raw_mode = raw_memory.get("extraction_mode")
    if raw_mode is None:
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
        if days is not None and (
            not isinstance(days, int) or isinstance(days, bool) or days <= 0
        ):
            raise MemoryConfigError(f"memory.tiers.{name}.days 必须是正整数或 null")
        if threshold is not None and (
            not isinstance(threshold, int)
            or isinstance(threshold, bool)
            or threshold < 0
        ):
            raise MemoryConfigError(
                f"memory.tiers.{name}.upgrade_threshold 必须是非负整数或 null"
            )
        if next_name is not None and next_name not in TEMPORARY_TIERS:
            raise MemoryConfigError(f"memory.tiers.{name}.next 无效")
        effective_next = (
            "permanent" if name == "half_year" and next_name is None else next_name
        )
        rules[name] = TierRule(name, days, threshold, effective_next)
    rules["permanent"] = TierRule("permanent", None, None, None)
    if [rules[name].next for name in TEMPORARY_TIERS] != [
        "one_month",
        "half_year",
        "permanent",
    ]:
        raise MemoryConfigError(
            "记忆档位升级路径必须是 seven_days→one_month→half_year→permanent"
        )
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
    chinese = "".join(
        item for item in words if len(item) == 1 and "\u4e00" <= item <= "\u9fff"
    )
    result.update(
        chinese[index : index + 2] for index in range(max(0, len(chinese) - 1))
    )
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


MemoryStore = SqliteMemoryStore
