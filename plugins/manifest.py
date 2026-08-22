"""Parse built-in plugin SKILL.md files and Provider tool definitions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from run.prompt_sources import (
    PromptRegistrationError,
    SkillDescriptor,
    natural_path_key,
    parse_skill_descriptor,
    read_required_text,
)


class PluginManifestError(RuntimeError):
    pass


_SECONDARY_HEADING = re.compile(r"^##\s+")
_TOOL_HEADING = re.compile(r"^##\s+Tool\s*$", re.IGNORECASE)
_JSON_FENCE = re.compile(r"```json\s*\n(.*?)\n```", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True, slots=True)
class PluginManifest:
    descriptor: SkillDescriptor
    tool: dict[str, Any]

    @property
    def enabled(self) -> bool:
        return bool(self.tool["enabled"])


def _tool_block(text: str, path: Path) -> dict[str, Any]:
    lines = text.splitlines()
    headings = [index for index, line in enumerate(lines) if _TOOL_HEADING.fullmatch(line.strip())]
    if not headings:
        raise PluginManifestError(f"插件 SKILL.md 缺少 ## Tool：{path}")
    if len(headings) != 1:
        raise PluginManifestError(f"每个插件只能声明一个 ## Tool：{path}")
    start = headings[0] + 1
    end = len(lines)
    for index in range(start, len(lines)):
        if _SECONDARY_HEADING.match(lines[index].strip()):
            end = index
            break
    matched = _JSON_FENCE.search("\n".join(lines[start:end]))
    if matched is None:
        raise PluginManifestError(f"插件 ## Tool 缺少 JSON 代码块：{path}")
    try:
        raw = json.loads(matched.group(1))
    except json.JSONDecodeError as exc:
        raise PluginManifestError(f"插件工具 JSON 无效：{path}（{exc}）") from exc
    if not isinstance(raw, dict):
        raise PluginManifestError(f"插件工具定义必须是对象：{path}")
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
    if not isinstance(raw["version"], str) or not raw["version"].strip():
        raise PluginManifestError(f"插件 version 必须是非空字符串：{path}")
    if not isinstance(raw["enabled"], bool):
        raise PluginManifestError(f"插件 enabled 必须是布尔值：{path}")
    if "strict" in raw and not isinstance(raw["strict"], bool):
        raise PluginManifestError(f"插件 strict 必须是布尔值：{path}")
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
    text = read_required_text(path)
    try:
        descriptor = parse_skill_descriptor(path, scope="plugins", root=root)
    except PromptRegistrationError as exc:
        raise PluginManifestError(str(exc)) from exc
    tool = _tool_block(text, path)
    _validate_plugin_tool(tool, path, descriptor.title)
    return PluginManifest(descriptor, tool)


def discover_plugin_manifests(root: Path) -> tuple[PluginManifest, ...]:
    base = root / "plugins"
    if not base.is_dir():
        return ()
    paths = [path / "SKILL.md" for path in base.iterdir() if path.is_dir() and (path / "SKILL.md").is_file()]
    paths.sort(key=lambda item: natural_path_key(item.relative_to(base)))
    return tuple(parse_plugin_manifest(path, root=root) for path in paths)
