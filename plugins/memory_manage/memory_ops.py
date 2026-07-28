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
SEARCH_ALL_TIERS = (*TIERS,)
IMPORTANT_FILENAME = "memory_temporary_important.md"
IMPORTANT_MEMORY_PLACEHOLDER = """# 临时重要记忆

> 此文件由 memory_temporary_important 子代理自动维护，权重仅次于永久记忆。

暂无可提取的重要记忆。当临时记忆层级中出现符合重要特征的碎片时，子代理会自动写入此文件。"""


def _memory_ref(tier: str, filename: str) -> str:
    return f"{tier}:{filename}"


def _validate_tier(tier: str) -> str:
    if tier not in MANAGED_TIERS:
        raise ValueError(f"不支持的记忆层级：{tier}")
    return tier


def _search_tiers(tier: str) -> tuple[str, ...]:
    if tier == "all":
        return SEARCH_ALL_TIERS
    return (_validate_tier(tier),)


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


def _atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _important_path(root: Path, user: str) -> Path:
    return root / "users" / user / IMPORTANT_FILENAME


def _important_entry(
    root: Path,
    user: str,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    path = _important_path(root, user)
    if path.is_symlink():
        raise MemoryError("临时重要记忆文件不能是符号链接")
    try:
        content = path.read_text("utf-8").strip()
    except FileNotFoundError:
        return []
    if not content:
        return []
    store = MemoryStore(root, user, config)
    featured_sources = []
    for filename in sorted(store.load_important_view_sources(), key=str.casefold):
        location = store.locate(filename)
        if location is not None and location.tier in TEMPORARY_TIERS:
            featured_sources.append(
                {"tier": location.tier, "filename": location.filename}
            )
    return [
        {
            "filename": IMPORTANT_FILENAME,
            "tier": "important",
            "content": content,
            "weight": 0,
            "updated_at": path.stat().st_mtime,
            "expires_at": None,
            "featured_sources": featured_sources,
        }
    ]


def _tier_entries(root: Path, user: str, config: dict[str, Any], tier: str) -> list[dict[str, Any]]:
    _validate_tier(tier)
    if tier == "important":
        return _important_entry(root, user, config)
    return MemoryStore(root, user, config).load_tier(tier)


def _bounded_integer(value: int, *, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} 必须是整数")
    return min(max(minimum, value), maximum)


def _summary(item: dict[str, Any], tier: str) -> dict[str, Any]:
    temporary = tier in TEMPORARY_TIERS
    filename = str(item["filename"])
    return {
        "memory_ref": _memory_ref(tier, filename),
        "filename": filename,
        "weight": int(item.get("weight", 0)) if temporary else None,
        "created_at": item.get("created_at"),
        "content_updated_at": item.get("content_updated_at"),
        "last_used_at": item.get("last_used_at"),
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
        names = [IMPORTANT_FILENAME] if _important_entry(root, user, config) else []
        entries = [
            {
                "memory_ref": _memory_ref(tier, filename),
                "filename": filename,
                "weight": None,
                "expires_at": None,
            }
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
            {
                "memory_ref": _memory_ref(tier, filename),
                "filename": filename,
                "weight": None,
                "expires_at": None,
            }
            for filename in names[:normalized_limit]
        ]
    else:
        index = MemoryStore(root, user, config).load_index(tier)
        names = sorted(index, key=str.casefold)
        entries = [
            {
                "memory_ref": _memory_ref(tier, filename),
                "filename": filename,
                "weight": int(index[filename].get("weight", 0)),
                "created_at": index[filename].get("created_at"),
                "content_updated_at": index[filename].get("content_updated_at"),
                "last_used_at": index[filename].get("last_used_at"),
                "expires_at": index[filename].get("expires_at"),
            }
            for filename in names[:normalized_limit]
        ]
    return {
        "action": "list",
        "tier": tier,
        "timezone": "UTC",
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
        entries = _important_entry(root, user, config)
        if not entries:
            raise FileNotFoundError(f"记忆不存在：{tier}/{IMPORTANT_FILENAME}")
        item = entries[0]
    else:
        store = MemoryStore(root, user, config)
        normalized = normalize_memory_filename(filename)
        location = store.locate_in_tier(tier, normalized)
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
        "memory_ref": _memory_ref(
            tier, normalized if tier != "important" else IMPORTANT_FILENAME
        ),
        "filename": normalized if tier != "important" else IMPORTANT_FILENAME,
        "content": str(item.get("content") or ""),
        "weight": int(item.get("weight", 0)) if tier in TEMPORARY_TIERS else None,
        "created_at": item.get("created_at"),
        "content_updated_at": item.get("content_updated_at"),
        "last_used_at": item.get("last_used_at"),
        "expires_at": item.get("expires_at") if tier in TEMPORARY_TIERS else None,
        "featured_sources": item.get("featured_sources", [])
        if tier == "important"
        else None,
        "timezone": "UTC",
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
    for current_tier in _search_tiers(tier):
        for item in _tier_entries(root, user, config, current_tier):
            stem = Path(str(item["filename"])).stem
            haystack = stem if case_sensitive else stem.casefold()
            if comparison in haystack:
                match = _summary(item, current_tier)
                if tier == "all":
                    match["tier"] = current_tier
                all_matches.append(match)
    return {
        "action": "search_by_title",
        "tier": tier,
        "timezone": "UTC",
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
    for current_tier in _search_tiers(tier):
        for item in _tier_entries(root, user, config, current_tier):
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
            match = {
                **_summary(item, current_tier),
                "snippet": snippet[:normalized_context],
            }
            if tier == "all":
                match["tier"] = current_tier
            matches.append(match)
    return {
        "action": "search_by_content",
        "tier": tier,
        "timezone": "UTC",
        "query": query,
        "matches": matches,
        "total_matches": total_matches,
        "truncated": total_matches > normalized_limit,
    }


def search_many(
    root: Path,
    user: str,
    config: dict[str, Any],
    tier: str,
    queries: list[dict[str, Any]],
    *,
    limit: int = 10,
    context_chars: int = 240,
    case_sensitive: bool = False,
) -> dict[str, Any]:
    """Search title and content for several candidates in one tool call."""

    tiers = _search_tiers(tier)
    if not isinstance(queries, list) or not queries:
        raise ValueError("search_many 需要非空 queries 数组")
    if len(queries) > 20:
        raise ValueError("search_many 单次最多接收 20 个查询")
    normalized_limit = _bounded_integer(
        limit,
        field="limit",
        minimum=1,
        maximum=50,
    )
    results: list[dict[str, Any]] = []
    for index, raw_query in enumerate(queries):
        if not isinstance(raw_query, dict):
            raise ValueError(f"queries[{index}] 必须是对象")
        title = str(raw_query.get("title") or "").strip()
        content = str(raw_query.get("content") or "").strip()
        if not title and not content:
            raise ValueError(f"queries[{index}] 至少需要 title 或 content")
        matches: dict[str, dict[str, Any]] = {}
        for current_tier in tiers:
            if title:
                title_result = search_by_title(
                    root,
                    user,
                    config,
                    current_tier,
                    title,
                    limit=normalized_limit,
                    case_sensitive=case_sensitive,
                )
                for match in title_result["matches"]:
                    memory_ref = str(match["memory_ref"])
                    matches[memory_ref] = {
                        **match,
                        "tier": current_tier,
                        "matched_by": ["title"],
                    }
            if content:
                content_result = search_by_content(
                    root,
                    user,
                    config,
                    current_tier,
                    content,
                    limit=normalized_limit,
                    context_chars=context_chars,
                    case_sensitive=case_sensitive,
                )
                for match in content_result["matches"]:
                    memory_ref = str(match["memory_ref"])
                    existing = matches.get(memory_ref)
                    if existing is None:
                        matches[memory_ref] = {
                            **match,
                            "tier": current_tier,
                            "matched_by": ["content"],
                        }
                    else:
                        existing["matched_by"] = ["title", "content"]
                        existing["snippet"] = match.get("snippet")
        ordered = sorted(
            matches.values(),
            key=lambda item: (
                -len(item.get("matched_by") or []),
                str(item.get("tier") or ""),
                str(item.get("filename") or "").casefold(),
            ),
        )
        results.append(
            {
                "index": index,
                "title": title,
                "content": content,
                "matches": ordered[:normalized_limit],
                "total_matches": len(ordered),
                "truncated": len(ordered) > normalized_limit,
            }
        )
    return {
        "action": "search_many",
        "tier": tier,
        "timezone": "UTC",
        "results": results,
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
        raise MemoryError("临时重要记忆文件不可删除")
    store = MemoryStore(root, user, config)
    normalized = normalize_memory_filename(filename)
    with store._lock:
        location = store.locate_in_tier(tier, normalized)
        if location is None or location.tier != tier:
            return {"action": "delete", "tier": tier, "filename": normalized, "deleted": False}
        file_existed = location.path.is_file()
        store._delete_location(location)
    return {
        "action": "delete",
        "tier": tier,
        "memory_ref": _memory_ref(tier, location.filename),
        "filename": location.filename,
        "deleted": True,
        "index_removed": location.indexed,
        "file_removed": file_existed,
        "repaired_orphan": location.indexed and not file_existed,
    }


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
        return {
            "action": "add",
            "tier": tier,
            "memory_ref": _memory_ref(tier, IMPORTANT_FILENAME),
            "filename": IMPORTANT_FILENAME,
        }
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
    return {
        "action": "add",
        "tier": tier,
        "memory_ref": _memory_ref(tier, normalized),
        "filename": normalized,
    }


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
        return {
            "action": "edit",
            "tier": tier,
            "memory_ref": _memory_ref(tier, IMPORTANT_FILENAME),
            "filename": IMPORTANT_FILENAME,
        }
    store = MemoryStore(root, user, config)
    normalized = normalize_memory_filename(filename)
    with store._lock:
        location = store.locate_in_tier(tier, normalized)
        if location is None or location.tier != tier:
            raise FileNotFoundError(f"记忆不存在：{tier}/{normalized}")
        source_name = location.filename
        target_name = normalize_memory_filename(new_filename or source_name)
        if target_name.casefold() == source_name.casefold():
            target_name = source_name
        if target_name != source_name and store.locate(target_name) is not None:
            raise FileExistsError(f"目标记忆已存在：{target_name}")
        target_path = store.fragment_path(tier, target_name)
        if target_name == source_name:
            previous = location.path.read_text("utf-8")
            _atomic_text(location.path, body)
            if tier in TEMPORARY_TIERS:
                try:
                    store._touch_temporary(
                        location,
                        utc_now(),
                        content_changed=True,
                    )
                except Exception:
                    _atomic_text(location.path, previous)
                    raise
        else:
            _atomic_text(target_path, body)
            if tier in TEMPORARY_TIERS:
                index = store.load_index(tier)
                meta = dict(index.pop(source_name))
                current = utc_now().isoformat()
                meta["content_updated_at"] = current
                meta["updated_at"] = current
                meta["last_used_at"] = current
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
        "timezone": "UTC",
        "memory_ref": _memory_ref(tier, target_name),
        "filename": source_name,
        "new_filename": target_name,
    }


def write_important_memory(root: Path, user: str, content: str) -> None:
    body = content.strip() or IMPORTANT_MEMORY_PLACEHOLDER
    path = _important_path(root, user)
    if contains_sensitive_credential(body):
        raise MemoryError("临时重要记忆包含疑似敏感凭据")
    _atomic_text(path, body)


def apply_important_memory_view(
    root: Path,
    user: str,
    config: dict[str, Any],
    content: str,
    featured: list[dict[str, Any]],
    reconciliations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Atomically publish the hot view and reconcile permanent duplicates.

    Temporary fragments mirrored by the view remain authoritative and continue
    their normal lifecycle.  Only a separately declared permanent reconciliation
    may remove a temporary source.
    """

    body = content.strip() or IMPORTANT_MEMORY_PLACEHOLDER
    if contains_sensitive_credential(body):
        raise MemoryError("临时重要记忆包含疑似敏感凭据")
    if not isinstance(featured, list) or not isinstance(reconciliations, list):
        raise MemoryError("临时重要记忆来源和永久协调结果必须是数组")

    store = MemoryStore(root, user, config)
    important_path = _important_path(root, user)
    featured_names: list[str] = []
    actions: list[dict[str, Any]] = []
    source_keys: set[tuple[str, str]] = set()
    target_names: set[str] = set()

    with store._lock:
        for index, raw in enumerate(featured):
            if not isinstance(raw, dict):
                raise MemoryError(f"featured[{index}] 必须是对象")
            tier = str(raw.get("tier") or "").strip()
            if tier not in TEMPORARY_TIERS:
                raise MemoryError(f"featured[{index}].tier 不是临时层")
            filename = normalize_memory_filename(raw.get("filename"))
            location = store.locate_in_tier(tier, filename)
            if location is None:
                raise MemoryError(f"临时重要记忆来源不存在：{tier}/{filename}")
            featured_names.append(location.filename)

        for index, raw in enumerate(reconciliations):
            if not isinstance(raw, dict):
                raise MemoryError(f"permanent_reconciliations[{index}] 必须是对象")
            action = str(raw.get("action") or "").strip().casefold()
            if action not in {"drop_duplicate", "merge_permanent"}:
                raise MemoryError(
                    f"permanent_reconciliations[{index}].action 无效"
                )
            tier = str(raw.get("tier") or "").strip()
            if tier not in TEMPORARY_TIERS:
                raise MemoryError(
                    f"permanent_reconciliations[{index}].tier 不是临时层"
                )
            filename = normalize_memory_filename(raw.get("filename"))
            source = store.locate_in_tier(tier, filename)
            if source is None:
                raise MemoryError(f"永久协调来源不存在：{tier}/{filename}")
            source_key = (tier, source.filename)
            if source_key in source_keys:
                raise MemoryError(f"永久协调来源重复：{tier}/{source.filename}")
            source_keys.add(source_key)

            permanent_filename = normalize_memory_filename(
                raw.get("permanent_filename")
            )
            target = store.locate_in_tier("permanent", permanent_filename)
            if target is None:
                raise MemoryError(f"永久协调目标不存在：{permanent_filename}")
            if action == "merge_permanent":
                merged_content = str(raw.get("content") or "").strip()
                if not merged_content:
                    raise MemoryError("永久记忆融合内容不能为空")
                if contains_sensitive_credential(merged_content):
                    raise MemoryError("永久记忆融合内容包含疑似敏感凭据")
                if target.filename in target_names:
                    raise MemoryError(f"同一永久记忆不能在单次巡检中重复融合：{target.filename}")
                target_names.add(target.filename)
            else:
                merged_content = None
            actions.append(
                {
                    "action": action,
                    "source": source,
                    "target": target,
                    "content": merged_content,
                }
            )

        reconciled_names = {item["source"].filename for item in actions}
        featured_names = list(
            dict.fromkeys(
                filename
                for filename in featured_names
                if filename not in reconciled_names
            )
        )

        paths = {important_path, store.important_view_path()}
        for item in actions:
            source = item["source"]
            paths.add(source.path)
            paths.add(store.path(source.tier))
            paths.add(item["target"].path)
        snapshots = {
            path: path.read_bytes() if path.is_file() else None
            for path in paths
        }

        try:
            _atomic_text(important_path, body)
            store.set_important_view_sources(featured_names)
            for item in actions:
                if item["action"] == "merge_permanent":
                    _atomic_text(item["target"].path, item["content"])
                store._delete_location(item["source"])
        except Exception:
            for path, previous in snapshots.items():
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    _atomic_bytes(path, previous)
            raise

    return {
        "featured": featured_names,
        "reconciled": [
            {
                "action": item["action"],
                "filename": item["source"].filename,
                "permanent_filename": item["target"].filename,
            }
            for item in actions
        ],
    }
