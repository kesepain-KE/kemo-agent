"""用户人格、品牌与用户子代理领域服务。"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
from typing import Any
import uuid

from run.agents import discover_agents
from web.constants import _AGENT_NAME_RE
from web.errors import InvalidRequestError, NotFoundError, WebServiceError
from web.services._io import atomic_write as _atomic_write
from web.services._paths import (
    _agent_registration_value,
    _flat_files,
    _reject_link_path,
    _reject_tree_links,
)


class IdentityServiceMixin:
    @staticmethod
    def _validated_soul_content(content: Any) -> str:
        if not isinstance(content, str) or not content.strip():
            raise InvalidRequestError("content 必须是非空字符串")
        if len(content) > 65_536:
            raise InvalidRequestError("content 不能超过 65536 字符")
        return content

    def _soul_document(self, path: Path, *, user: str | None = None) -> dict[str, Any]:
        if not path.is_file() or path.is_symlink():
            label = "用户人格文件尚未创建" if user is not None else "全局人格文件不存在"
            raise NotFoundError(label)
        try:
            content = path.read_text("utf-8")
            stat = path.stat()
        except (OSError, UnicodeError) as exc:
            raise WebServiceError("人格文件不可读") from exc
        result: dict[str, Any] = {
            "path": self._project_path(path),
            "content": content,
            "size": stat.st_size,
            "updated_at": stat.st_mtime,
        }
        if user is not None:
            result = {"user": user, **result}
        return result

    def user_soul(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        return self._soul_document(
            self.root / "users" / name / "user_soul.md",
            user=name,
        )

    def update_user_soul(self, user: Any, content: Any) -> dict[str, Any]:
        name = self.require_user(user)
        path = self.root / "users" / name / "user_soul.md"
        _atomic_write(path, self._validated_soul_content(content).encode("utf-8"))
        return self._soul_document(path, user=name)

    def global_soul(self) -> dict[str, Any]:
        return self._soul_document(self.root / "config" / "global_soul.md")

    def update_global_soul(self, content: Any) -> dict[str, Any]:
        path = self.root / "config" / "global_soul.md"
        _atomic_write(path, self._validated_soul_content(content).encode("utf-8"))
        return self._soul_document(path)

    def logo(self) -> Path | None:
        path = self.root / "kemo-agent.jpg"
        return path if path.is_file() and not path.is_symlink() else None

    def agents(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        definitions = sorted(
            discover_agents(self.root, name).agents.values(),
            key=lambda item: (item.source, item.name.casefold(), item.name),
        )
        agents = [
            {
                "name": definition.name,
                "version": definition.version,
                "description": definition.description,
                "enabled": definition.enabled,
                "source": "global" if definition.source == "builtin" else "user",
                "trigger": (
                    _agent_registration_value(definition.trigger_registration, "触发")
                    or "未声明独立触发条件"
                ),
                "rules": definition.instruction,
                "executor": definition.executor,
                "execution": definition.execution,
                "model_profile": definition.model_profile,
                "exposure": definition.capabilities.exposure,
                "root": self._project_path(definition.directory),
                "files": _flat_files(definition.directory),
            }
            for definition in definitions
        ]
        return {
            "user": name,
            "summary": {
                "total": len(agents),
                "enabled": sum(item["enabled"] for item in agents),
                "global": sum(item["source"] == "global" for item in agents),
                "user": sum(item["source"] == "user" for item in agents),
            },
            "agents": agents,
        }

    def delete_user_agent(self, user: Any, agent: Any) -> dict[str, Any]:
        name = self.require_user(user)
        if not isinstance(agent, str) or not _AGENT_NAME_RE.fullmatch(agent.strip()):
            raise InvalidRequestError("agent 必须是有效的子代理名称")
        agent_name = agent.strip()
        definition = discover_agents(self.root, name).agents.get(agent_name)
        if definition is None or definition.source != "user":
            raise NotFoundError(f"用户子代理不存在：{agent_name}")

        directory = (self.root / "users" / name / "agents").resolve()
        target = definition.directory.resolve()
        try:
            target.relative_to(directory)
        except ValueError:
            raise InvalidRequestError("用户子代理路径越出允许目录") from None
        _reject_link_path(directory, definition.directory)
        _reject_tree_links(definition.directory)

        tombstone = directory / f"_{agent_name}.{uuid.uuid4().hex}.deleting"
        try:
            os.replace(definition.directory, tombstone)
            shutil.rmtree(tombstone)
        except OSError as exc:
            if tombstone.exists() and not definition.directory.exists():
                try:
                    os.replace(tombstone, definition.directory)
                except OSError:
                    pass
            raise WebServiceError(f"用户子代理删除失败：{agent_name}") from exc
        return {
            "user": name,
            "name": agent_name,
            "path": f"users/{name}/agents/{agent_name}",
            "deleted": True,
        }

