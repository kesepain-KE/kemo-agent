"""Cron任务生成服务：通过AgentRunner调用time_plan子代理。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from provider.factory import create_provider
from run.agent_runner import AgentRunError, AgentRunner
from run.config import load_config
from run.cron_store import (
    CronConflictError,
    CronValidationError,
    normalize_task,
)
from cron.schedule import compute_next_run
from run.tools import ToolRegistry, discover_tools


class CronGenerationError(RuntimeError):
    pass


class CronSkipped(CronGenerationError):
    """The sub-agent decided a cron task is unnecessary."""
    pass


def _current_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def generate_cron_task(
    *,
    root: Path,
    user: str,
    user_request: str,
    source: str = "cli",
    config: dict[str, Any] | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
    tool_registry: ToolRegistry | None = None,
    existing_task: dict[str, Any] | None = None,
    edit_request: str | None = None,
) -> dict[str, Any]:
    """Call the time_plan sub-agent to produce a structured cron task draft,
    compute next_run_at, and normalize it.  Returns the task dict (not yet
    persisted).

    Raises ``CronSkipped`` when the sub-agent decides a task is unnecessary.
    Raises ``CronGenerationError`` on sub-agent timeout, non-JSON output,
    or invalid schedule.
    """
    cfg = config or load_config(user, root)
    runner = AgentRunner(root, user, config=cfg, provider_factory=provider_factory)

    input_data: dict[str, Any] = {
        "action": "edit" if existing_task is not None else "create",
        "user_request": user_request,
        "current_time_utc": _current_utc(),
    }
    if existing_task is not None:
        input_data["existing_task"] = existing_task
    if edit_request is not None:
        input_data["edit_request"] = edit_request

    try:
        result = runner.run("time_plan", input_data)
    except AgentRunError as exc:
        raise CronGenerationError(f"time_plan 子代理执行失败：{exc}") from exc

    data = result.data
    action = data.get("action")

    if action == "skip":
        raise CronSkipped(str(data.get("message") or "无法解析为定时任务"))

    if action not in ("create", "edit", "delete"):
        raise CronGenerationError(f"time_plan 子代理返回未知 action：{action!r}")

    if action == "delete":
        return {"action": "delete"}

    schedule = data.get("schedule")
    if not isinstance(schedule, dict) or not schedule.get("type"):
        raise CronGenerationError("time_plan 子代理未返回有效的 schedule")

    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        raise CronGenerationError("time_plan 子代理未返回有效的 title")

    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise CronGenerationError("time_plan 子代理未返回有效的 prompt")

    session_id = data.get("session_id", "cron")

        # 使用确定性调度计算来计算 next_run_at
    try:
        next_run_at = compute_next_run(schedule)
    except CronValidationError as exc:
        raise CronGenerationError(f"调度时间计算失败：{exc}") from exc

    try:
        task = normalize_task(
            title=title,
            prompt=prompt,
            user=user,
            schedule=schedule,
            source=source,
            session_id=session_id if isinstance(session_id, str) and session_id.strip() else "cron",
            next_run_at=next_run_at,
        )
    except CronValidationError as exc:
        raise CronGenerationError(f"任务校验失败：{exc}") from exc

    return task


def edit_cron_task(
    *,
    root: Path,
    user: str,
    task: dict[str, Any],
    edit_request: str,
    config: dict[str, Any] | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
) -> dict[str, Any]:
    """Call the time_plan sub-agent to edit an existing cron task."""
    cfg = config or load_config(user, root)
    runner = AgentRunner(root, user, config=cfg, provider_factory=provider_factory)

    input_data = {
        "action": "edit",
        "user_request": edit_request,
        "existing_task": task,
        "current_time_utc": _current_utc(),
    }

    try:
        result = runner.run("time_plan", input_data)
    except AgentRunError as exc:
        raise CronGenerationError(f"time_plan 子代理执行失败：{exc}") from exc

    data = result.data
    action = data.get("action")
    if action not in ("create", "edit"):
        raise CronGenerationError(f"time_plan 子代理返回未知 action：{action!r}")

    schedule = data.get("schedule")
    if not isinstance(schedule, dict) or not schedule.get("type"):
        raise CronGenerationError("time_plan 子代理未返回有效的 schedule")

    title = data.get("title") or task.get("title", "")
    prompt = data.get("prompt") or task.get("prompt", "")
    session_id = data.get("session_id", task.get("session_id", "cron"))

    try:
        next_run_at = compute_next_run(schedule)
    except CronValidationError as exc:
        raise CronGenerationError(f"调度时间计算失败：{exc}") from exc

    try:
        return normalize_task(
            task_id=task.get("task_id"),
            title=title,
            prompt=prompt,
            user=user,
            schedule=schedule,
            source=task.get("source", "cli"),
            session_id=session_id if isinstance(session_id, str) and session_id.strip() else "cron",
            next_run_at=next_run_at,
            status=task.get("status", "enabled"),
        )
    except CronValidationError as exc:
        raise CronGenerationError(f"任务校验失败：{exc}") from exc
