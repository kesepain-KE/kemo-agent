"""Deterministic assembly of the complete per-request system prompt."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from plugins.manifest import PluginManifest, discover_plugin_manifests
from run.knowledge import KnowledgeIndexSelection, select_knowledge_index
from run.memory import MemoryStore
from run.prompt_sources import (
    SkillDescriptor,
    load_prompt_source_registry,
    read_optional_text,
    relative_path,
    truncate_chars,
)
from run.source_policy import MainAgentSourcePolicy
from run.task_plan_store import select_prompt_plans


PROMPT_SECTION_ORDER = (
    "user_soul",
    "global_soul",
    "agents_manual",
    "global_subagent_registry",
    "user_subagent_registry",
    "plugins",
    "skills",
    "knowledge_index",
    "permanent_memory",
    "important_memory",
    "temporary_memory:half_year",
    "temporary_memory:one_month",
    "temporary_memory:seven_days",
    "task_plan",
    "expand_data",
    "perception",
)

DEFAULT_TEMPORARY_MEMORY_LIMITS = {
    "half_year": 300,
    "one_month": 200,
    "seven_days": 100,
}
DEFAULT_CHAR_LIMITS = {
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

    char_limits = _nonnegative_group(raw.get("char_limits"), DEFAULT_CHAR_LIMITS, "char_limits")
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
    important_memory_max_chars = memory_raw.get("important_memory_max_chars", 2000)
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
        piece = f"### {descriptor.title}"
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


def _knowledge_index_prompt(
    selection: KnowledgeIndexSelection,
    *,
    scopes: tuple[str, ...],
) -> tuple[str, tuple[Path, ...], int]:
    """Render every enabled local knowledge index without external replacement."""

    pieces: list[str] = []
    injected_paths: list[Path] = []
    injected_items = 0
    for scope in scopes:
        for document in selection.documents:
            if document.scope != scope:
                continue
            pieces.append(
                f"[{document.scope}:{document.relative_path}]\n{document.content}"
            )
            injected_paths.append(document.path)
            injected_items += 1
    return "\n\n".join(pieces), tuple(injected_paths), injected_items


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


def build_prompt_bundle(
    root: Path,
    user: str,
    config: dict[str, Any],
    *,
    plugin_manifests: tuple[PluginManifest, ...] | None = None,
    memory_store: MemoryStore | None = None,
) -> PromptBundle:
    """Build one immutable prompt snapshot for a complete provider/tool loop."""

    root = root.resolve()
    settings = parse_prompt_settings(config)
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

    section = _base_section(root, "user_soul", root / "users" / user / "user_soul.md")
    if section:
        sections.append(section)
    section = _base_section(root, "global_soul", root / "config" / "global_soul.md")
    if section:
        sections.append(section)
    section = _base_section(root, "agents_manual", root / "agents.md")
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

    knowledge = select_knowledge_index(
        root,
        user,
        scopes=source_policy.knowledge_scopes,
    )
    knowledge_text, knowledge_paths, knowledge_injected_items = _knowledge_index_prompt(
        knowledge,
        scopes=source_policy.knowledge_scopes,
    )
    if knowledge_text:
        sections.append(
            PromptSection(
                "knowledge_index",
                knowledge_text,
                tuple(relative_path(path, root) for path in knowledge_paths),
                original_chars=knowledge.original_chars,
                injected_chars=len(knowledge_text),
                original_items=knowledge.original_items,
                injected_items=knowledge_injected_items,
                truncated=knowledge.truncated,
                mode=INJECTION_MODE,
            )
        )

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

    plans = select_prompt_plans(root, user, max_chars=settings.char_limits["task_plan"])
    if plans.text:
        sections.append(
            PromptSection(
                "task_plan",
                plans.text,
                plans.source_files,
                original_chars=plans.original_chars,
                injected_chars=plans.injected_chars,
                original_items=plans.original_items,
                injected_items=plans.injected_items,
                truncated=plans.truncated,
                mode=INJECTION_MODE,
            )
        )
    expand = registered_sources.select_expand(
        max_chars=settings.char_limits["expand_data"],
        mode=INJECTION_MODE,
        allow={
            "global": source_policy.global_expand.selector(),
            "shared": source_policy.shared_expand.selector(),
            "user": None,
        },
    )
    if expand.text:
        sections.append(
            PromptSection(
                "expand_data",
                expand.text,
                expand.source_files,
                original_chars=expand.original_chars,
                injected_chars=expand.injected_chars,
                original_items=expand.original_items,
                injected_items=expand.injected_items,
                truncated=expand.truncated,
                mode=INJECTION_MODE,
            )
        )
    perception = registered_sources.select_perception(
        max_chars=settings.char_limits["perception"],
        mode=INJECTION_MODE,
        allow_modules=source_policy.global_perception.selector(),
    )
    if perception.text:
        sections.append(
            PromptSection(
                "perception",
                perception.text,
                perception.source_files,
                original_chars=perception.original_chars,
                injected_chars=perception.injected_chars,
                original_items=perception.original_items,
                injected_items=perception.injected_items,
                truncated=perception.truncated,
                mode=INJECTION_MODE,
            )
        )

    section_map = {section.name: section for section in sections}
    padded: list[PromptSection] = []
    for name in PROMPT_SECTION_ORDER:
        if name in section_map:
            padded.append(section_map[name])
        else:
            padded.append(PromptSection(name=name, content="（无）"))
    order = [section.name for section in padded]
    expected = list(PROMPT_SECTION_ORDER)
    if order != expected:
        raise AssertionError(f"提示词段顺序偏离固定契约：{order}")
    text = "\n\n".join(f"[{section.name}]\n{section.content}" for section in padded)
    diagnostics = {
        "total_chars": len(text),
        "section_order": order,
        "sections": {section.name: _section_diagnostic(section) for section in padded},
        "knowledge_documents": [
            {"scope": item.scope, "path": item.relative_path, "title": item.title}
            for item in knowledge.documents
        ],
        "source_policy": source_policy.public_summary(),
        "source_selection": registered_sources.selection_diagnostics(),
        "memory_integrity_warnings": list(dict.fromkeys(memory_integrity_warnings)),
    }
    return PromptBundle(
        text=text,
        sections=tuple(padded),
        memory_ids=tuple(memory_ids),
        diagnostics=diagnostics,
        memory_files=tuple(memory_files),
    )


def build_system_prompt(root: Path, user: str, config: dict[str, Any]) -> str:
    return build_prompt_bundle(root, user, config).text
