"""Explicit identity, dependency, and mutable state for one conversation run."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from run.tools import ToolRegistry
from run.usage import new_usage_total


@dataclass(frozen=True, slots=True)
class RunIdentity:
    root: Path
    user: str
    source: str
    session_id: str
    run_id: str


@dataclass(frozen=True, slots=True)
class RunDependencies:
    provider_factory: Callable[[dict[str, Any]], Any]
    tool_registry_factory: Callable[[Path, str], ToolRegistry]
    cancel_event: threading.Event | None


@dataclass(slots=True)
class RoundState:
    run_started: float
    all_text: list[str] = field(default_factory=list)
    all_reasoning: list[str] = field(default_factory=list)
    observed_text: list[str] = field(default_factory=list)
    observed_reasoning: list[str] = field(default_factory=list)
    tool_records: list[dict[str, Any]] = field(default_factory=list)
    pending_tool_calls: dict[str, dict[str, Any]] = field(default_factory=dict)
    consumed_guidance: list[str] = field(default_factory=list)
    consumed_guidance_details: list[dict[str, Any]] = field(default_factory=list)
    provider_responses: list[dict[str, Any]] = field(default_factory=list)
    tool_argument_retries: int = 0
    usage_total: dict[str, Any] = field(default_factory=new_usage_total)
    context_stats: dict[str, Any] = field(default_factory=dict)
    finalized: bool = False
    history_run_registered: bool = False
    history_run_error: dict[str, Any] | None = None
