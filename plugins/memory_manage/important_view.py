"""Atomic maintenance of the user's derived temporary-important memory view."""

from __future__ import annotations

import os
import uuid
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from run.memory import (
    TEMPORARY_TIERS,
    MemoryError,
    MemoryStore,
    contains_sensitive_credential,
    normalize_memory_filename,
)


IMPORTANT_FILENAME = "memory_temporary_important.md"
IMPORTANT_MEMORY_PLACEHOLDER = """# 临时重要记忆

> 此文件由 memory_temporary_important 子代理自动维护，权重仅次于永久记忆。

暂无可提取的重要记忆。当临时记忆层级中出现符合重要特征的碎片时，子代理会自动写入此文件。"""


def important_path(root: Path, user: str) -> Path:
    return root / "users" / user / IMPORTANT_FILENAME


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


def write_important_memory(root: Path, user: str, content: str) -> None:
    body = content.strip() or IMPORTANT_MEMORY_PLACEHOLDER
    path = important_path(root, user)
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
    """Atomically publish the hot view and reconcile permanent duplicates."""

    body = content.strip() or IMPORTANT_MEMORY_PLACEHOLDER
    if contains_sensitive_credential(body):
        raise MemoryError("临时重要记忆包含疑似敏感凭据")
    if not isinstance(featured, list) or not isinstance(reconciliations, list):
        raise MemoryError("临时重要记忆来源和永久协调结果必须是数组")

    store = MemoryStore(root, user, config)
    view_path = important_path(root, user)
    featured_names: list[str] = []
    actions: list[dict[str, Any]] = []
    source_keys: set[tuple[str, str]] = set()
    target_names: set[str] = set()
    reference_corrections: list[dict[str, str]] = []

    def resolve_location(tier: str, value: Any, field: str):
        requested = normalize_memory_filename(value)
        exact = store.locate_in_tier(tier, requested)
        if exact is not None:
            return exact
        scores = sorted(
            (
                (
                    SequenceMatcher(
                        None,
                        requested.casefold(),
                        str(item["filename"]).casefold(),
                    ).ratio(),
                    str(item["filename"]),
                )
                for item in store.load_tier(tier)
            ),
            reverse=True,
        )
        if not scores:
            return None
        best_score, best_name = scores[0]
        second_score = scores[1][0] if len(scores) > 1 else 0.0
        if best_score < 0.88 or best_score - second_score < 0.08:
            return None
        resolved = store.locate_in_tier(tier, best_name)
        if resolved is not None:
            reference_corrections.append(
                {
                    "field": field,
                    "tier": tier,
                    "requested": requested,
                    "resolved": resolved.filename,
                }
            )
        return resolved

    with store._lock:
        for index, raw in enumerate(featured):
            if not isinstance(raw, dict):
                raise MemoryError(f"featured[{index}] 必须是对象")
            tier = str(raw.get("tier") or "").strip()
            if tier not in TEMPORARY_TIERS:
                raise MemoryError(f"featured[{index}].tier 不是临时层")
            filename = normalize_memory_filename(raw.get("filename"))
            location = resolve_location(tier, filename, f"featured[{index}].filename")
            if location is None:
                raise MemoryError(f"临时重要记忆来源不存在：{tier}/{filename}")
            featured_names.append(location.filename)

        for index, raw in enumerate(reconciliations):
            if not isinstance(raw, dict):
                raise MemoryError(f"permanent_reconciliations[{index}] 必须是对象")
            action = str(raw.get("action") or "").strip().casefold()
            if action not in {"drop_duplicate", "merge_permanent"}:
                raise MemoryError(f"permanent_reconciliations[{index}].action 无效")
            tier = str(raw.get("tier") or "").strip()
            if tier not in TEMPORARY_TIERS:
                raise MemoryError(f"permanent_reconciliations[{index}].tier 不是临时层")
            filename = normalize_memory_filename(raw.get("filename"))
            source = resolve_location(
                tier,
                filename,
                f"permanent_reconciliations[{index}].filename",
            )
            if source is None:
                raise MemoryError(f"永久协调来源不存在：{tier}/{filename}")
            source_key = (tier, source.filename)
            if source_key in source_keys:
                raise MemoryError(f"永久协调来源重复：{tier}/{source.filename}")
            source_keys.add(source_key)

            permanent_filename = normalize_memory_filename(
                raw.get("permanent_filename")
            )
            target = resolve_location(
                "permanent",
                permanent_filename,
                f"permanent_reconciliations[{index}].permanent_filename",
            )
            if target is None:
                raise MemoryError(f"永久协调目标不存在：{permanent_filename}")
            if action == "merge_permanent":
                merged_content = str(raw.get("content") or "").strip()
                if not merged_content:
                    raise MemoryError("永久记忆融合内容不能为空")
                if contains_sensitive_credential(merged_content):
                    raise MemoryError("永久记忆融合内容包含疑似敏感凭据")
                if target.filename in target_names:
                    raise MemoryError(
                        f"同一永久记忆不能在单次巡检中重复融合：{target.filename}"
                    )
                target_names.add(target.filename)
            else:
                merged_content = None
            actions.append(
                {
                    "action": action,
                    "tier": source.tier,
                    "filename": source.filename,
                    "permanent_filename": target.filename,
                    "content": merged_content,
                }
            )

        reconciled_names = {str(item["filename"]) for item in actions}
        featured_names = list(
            dict.fromkeys(
                filename
                for filename in featured_names
                if filename not in reconciled_names
            )
        )
        previous = view_path.read_bytes() if view_path.is_file() else None
        try:
            _atomic_text(view_path, body)
            store.reconcile_important_memory(featured_names, actions)
        except Exception:
            if previous is None:
                view_path.unlink(missing_ok=True)
            else:
                _atomic_bytes(view_path, previous)
            raise

    return {
        "featured": featured_names,
        "reference_corrections": reference_corrections,
        "reconciled": [
            {
                "action": item["action"],
                "filename": item["filename"],
                "permanent_filename": item["permanent_filename"],
            }
            for item in actions
        ],
    }


__all__ = [
    "IMPORTANT_FILENAME",
    "IMPORTANT_MEMORY_PLACEHOLDER",
    "apply_important_memory_view",
    "important_path",
    "write_important_memory",
]
