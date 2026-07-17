"""Minimal fixed-order system prompt assembly."""

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


def build_system_prompt(root: Path, user: str, config: dict) -> str:
    """Assemble enabled prompt sections in the architecture's fixed order.

    Fragmented memories, tools, child-agent instructions and on-demand
    knowledge are intentionally left for later stages.  The hot-memory file is
    included because it already has a stable path and is part of the permanent
    base sequence; an empty file contributes nothing.
    """

    prompt_config = config.get("prompt") or {}
    enabled = {
        "global_soul": bool(prompt_config.get("include_global_soul", True)),
        "user_soul": bool(prompt_config.get("include_user_soul", True)),
        "agents_manual": bool(prompt_config.get("include_agents_manual", True)),
        "important_memory": True,
    }
    sections: list[str] = []
    for name, relative in _PROMPT_SOURCES:
        if not enabled[name]:
            continue
        path = root / Path(str(relative).format(user=user))
        content = _read_optional(path)
        if content:
            sections.append(f"[{name}]\n{content}")
    return "\n\n".join(sections)
