"""Load plugin methods from SKILL.md and machine contracts from plugin.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, SchemaError

from run.config import (
    PromptRegistrationError,
    SkillDescriptor,
    natural_path_key,
    parse_skill_descriptor,
)

PLUGIN_META_FILENAME = "plugin.yaml"


class PluginManifestError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PluginManifest:
    descriptor: SkillDescriptor
    tool: dict[str, Any]

    @property
    def enabled(self) -> bool:
        return bool(self.tool["enabled"])


def _load_plugin_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text("utf-8-sig"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise PluginManifestError(f"无法读取插件元数据：{path}（{exc}）") from exc
    if not isinstance(raw, dict):
        raise PluginManifestError(f"插件元数据必须是映射：{path}")
    return raw


def _validate_plugin_tool(raw: dict[str, Any], path: Path, title: str) -> None:
    required = ("name", "description", "input_schema", "version", "enabled", "entrypoint")
    missing = [name for name in required if name not in raw]
    if missing:
        raise PluginManifestError(f"插件工具定义缺少字段 {', '.join(missing)}：{path}")
    directory_name = path.parent.name
    if title != directory_name or raw.get("name") != directory_name:
        raise PluginManifestError(f"插件标题、工具名与目录名必须一致：{path}")
    if not isinstance(raw["description"], str) or not raw["description"].strip():
        raise PluginManifestError(f"插件 description 必须是非空字符串：{path}")
    schema = raw["input_schema"]
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise PluginManifestError(f"插件 input_schema 必须是 object JSON Schema：{path}")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise PluginManifestError(
            f"插件 input_schema 无效；null 类型需写成字符串 'null'：{path}（{exc.message}）"
        ) from exc
    if raw.get("origin", "builtin") not in ("builtin", "user"):
        raise PluginManifestError(f"插件 origin 必须是 builtin 或 user：{path}")
    if not isinstance(raw["version"], str) or not raw["version"].strip():
        raise PluginManifestError(f"插件 version 必须是非空字符串：{path}")
    if not isinstance(raw["enabled"], bool):
        raise PluginManifestError(f"插件 enabled 必须是布尔值：{path}")
    if "strict" in raw and not isinstance(raw["strict"], bool):
        raise PluginManifestError(f"插件 strict 必须是布尔值：{path}")
    permission = raw.get("permission", "write")
    if not isinstance(permission, str) or permission not in {
        "read", "write", "delete", "external"
    }:
        raise PluginManifestError(
            f"插件 permission 必须是 read、write、delete 或 external：{path}"
        )
    timeout_policy = raw.get("timeout_policy", "argument_or_default")
    if not isinstance(timeout_policy, str) or timeout_policy not in {
        "argument_or_default",
        "agent_runtime",
    }:
        raise PluginManifestError(
            "插件 timeout_policy 必须是 argument_or_default 或 agent_runtime："
            f"{path}"
        )
    execution_mode = raw.get("execution_mode", "process")
    if execution_mode not in {"process", "thread"}:
        raise PluginManifestError(
            f"插件 execution_mode 必须是 process 或 thread：{path}"
        )
    timeout_grace = raw.get("timeout_grace_seconds", 0)
    if (
        isinstance(timeout_grace, bool)
        or not isinstance(timeout_grace, (int, float))
        or timeout_grace < 0
        or timeout_grace > 30
    ):
        raise PluginManifestError(
            f"插件 timeout_grace_seconds 必须是 0..30 秒的数字：{path}"
        )
    entrypoint = raw["entrypoint"]
    if not isinstance(entrypoint, str):
        raise PluginManifestError(f"插件 entrypoint 必须是字符串：{path}")
    file_name, separator, function_name = entrypoint.partition(":")
    if not separator or not file_name or not function_name:
        raise PluginManifestError(f"插件 entrypoint 无效：{path}")
    entry_path = Path(file_name)
    if entry_path.is_absolute() or ".." in entry_path.parts:
        raise PluginManifestError(f"插件 entrypoint 不得跳出插件目录：{path}")
    try:
        (path.parent / entry_path).resolve().relative_to(path.parent.resolve())
    except ValueError as exc:
        raise PluginManifestError(f"插件 entrypoint 不得跳出插件目录：{path}") from exc


def parse_plugin_manifest(path: Path, *, root: Path) -> PluginManifest:
    try:
        descriptor = parse_skill_descriptor(path, scope="plugins", root=root)
    except PromptRegistrationError as exc:
        raise PluginManifestError(str(exc)) from exc
    meta_path = path.parent / PLUGIN_META_FILENAME
    tool = _load_plugin_yaml(meta_path)
    _validate_plugin_tool(tool, meta_path, descriptor.title)
    return PluginManifest(descriptor, tool)


def discover_plugin_manifests(root: Path) -> tuple[PluginManifest, ...]:
    base = root / "plugins"
    if not base.is_dir():
        return ()
    paths = [
        path / "SKILL.md" for path in base.iterdir()
        if path.is_dir()
        and (path / "SKILL.md").is_file()
        and (path / PLUGIN_META_FILENAME).is_file()
    ]
    paths.sort(key=lambda item: natural_path_key(item.relative_to(base)))
    return tuple(parse_plugin_manifest(path, root=root) for path in paths)
