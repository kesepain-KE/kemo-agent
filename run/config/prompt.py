"""Deterministic stable prefix; runtime state belongs only to the dynamic tail."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from plugins.manifest import PluginManifest, discover_plugin_manifests
from run.config import memory_prompt_injection
from run.memory import MemoryStore
from run.config.prompt_sources import (
    SkillDescriptor,
    load_prompt_source_registry,
    read_optional_text,
    relative_path,
    truncate_chars,
)
from run.config.source_policy import MainAgentSourcePolicy


PROMPT_SECTION_ORDER = (
    "user_soul",
    "global_soul",
    "agents_manual",
    "global_subagent_registry",
    "user_subagent_registry",
    "plugins",
    "skills",
    "expansions",
    "permanent_memory",
    "important_memory",
    "temporary_memory:half_year",
    "temporary_memory:one_month",
    "temporary_memory:seven_days",
)
DEFAULT_TEMPORARY_MEMORY_LIMITS = {
    "half_year": 300,
    "one_month": 200,
    "seven_days": 100,
}
DEFAULT_IMPORTANT_MEMORY_MAX_CHARS = 20000
DEFAULT_CHAR_LIMITS = {
    "user_soul": 12000,
    "agents_manual": 26000,
    "task_plan": 6000,
    "perception": 20000,
    "expand_data": 20000,
    "skill_prompts": 80000,
    "plugin_prompts": 80000,
}
INJECTION_MODE = "full"
_LEGACY_INJECTION_MODE_KEYS = frozenset(
    {
        "permanent_memory",
        "important_memory",
        "temporary_seven_days",
        "temporary_one_month",
        "temporary_half_year",
        "knowledge_index",
        "task_plan",
        "expand_data",
        "perception",
    }
)


class PromptConfigError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PromptSettings:
    char_limits: dict[str, int]
    temporary_memory_limits: dict[str, int]
    important_memory_max_chars: int


@dataclass(frozen=True, slots=True)
class PromptSection:
    name: str
    content: str
    source_files: tuple[str, ...] = ()
    item_ids: tuple[str, ...] = ()
    original_chars: int = 0
    injected_chars: int = 0
    original_items: int = 0
    injected_items: int = 0
    truncated: bool = False
    mode: str = "full"


@dataclass(frozen=True, slots=True)
class PromptBundle:
    text: str
    sections: tuple[PromptSection, ...]
    memory_ids: tuple[str, ...]
    diagnostics: dict[str, Any]
    memory_files: tuple[str, ...] = ()


def _nonnegative_group(raw: Any, defaults: dict[str, int], name: str) -> dict[str, int]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise PromptConfigError(f"prompt.{name} 必须是对象")
    unknown = sorted(set(raw) - set(defaults))
    if unknown:
        raise PromptConfigError(f"prompt.{name} 包含未知项：{', '.join(unknown)}")
    result = dict(defaults)
    for key in defaults:
        if key not in raw:
            continue
        value = raw[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PromptConfigError(f"prompt.{name}.{key} 必须是非负整数")
        result[key] = value
    return result


def parse_prompt_settings(config: dict[str, Any]) -> PromptSettings:
    raw = config.get("prompt")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise PromptConfigError("prompt 必须是对象")
    unknown_prompt = sorted(set(raw) - {"char_limits", "injection_mode"})
    if unknown_prompt:
        raise PromptConfigError(
            "prompt 包含已移除或未知项：" + ", ".join(unknown_prompt)
        )

    raw_char_limits = raw.get("char_limits")
    if isinstance(raw_char_limits, dict) and "knowledge_index" in raw_char_limits:
        raw_char_limits = {
            key: value
            for key, value in raw_char_limits.items()
            if key != "knowledge_index"
        }
    char_limits = _nonnegative_group(
        raw_char_limits,
        DEFAULT_CHAR_LIMITS,
        "char_limits",
    )
    mode_raw = raw.get("injection_mode")
    if mode_raw is not None:
        # Compatibility bridge for deployments that keep an older global or user
        # config during update. The option is no longer configurable, but an old
        # all-full declaration is semantically identical and can be ignored safely.
        if not isinstance(mode_raw, dict):
            raise PromptConfigError("已移除的 prompt.injection_mode 必须是对象")
        unknown_modes = sorted(set(mode_raw) - _LEGACY_INJECTION_MODE_KEYS)
        if unknown_modes:
            raise PromptConfigError(
                "已移除的 prompt.injection_mode 包含未知项："
                + ", ".join(unknown_modes)
            )
        for key, value in mode_raw.items():
            if key == "knowledge_index":
                continue
            if not isinstance(value, str) or value.strip().casefold() != INJECTION_MODE:
                raise PromptConfigError(
                    f"prompt.injection_mode.{key} 已移除；仅兼容旧值 'full'"
                )
    memory_raw = config.get("memory") or {}
    if not isinstance(memory_raw, dict):
        raise PromptConfigError("memory 必须是对象")
    limits_raw = memory_raw.get("temporary_injection_limits")
    if limits_raw is None:
        limits_raw = {}
    if not isinstance(limits_raw, dict):
        raise PromptConfigError("memory.temporary_injection_limits 必须是对象")
    unknown_limits = sorted(set(limits_raw) - set(DEFAULT_TEMPORARY_MEMORY_LIMITS))
    if unknown_limits:
        raise PromptConfigError(
            "memory.temporary_injection_limits 包含未知项：" + ", ".join(unknown_limits)
        )
    temporary_memory_limits = dict(DEFAULT_TEMPORARY_MEMORY_LIMITS)
    for tier, value in limits_raw.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PromptConfigError(f"memory.temporary_injection_limits.{tier} 必须是非负整数")
        temporary_memory_limits[tier] = value
    important_memory_max_chars = memory_raw.get(
        "important_memory_max_chars",
        DEFAULT_IMPORTANT_MEMORY_MAX_CHARS,
    )
    if (
        isinstance(important_memory_max_chars, bool)
        or not isinstance(important_memory_max_chars, int)
        or important_memory_max_chars < 0
    ):
        raise PromptConfigError("memory.important_memory_max_chars 必须是非负整数")
    return PromptSettings(
        char_limits=char_limits,
        temporary_memory_limits=temporary_memory_limits,
        important_memory_max_chars=important_memory_max_chars,
    )


def _base_section(root: Path, name: str, path: Path) -> PromptSection | None:
    content = read_optional_text(path)
    if not content:
        return None
    return PromptSection(
        name=name,
        content=content,
        source_files=(relative_path(path, root),),
        original_chars=len(content),
        injected_chars=len(content),
        original_items=1,
        injected_items=1,
    )


def _descriptor_section(
    name: str,
    descriptors: Iterable[SkillDescriptor],
    *,
    max_chars: int,
) -> PromptSection | None:
    if max_chars == 0:
        return None
    values = list(descriptors)
    pieces: list[str] = []
    files: list[str] = []
    offsets: list[int] = []
    used = 0
    for descriptor in values:
        piece = (
            f"### {descriptor.title}\n"
            f"来源（{descriptor.scope}）：`{descriptor.relative_path}`\n"
            "以下为发现摘要；匹配当前任务时，先按来源路径读取完整说明，再使用已有获准工具执行。"
        )
        if descriptor.description:
            piece += f"\n{descriptor.description}"
        offsets.append(used + (2 if pieces else 0))
        pieces.append(piece)
        files.append(descriptor.relative_path)
        used += len(piece) + (2 if len(pieces) > 1 else 0)
    full_text = "\n\n".join(pieces)
    content, truncated = truncate_chars(full_text, max_chars)
    if not content:
        return None
    injected_items = sum(offset < len(content) for offset in offsets)
    return PromptSection(
        name=name,
        content=content,
        source_files=tuple(files[:injected_items]),
        original_chars=len(full_text),
        injected_chars=len(content),
        original_items=len(pieces),
        injected_items=injected_items,
        truncated=truncated,
    )


def _subagent_registry_section(
    root: Path,
    *,
    name: str,
    definitions: Iterable[Any],
    introduction: str,
) -> PromptSection | None:
    values = [definition for definition in definitions if definition.trigger_registration]
    if not values:
        return None
    pieces = [introduction]
    files: list[str] = []
    for definition in values:
        pieces.append(f"### {definition.name}\n{definition.trigger_registration}")
        files.append(relative_path(definition.directory / definition.trigger_file, root))
    content = "\n\n".join(pieces)
    return PromptSection(
        name=name,
        content=content,
        source_files=tuple(files),
        original_chars=len(content),
        injected_chars=len(content),
        original_items=len(values),
        injected_items=len(values),
        item_ids=tuple(definition.name for definition in values),
    )


def _bounded_section(
    section: PromptSection | None,
    *,
    max_chars: int,
) -> PromptSection | None:
    if section is None or max_chars == 0:
        return None
    content, truncated = truncate_chars(section.content, max_chars)
    if not content:
        return None
    return PromptSection(
        name=section.name,
        content=content,
        source_files=section.source_files,
        item_ids=section.item_ids,
        original_chars=section.original_chars,
        injected_chars=len(content),
        original_items=section.original_items,
        injected_items=section.injected_items,
        truncated=section.truncated or truncated,
        mode=section.mode,
    )


def _global_subagent_registry_section(root: Path) -> PromptSection | None:
    # 延迟导入避免 schema -> prompt_sources -> prompt 的初始化环。
    from agents._runtime.schema import discover_agents

    definitions = [
        definition
        for definition in discover_agents(root).enabled_agents()
        if definition.source == "builtin"
    ]
    return _subagent_registry_section(
        root,
        name="global_subagent_registry",
        definitions=definitions,
        introduction=(
            "以下框架内置子代理可按注册条件调用。这里只提供注册摘要；"
            "详细操作信息位于对应 trigger.md，调用前按需读取。"
        ),
    )


def _user_subagent_registry_section(root: Path, user: str) -> PromptSection | None:
    # 延迟导入避免 schema -> prompt_sources -> prompt 的初始化环。
    from agents._runtime.schema import discover_agents

    definitions = [
        definition
        for definition in discover_agents(root, user).enabled_agents()
        if definition.source == "user"
    ]
    return _subagent_registry_section(
        root,
        name="user_subagent_registry",
        definitions=definitions,
        introduction=(
            "以下当前用户自建子代理可按注册条件调用。这里只提供注册摘要；"
            "详细操作信息位于对应 trigger.md，调用前按需读取。"
        ),
    )


def _section_diagnostic(section: PromptSection) -> dict[str, Any]:
    return {
        "mode": section.mode,
        "original_items": section.original_items,
        "injected_items": section.injected_items,
        "original_chars": section.original_chars,
        "injected_chars": section.injected_chars,
        "truncated": section.truncated,
        "source_files": list(section.source_files),
        "item_ids": list(section.item_ids),
    }


def _ordered_prompt_sections(
    sections: Iterable[PromptSection],
    *,
    disabled_names: frozenset[str] = frozenset(),
) -> tuple[PromptSection, ...]:
    section_map = {section.name: section for section in sections}
    padded = tuple(
        PromptSection(name=name, content="", mode="disabled")
        if name in disabled_names
        else section_map.get(name, PromptSection(name=name, content="（无）"))
        for name in PROMPT_SECTION_ORDER
    )
    order = [section.name for section in padded]
    expected = list(PROMPT_SECTION_ORDER)
    if order != expected:
        raise AssertionError(f"提示词段顺序偏离固定契约：{order}")
    return padded


def _render_prompt_sections(sections: Iterable[PromptSection]) -> str:
    return "\n\n".join(
        f"[{section.name}]\n{section.content}"
        for section in sections
        if section.mode != "disabled"
    )


def build_prompt_bundle(
    root: Path,
    user: str,
    config: dict[str, Any],
    *,
    plugin_manifests: tuple[PluginManifest, ...] | None = None,
    memory_store: MemoryStore | None = None,
    source: str | None = None,
    session_id: str | None = None,
) -> PromptBundle:
    """Build the initial prompt snapshot for one user-visible conversation run."""

    root = root.resolve()
    settings = parse_prompt_settings(config)
    inject_memory = memory_prompt_injection(config)
    source_policy = MainAgentSourcePolicy.from_config(config)
    manifests = (
        discover_plugin_manifests(root)
        if plugin_manifests is None
        else plugin_manifests
    )
    manifests = tuple(
        manifest
        for manifest in manifests
        if source_policy.plugins.allows(str(manifest.tool.get("name") or ""))
    )
    store = memory_store or MemoryStore(root, user, config)
    registered_sources = load_prompt_source_registry(root, user)
    sections: list[PromptSection] = []

    section = _bounded_section(
        _base_section(root, "user_soul", root / "users" / user / "user_soul.md"),
        max_chars=settings.char_limits["user_soul"],
    )
    if section:
        sections.append(section)
    section = _base_section(root, "global_soul", root / "config" / "global_soul.md")
    if section:
        sections.append(section)
    section = _bounded_section(
        _base_section(root, "agents_manual", root / "agents.md"),
        max_chars=settings.char_limits["agents_manual"],
    )
    if section:
        sections.append(section)
    section = _global_subagent_registry_section(root)
    if section:
        sections.append(section)
    section = _user_subagent_registry_section(root, user)
    if section:
        sections.append(section)

    section = _descriptor_section(
        "plugins",
        (manifest.descriptor for manifest in manifests if manifest.enabled),
        max_chars=settings.char_limits["plugin_prompts"],
    )
    if section:
        sections.append(section)
    section = _descriptor_section(
        "skills",
        registered_sources.select_skills(
            allow={
                "shared": source_policy.shared_skills.selector(),
                "user": source_policy.user_skills.selector(),
            }
        ),
        max_chars=settings.char_limits["skill_prompts"],
    )
    if section:
        sections.append(section)

    capabilities = registered_sources.expand_capabilities(max_chars=settings.char_limits["expand_data"], allow={
        "global": source_policy.global_expand.selector(),
        "shared": source_policy.shared_expand.selector(),
        "user": None,
    })
    if capabilities.text:
        sections.append(PromptSection(
            "expansions", capabilities.text, source_files=capabilities.source_files,
            original_chars=capabilities.original_chars,
            injected_chars=capabilities.injected_chars,
            original_items=capabilities.original_items,
            injected_items=capabilities.injected_items, truncated=capabilities.truncated,
        ))

    tier_specs_all = (
        ("permanent", "permanent_memory", None),
        (
            "half_year",
            "temporary_memory:half_year",
            settings.temporary_memory_limits["half_year"],
        ),
        (
            "one_month",
            "temporary_memory:one_month",
            settings.temporary_memory_limits["one_month"],
        ),
        (
            "seven_days",
            "temporary_memory:seven_days",
            settings.temporary_memory_limits["seven_days"],
        ),
    )
    tier_sections: dict[str, PromptSection] = {}
    memory_ids: list[str] = []
    memory_files: list[str] = []
    memory_integrity_warnings: list[str] = []
    if inject_memory:
        for (
            tier,
            section_name,
            max_files,
        ) in tier_specs_all:
            selection = store.select_tier_for_prompt(
                tier,
                max_files=max_files,
                mode=INJECTION_MODE,
            )
            memory_integrity_warnings.extend(selection.integrity_warnings)
            memory_files.extend(f"{tier}/{filename}" for filename in selection.selected_ids)
            if tier != "permanent":
                memory_ids.extend(selection.selected_ids)
            if selection.text:
                tier_sections[section_name] = PromptSection(
                    section_name,
                    selection.text,
                    tuple(relative_path(path, root) for path in selection.source_files),
                    selection.selected_ids,
                    selection.original_chars,
                    len(selection.text),
                    selection.original_items,
                    selection.injected_items,
                    selection.truncated,
                    INJECTION_MODE,
                )
        if "permanent_memory" in tier_sections:
            sections.append(tier_sections["permanent_memory"])

        if settings.important_memory_max_chars > 0:
            important_path = root / "users" / user / "memory_temporary_important.md"
            important = (
                read_optional_text(important_path)
                if store.important_view_is_current()
                else ""
            )
            injected, truncated = truncate_chars(important, settings.important_memory_max_chars)
            if injected:
                memory_files.append("memory_temporary_important.md")
                sections.append(
                    PromptSection(
                        "important_memory",
                        injected,
                        (relative_path(important_path, root),),
                        original_chars=len(important),
                        injected_chars=len(injected),
                        original_items=1,
                        injected_items=1,
                        truncated=truncated,
                        mode=INJECTION_MODE,
                    )
                )
        for name in (
            "temporary_memory:half_year",
            "temporary_memory:one_month",
            "temporary_memory:seven_days",
        ):
            if name in tier_sections:
                sections.append(tier_sections[name])
    else:
        notice = "当前记忆没有开启注入，可以通过工具进行记忆检索"
        sections.append(PromptSection(
            "important_memory", notice,
            original_chars=len(notice), injected_chars=len(notice),
        ))

    padded = _ordered_prompt_sections(
        sections,
        disabled_names=(frozenset(name for _, name, _ in tier_specs_all)
                        if not inject_memory else frozenset()),
    )
    order = [section.name for section in padded]
    text = _render_prompt_sections(padded)
    diagnostics = {
        "total_chars": len(text),
        "section_order": order,
        "sections": {section.name: _section_diagnostic(section) for section in padded},
        "source_policy": source_policy.public_summary(),
        "source_selection": registered_sources.selection_diagnostics(),
        "memory_integrity_warnings": list(dict.fromkeys(memory_integrity_warnings)),
        "memory_prompt_injection": inject_memory,
        "task_plan_scope": {"source": source, "session_id": session_id},
    }
    return PromptBundle(
        text=text,
        sections=padded,
        memory_ids=tuple(memory_ids),
        diagnostics=diagnostics,
        memory_files=tuple(memory_files),
    )


def build_system_prompt(root: Path, user: str, config: dict[str, Any]) -> str:
    return build_prompt_bundle(root, user, config).text


def refresh_memory_prompt_bundle(bundle: PromptBundle, root: Path, user: str,
                                 config: dict[str, Any], *, memory_store: MemoryStore) -> PromptBundle:
    """Refresh only low-frequency memory; stable persona/capability bytes stay fixed."""
    current = build_prompt_bundle(root, user, config, memory_store=memory_store)
    names = {name for name in PROMPT_SECTION_ORDER if "memory" in name}
    replacement = {section.name: section for section in current.sections if section.name in names}
    sections = tuple(replacement.get(section.name, section) for section in bundle.sections)
    text = _render_prompt_sections(sections)
    diagnostics = copy.deepcopy(bundle.diagnostics)
    diagnostics["total_chars"] = len(text)
    diagnostics["sections"] = {section.name: _section_diagnostic(section) for section in sections}
    diagnostics["memory_integrity_warnings"] = current.diagnostics.get("memory_integrity_warnings", [])
    diagnostics["memory_prompt_injection"] = current.diagnostics["memory_prompt_injection"]
    return PromptBundle(text, sections, current.memory_ids, diagnostics, current.memory_files)
