"""Capability-enforced prompt and tool snapshots for one sub-agent invocation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents._runtime.schema import AgentDefinition
from run.config import MainAgentSourcePolicy, select_knowledge_index
from run.config import SkillDescriptor, load_prompt_source_registry, truncate_chars
from run.tools import ToolRegistry, apply_runtime_tool_policy, discover_tools


class AgentCapabilityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AgentPromptBundle:
    text: str
    section_order: tuple[str, ...]
    diagnostics: dict[str, Any]


def effective_knowledge_scopes(
    definition: AgentDefinition,
    config: dict[str, Any],
) -> tuple[str, ...]:
    """Return the capability scopes still allowed by the user's policy.

    A subagent manifest is an upper bound, not a way to bypass the user's
    disabled global/shared knowledge settings.  Preserve manifest order so
    prompt and tool diagnostics remain deterministic.
    """

    capabilities = definition.capabilities
    if not capabilities.knowledge_index_enabled:
        return ()
    allowed = set(MainAgentSourcePolicy.from_config(config).direct_knowledge_scopes())
    return tuple(scope for scope in capabilities.knowledge_scopes if scope in allowed)


def _descriptor_text(descriptors: tuple[SkillDescriptor, ...], max_chars: int) -> str:
    pieces = []
    for descriptor in descriptors:
        piece = f"### {descriptor.title}"
        if descriptor.description:
            piece += f"\n{descriptor.description}"
        pieces.append(piece)
    return truncate_chars("\n\n".join(pieces), max_chars)[0]


def build_agent_prompt_bundle(
    root: Path,
    user: str,
    definition: AgentDefinition,
    config: dict[str, Any],
) -> AgentPromptBundle:
    capabilities = definition.capabilities
    prompt_config = config.get("prompt") or {}
    char_limits = prompt_config.get("char_limits") or {}
    skill_limit = max(0, int(char_limits.get("skill_prompts", 4000)))
    sources = load_prompt_source_registry(root, user)
    skills = sources.select_skills(
        allow={
            "shared": capabilities.shared_skills,
            "user": (),
        }
    )
    skill_text = _descriptor_text(skills, skill_limit)
    knowledge_scopes = effective_knowledge_scopes(definition, config)
    knowledge = select_knowledge_index(
        root,
        user,
        scopes=knowledge_scopes,
    )
    sections: list[tuple[str, str]] = [("agent_instruction", definition.instruction)]
    if definition.trigger_registration:
        sections.append(("trigger_registration", definition.trigger_registration))
    if skill_text:
        sections.append(("skills", skill_text))
    if knowledge.text:
        sections.append(("knowledge_index", knowledge.text))
    contract = (
        "只处理调用方显式传入的数据，不假设拥有主对话上下文。\n"
        "必须只返回符合调用方提供的 JSON Schema 的 JSON 对象，不使用 Markdown。"
    )
    sections.append(("execution_contract", contract))
    text = "\n\n".join(f"[{name}]\n{content}" for name, content in sections)
    diagnostics = {
        "section_order": [name for name, _ in sections],
        "total_chars": len(text),
        "skills": [item.relative_path for item in skills],
        "trigger_file": definition.trigger_file,
        "knowledge_indexes": [
            {"scope": item.scope, "path": item.relative_path}
            for item in knowledge.documents
        ],
        "knowledge_scopes": list(knowledge_scopes),
    }
    return AgentPromptBundle(text, tuple(name for name, _ in sections), diagnostics)


def build_agent_tool_registry(
    root: Path,
    user: str,
    definition: AgentDefinition,
    config: dict[str, Any] | None = None,
) -> ToolRegistry:
    discovered = discover_tools(root, user)
    allowed = set(definition.capabilities.plugin_tools)
    forbidden = {"subagent_dispatch"}
    unknown = sorted((allowed - forbidden) - set(discovered.tools))
    if unknown:
        raise AgentCapabilityError(
            f"子代理 {definition.name} 工具白名单包含未知插件：{', '.join(unknown)}"
        )
    runtime_registry = apply_runtime_tool_policy(discovered, config or {})
    selected = {
        name: tool
        for name, tool in runtime_registry.tools.items()
        if name in allowed and name not in forbidden
    }
    return runtime_registry.selected(set(selected))
