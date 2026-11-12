"""三层知识库：真实目录、多格式预览与文件操作。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import threading
from typing import Any, TYPE_CHECKING

from run.config import IGNORED_RUNTIME_DIRECTORY_NAMES, MainAgentSourcePolicy, load_config
from web.constants import _KNOWLEDGE_SCOPES, _KNOWLEDGE_SUFFIXES
from web.errors import ConflictError, InvalidRequestError, NotFoundError
from web.services._io import atomic_write as _atomic_write, validated_text as _validated_text
from web.services._paths import _reject_link_path, _safe_relative_target

if TYPE_CHECKING:
    from web.service import WebRunService


_TRANSFER_LOCK = threading.Lock()


class KnowledgeServiceMixin:
    if TYPE_CHECKING:
        require_user = WebRunService.require_user
        _media_preview = WebRunService._media_preview
        _write_area_file = WebRunService._write_area_file
        root: Path

    def knowledge(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        policy = MainAgentSourcePolicy.from_config(load_config(name, self.root))
        roots = []
        directories = []
        documents = []
        for scope in ("user", "shared", "global"):
            _, base = self._knowledge_root(name, scope)
            active = scope in policy.direct_knowledge_scopes()
            roots.append({
                "scope": scope,
                "relative_path": base.relative_to(self.root).as_posix(),
                "absolute_path": str(base.resolve()),
                "active_for_main_agent": active,
            })
            for current, dirs, files in os.walk(base, followlinks=False):
                directory = Path(current)
                dirs[:] = sorted(
                    (item for item in dirs if self._knowledge_visible(directory / item)),
                    key=str.casefold,
                )
                for item in dirs:
                    target = directory / item
                    directories.append({
                        "scope": scope,
                        "relative_path": target.relative_to(base).as_posix(),
                        "name": item,
                        "updated_at": target.stat().st_mtime,
                    })
                for item in sorted(files, key=str.casefold):
                    target = directory / item
                    if not self._knowledge_visible(target) or not target.is_file():
                        continue
                    stat = target.stat()
                    documents.append({
                        "scope": scope,
                        "relative_path": target.relative_to(base).as_posix(),
                        "title": target.stem,
                        "size": stat.st_size,
                        "updated_at": stat.st_mtime,
                        "active_for_main_agent": active,
                    })
        return {
            "user": name,
            "enabled": True,
            "retrieval": {"mode": "index_only", "full_index": True},
            "summary": {
                "documents": len(documents),
                **{f"{scope}_documents": sum(d["scope"] == scope for d in documents)
                   for scope in _KNOWLEDGE_SCOPES},
            },
            "roots": roots,
            "directories": directories,
            "documents": documents,
            "source_policy": policy.public_summary(),
        }

    @staticmethod
    def _knowledge_visible(path: Path) -> bool:
        return (
            not path.name.startswith(".")
            and path.name.casefold() not in IGNORED_RUNTIME_DIRECTORY_NAMES
            and path.name not in {"__pycache__", "classify.sql", "classify.sqlite3"}
            and not path.is_symlink()
            and not getattr(path, "is_junction", lambda: False)()
        )

    def _knowledge_root(self, user: Any, scope: Any) -> tuple[str, Path]:
        name = self.require_user(user)
        if scope not in _KNOWLEDGE_SCOPES:
            raise InvalidRequestError("scope 只允许 user、shared 或 global")
        roots = {
            "user": self.root / "users" / name / "knowledge",
            "shared": self.root / "knowledge" / "share",
            "global": self.root / "knowledge" / "global",
        }
        base = roots[scope]
        _reject_link_path(self.root.resolve(), base)
        return name, base

    def _knowledge_entry_target(self, user: Any, scope: Any, path: Any) -> tuple[str, str, Path]:
        name, root = self._knowledge_root(user, scope)
        relative, target = _safe_relative_target(root, path)
        if relative == ".":
            raise InvalidRequestError("不能修改知识库根目录")
        _reject_link_path(root.resolve(), target)
        if any(not self._knowledge_visible(root.joinpath(*Path(relative).parts[:index + 1]))
               for index in range(len(Path(relative).parts))):
            raise InvalidRequestError("知识库内部数据或隐藏路径不能作为知识条目操作")
        return name, str(scope), target

    def _knowledge_target(self, user: Any, scope: Any, path: Any) -> tuple[str, str, Path]:
        result = self._knowledge_entry_target(user, scope, path)
        if result[2].suffix.lower() not in _KNOWLEDGE_SUFFIXES:
            raise InvalidRequestError("在线编辑和新建只支持 .md、.txt 或 .json，其他文件请使用预览或导入")
        return result

    def knowledge_file_download(self, user: Any, scope: Any, path: Any) -> Path:
        _, _, target = self._knowledge_entry_target(user, scope, path)
        if not target.is_file():
            raise NotFoundError(f"知识文件不存在：{path}")
        return target

    def knowledge_file_preview(self, user: Any, scope: Any, path: Any) -> tuple[Path, str, str]:
        name, scope, target = self._knowledge_entry_target(user, scope, path)
        _, base = self._knowledge_root(name, scope)
        return self._media_preview(base, target.relative_to(base.resolve()).as_posix())

    def save_knowledge_file(self, user: Any, scope: Any, path: Any, data: bytes) -> dict[str, Any]:
        name, scope, target = self._knowledge_entry_target(user, scope, path)
        if target.suffix.lower() in _KNOWLEDGE_SUFFIXES:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                raise InvalidRequestError("知识文件不是有效 UTF-8 文本") from None
            return self.put_knowledge_document(name, scope, path, text)
        _, base = self._knowledge_root(name, scope)
        return {"user": name, "scope": scope, **self._write_area_file(base, path, data)}

    def knowledge_document(self, user: Any, scope: Any, path: Any) -> dict[str, Any]:
        name, scope, target = self._knowledge_target(user, scope, path)
        if not target.is_file():
            raise NotFoundError(f"知识文件不存在：{path}")
        try:
            content = target.read_text("utf-8")
        except UnicodeDecodeError:
            raise InvalidRequestError("知识文件不是有效 UTF-8 文本") from None
        return {
            "user": name, "scope": scope,
            "relative_path": target.relative_to(self._knowledge_root(name, scope)[1]).as_posix(),
            "content": content, "size": len(content.encode("utf-8")),
            "updated_at": target.stat().st_mtime,
        }

    def put_knowledge_document(self, user: Any, scope: Any, path: Any, content: Any) -> dict[str, Any]:
        name, scope, target = self._knowledge_target(user, scope, path)
        text = _validated_text(content)
        if target.suffix.lower() == ".json":
            try:
                json.loads(text)
            except json.JSONDecodeError as exc:
                raise InvalidRequestError(f"JSON 知识文件格式无效：{exc.msg}") from None
        _atomic_write(target, text.encode("utf-8"))
        return {
            "user": name, "scope": scope,
            "relative_path": target.relative_to(self._knowledge_root(name, scope)[1]).as_posix(),
            "size": len(text.encode("utf-8")), "updated": True, "index_refresh": "next_request",
        }

    def create_knowledge_entry(self, user: Any, scope: Any, path: Any, kind: str) -> dict[str, Any]:
        name, scope, target = self._knowledge_entry_target(user, scope, path)
        if target.exists():
            raise ConflictError(f"目标已存在：{path}")
        if kind == "directory":
            target.mkdir(parents=True)
        elif kind == "file":
            self._knowledge_target(name, scope, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("x", encoding="utf-8") as stream:
                stream.write("{}\n" if target.suffix.lower() == ".json" else "")
        else:
            raise InvalidRequestError("kind 只允许 file 或 directory")
        return {"user": name, "scope": scope, "relative_path": path, "created": True}

    def _validate_knowledge_subtree(self, target: Path) -> None:
        # A recursive action must not silently affect hidden runtime data or links.
        for current, dirs, files in os.walk(target, followlinks=False):
            for item in (*dirs, *files):
                if not self._knowledge_visible(Path(current) / item):
                    raise InvalidRequestError("目录包含内部数据、隐藏文件或链接，不能整体操作")

    def transfer_knowledge_entry(
        self, user: Any, scope: Any, path: Any, *, action: str,
        target_scope: str, target_path: str, auto_rename: bool = False,
    ) -> dict[str, Any]:
        name, scope, source = self._knowledge_entry_target(user, scope, path)
        _, target_scope, target = self._knowledge_entry_target(name, target_scope, target_path)
        if action not in {"copy", "move"}:
            raise InvalidRequestError("action 只允许 copy 或 move")
        # Naming and transfer share a lock so concurrent pastes cannot overwrite
        # a name selected by another request in this server.
        with _TRANSFER_LOCK:
            if not source.exists():
                raise NotFoundError(f"知识条目不存在：{path}")
            directory = source.is_dir()
            if target != source and target.is_relative_to(source):
                raise InvalidRequestError("不能将目录复制或移动到自身内部")
            if directory:
                self._validate_knowledge_subtree(source)
            if action == "copy" and auto_rename:
                original = target
                suffix = "" if directory else original.suffix
                stem = original.name[:-len(suffix)] if suffix else original.name
                number = 0
                while os.path.lexists(target):
                    number += 1
                    counter = "" if number == 1 else f" ({number})"
                    target = original.with_name(f"{stem} - 副本{counter}{suffix}")
            elif target.exists():
                raise ConflictError(f"目标已存在：{target_path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            if action == "move":
                shutil.move(str(source), str(target))
            elif directory:
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
        return {
            "user": name, "scope": scope, "relative_path": path,
            "target_scope": target_scope,
            "new_relative_path": target.relative_to(self._knowledge_root(name, target_scope)[1]).as_posix(),
            "action": action,
        }

    def delete_knowledge_entry(self, user: Any, scope: Any, path: Any) -> dict[str, Any]:
        name, scope, target = self._knowledge_entry_target(user, scope, path)
        if target.is_dir():
            self._validate_knowledge_subtree(target)
            shutil.rmtree(target)
        elif target.is_file():
            target.unlink()
        else:
            raise NotFoundError(f"知识条目不存在：{path}")
        return {"user": name, "scope": scope, "relative_path": path, "deleted": True}

    def delete_knowledge_document(self, user: Any, scope: Any, path: Any) -> dict[str, Any]:
        _, _, target = self._knowledge_target(user, scope, path)
        if not target.is_file():
            raise NotFoundError(f"知识文件不存在：{path}")
        return self.delete_knowledge_entry(user, scope, path)

    def move_knowledge_document(self, user: Any, scope: Any, path: Any, new_path: Any) -> dict[str, Any]:
        _, _, target = self._knowledge_target(user, scope, path)
        if not target.is_file():
            raise NotFoundError(f"知识文件不存在：{path}")
        self._knowledge_target(user, scope, new_path)
        return {**self.transfer_knowledge_entry(user, scope, path, action="move",
                                               target_scope=scope, target_path=new_path), "moved": True}
