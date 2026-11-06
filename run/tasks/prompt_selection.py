"""Bounded selection of unfinished plans for prompt injection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from run.config import truncate_chars
from run.tasks.errors import PlanError
from run.tasks.store import TASK_PLAN_DB_FILENAME, PlanStore, _plan_dir


@dataclass(frozen=True, slots=True)
class TaskPlanSelection:
    text: str
    source_files: tuple[str, ...]
    original_chars: int
    injected_chars: int
    original_items: int
    injected_items: int
    truncated: bool
    revisions: tuple[tuple[str, int], ...] = ()


def _prompt_step(step: dict[str, Any]) -> str:
    description = step.get("description") or step.get("title") or "未命名步骤"
    status = str(step.get("status") or "pending")
    if status == "completed":
        return f"- [x] {description} ✓"
    return f"- [ ] {description}（{status}）"


def select_prompt_plans(
    root: Path,
    user: str,
    *,
    max_chars: int,
    source: str | None = None,
    session_id: str | None = None,
) -> TaskPlanSelection:
    """Read unfinished plans, optionally limited to one conversation space."""

    if max_chars == 0:
        return TaskPlanSelection("", (), 0, 0, 0, 0, False)
    directory = _plan_dir(root, user)
    if not directory.is_dir():
        return TaskPlanSelection("", (), 0, 0, 0, 0, False)
    plans = PlanStore(root, user).list_plans(
        statuses=("pending", "approved", "running", "paused"),
        source=source,
        session_id=session_id,
    )
    status_map = {
        "pending": "pending",
        "approved": "active",
        "running": "active",
        "paused": "active",
        "completed": "completed",
        "failed": "aborted",
        "cancelled": "aborted",
    }
    pieces: list[str] = []
    selected_paths: list[str] = []
    offsets: list[int] = []
    used = 0
    for data in plans:
        if source is not None and str(data.get("source") or "") != source:
            continue
        if session_id is not None and str(data.get("session_id") or "") != session_id:
            continue
        plan_id = str(data.get("plan_id") or "")
        mapped = status_map.get(str(data.get("status") or ""))
        if mapped is None:
            raise PlanError(f"计划状态无效：{plan_id}")
        if mapped in {"completed", "aborted"}:
            continue
        title = str(data.get("title") or plan_id)
        description = str(data.get("description") or "").strip()
        lines = [
            f"[plan:{plan_id}]",
            f"title: {title}",
            f"status: {mapped}",
            f"revision: {data['revision']}",
        ]
        if description:
            lines.append(f"description: {description}")
        steps = data.get("steps") or []
        if not isinstance(steps, list):
            raise PlanError(f"计划 steps 必须是数组：{plan_id}")
        lines.extend(_prompt_step(step) for step in steps if isinstance(step, dict))
        piece = "\n".join(lines)
        offsets.append(used + (2 if pieces else 0))
        pieces.append(piece)
        selected_paths.append(
            f"users/{user}/task_plan/{TASK_PLAN_DB_FILENAME}#{plan_id}"
        )
        used += len(piece) + (2 if len(pieces) > 1 else 0)
    full_text = "\n\n".join(pieces)
    text, truncated = truncate_chars(full_text, max_chars)
    injected_count = sum(offset < len(text) for offset in offsets)
    return TaskPlanSelection(
        text=text,
        source_files=tuple(selected_paths[:injected_count]),
        original_chars=len(full_text),
        injected_chars=len(text),
        original_items=len(pieces),
        injected_items=injected_count,
        truncated=truncated,
        revisions=tuple((str(plan["plan_id"]), int(plan["revision"])) for plan in plans),
    )
