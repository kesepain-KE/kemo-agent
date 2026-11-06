"""Read-only fixed Prompt diagnostics for the settings endpoint."""
from __future__ import annotations

from typing import Any

from run.config import PROMPT_SECTION_ORDER, build_prompt_bundle, load_config
from run.tools import ToolRegistry, apply_runtime_tool_policy, discover_tools


def prompt_sections(service, user: Any) -> dict[str, Any]:
    name = service.require_user(user)
    config = load_config(name, service.root)
    registry = (
        apply_runtime_tool_policy(discover_tools(service.root, name), config)
        if (config.get("tools") or {}).get("enabled", True) else ToolRegistry({})
    )
    bundle = build_prompt_bundle(
        service.root, name, config, plugin_manifests=registry.plugin_manifests,
    )
    selected = bundle.diagnostics.get("sections") or {}
    sections = []
    for section_name in PROMPT_SECTION_ORDER:
        detail = selected.get(section_name)
        status = (
            "disabled"
            if isinstance(detail, dict) and detail.get("mode") == "disabled"
            else "injected"
            if isinstance(detail, dict)
            else "omitted"
        )
        sections.append(
            {
                "name": section_name,
                "status": status,
                "original_items": int((detail or {}).get("original_items") or 0),
                "injected_items": int((detail or {}).get("injected_items") or 0),
                "original_chars": int((detail or {}).get("original_chars") or 0),
                "injected_chars": int((detail or {}).get("injected_chars") or 0),
                "truncated": bool((detail or {}).get("truncated", False)),
                "source_files": list((detail or {}).get("source_files") or []),
            }
        )
    source_selection = bundle.diagnostics.get("source_selection") or {}
    return {
        "user": name,
        "total_chars": int(bundle.diagnostics.get("total_chars") or 0),
        "sections": sections,
        "source_policy": bundle.diagnostics.get("source_policy") or {},
        "source_selection": source_selection,
        "expand": source_selection.get("expand") or {},
    }
