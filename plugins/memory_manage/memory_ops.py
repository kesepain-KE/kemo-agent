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


def search_by_title(
    root: Path,
    user: str,
    config: dict[str, Any],
    tier: str,
    query: str,
) -> dict[str, Any]:
    needle = query.casefold().strip()
    matches = [
        {
            "filename": item["filename"],
            "tier": tier,
            "weight": int(item.get("weight", 0)),
            "expires_at": item.get("expires_at"),
        }
        for item in _tier_entries(root, user, config, tier)
        if not needle or needle in Path(str(item["filename"])).stem.casefold()
    ]
    return {"action": "search_by_title", "tier": tier, "query": query, "matches": matches}


def search_by_content(
    root: Path,
    user: str,
    config: dict[str, Any],
    tier: str,
    query: str,
) -> dict[str, Any]:
    needle = query.casefold().strip()
    matches: list[dict[str, Any]] = []
    for item in _tier_entries(root, user, config, tier):
        content = str(item.get("content") or "")
        folded = content.casefold()
        if needle and needle not in folded:
            continue
        index = folded.find(needle) if needle else 0
        start = max(0, index - 120)
        end = min(len(content), max(index + len(needle) + 120, 240))
        matches.append(
            {
                "filename": item["filename"],
                "tier": tier,
                "content": content if not needle else None,
                "snippet": content[start:end],
                "weight": int(item.get("weight", 0)),
                "expires_at": item.get("expires_at"),
            }
        )
    return {"action": "search_by_content", "tier": tier, "query": query, "matches": matches}


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
