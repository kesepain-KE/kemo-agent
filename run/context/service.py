"""CardStream context inspection. Reading status never creates a history store."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from run.cards import CardStore, DynamicTail, RequestOccupancy, estimate_tokens, json_text
from run.config import (
    ConfigError, build_prompt_bundle, load_config, project_root,
    provider_runtime_config, read_dynamic_tail,
)
from run.conversation import required_text
from run.tools import ToolRegistry, apply_runtime_tool_policy, discover_tools
from run.extensions import cached_model_capabilities, context_input_budget, model_output_token_limit


def context_status(request: dict[str, Any], *, root: Path | None = None,
                   tool_registry_factory=discover_tools, prompt_bundle=None) -> dict[str, Any]:
    user, source, session_id = (required_text(request, key) for key in ("user", "source", "session_id"))
    base = (root or project_root()).resolve()
    config = load_config(user, base)
    agents = config.get("agents") or {}
    registry = (apply_runtime_tool_policy(tool_registry_factory(base, user), config)
                if (config.get("tools") or {}).get("enabled", True) else ToolRegistry({}))
    bundle = prompt_bundle or build_prompt_bundle(
        base, user, config, plugin_manifests=registry.plugin_manifests,
        source=source, session_id=session_id)
    store = CardStore(base, user)
    state = store.session(source, session_id) if store.path.is_file() else None
    snapshot = store.snapshot(source, session_id) if state else None
    dynamic = read_dynamic_tail(base, user, config, source=source, session_id=session_id)
    tail_items = DynamicTail(json_text(dynamic)).items()
    output_reserve = int(agents.get("output_reserve", 10000))
    provider = config.get("provider") or {}
    configured = int(agents.get("token_limit", 1000000))
    try:
        capabilities = cached_model_capabilities(provider_runtime_config(config))
    except ConfigError:
        capabilities = None  # Status remains readable before API credentials exist.
    max_output_tokens = model_output_token_limit(
        capabilities, configured=provider.get("max_tokens"),
    )
    capacity = context_input_budget(
        configured, capabilities,
        output_reserve=min(output_reserve, max_output_tokens) if max_output_tokens else output_reserve,
    )
    limit = capacity["effective_token_limit"]
    compression_ratio = agents.get("token_compression_ratio", .3)
    tools = [tool.model_dump(mode="json", exclude_none=True) for tool in registry.protocol_tools()]
    occupancy = RequestOccupancy(
        estimate_tokens(bundle.text) if bundle.text else 0, snapshot.history_tokens if snapshot else 0,
        estimate_tokens(tail_items[0].model_dump(mode="json")) if tail_items else 0,
        estimate_tokens(tools) if tools else 0, limit,
    )
    summary = next((card for card in snapshot.cards if card.view_kind == "summary"), None) if snapshot else None
    return {
        "user": user, "source": source, "session_id": session_id,
        "engine": "cardstream", "storage": "cards.sqlite3", "exists": bool(state),
        "lifecycle": state["lifecycle"] if state else "new",
        "context": {
            "fixed_tokens": occupancy.fixed_tokens, "history_tokens": occupancy.history_tokens,
            "dynamic_tokens": occupancy.dynamic_tokens, "schema_tokens": occupancy.schema_tokens,
            "estimated_tokens_before": occupancy.total, "input_budget": limit,
            "effective_token_limit": limit, "capability_unknown": capacity["capability_unknown"],
            "context_window_tokens": capacity["context_window_tokens"],
            "context_window_source": capacity["context_window_source"],
            "input_limit_source": capacity["input_limit_source"],
            "cards_active": len(snapshot.cards) if snapshot else 0,
            "last_seq": snapshot.last_seq if snapshot else 0,
            "view_epoch": snapshot.view_epoch if snapshot else 0,
            "compression_needed": occupancy.should_compact(),
            "compression_threshold": int(limit * .8),
            "compression_target_tokens": int(occupancy.history_tokens * compression_ratio),
            "summary": {"exists": summary is not None,
                        "card_id": summary.card_id if summary else None,
                        "compression_stage": summary.compression_stage if summary else 0},
            "measurement": "local_estimate",
        },
        "prompt": bundle.diagnostics,
        "policy": {"token_limit": configured, "output_reserve": capacity["output_reserve"],
                   "compression_ratio": compression_ratio,
                   "trigger_ratio": .8, "compression_unit": "card"},
    }
