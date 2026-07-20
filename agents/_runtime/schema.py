"""Schema-v2 agent package discovery with per-user hot-plug support."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from run.prompt_sources import natural_path_key


AGENT_SCHEMA_VERSION = 2
_LEGACY_SCHEMA_VERSION = 1
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_EXECUTIONS = {"sync", "background_serial"}
_WRITE_POLICIES = {"none", "derived_cache", "user_memory", "user_task"}
_EXPOSURES = {"internal", "tool"}
_COMPACT_MANIFEST_FIELDS = {"name", "version", "description", "trigger"}
_LOOSE_OBJECT_SCHEMA = {"type": "object", "additionalProperties": True}
_BUILTIN_DEFAULTS: dict[str, dict[str, str]] = {
    "context_manage": {
        "execution": "sync",
        "write_policy": "derived_cache",
        "model_profile": "cheap",
    },
    "memory_temporary_important": {
        "execution": "background_serial",
        "write_policy": "user_memory",
        "model_profile": "cheap",
    },
    "self_improve": {
        "execution": "background_serial",
        "write_policy": "user_memory",
        "model_profile": "reasoning",
    },
    "task_plan": {
        "execution": "background_serial",
        "write_policy": "user_task",
        "model_profile": "reasoning",
    },
    "time_plan": {
        "execution": "background_serial",
        "write_policy": "user_task",
        "model_profile": "default",
    },
}


class AgentError(RuntimeError):
    pass


class AgentManifestError(AgentError):
    pass


class AgentDisabledError(AgentError):
    pass


def _string_list(value: Any, *, field: str, path: Path) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise AgentManifestError(f"{field} 必须是非空字符串数组：{path}")
    return tuple(item.strip() for item in value)


@dataclass(frozen=True, slots=True)
class AgentCapabilities:
    exposure: Literal["internal", "tool"] = "internal"
    allowed_callers: tuple[str, ...] = ()
    plugin_tools: tuple[str, ...] = ()
    max_tool_iterations: int = 1
    shared_skills: tuple[str, ...] = ()
    user_skills: tuple[str, ...] = ()
    global_expand: tuple[str, ...] = ()
    shared_expand: tuple[str, ...] = ()
    user_expand: tuple[str, ...] = ()
    knowledge_scopes: tuple[str, ...] = ()
    knowledge_index_enabled: bool = False
    knowledge_body_access: str = "none"
    inherit_main_history: bool = False
    inherit_current_request: bool = False


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    name: str
    version: str
    description: str
    enabled: bool
    instruction_file: str
    instruction: str
    executor: str
    model_profile: str
    timeout: float
    execution: str
    write_policy: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    capabilities: AgentCapabilities
    source: Literal["builtin", "user"]
    directory: Path
    manifest_path: Path
    config_path: Path | None
    trigger_file: str = ""
    trigger_content: str = ""
    trigger_registration: str = ""


@dataclass(frozen=True, slots=True)
class AgentRegistry:
    agents: dict[str, AgentDefinition]

    def enabled_agents(self) -> list[AgentDefinition]:
        return [definition for definition in self.agents.values() if definition.enabled]

    def public_agents(self, caller: str = "main_agent") -> list[AgentDefinition]:
        return [
            definition
            for definition in self.enabled_agents()
            if definition.capabilities.exposure == "tool"
            and caller in definition.capabilities.allowed_callers
        ]

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


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text("utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AgentManifestError(f"{label}不可读：{path}（{exc}）") from exc
    if not isinstance(raw, dict):
        raise AgentManifestError(f"{label}必须是 JSON 对象：{path}")
    return raw


def _allow_list(container: Any, key: str, *, field: str, path: Path) -> tuple[str, ...]:
    if container is None:
        return ()
    if not isinstance(container, dict):
        raise AgentManifestError(f"{field} 必须是对象：{path}")
    value = container.get(key, [])
    return _string_list(value, field=f"{field}.{key}", path=path)


def _load_legacy_capabilities(path: Path | None, *, legacy: bool) -> AgentCapabilities:
    if path is None:
        return AgentCapabilities()
    raw = _read_json_object(path, label="子代理能力配置")
    if raw.get("schema_version") != 1:
        raise AgentManifestError(f"agent-config schema_version 必须为 1：{path}")
    exposure_raw = raw.get("exposure") or {}
    if not isinstance(exposure_raw, dict):
        raise AgentManifestError(f"exposure 必须是对象：{path}")
    exposure = str(exposure_raw.get("mode") or "internal").strip()
    if exposure not in _EXPOSURES:
        raise AgentManifestError(f"exposure.mode 必须是 {_EXPOSURES} 之一：{path}")
    callers = _string_list(
        exposure_raw.get("allowed_callers", []),
        field="exposure.allowed_callers",
        path=path,
    )
    tools_raw = raw.get("tools") or {}
    if not isinstance(tools_raw, dict):
        raise AgentManifestError(f"tools 必须是对象：{path}")
    plugins_raw = tools_raw.get("plugins") or {}
    plugin_tools = _allow_list(plugins_raw, "allow", field="tools.plugins", path=path)
    max_iterations = tools_raw.get("max_iterations", 1)
    if isinstance(max_iterations, bool) or not isinstance(max_iterations, int) or max_iterations < 1:
        raise AgentManifestError(f"tools.max_iterations 必须是正整数：{path}")
    prompt_raw = raw.get("prompt_sources") or {}
    if not isinstance(prompt_raw, dict):
        raise AgentManifestError(f"prompt_sources 必须是对象：{path}")
    skills = prompt_raw.get("skills") or {}
    expand = prompt_raw.get("expand") or {}
    knowledge = raw.get("knowledge") or {}
    if not isinstance(knowledge, dict):
        raise AgentManifestError(f"knowledge 必须是对象：{path}")
    scopes = _string_list(knowledge.get("scopes", []), field="knowledge.scopes", path=path)
    invalid_scopes = sorted(set(scopes) - {"global", "shared", "user"})
    if invalid_scopes:
        raise AgentManifestError(f"knowledge.scopes 包含未知范围：{', '.join(invalid_scopes)}")
    context = raw.get("context") or {}
    if not isinstance(context, dict):
        raise AgentManifestError(f"context 必须是对象：{path}")
    inherit_history = context.get("inherit_main_history", False)
    inherit_request = context.get("inherit_current_request", False)
    if not isinstance(inherit_history, bool) or not isinstance(inherit_request, bool):
        raise AgentManifestError(f"context 继承开关必须是布尔值：{path}")
    return AgentCapabilities(
        exposure=exposure,
        allowed_callers=callers,
        plugin_tools=plugin_tools,
        max_tool_iterations=max_iterations,
        shared_skills=_allow_list(skills, "shared", field="prompt_sources.skills", path=path),
        user_skills=_allow_list(skills, "user", field="prompt_sources.skills", path=path),
        global_expand=_allow_list(expand, "global", field="prompt_sources.expand", path=path),
        shared_expand=_allow_list(expand, "shared", field="prompt_sources.expand", path=path),
        user_expand=_allow_list(expand, "user", field="prompt_sources.expand", path=path),
        knowledge_scopes=scopes,
        knowledge_index_enabled=bool(knowledge.get("index_enabled", False)),
        knowledge_body_access="none",
        inherit_main_history=inherit_history,
        inherit_current_request=inherit_request,
    )


def _load_capabilities(path: Path) -> AgentCapabilities:
    raw = _read_json_object(path, label="子代理能力配置")
    if raw.get("schema_version") != 1:
        raise AgentManifestError(f"agent-config schema_version 必须为 1：{path}")
    internal_mode = raw.get("internal_mode", True)
    global_knowledge = raw.get("global_knowledge", False)
    shared_knowledge = raw.get("shared_knowledge", False)
    inherit_main_history = raw.get("inherit_main_history", False)
    boolean_fields = {
        "internal_mode": internal_mode,
        "global_knowledge": global_knowledge,
        "shared_knowledge": shared_knowledge,
        "inherit_main_history": inherit_main_history,
    }
    invalid_booleans = [name for name, value in boolean_fields.items() if not isinstance(value, bool)]
    if invalid_booleans:
        raise AgentManifestError(
            f"{', '.join(invalid_booleans)} 必须是布尔值：{path}"
        )
    callers = _string_list(
        raw.get("allowed_callers", []),
        field="allowed_callers",
        path=path,
    )
    tools_raw = raw.get("tools") or {}
    if not isinstance(tools_raw, dict):
        raise AgentManifestError(f"tools 必须是对象：{path}")
    plugin_tools = _allow_list(
        tools_raw.get("plugins"),
        "allow",
        field="tools.plugins",
        path=path,
    )
    shared_skills = _allow_list(
        tools_raw.get("shared_skills"),
        "allow",
        field="tools.shared_skills",
        path=path,
    )
    max_iterations = tools_raw.get("max_iterations", 20)
    if (
        isinstance(max_iterations, bool)
        or not isinstance(max_iterations, int)
        or max_iterations < 1
    ):
        raise AgentManifestError(f"tools.max_iterations 必须是正整数：{path}")
    scopes = tuple(
        scope
        for scope, enabled in (
            ("global", global_knowledge),
            ("shared", shared_knowledge),
        )
        if enabled
    )
    return AgentCapabilities(
        exposure="internal" if internal_mode else "tool",
        allowed_callers=callers,
        plugin_tools=plugin_tools,
        max_tool_iterations=max_iterations,
        shared_skills=shared_skills,
        user_skills=(),
        global_expand=(),
        shared_expand=(),
        user_expand=(),
        knowledge_scopes=scopes,
        knowledge_index_enabled=bool(scopes),
        knowledge_body_access="none",
        inherit_main_history=inherit_main_history,
        inherit_current_request=inherit_main_history,
    )


def _read_agent_timeout(root: Path) -> float:
    try:
        config_path = root / "config" / "global_config.json"
        if config_path.is_file():
            config = json.loads(config_path.read_text("utf-8-sig"))
            value = (config.get("agent_runtime") or {}).get("default_timeout", 600)
            timeout = float(value)
            if timeout > 0:
                return timeout
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return 600.0


def _same_directory_file(value: Any, *, field: str, path: Path) -> str:
    name = str(value or "").strip()
    if not name or Path(name).name != name:
        raise AgentManifestError(f"{field} 必须是清单同目录文件名：{path}")
    return name


def _read_required_package_text(path: Path, *, label: str) -> str:
    try:
        content = path.read_text("utf-8-sig").strip()
    except (OSError, UnicodeError) as exc:
        raise AgentManifestError(f"{label}不可读：{path}（{exc}）") from exc
    if not content:
        raise AgentManifestError(f"{label}为空：{path}")
    return content


def _trigger_registration(content: str, *, path: Path) -> str:
    lines = content.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if line.strip() == "# 注册信息":
            start = index + 1
            break
    if start is None:
        raise AgentManifestError(f"trigger.md 缺少 '# 注册信息'：{path}")
    selected: list[str] = []
    for line in lines[start:]:
        if line.startswith("# "):
            break
        selected.append(line)
    registration = "\n".join(selected).strip()
    if not registration:
        raise AgentManifestError(f"trigger.md 注册信息为空：{path}")
    return registration


def _validate_executor(
    directory: Path,
    executor: str,
    *,
    source: Literal["builtin", "user"],
    manifest_path: Path,
) -> None:
    if executor == "builtin:llm":
        return
    file_name, separator, function_name = executor.partition(":")
    if (
        not separator
        or not file_name
        or not function_name
        or Path(file_name).name != file_name
    ):
        raise AgentManifestError(f"executor 必须是同目录 file.py:function：{manifest_path}")
    executor_path = (directory / file_name).resolve()
    try:
        executor_path.relative_to(directory.resolve())
    except ValueError as exc:
        raise AgentManifestError(f"executor 不得跳出子代理目录：{manifest_path}") from exc
    if not executor_path.is_file():
        raise AgentManifestError(f"executor 文件不存在：{executor_path}")


def _load_compact_manifest(
    path: Path,
    *,
    source: Literal["builtin", "user"],
    root: Path,
) -> AgentDefinition:
    raw = _read_json_object(path, label="子代理清单")
    missing = sorted(_COMPACT_MANIFEST_FIELDS - set(raw))
    unknown = sorted(set(raw) - _COMPACT_MANIFEST_FIELDS)
    if missing:
        raise AgentManifestError(f"子代理清单缺少字段 {', '.join(missing)}：{path}")
    if unknown:
        raise AgentManifestError(f"精简子代理清单包含未知字段 {', '.join(unknown)}：{path}")
    name = str(raw["name"]).strip()
    if not _NAME_RE.fullmatch(name):
        raise AgentManifestError(f"子代理名称无效：{name!r}（{path}）")
    if name != path.parent.name:
        raise AgentManifestError(f"子代理名称必须与目录名一致：{name!r} != {path.parent.name!r}")
    version = str(raw["version"]).strip()
    description = str(raw["description"]).strip()
    if not version or not description:
        raise AgentManifestError(f"version 和 description 必须是非空字符串：{path}")
    trigger_file = _same_directory_file(raw["trigger"], field="trigger", path=path)
    trigger_path = path.parent / trigger_file
    trigger_content = _read_required_package_text(trigger_path, label="子代理 trigger")
    registration = _trigger_registration(trigger_content, path=trigger_path)
    instruction_path = path.parent / "AGENT.md"
    instruction = _read_required_package_text(instruction_path, label="子代理指令")
    config_path = path.parent / "agent-config.json"
    if not config_path.is_file():
        raise AgentManifestError(f"子代理能力配置不存在：{config_path}")
    defaults = _BUILTIN_DEFAULTS.get(name, {}) if source == "builtin" else {}
    executor_path = path.parent / "executor.py"
    executor = "executor.py:execute" if executor_path.is_file() else "builtin:llm"
    _validate_executor(path.parent, executor, source=source, manifest_path=path)
    execution = defaults.get("execution", "sync")
    write_policy = defaults.get("write_policy", "derived_cache")
    model_profile = defaults.get("model_profile", "default")
    return AgentDefinition(
        name=name,
        version=version,
        description=description,
        enabled=True,
        instruction_file="AGENT.md",
        instruction=instruction,
        executor=executor,
        model_profile=model_profile,
        timeout=_read_agent_timeout(root),
        execution=execution,
        write_policy=write_policy,
        input_schema=dict(_LOOSE_OBJECT_SCHEMA),
        output_schema=dict(_LOOSE_OBJECT_SCHEMA),
        capabilities=_load_capabilities(config_path),
        source=source,
        directory=path.parent,
        manifest_path=path,
        config_path=config_path,
        trigger_file=trigger_file,
        trigger_content=trigger_content,
        trigger_registration=registration,
    )


def _load_legacy_manifest(path: Path, *, source: Literal["builtin", "user"], root: Path) -> AgentDefinition:
    raw = _read_json_object(path, label="子代理清单")
    schema_version = raw.get("schema_version")
    if schema_version not in {AGENT_SCHEMA_VERSION, _LEGACY_SCHEMA_VERSION}:
        raise AgentManifestError(f"不支持的子代理 schema_version={schema_version!r}：{path}")
    legacy = schema_version == _LEGACY_SCHEMA_VERSION
    required = (
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
    name = str(raw["name"]).strip()
    if not _NAME_RE.fullmatch(name):
        raise AgentManifestError(f"子代理名称无效：{name!r}（{path}）")
    if name != path.parent.name:
        raise AgentManifestError(f"子代理名称必须与目录名一致：{name!r} != {path.parent.name!r}")
    if not isinstance(raw["enabled"], bool):
        raise AgentManifestError(f"enabled 必须是布尔值：{path}")
    instruction_file = str(raw["instruction"]).strip()
    if not instruction_file or Path(instruction_file).name != instruction_file:
        raise AgentManifestError(f"instruction 必须是清单同目录文件名：{path}")
    instruction_path = path.parent / instruction_file
    try:
        instruction = instruction_path.read_text("utf-8-sig").strip()
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
    executor = str(raw.get("executor") or "builtin:llm").strip()
    config_file = str(raw.get("config") or "agent-config.json").strip()
    config_path: Path | None = None
    if not legacy:
        if Path(config_file).name != config_file:
            raise AgentManifestError(f"config 必须是清单同目录文件名：{path}")
        config_path = path.parent / config_file
        if not config_path.is_file():
            raise AgentManifestError(f"子代理能力配置不存在：{config_path}")
    if source == "user" and legacy:
        raise AgentManifestError(f"用户子代理只支持 schema_version=2：{path}")
    _validate_executor(path.parent, executor, source=source, manifest_path=path)
    capabilities = _load_legacy_capabilities(config_path, legacy=legacy)
    return AgentDefinition(
        name=name,
        version=str(raw["version"]).strip(),
        description=str(raw["description"]).strip(),
        enabled=raw["enabled"],
        instruction_file=instruction_file,
        instruction=instruction,
        executor=executor,
        model_profile=str(raw["model_profile"]).strip() or "default",
        timeout=timeout,
        execution=execution,
        write_policy=write_policy,
        input_schema=_object_schema(raw["input_schema"], field="input_schema", path=path),
        output_schema=_object_schema(raw["output_schema"], field="output_schema", path=path),
        capabilities=capabilities,
        source=source,
        directory=path.parent,
        manifest_path=path,
        config_path=config_path,
    )


def _load_manifest(
    path: Path,
    *,
    source: Literal["builtin", "user"],
    root: Path,
) -> AgentDefinition:
    raw = _read_json_object(path, label="子代理清单")
    if raw.get("schema_version") is None:
        return _load_compact_manifest(path, source=source, root=root)
    return _load_legacy_manifest(path, source=source, root=root)


def _manifests(base: Path) -> tuple[Path, ...]:
    if not base.is_dir():
        return ()
    paths = [path / "agent.json" for path in base.iterdir() if path.is_dir() and not path.name.startswith("_") and (path / "agent.json").is_file()]
    paths.sort(key=lambda item: natural_path_key(item.relative_to(base)))
    return tuple(paths)


def discover_agents(root: Path, user: str | None = None) -> AgentRegistry:
    """Discover built-ins plus one user's data-only agents on every call."""

    base = root.resolve()
    definitions: dict[str, AgentDefinition] = {}
    for path in _manifests(base / "agents"):
        definition = _load_manifest(path, source="builtin", root=base)
        if definition.name in definitions:
            raise AgentManifestError(f"子代理名称重复：{definition.name}")
        definitions[definition.name] = definition
    if user:
        for path in _manifests(base / "users" / user / "agents"):
            definition = _load_manifest(path, source="user", root=base)
            if definition.name in definitions:
                raise AgentManifestError(f"用户子代理不得覆盖已有名称：{definition.name}")
            definitions[definition.name] = definition
    return AgentRegistry(definitions)
