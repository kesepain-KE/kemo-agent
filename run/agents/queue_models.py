"""In-memory scheduler task and bounded snapshot contract."""
from __future__ import annotations

import copy
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal
from events import RunEvent
from run.agents.contracts import AgentRunResult


TaskStatus = Literal[
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
    "timed_out",
    "timed_out_running",
    "tool_limit",
]


@dataclass(slots=True)
class AgentTask:
    id: str
    agent: str
    input_data: dict[str, Any]
    source: str = ""
    session_id: str = ""
    status: TaskStatus = "queued"
    submission_order: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str = ""
    finished_at: str = ""
    timeout: float | None = None
    survival_seconds: float = 120.0
    completed_after_timeout: bool = False
    model_override: str | None = None
    max_tokens: int | None = None
    config: dict[str, Any] | None = field(default=None, repr=False)
    job: Callable[[threading.Event], Any] | None = field(default=None, repr=False)
    serial: bool = True
    result_handler: Callable[[AgentRunResult], None] | None = field(default=None, repr=False)
    event_callback: Callable[[RunEvent], None] | None = field(default=None, repr=False)
    result: Any = None
    error: dict[str, Any] | None = None
    host_exception: BaseException | None = field(default=None, repr=False)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    done_event: threading.Event = field(default_factory=threading.Event, repr=False)
    ledger: Any = field(default=None, repr=False)
    run_count: int = 1
    resumable: bool = False
    resume_state: dict[str, Any] | None = field(default=None, repr=False)
    previous_elapsed_ms: int = 0
    max_tool_calls: int | None = None
    progress: dict[str, Any] = field(default_factory=dict)
    origin_task_id: str = ""

    @property
    def scope(self) -> tuple[str, str]:
        return self.source, self.session_id

    def snapshot(self) -> dict[str, Any]:
        result = self.result
        if isinstance(result, AgentRunResult):
            result_snapshot: Any = {
                "agent": result.agent,
                "data": copy.deepcopy(result.data),
                "usage": copy.deepcopy(result.usage),
                "model": result.model,
                "metadata": copy.deepcopy(result.metadata),
            }
        elif result is not None:
            result_snapshot = copy.deepcopy(result)
        else:
            result_snapshot = None
        return {
            "id": self.id,
            "agent": self.agent,
            "source": self.source,
            "session_id": self.session_id,
            "status": self.status,
            "submission_order": self.submission_order,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "timeout": self.timeout,
            "survival_seconds": self.survival_seconds,
            "completed_after_timeout": self.completed_after_timeout,
            "model_override": self.model_override,
            "result": result_snapshot,
            "error": copy.deepcopy(self.error),
            "run_count": self.run_count,
            "resumable": self.resumable and self.status in {"timed_out", "tool_limit"},
            "budget": copy.deepcopy((result.metadata if isinstance(result, AgentRunResult) else {}).get("budget") or self.progress.get("budget") or {}),
            "progress": copy.deepcopy(self.progress),
            "origin_task_id": self.origin_task_id,
            "usage": copy.deepcopy(result.usage if isinstance(result, AgentRunResult) else self.progress.get("usage") or {}),
            "stop_reason": (self.error or {}).get("stop_reason"),
            "previous_elapsed_ms": self.previous_elapsed_ms,
        }

