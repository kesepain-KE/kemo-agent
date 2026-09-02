"""Mutable state shared by the conversation runtime stages.

The state object keeps stage boundaries explicit without changing the public
conversation API.  Fields are intentionally optional because request setup,
compression, provider exchange, and terminal commit populate them in order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from run.conversation.run_state import RunDependencies, RunIdentity, RoundState


@dataclass(slots=True)
class RoundRuntime:
    """Per-request state handed between conversation orchestration stages."""

    request: dict[str, Any]
    identity: RunIdentity
    dependencies: RunDependencies
    round_state: RoundState
    config: dict[str, Any] | None = None
    context_policy: Any | None = None
    source_policy: Any | None = None
    runtime_provider: dict[str, Any] = field(default_factory=dict)
    provider: Any | None = None
    agent_runner: Any | None = None
    window_path: Path | None = None
    runtime_path: Path | None = None
    archive_window: dict[str, Any] = field(default_factory=dict)
    window: dict[str, Any] = field(default_factory=dict)
    content_blocks: list[Any] = field(default_factory=list)
    prompt: str = ""
    uploaded_descriptors: list[dict[str, Any]] = field(default_factory=list)
    provider_media: list[Any] = field(default_factory=list)
    direct_asset_ids: set[str] = field(default_factory=set)
    vision_route: str | None = None
    uploaded_file_context: str = ""
    durable_user_content_blocks: list[Any] = field(default_factory=list)
    registry: Any | None = None
    tool_schemas: list[dict[str, Any]] | None = None
    prompt_bundle: Any | None = None
    system_message: dict[str, Any] | None = None
    summary_cache: dict[str, Any] | None = None
