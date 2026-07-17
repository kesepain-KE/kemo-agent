"""子代理清单发现和验证。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


AGENT_SCHEMA_VERSION = 1
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_EXECUTIONS = {"sync", "background_serial"}
_WRITE_POLICIES = {"none", "derived_cache", "user_memory", "user_task"}


class AgentError(RuntimeError):
    pass


class AgentManifestError(AgentError):
    pass


class AgentDisabledError(AgentError):
    pass


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    name: str
    version: str
    description: str
    enabled: bool
    instruction_file: str
    instruction: str
    model_profile: str
    timeout: float
    execution: str
    write_policy: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    directory: Path
    manifest_path: Path


@dataclass(frozen=True, slots=True)
class AgentRegistry:
    agents: dict[str, AgentDefinition]

    def enabled_agents(self) -> list[AgentDefinition]:
        return [definition for definition in self.agents.values() if definition.enabled]

    def get(self, name: str) -> AgentDefinition:
        definition = self.agents.get(name)
        if definition is None:
            raise AgentError(f"未知子代理：{name}")
        if not definition.enabled:
            raise AgentDisabledError(f"子代理已禁用：{name}")
        return definition


def _object_schema(value: Any, *, field: str, path: Path) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("type") != "object":
        raise AgentManifestError(f"{field} 必须是 object JSON Schema：{path}")
    return value


def _load_manifest(path: Path) -> AgentDefinition:
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AgentManifestError(f"子代理清单不可读：{path}（{exc}）") from exc
    if not isinstance(raw, dict):
        raise AgentManifestError(f"子代理清单必须是 JSON 对象：{path}")
    required = (
        "schema_version",
        "name",
        "version",
        "description",
        "enabled",
        "instruction",
        "model_profile",
        "timeout",
        "execution",
        "write_policy",
        "input_schema",
        "output_schema",
    )
    missing = [name for name in required if name not in raw]
    if missing:
        raise AgentManifestError(f"子代理清单缺少字段 {', '.join(missing)}：{path}")
    if raw["schema_version"] != AGENT_SCHEMA_VERSION:
        raise AgentManifestError(
            f"不支持的子代理 schema_version={raw['schema_version']!r}：{path}"
        )
    name = str(raw["name"]).strip()
    if not _NAME_RE.fullmatch(name):
        raise AgentManifestError(f"子代理名称无效：{name!r}（{path}）")
    if name != path.parent.name:
        raise AgentManifestError(f"子代理名称必须与目录名一致：{name!r} != {path.parent.name!r}")
    instruction_file = str(raw["instruction"]).strip()
    if not instruction_file or Path(instruction_file).name != instruction_file:
        raise AgentManifestError(f"instruction 必须是清单同目录文件名：{path}")
    instruction_path = path.parent / instruction_file
    try:
        instruction = instruction_path.read_text("utf-8").strip()
    except OSError as exc:
        raise AgentManifestError(f"子代理指令不可读：{instruction_path}（{exc}）") from exc
    if not instruction:
        raise AgentManifestError(f"子代理指令为空：{instruction_path}")
    execution = str(raw["execution"]).strip()
    if execution not in _EXECUTIONS:
        raise AgentManifestError(f"execution 必须是 {_EXECUTIONS} 之一：{path}")
    write_policy = str(raw["write_policy"]).strip()
    if write_policy not in _WRITE_POLICIES:
        raise AgentManifestError(f"write_policy 必须是 {_WRITE_POLICIES} 之一：{path}")
    try:
        timeout = float(raw["timeout"])
    except (TypeError, ValueError) as exc:
        raise AgentManifestError(f"timeout 必须是正数：{path}") from exc
    if timeout <= 0:
        raise AgentManifestError(f"timeout 必须是正数：{path}")
    return AgentDefinition(
        name=name,
        version=str(raw["version"]).strip(),
        description=str(raw["description"]).strip(),
        enabled=bool(raw["enabled"]),
        instruction_file=instruction_file,
        instruction=instruction,
        model_profile=str(raw["model_profile"]).strip() or "default",
        timeout=timeout,
        execution=execution,
        write_policy=write_policy,
        input_schema=_object_schema(raw["input_schema"], field="input_schema", path=path),
        output_schema=_object_schema(raw["output_schema"], field="output_schema", path=path),
        directory=path.parent,
        manifest_path=path,
    )


def discover_agents(root: Path) -> AgentRegistry:
    agents_root = root / "agents"
    definitions: dict[str, AgentDefinition] = {}
    if not agents_root.is_dir():
        return AgentRegistry(definitions)
    manifests = sorted(agents_root.glob("*/agent.json"), key=lambda item: str(item).casefold())
    for path in manifests:
        definition = _load_manifest(path)
        if definition.name in definitions:
            raise AgentManifestError(f"子代理名称重复：{definition.name}")
        definitions[definition.name] = definition
    return AgentRegistry(definitions)
