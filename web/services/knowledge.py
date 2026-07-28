"""三层知识文件领域服务。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from run.config import load_config
from run.prompt_sources import iter_files
from run.source_policy import MainAgentSourcePolicy
from web.constants import _KNOWLEDGE_SCOPES, _KNOWLEDGE_SUFFIXES
from web.errors import ConflictError, InvalidRequestError, NotFoundError
from web.services._io import (
    atomic_write as _atomic_write,
    validated_text as _validated_text,
)
from web.services._paths import _reject_link_path, _safe_relative_target


class KnowledgeServiceMixin:
    def knowledge(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        config = load_config(name, self.root)
        source_policy = MainAgentSourcePolicy.from_config(config)
        policy_summary = source_policy.public_summary()
        documents = []
        for scope, base in (
            ("user", self.root / "users" / name / "knowledge"),
            ("shared", self.root / "shared_knowledge"),
            ("global", self.root / "global_knowledge"),
        ):
            for path in iter_files(base, suffixes={".md", ".txt", ".json"}):
                try:
                    stat = path.stat()
                    size = stat.st_size
                    updated_at = stat.st_mtime
                except OSError:
                    size = 0
                    updated_at = 0
                documents.append(
                    {
                        "scope": scope,
                        "relative_path": path.relative_to(base).as_posix(),
                        "title": path.stem,
                        "size": size,
                        "updated_at": updated_at,
                        "active_for_main_agent": scope
                        in source_policy.direct_knowledge_scopes(),
                    }
                )
        return {
            "user": name,
            "enabled": True,
            "retrieval": {
                "mode": "index_only",
                "full_index": True,
            },
            "summary": {
                "documents": len(documents),
                "user_documents": sum(item["scope"] == "user" for item in documents),
                "shared_documents": sum(item["scope"] == "shared" for item in documents),
                "global_documents": sum(item["scope"] == "global" for item in documents),
            },
            "documents": documents,
            "extensions": {"kemo_graph": policy_summary["kemo_graph"]["status"]},
            "source_policy": policy_summary,
        }

    def _knowledge_root(self, user: Any, scope: Any) -> tuple[str, Path]:
        name = self.require_user(user)
        if scope not in _KNOWLEDGE_SCOPES:
            raise InvalidRequestError("scope 只允许 user、shared 或 global")
        roots = {
            "user": self.root / "users" / name / "knowledge",
            "shared": self.root / "shared_knowledge",
            "global": self.root / "global_knowledge",
        }
        return name, roots[scope]

    def _knowledge_target(self, user: Any, scope: Any, path: Any) -> tuple[str, str, Path]:
        name, root = self._knowledge_root(user, scope)
        relative, target = _safe_relative_target(root, path)
        _reject_link_path(root.resolve(), target)
        if Path(relative).suffix.lower() not in _KNOWLEDGE_SUFFIXES:
            raise InvalidRequestError("知识文件只允许 .md、.txt 或 .json")
        return name, str(scope), target

    def knowledge_document(self, user: Any, scope: Any, path: Any) -> dict[str, Any]:
        name, normalized_scope, target = self._knowledge_target(user, scope, path)
        if not target.is_file():
            raise NotFoundError(f"知识文件不存在：{path}")
        try:
            content = target.read_text("utf-8")
        except UnicodeDecodeError:
            raise InvalidRequestError("知识文件不是有效 UTF-8 文本") from None
        return {
            "user": name,
            "scope": normalized_scope,
            "relative_path": target.relative_to(
                self._knowledge_root(name, normalized_scope)[1]
            ).as_posix(),
            "content": content,
            "size": len(content.encode("utf-8")),
            "updated_at": target.stat().st_mtime,
        }

    def put_knowledge_document(
        self,
        user: Any,
        scope: Any,
        path: Any,
        content: Any,
    ) -> dict[str, Any]:
        name, normalized_scope, target = self._knowledge_target(user, scope, path)
        text = _validated_text(content)
        if target.suffix.lower() == ".json":
            try:
                json.loads(text)
            except json.JSONDecodeError as exc:
                raise InvalidRequestError(f"JSON 知识文件格式无效：{exc.msg}") from None
        _atomic_write(target, text.encode("utf-8"))
        return {
            "user": name,
            "scope": normalized_scope,
            "relative_path": target.relative_to(
                self._knowledge_root(name, normalized_scope)[1]
            ).as_posix(),
            "size": len(text.encode("utf-8")),
            "updated": True,
            "index_refresh": "next_request",
        }

    def delete_knowledge_document(self, user: Any, scope: Any, path: Any) -> dict[str, Any]:
        name, normalized_scope, target = self._knowledge_target(user, scope, path)
        if not target.is_file():
            raise NotFoundError(f"知识文件不存在：{path}")
        target.unlink()
        return {
            "user": name,
            "scope": normalized_scope,
            "relative_path": target.relative_to(
                self._knowledge_root(name, normalized_scope)[1]
            ).as_posix(),
            "deleted": True,
        }

    def move_knowledge_document(
        self,
        user: Any,
        scope: Any,
        path: Any,
        new_path: Any,
    ) -> dict[str, Any]:
        name, normalized_scope, source = self._knowledge_target(user, scope, path)
        _, _, target = self._knowledge_target(user, scope, new_path)
        if not source.is_file():
            raise NotFoundError(f"知识文件不存在：{path}")
        if target.exists():
            raise ConflictError(f"知识文件已存在：{new_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)
        root = self._knowledge_root(name, normalized_scope)[1]
        return {
            "user": name,
            "scope": normalized_scope,
            "relative_path": source.relative_to(root).as_posix(),
            "new_relative_path": target.relative_to(root).as_posix(),
            "moved": True,
        }


