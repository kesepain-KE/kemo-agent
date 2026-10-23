"""三层知识文件领域服务。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from web.service import WebRunService

import json
import os
from pathlib import Path
from typing import Any

from run.config import load_config
from run.config import IGNORED_RUNTIME_DIRECTORY_NAMES, iter_files
from run.config import MainAgentSourcePolicy
from run.config import (
    CLASSIFICATION_FILENAME,
    KnowledgeClassificationError,
    UNCATEGORIZED,
    classification_path,
    ensure_classification_file,
    read_classifications,
    remove_classification,
    rename_classification,
    set_classification,
)
from web.constants import _KNOWLEDGE_SCOPES, _KNOWLEDGE_SUFFIXES
from web.errors import ConflictError, InvalidRequestError, NotFoundError
from web.services._io import (
    atomic_write as _atomic_write,
    validated_text as _validated_text,
)
from web.services._paths import _reject_link_path, _safe_relative_target


class KnowledgeServiceMixin:
    if TYPE_CHECKING:
        require_user = WebRunService.require_user
        root: Path

    def knowledge(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        config = load_config(name, self.root)
        source_policy = MainAgentSourcePolicy.from_config(config)
        policy_summary = source_policy.public_summary()
        documents = []
        classifications: dict[str, dict[str, str]] = {}
        classification_errors: list[str] = []
        for scope, base in (
            ("user", self.root / "users" / name / "knowledge"),
            ("shared", self.root / "shared_knowledge"),
            ("global", self.root / "global_knowledge"),
        ):
            try:
                ensure_classification_file(base)
                classifications[scope] = read_classifications(base)
            except KnowledgeClassificationError as exc:
                classifications[scope] = {}
                classification_errors.append(f"{scope} 层分类标记不可用：{exc}")
            for path in iter_files(base, suffixes={".md", ".txt", ".json"}):
                try:
                    stat = path.stat()
                    size = stat.st_size
                    updated_at = stat.st_mtime
                except OSError:
                    size = 0
                    updated_at = 0
                relative_path = path.relative_to(base).as_posix()
                documents.append(
                    {
                        "scope": scope,
                        "relative_path": relative_path,
                        "title": path.stem,
                        "size": size,
                        "updated_at": updated_at,
                        "category": classifications.get(scope, {}).get(
                            relative_path,
                            UNCATEGORIZED,
                        ),
                        "active_for_main_agent": scope
                        in source_policy.direct_knowledge_scopes(),
                    }
                )
        category_counts: dict[str, dict[str, int]] = {
            scope: {UNCATEGORIZED: 0} for scope in _KNOWLEDGE_SCOPES
        }
        category_counts["all"] = {UNCATEGORIZED: 0}
        for document in documents:
            category = str(document["category"])
            for scope in ("all", document["scope"]):
                counts = category_counts[scope]
                counts[category] = counts.get(category, 0) + 1
        categories = {
            scope: [
                {"name": category, "count": count}
                for category, count in sorted(
                    counts.items(),
                    key=lambda item: (
                        0 if item[0] == UNCATEGORIZED else 1,
                        item[0].casefold(),
                        item[0],
                    ),
                )
            ]
            for scope, counts in category_counts.items()
        }
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
            "categories": categories,
            "classification_file": CLASSIFICATION_FILENAME,
            "classification_errors": classification_errors,
            "source_policy": policy_summary,
        }

    def _knowledge_classifications(self, user: Any, scope: Any) -> tuple[str, Path, dict[str, str]]:
        name, root = self._knowledge_root(user, scope)
        path = classification_path(root)
        try:
            _reject_link_path(root.resolve(), path)
            ensure_classification_file(root)
            return name, root, read_classifications(root)
        except KnowledgeClassificationError as exc:
            raise InvalidRequestError(f"{CLASSIFICATION_FILENAME} 格式无效：{exc}") from None

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
        if any(
            part.casefold() in IGNORED_RUNTIME_DIRECTORY_NAMES
            for part in Path(relative).parts
        ):
            raise InvalidRequestError("知识图谱运行时目录不能作为知识文件访问或修改")
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
        _, root, classifications = self._knowledge_classifications(name, normalized_scope)
        return {
            "user": name,
            "scope": normalized_scope,
            "relative_path": target.relative_to(
                self._knowledge_root(name, normalized_scope)[1]
            ).as_posix(),
            "content": content,
            "size": len(content.encode("utf-8")),
            "updated_at": target.stat().st_mtime,
            "category": classifications.get(
                target.relative_to(root).as_posix(),
                UNCATEGORIZED,
            ),
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
        ensure_classification_file(self._knowledge_root(name, normalized_scope)[1])
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

    def set_knowledge_category(
        self,
        user: Any,
        scope: Any,
        path: Any,
        category: Any,
    ) -> dict[str, Any]:
        name, normalized_scope, target = self._knowledge_target(user, scope, path)
        if not target.is_file():
            raise NotFoundError(f"知识文件不存在：{path}")
        root = self._knowledge_root(name, normalized_scope)[1]
        try:
            _reject_link_path(root.resolve(), classification_path(root))
            assigned = set_classification(
                root,
                target.relative_to(root).as_posix(),
                category,
            )
        except KnowledgeClassificationError as exc:
            raise InvalidRequestError(f"分类标记无效：{exc}") from None
        return {
            "user": name,
            "scope": normalized_scope,
            "relative_path": target.relative_to(root).as_posix(),
            "category": assigned,
            "updated": True,
            "classification_file": CLASSIFICATION_FILENAME,
        }

    def delete_knowledge_document(self, user: Any, scope: Any, path: Any) -> dict[str, Any]:
        name, normalized_scope, target = self._knowledge_target(user, scope, path)
        if not target.is_file():
            raise NotFoundError(f"知识文件不存在：{path}")
        root = self._knowledge_root(name, normalized_scope)[1]
        relative_path = target.relative_to(root).as_posix()
        try:
            # Validate the sidecar before changing the document.  A malformed
            # classification file must never be silently discarded.
            read_classifications(root)
            original_bytes = target.read_bytes()
        except (KnowledgeClassificationError, OSError) as exc:
            if isinstance(exc, KnowledgeClassificationError):
                raise InvalidRequestError(
                    f"{CLASSIFICATION_FILENAME} 格式无效：{exc}"
                ) from None
            raise InvalidRequestError(f"知识文件无法读取：{exc}") from None
        try:
            target.unlink()
            remove_classification(root, relative_path)
        except (KnowledgeClassificationError, OSError) as exc:
            # Keep the document and its metadata in sync if the sidecar
            # update fails after the document has been removed.
            if not target.exists():
                try:
                    target.write_bytes(original_bytes)
                except OSError:
                    pass
            if isinstance(exc, KnowledgeClassificationError):
                raise InvalidRequestError(
                    f"{CLASSIFICATION_FILENAME} 格式无效：{exc}"
                ) from None
            raise InvalidRequestError(f"知识文件删除失败：{exc}") from None
        return {
            "user": name,
            "scope": normalized_scope,
            "relative_path": relative_path,
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
        root = self._knowledge_root(name, normalized_scope)[1]
        source_relative = source.relative_to(root).as_posix()
        target_relative = target.relative_to(root).as_posix()
        try:
            # Validate before moving so a malformed sidecar cannot leave the
            # document at a new path with stale metadata.
            read_classifications(root)
        except KnowledgeClassificationError as exc:
            raise InvalidRequestError(f"{CLASSIFICATION_FILENAME} 格式无效：{exc}") from None
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(source, target)
            rename_classification(
                root,
                source_relative,
                target_relative,
            )
        except KnowledgeClassificationError as exc:
            try:
                if target.exists() and not source.exists():
                    os.replace(target, source)
            except OSError:
                pass
            raise InvalidRequestError(f"{CLASSIFICATION_FILENAME} 格式无效：{exc}") from None
        except OSError as exc:
            try:
                if target.exists() and not source.exists():
                    os.replace(target, source)
            except OSError:
                pass
            raise InvalidRequestError(f"知识文件移动失败：{exc}") from None
        return {
            "user": name,
            "scope": normalized_scope,
            "relative_path": source_relative,
            "new_relative_path": target_relative,
            "moved": True,
        }
