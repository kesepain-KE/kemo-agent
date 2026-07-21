from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from run.memory import (
    TEMPORARY_TIERS,
    TIERS,
    MemoryError,
    MemoryStore,
    contains_sensitive_credential,
    normalize_memory_filename,
    utc_now,
)


MANAGED_TIERS = frozenset((*TIERS, "important"))
IMPORTANT_FILENAME = "memory_temporary_important.md"


def _validate_tier(tier: str) -> str:
    if tier not in MANAGED_TIERS:
        raise ValueError(f"不支持的记忆层级：{tier}")
    return tier


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            if content and not content.endswith("\n"):
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _important_path(root: Path, user: str) -> Path:
    return root / "users" / user / IMPORTANT_FILENAME


def _important_entry(root: Path, user: str) -> list[dict[str, Any]]:
    path = _important_path(root, user)
    if path.is_symlink():
        raise MemoryError("临时重要记忆文件不能是符号链接")
    try:
        content = path.read_text("utf-8").strip()
    except FileNotFoundError:
        return []
    if not content:
        return []
    return [
        {
            "filename": IMPORTANT_FILENAME,
            "tier": "important",
            "content": content,
            "weight": 0,
            "updated_at": path.stat().st_mtime,
            "expires_at": None,
        }
    ]


def _tier_entries(root: Path, user: str, config: dict[str, Any], tier: str) -> list[dict[str, Any]]:
    _validate_tier(tier)
    if tier == "important":
        return _important_entry(root, user)
    return MemoryStore(root, user, config).load_tier(tier)


def _bounded_integer(value: int, *, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} 必须是整数")
    return min(max(minimum, value), maximum)


def _summary(item: dict[str, Any], tier: str) -> dict[str, Any]:
    temporary = tier in TEMPORARY_TIERS
    return {
        "filename": item["filename"],
        "weight": int(item.get("weight", 0)) if temporary else None,
        "expires_at": item.get("expires_at") if temporary else None,
    }


def list_entries(
    root: Path,
    user: str,
    config: dict[str, Any],
    tier: str,
    limit: int = 50,
) -> dict[str, Any]:
    _validate_tier(tier)
    normalized_limit = _bounded_integer(limit, field="limit", minimum=1, maximum=500)
    if tier == "important":
        names = [IMPORTANT_FILENAME] if _important_entry(root, user) else []
        entries = [
            {"filename": filename, "weight": None, "expires_at": None}
            for filename in names[:normalized_limit]
        ]
    elif tier == "permanent":
        directory = MemoryStore(root, user, config).tier_dir("permanent")
        names = sorted(
            (
                path.name
                for path in directory.glob("*.md")
                if path.is_file() and not path.is_symlink()
            ),
            key=str.casefold,
        ) if directory.is_dir() else []
        entries = [
            {"filename": filename, "weight": None, "expires_at": None}
            for filename in names[:normalized_limit]
        ]
    else:
        index = MemoryStore(root, user, config).load_index(tier)
        names = sorted(index, key=str.casefold)
        entries = [
            {
                "filename": filename,
                "weight": int(index[filename].get("weight", 0)),
                "expires_at": index[filename].get("expires_at"),
            }
            for filename in names[:normalized_limit]
        ]
    return {
        "action": "list",
        "tier": tier,
        "entries": entries,
        "total": len(names),
        "truncated": len(names) > normalized_limit,
    }


def get_fragment(
    root: Path,
    user: str,
    config: dict[str, Any],
    tier: str,
    filename: str,
) -> dict[str, Any]:
    _validate_tier(tier)
    if tier == "important":
        if not isinstance(filename, str) or filename.strip().casefold() != IMPORTANT_FILENAME.casefold():
            raise FileNotFoundError(f"记忆不存在：{tier}/{filename}")
        normalized = IMPORTANT_FILENAME
        entries = _important_entry(root, user)
        if not entries:
            raise FileNotFoundError(f"记忆不存在：{tier}/{IMPORTANT_FILENAME}")
        item = entries[0]
    else:
        store = MemoryStore(root, user, config)
        normalized = normalize_memory_filename(filename)
        location = store.locate(normalized)
        if location is None or location.tier != tier:
            raise FileNotFoundError(f"记忆不存在：{tier}/{normalized}")
        if location.path.is_symlink():
            raise MemoryError("记忆文件不能是符号链接")
        item = store._entry(
            location,
            store.load_index(tier).get(location.filename)
            if tier in TEMPORARY_TIERS
            else None,
        )
        normalized = location.filename
    return {
        "action": "get",
        "tier": tier,
        "filename": normalized if tier != "important" else IMPORTANT_FILENAME,
        "content": str(item.get("content") or ""),
        "weight": int(item.get("weight", 0)) if tier in TEMPORARY_TIERS else None,
        "expires_at": item.get("expires_at") if tier in TEMPORARY_TIERS else None,
    }


def search_by_title(
    root: Path,
    user: str,
    config: dict[str, Any],
    tier: str,
    query: str,
    limit: int = 50,
    case_sensitive: bool = False,
) -> dict[str, Any]:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query 不能为空；列出全部记忆请使用 list action")
    if not isinstance(case_sensitive, bool):
        raise ValueError("case_sensitive 必须是布尔值")
    normalized_limit = _bounded_integer(limit, field="limit", minimum=1, maximum=500)
    needle = query.strip()
    comparison = needle if case_sensitive else needle.casefold()
    all_matches = []
    for item in _tier_entries(root, user, config, tier):
        stem = Path(str(item["filename"])).stem
        haystack = stem if case_sensitive else stem.casefold()
        if comparison in haystack:
            all_matches.append(_summary(item, tier))
    return {
        "action": "search_by_title",
        "tier": tier,
        "query": query,
        "matches": all_matches[:normalized_limit],
        "total_matches": len(all_matches),
        "truncated": len(all_matches) > normalized_limit,
    }


def search_by_content(
    root: Path,
    user: str,
    config: dict[str, Any],
    tier: str,
    query: str,
    limit: int = 50,
    context_chars: int = 240,
    case_sensitive: bool = False,
) -> dict[str, Any]:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query 不能为空；列出全部记忆请使用 list action")
    if not isinstance(case_sensitive, bool):
        raise ValueError("case_sensitive 必须是布尔值")
    normalized_limit = _bounded_integer(limit, field="limit", minimum=1, maximum=500)
    normalized_context = _bounded_integer(
        context_chars, field="context_chars", minimum=60, maximum=2000
    )
    needle = query.strip()
    comparison = needle if case_sensitive else needle.casefold()
    matches: list[dict[str, Any]] = []
    total_matches = 0
    for item in _tier_entries(root, user, config, tier):
        content = str(item.get("content") or "")
        haystack = content if case_sensitive else content.casefold()
        index = haystack.find(comparison)
        if index < 0:
            continue
        total_matches += 1
        if len(matches) >= normalized_limit:
            continue
        match_end = index + len(needle)
        body_budget = normalized_context
        center = (index + match_end) // 2
        start = max(0, min(len(content) - body_budget, center - body_budget // 2))
        end = min(len(content), start + body_budget)
        marker_chars = int(start > 0) + int(end < len(content))
        body_budget = max(1, normalized_context - marker_chars)
        start = max(0, min(len(content) - body_budget, center - body_budget // 2))
        end = min(len(content), start + body_budget)
        snippet = f"{'…' if start > 0 else ''}{content[start:end]}{'…' if end < len(content) else ''}"
        matches.append({**_summary(item, tier), "snippet": snippet[:normalized_context]})
    return {
        "action": "search_by_content",
        "tier": tier,
        "query": query,
        "matches": matches,
        "total_matches": total_matches,
        "truncated": total_matches > normalized_limit,
    }


def delete_fragment(
    root: Path,
    user: str,
    config: dict[str, Any],
    tier: str,
    filename: str,
) -> dict[str, Any]:
    _validate_tier(tier)
    if tier == "important":
        path = _important_path(root, user)
        existed = path.is_file()
        path.unlink(missing_ok=True)
        return {"action": "delete", "tier": tier, "filename": IMPORTANT_FILENAME, "deleted": existed}
    store = MemoryStore(root, user, config)
    normalized = normalize_memory_filename(filename)
    with store._lock:
        location = store.locate(normalized)
        if location is None or location.tier != tier:
            return {"action": "delete", "tier": tier, "filename": normalized, "deleted": False}
        store._delete_location(location)
    return {"action": "delete", "tier": tier, "filename": normalized, "deleted": True}


def add_fragment(
    root: Path,
    user: str,
    config: dict[str, Any],
    tier: str,
    filename: str,
    content: str,
) -> dict[str, Any]:
    _validate_tier(tier)
    body = content.strip()
    if not body:
        raise ValueError("记忆内容不能为空")
    if contains_sensitive_credential(body):
        raise ValueError("记忆内容包含疑似敏感凭据")
    if tier == "important":
        path = _important_path(root, user)
        if path.exists():
            raise FileExistsError("临时重要记忆文件已存在，请使用 edit")
        _atomic_text(path, body)
        return {"action": "add", "tier": tier, "filename": IMPORTANT_FILENAME}
    store = MemoryStore(root, user, config)
    normalized = normalize_memory_filename(filename)
    with store._lock:
        if store.locate(normalized) is not None:
            raise FileExistsError(f"同名记忆已存在：{normalized}")
        path = store.fragment_path(tier, normalized)
        _atomic_text(path, body)
        if tier in TEMPORARY_TIERS:
            index = store.load_index(tier)
            index[normalized] = store._new_meta(tier, utc_now())
            try:
                store.write_index(tier, index)
            except Exception:
                path.unlink(missing_ok=True)
                raise
    return {"action": "add", "tier": tier, "filename": normalized}


def edit_fragment(
    root: Path,
    user: str,
    config: dict[str, Any],
    tier: str,
    filename: str,
    content: str,
    *,
    new_filename: str | None = None,
) -> dict[str, Any]:
    _validate_tier(tier)
    body = content.strip()
    if not body:
        raise ValueError("记忆内容不能为空")
    if contains_sensitive_credential(body):
        raise ValueError("记忆内容包含疑似敏感凭据")
    if tier == "important":
        path = _important_path(root, user)
        if not path.is_file():
            raise FileNotFoundError("临时重要记忆文件不存在")
        _atomic_text(path, body)
        return {"action": "edit", "tier": tier, "filename": IMPORTANT_FILENAME}
    store = MemoryStore(root, user, config)
    normalized = normalize_memory_filename(filename)
    target_name = normalize_memory_filename(new_filename or normalized)
    with store._lock:
        location = store.locate(normalized)
        if location is None or location.tier != tier:
            raise FileNotFoundError(f"记忆不存在：{tier}/{normalized}")
        if target_name != normalized and store.locate(target_name) is not None:
            raise FileExistsError(f"目标记忆已存在：{target_name}")
        target_path = store.fragment_path(tier, target_name)
        if target_name == normalized:
            previous = location.path.read_text("utf-8")
            _atomic_text(location.path, body)
            if tier in TEMPORARY_TIERS:
                index = store.load_index(tier)
                index[normalized]["updated_at"] = utc_now().isoformat()
                try:
                    store.write_index(tier, index)
                except Exception:
                    _atomic_text(location.path, previous)
                    raise
        else:
            _atomic_text(target_path, body)
            if tier in TEMPORARY_TIERS:
                index = store.load_index(tier)
                meta = dict(index.pop(normalized))
                meta["updated_at"] = utc_now().isoformat()
                index[target_name] = meta
                try:
                    store.write_index(tier, index)
                except Exception:
                    target_path.unlink(missing_ok=True)
                    raise
            location.path.unlink()
    return {
        "action": "edit",
        "tier": tier,
        "filename": normalized,
        "new_filename": target_name,
    }


def write_important_memory(root: Path, user: str, content: str) -> None:
    body = content.strip()
    path = _important_path(root, user)
    if not body:
        path.unlink(missing_ok=True)
        return
    if contains_sensitive_credential(body):
        raise MemoryError("临时重要记忆包含疑似敏感凭据")
    _atomic_text(path, body)
