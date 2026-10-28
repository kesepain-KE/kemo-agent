"""Attach the native media host to one main-run attempt."""

from __future__ import annotations

from typing import Any

from run.extensions import MediaInputHost


def initialize_media_inputs(values: dict[str, Any], input_limit: int) -> MediaInputHost:
    request = values["request"]
    state = request.get("_retry_state")
    if not isinstance(state, dict):
        state = {}
    return MediaInputHost(
        root=values["base"], user=values["user"],
        scope=("main", values["source"], values["session_id"], values["run_id"]),
        state=state, config=values["config"],
        runtime_provider=values["runtime_provider"], provider=values["provider"],
        session_id=values["session_id"], known=values["uploaded_descriptors"],
        user_text=values["prompt"], cancel_event=values["cancel_event"],
        input_limit=input_limit, audit=values["round_state"].media_inputs,
        visible_asset_ids=values.get("direct_asset_ids"),
    )
