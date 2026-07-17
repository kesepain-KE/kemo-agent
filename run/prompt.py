"""Fixed-order system prompt assembly."""

from __future__ import annotations

from pathlib import Path


_PROMPT_SOURCES = (
    ("global_soul", Path("config") / "global_soul.md"),
    ("user_soul", Path("users") / "{user}" / "user_soul.md"),
    ("agents_manual", Path("agents.md")),
    ("important_memory", Path("users") / "{user}" / "memory_temporary_important.md"),
)


def _read_optional(path: Path) -> str:
    try:
        return path.read_text("utf-8").strip()
    except FileNotFoundError:
        return ""


def build_system_prompt(
    root: Path,
    user: str,
    config: dict,
    *,
    memory_text: str = "",
    knowledge_text: str = "",
) -> str:
    """Assemble stable prompt sections from highest-priority base to context data.

    Order is intentionally fixed:
      global soul → user soul → framework manual → hot memory
      → selected memory fragments → selected knowledge documents.

    Tool schemas are sent through the Provider request and sub-agent instructions
    remain private to AgentRunner; neither is duplicated into the main prompt.
    Conversation history is appended later by the context selector.
    """

    prompt_config = config.get("prompt") or {}
    enabled = {
        "global_soul": bool(prompt_config.get("include_global_soul", True)),
        "user_soul": bool(prompt_config.get("include_user_soul", True)),
        "agents_manual": bool(prompt_config.get("include_agents_manual", True)),
        "important_memory": bool(prompt_config.get("include_important_memory", True)),
    }
    sections: list[str] = []
    for name, relative in _PROMPT_SOURCES:
        if not enabled[name]:
            continue
        path = root / Path(str(relative).format(user=user))
        content = _read_optional(path)
        if content:
            sections.append(f"[{name}]\n{content}")
    if memory_text.strip():
        sections.append(f"[relevant_memory]\n{memory_text.strip()}")
    if knowledge_text.strip():
        sections.append(f"[relevant_knowledge]\n{knowledge_text.strip()}")
    return "\n\n".join(sections)
