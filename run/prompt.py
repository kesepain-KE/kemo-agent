"""Deterministic assembly of the complete per-request system prompt."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from plugins.manifest import PluginManifest, discover_plugin_manifests
from run.knowledge import KnowledgeIndexSelection, select_knowledge_index
from run.kemo_graph import KemoGraphPromptContext, load_kemo_graph_prompt_context
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
    "kemo_graph",
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
    "perception": 8000,
    "expand_data": 10000,
    "skill_prompts": 8000,
    "plugin_prompts": 10000,
}
DEFAULT_INJECTION_MODES = {
    "permanent_memory": "full",
    "important_memory": "full",
    "temporary_seven_days": "full",
    "temporary_one_month": "full",
    "temporary_half_year": "full",
    "knowledge_index": "full",
    "task_plan": "full",
    "expand_data": "full",
    "perception": "full",
}


class PromptConfigError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PromptSettings:
    char_limits: dict[str, int]
    injection_mode: dict[str, str]
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
    if mode_raw is None:
        mode_raw = {}
    if not isinstance(mode_raw, dict):
        raise PromptConfigError("prompt.injection_mode 必须是对象")
    modes = dict(DEFAULT_INJECTION_MODES)
    unknown_modes = sorted(set(mode_raw) - set(modes))
    if unknown_modes:
        raise PromptConfigError(
            f"prompt.injection_mode 包含未知项：{', '.join(unknown_modes)}"
        )
    for key in modes:
        if key not in mode_raw:
            continue
        value = mode_raw[key]
        if not isinstance(value, str) or not value.strip():
            raise PromptConfigError(f"prompt.injection_mode.{key} 必须是非空字符串")
        modes[key] = value.strip().casefold()
    for key, mode in modes.items():
        if mode != "full":
            raise PromptConfigError(
                f"prompt.injection_mode.{key}={mode!r} 暂不支持；当前只支持 'full'"
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
        injection_mode=modes,
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


_KNOWLEDGE_REPLACEMENT_PATHS = {
    "shared": "shared_knowledge/",
    "global": "global_knowledge/",
}


def _knowledge_index_prompt(
    selection: KnowledgeIndexSelection,
    *,
    user: str,
    scopes: tuple[str, ...],
    replaced_scopes: tuple[str, ...],
) -> tuple[str, tuple[Path, ...], int]:
    """Render full indexes while retaining explicit graph-replacement markers."""

    replaced = set(replaced_scopes)
    pieces: list[str] = []
    injected_paths: list[Path] = []
    injected_items = 0
    for scope in scopes:
        if scope in replaced:
            base = _KNOWLEDGE_REPLACEMENT_PATHS.get(
                scope, f"users/{user}/knowledge/"
            )
            pieces.append(f"# {base} 目录结构，已被知识图谱替代")
            injected_items += 1
            continue
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
    graph_context_loader: Callable[..., KemoGraphPromptContext] = (
        load_kemo_graph_prompt_context
    ),
) -> PromptBundle:
    """Build one immutable prompt snapshot for a complete provider/tool loop."""

    root = root.resolve()
    settings = parse_prompt_settings(config)
    source_policy = MainAgentSourcePolicy.from_config(config)
    replaces_knowledge = any(
        (
            source_policy.kemo_graph_global_knowledge,
            source_policy.kemo_graph_shared_knowledge,
            source_policy.kemo_graph_user_knowledge,
        )
    )
    graph_context = graph_context_loader(
        root,
        user,
        config,
        replaces_knowledge=replaces_knowledge,
        replaces_memory=source_policy.kemo_graph_replaces_temporary_memory,
    )
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
    replaced_knowledge_scopes = source_policy.replaced_knowledge_scopes()
    knowledge_text, knowledge_paths, knowledge_injected_items = _knowledge_index_prompt(
        knowledge,
        user=user,
        scopes=source_policy.knowledge_scopes,
        replaced_scopes=replaced_knowledge_scopes,
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
                mode=settings.injection_mode["knowledge_index"],
            )
        )

    if graph_context.text:
        sections.append(
            PromptSection(
                "kemo_graph",
                graph_context.text,
                tuple(relative_path(path, root) for path in graph_context.source_files),
                original_chars=len(graph_context.text),
                injected_chars=len(graph_context.text),
                original_items=sum(layer.enabled for layer in graph_context.layers),
                injected_items=sum(
                    layer.enabled and bool(layer.text.strip())
                    for layer in graph_context.layers
                ),
            )
        )

    tier_specs_all = (
        ("permanent", "permanent_memory", "permanent_memory", None),
        (
            "half_year",
            "temporary_memory:half_year",
            "temporary_half_year",
            settings.temporary_memory_limits["half_year"],
        ),
        (
            "one_month",
            "temporary_memory:one_month",
            "temporary_one_month",
            settings.temporary_memory_limits["one_month"],
        ),
        (
            "seven_days",
            "temporary_memory:seven_days",
            "temporary_seven_days",
            settings.temporary_memory_limits["seven_days"],
        ),
    )
    memory_replacement_labels = {
        "half_year": "# 用户的临时重要记忆，遗忘周期6个月，已被知识图谱替代",
        "one_month": "# 用户的临时重要记忆，遗忘周期一个月，已被知识图谱替代",
        "seven_days": "# 用户的临时重要记忆，遗忘周期七天，已被知识图谱替代",
    }
    tier_sections: dict[str, PromptSection] = {}
    memory_ids: list[str] = []
    memory_files: list[str] = []
    memory_integrity_warnings: list[str] = []
    for (
        tier,
        section_name,
        config_name,
        max_files,
    ) in tier_specs_all:
        if tier != "permanent" and source_policy.kemo_graph_replaces_temporary_memory:
            content = memory_replacement_labels[tier]
            tier_sections[section_name] = PromptSection(
                section_name,
                content,
                original_chars=len(content),
                injected_chars=len(content),
                original_items=1,
                injected_items=1,
                mode=settings.injection_mode[config_name],
            )
            continue
        selection = store.select_tier_for_prompt(
            tier,
            max_files=max_files,
            mode=settings.injection_mode[config_name],
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
                selection.injected_chars,
                selection.original_items,
                selection.injected_items,
                selection.truncated,
                settings.injection_mode[config_name],
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
                    mode=settings.injection_mode["important_memory"],
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
                mode=settings.injection_mode["task_plan"],
            )
        )
    expand = registered_sources.select_expand(
        max_chars=settings.char_limits["expand_data"],
        mode=settings.injection_mode["expand_data"],
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
                mode=settings.injection_mode["expand_data"],
            )
        )
    perception = registered_sources.select_perception(
        max_chars=settings.char_limits["perception"],
        mode=settings.injection_mode["perception"],
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
                mode=settings.injection_mode["perception"],
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
        "knowledge_replaced_scopes": list(replaced_knowledge_scopes),
        "source_policy": source_policy.public_summary(),
        "kemo_graph": graph_context.diagnostics(
            replaces_knowledge=replaces_knowledge,
            replaces_memory=source_policy.kemo_graph_replaces_temporary_memory,
        ),
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
