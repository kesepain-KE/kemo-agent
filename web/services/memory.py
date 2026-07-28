"""记忆浏览与人工维护领域服务。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from run.config import load_config
from run.memory import (
    TIERS,
    MemoryError as RuntimeMemoryError,
    MemoryStore,
    contains_sensitive_credential,
    normalize_memory_filename,
    utc_now,
)
from web.constants import IMPORTANT_MEMORY_MAX_HARD_CHARS
from web.errors import InvalidRequestError, NotFoundError
from web.services._io import (
    TEXT_DOCUMENT_MAX_CHARS,
    atomic_write as _atomic_write,
    validated_text as _validated_text,
)
class MemoryServiceMixin:
    def memory_summary(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        config = load_config(name, self.root)
        items = MemoryStore(self.root, name, config).list_items()
        result = []
        for item in items:
            content = str(item.get("content") or "")
            result.append(
                {
                    "memory_ref": f"{item.get('tier')}:{item.get('filename')}",
                    "filename": str(item.get("filename") or ""),
                    "tier": str(item.get("tier") or ""),
                    "weight": int(item.get("weight") or 0),
                    "created_at": str(item.get("created_at") or ""),
                    "content_updated_at": str(
                        item.get("content_updated_at") or item.get("updated_at") or ""
                    ),
                    "updated_at": str(item.get("updated_at") or ""),
                    "last_used_at": item.get("last_used_at"),
                    "tier_entered_at": item.get("tier_entered_at"),
                    "expires_at": item.get("expires_at"),
                    "timezone": "UTC",
                    "preview": content[:160],
                    "truncated": len(content) > 160,
                }
            )
        tiers = ("seven_days", "one_month", "half_year", "permanent")
        return {
            "user": name,
            "summary": {
                "total": len(result),
                **{tier: sum(item["tier"] == tier for item in result) for tier in tiers},
            },
            "items": result,
        }

    def memory_item(self, user: Any, tier: Any, filename: Any) -> dict[str, Any]:
        name = self.require_user(user)
        try:
            normalized = normalize_memory_filename(filename)
            target_tier = str(tier or "")
            if target_tier not in TIERS:
                raise InvalidRequestError(f"tier 只允许 {', '.join(TIERS)}")
            store = MemoryStore(self.root, name, load_config(name, self.root))
            location = store.locate_in_tier(target_tier, normalized)
            item = (
                store._entry(
                    location,
                    store.load_index(target_tier).get(location.filename)
                    if target_tier != "permanent"
                    else None,
                )
                if location is not None
                else None
            )
        except RuntimeMemoryError as exc:
            raise InvalidRequestError(str(exc)) from None
        if item is None:
            raise NotFoundError(f"记忆不存在：{target_tier}/{normalized}")
        return {
            "user": name,
            "memory_ref": f"{item['tier']}:{item['filename']}",
            **item,
        }

    def put_memory(
        self,
        user: Any,
        filename: Any,
        content: Any,
        tier: Any = None,
    ) -> dict[str, Any]:
        name = self.require_user(user)
        text = _validated_text(content, max_chars=TEXT_DOCUMENT_MAX_CHARS)
        if not text.strip():
            raise InvalidRequestError("记忆内容不能为空")
        if contains_sensitive_credential(text):
            raise InvalidRequestError("记忆内容包含疑似敏感凭据，已拒绝写入")
        try:
            normalized = normalize_memory_filename(filename)
            target_tier = tier if tier is not None else None
            if target_tier is not None and target_tier not in TIERS:
                raise InvalidRequestError(f"tier 只允许 {', '.join(TIERS)}")
            store = MemoryStore(self.root, name, load_config(name, self.root))
            scoped_existing = (
                store.locate_in_tier(target_tier, normalized)
                if target_tier is not None
                else None
            )
            if scoped_existing is not None:
                if not scoped_existing.path.is_file():
                    raise RuntimeMemoryError(
                        f"记忆索引指向不存在的文件：{scoped_existing.path}"
                    )
                previous = scoped_existing.path.read_bytes()
                _atomic_write(scoped_existing.path, text.encode("utf-8"))
                try:
                    if scoped_existing.indexed:
                        store._touch_temporary(
                            scoped_existing,
                            utc_now(),
                            content_changed=True,
                        )
                except Exception:
                    _atomic_write(scoped_existing.path, previous)
                    raise
                existing = scoped_existing
            else:
                existing = store.locate(normalized)
                if existing is not None and target_tier == "permanent":
                    result = store.upsert_candidates(
                        [{"filename": normalized, "content": text, "explicit": True}]
                    )
                else:
                    result = store.upsert_candidates(
                        [{"filename": normalized, "content": text}]
                    )
                if result.get("rejected"):
                    raise RuntimeMemoryError("记忆内容未通过运行时校验")
                existing = store.locate(normalized)
                if existing is not None and target_tier and target_tier != existing.tier:
                    if existing.tier == "permanent":
                        raise RuntimeMemoryError("永久记忆不能降级到临时层")
                    rank = {tier_name: index for index, tier_name in enumerate(TIERS)}
                    if rank[target_tier] < rank[existing.tier]:
                        raise RuntimeMemoryError("临时记忆只能向更长期层级晋升")
                    store._promote_location(existing, target_tier, utc_now())
                    existing = store.locate_in_tier(target_tier, normalized)
            if existing is None:
                raise RuntimeMemoryError(f"记忆写入后无法定位：{normalized}")
            item = store._entry(
                existing,
                store.load_index(existing.tier).get(existing.filename)
                if existing.indexed
                else None,
            )
        except RuntimeMemoryError as exc:
            raise InvalidRequestError(str(exc)) from None
        return {
            "user": name,
            "memory_ref": f"{item['tier']}:{item['filename']}",
            **item,
            "updated": True,
        }

    def delete_memory(self, user: Any, tier: Any, filename: Any) -> dict[str, Any]:
        name = self.require_user(user)
        try:
            normalized = normalize_memory_filename(filename)
            target_tier = str(tier or "")
            if target_tier not in TIERS:
                raise InvalidRequestError(f"tier 只允许 {', '.join(TIERS)}")
            store = MemoryStore(self.root, name, load_config(name, self.root))
            location = store.locate_in_tier(target_tier, normalized)
            if location is None:
                raise NotFoundError(f"记忆不存在：{target_tier}/{normalized}")
            file_existed = location.path.is_file()
            store._delete_location(location)
        except RuntimeMemoryError as exc:
            raise InvalidRequestError(str(exc)) from None
        return {
            "user": name,
            "tier": target_tier,
            "memory_ref": f"{target_tier}:{location.filename}",
            "filename": location.filename,
            "deleted": True,
            "index_removed": location.indexed,
            "file_removed": file_existed,
            "repaired_orphan": location.indexed and not file_existed,
        }

    def important_memory(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        path = self.root / "users" / name / "memory_temporary_important.md"
        if not path.is_file():
            raise NotFoundError("临时重要记忆不存在")
        content = path.read_text("utf-8")
        return {
            "user": name,
            "path": f"users/{name}/memory_temporary_important.md",
            "content": content,
            "size": len(content.encode()),
            "updated_at": datetime.fromtimestamp(
                path.stat().st_mtime, timezone.utc
            ).astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
        }

    def update_important_memory(self, user: Any, content: Any) -> dict[str, Any]:
        name = self.require_user(user)
        config = load_config(name, self.root)
        configured_limit = int(
            (config.get("memory") or {}).get(
                "important_memory_max_chars", IMPORTANT_MEMORY_MAX_HARD_CHARS
            )
        )
        text = _validated_text(
            content,
            max_chars=min(IMPORTANT_MEMORY_MAX_HARD_CHARS, max(1, configured_limit)),
        )
        if not text.strip():
            raise InvalidRequestError("临时重要记忆内容不能为空")
        if contains_sensitive_credential(text):
            raise InvalidRequestError("临时重要记忆包含疑似敏感凭据，已拒绝写入")
        path = self.root / "users" / name / "memory_temporary_important.md"
        _atomic_write(path, text.encode("utf-8"))
        return {
            "user": name,
            "path": f"users/{name}/memory_temporary_important.md",
            "content": text,
            "size": len(text.encode()),
            "updated_at": datetime.fromtimestamp(
                path.stat().st_mtime, timezone.utc
            ).astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
            "updated": True,
        }

