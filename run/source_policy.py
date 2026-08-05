"""Per-request source controls for the main agent only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from run.config import ConfigError


@dataclass(frozen=True, slots=True)
class NameFilter:
    unrestricted: bool
    names: tuple[str, ...] = ()

    @classmethod
    def from_config(cls, value: Any, *, field: str) -> "NameFilter":
        if value is None:
            value = []
        if not isinstance(value, list):
            raise ConfigError(f"{field} 必须是字符串数组")
        names: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ConfigError(f"{field} 必须是非空字符串数组")
            name = item.strip().replace("\\", "/")
            if name == "*":
                raise ConfigError(f"{field} 不支持 *；空数组表示全量允许")
            if name not in seen:
                seen.add(name)
                names.append(name)
        return cls(unrestricted=not names, names=tuple(names))

    def allows(self, name: str) -> bool:
        return self.unrestricted or name in self.names

    def selector(self) -> tuple[str, ...] | None:
        return None if self.unrestricted else self.names

    def public_summary(self) -> dict[str, Any]:
        if self.unrestricted:
            return {"mode": "all", "names": []}
        return {"mode": "allowlist", "names": list(self.names)}


def _object(config: dict[str, Any], key: str) -> dict[str, Any]:
    value = config.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{key} 必须是对象")
    return value


def _boolean(container: dict[str, Any], key: str, *, field: str, default: bool) -> bool:
    value = container.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{field} 必须是布尔值")
    return value


def _reject_unknown(
    container: dict[str, Any], allowed: set[str], *, field: str
) -> None:
    unknown = sorted(set(container) - allowed)
    if unknown:
        raise ConfigError(f"{field} 包含已移除或未知项：{', '.join(unknown)}")


@dataclass(frozen=True, slots=True)
class MainAgentSourcePolicy:
    knowledge_scopes: tuple[str, ...]
    plugins: NameFilter
    shared_skills: NameFilter
    user_skills: NameFilter
    global_expand: NameFilter
    shared_expand: NameFilter
    global_perception: NameFilter

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "MainAgentSourcePolicy":
        if not isinstance(config, dict):
            raise ConfigError("运行配置必须是对象")
        knowledge = _object(config, "knowledge")
        skills = _object(config, "skills")
        expand = _object(config, "expand")
        perception = _object(config, "perception")
        plugins = _object(config, "plugins")
        _reject_unknown(knowledge, {"use_shared", "use_global"}, field="knowledge")
        _reject_unknown(skills, {"shared_whitelist"}, field="skills")
        _reject_unknown(
            expand,
            {"global_whitelist", "shared_whitelist"},
            field="expand",
        )
        _reject_unknown(perception, {"global_whitelist"}, field="perception")
        _reject_unknown(plugins, {"whitelist"}, field="plugins")

        use_shared = _boolean(
            knowledge,
            "use_shared",
            field="knowledge.use_shared",
            default=True,
        )
        use_global = _boolean(
            knowledge,
            "use_global",
            field="knowledge.use_global",
            default=True,
        )
        scopes: list[str] = ["user"]
        if use_shared:
            scopes.append("shared")
        if use_global:
            scopes.append("global")
        return cls(
            knowledge_scopes=tuple(scopes),
            plugins=NameFilter.from_config(
                plugins.get("whitelist", []),
                field="plugins.whitelist",
            ),
            shared_skills=NameFilter.from_config(
                skills.get("shared_whitelist", []),
                field="skills.shared_whitelist",
            ),
            user_skills=NameFilter(unrestricted=True),
            global_expand=NameFilter.from_config(
                expand.get("global_whitelist", []),
                field="expand.global_whitelist",
            ),
            shared_expand=NameFilter.from_config(
                expand.get("shared_whitelist", []),
                field="expand.shared_whitelist",
            ),
            global_perception=NameFilter.from_config(
                perception.get("global_whitelist", []),
                field="perception.global_whitelist",
            ),
        )

    def direct_knowledge_scopes(self) -> tuple[str, ...]:
        """Return enabled scopes still allowed to expose their local files."""

        return self.knowledge_scopes

    def public_summary(self) -> dict[str, Any]:
        return {
            "knowledge": {
                "enabled": True,
                "configured_scopes": list(self.knowledge_scopes),
                "effective_scopes": list(self.direct_knowledge_scopes()),
            },
            "plugins": self.plugins.public_summary(),
            "skills": {
                "shared": self.shared_skills.public_summary(),
                "user": self.user_skills.public_summary(),
            },
            "expand": {
                "global": self.global_expand.public_summary(),
                "shared": self.shared_expand.public_summary(),
            },
            "perception": {
                "global": self.global_perception.public_summary(),
            },
        }
