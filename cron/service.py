"""Cron 任务生成服务：通过 time_plan 子代理生成扁平任务草案。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from cron.schedule import compute_next_run
from provider.factory import create_provider
from run.agent_runner import AgentRunError, AgentRunner
from run.config import load_config
from run.cron_store import CronValidationError, normalize_task
from run.tools import ToolRegistry


BEIJING = ZoneInfo("Asia/Shanghai")


class CronGenerationError(RuntimeError):
    pass


class CronSkipped(CronGenerationError):
    pass


def _current_beijing() -> str:
    return datetime.now(BEIJING).isoformat()


def _draft_from_agent(data: dict[str, Any]) -> dict[str, Any]:
    task_type = data.get("type")
    if task_type not in {"recurring", "daily", "once"}:
        raise CronGenerationError(f"time_plan 子代理未返回有效 type：{task_type!r}")
    draft: dict[str, Any] = {"type": task_type}
    if task_type == "recurring":
        interval = data.get("interval_seconds")
        if isinstance(interval, bool) or not isinstance(interval, int) or interval < 60:
            raise CronGenerationError("用户 recurring 任务需要 interval_seconds >= 60")
        draft["interval_seconds"] = interval
    elif task_type == "daily":
        draft["time"] = data.get("time")
    else:
        next_run_at = data.get("next_run_at")
        if not isinstance(next_run_at, str) or not next_run_at.strip():
            raise CronGenerationError("once 任务需要 next_run_at")
        draft["next_run_at"] = next_run_at
    try:
        draft["next_run_at"] = compute_next_run(draft)
    except CronValidationError as exc:
        raise CronGenerationError(f"调度时间计算失败：{exc}") from exc
    return draft


def _agent_result(
    *,
    runner: AgentRunner,
    input_data: dict[str, Any],
) -> dict[str, Any]:
    try:
        result = runner.run("time_plan", input_data)
    except AgentRunError as exc:
        raise CronGenerationError(f"time_plan 子代理执行失败：{exc}") from exc
    data = result.data
    action = data.get("action")
    if action == "skip":
        raise CronSkipped(str(data.get("message") or "无法解析为定时任务"))
    if action not in {"create", "edit", "delete"}:
        raise CronGenerationError(f"time_plan 子代理返回未知 action：{action!r}")
    return data


def generate_cron_task(
    *,
    root: Path,
    user: str,
    user_request: str,
    source: str = "cli",
    session_id: str = "cron",
    config: dict[str, Any] | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
    tool_registry: ToolRegistry | None = None,
    existing_task: dict[str, Any] | None = None,
    edit_request: str | None = None,
) -> dict[str, Any]:
    """生成未持久化的精简 cron 任务。"""
    del source, session_id, tool_registry
    cfg = config if config is not None else load_config(user, root)
    runner = AgentRunner(root, user, config=cfg, provider_factory=provider_factory)
    input_data: dict[str, Any] = {
        "action": "edit" if existing_task is not None else "create",
        "user_request": user_request,
        "current_time_beijing": _current_beijing(),
    }
    if existing_task is not None:
        input_data["existing_task"] = existing_task
    if edit_request is not None:
        input_data["edit_request"] = edit_request

    data = _agent_result(runner=runner, input_data=input_data)
    if data.get("action") == "delete":
        return {"action": "delete"}
    title = data.get("title")
    prompt = data.get("prompt")
    if not isinstance(title, str) or not title.strip():
        raise CronGenerationError("time_plan 子代理未返回有效 title")
    if not isinstance(prompt, str) or not prompt.strip():
        raise CronGenerationError("time_plan 子代理未返回有效 prompt")
    draft = _draft_from_agent(data)
    try:
        return normalize_task(
            title=title,
            prompt=prompt,
            user=user,
            type=draft["type"],
            interval_seconds=draft.get("interval_seconds"),
            time=draft.get("time"),
            next_run_at=draft["next_run_at"],
        )
    except CronValidationError as exc:
        raise CronGenerationError(f"任务校验失败：{exc}") from exc


def edit_cron_task(
    *,
    root: Path,
    user: str,
    task: dict[str, Any],
    edit_request: str,
    config: dict[str, Any] | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
) -> dict[str, Any]:
    """生成保留任务身份与运行时间的编辑结果。"""
    cfg = config if config is not None else load_config(user, root)
    runner = AgentRunner(root, user, config=cfg, provider_factory=provider_factory)
    data = _agent_result(
        runner=runner,
        input_data={
            "action": "edit",
            "user_request": edit_request,
            "existing_task": task,
            "current_time_beijing": _current_beijing(),
        },
    )
    if data.get("action") == "delete":
        return {"action": "delete"}
    title = data.get("title") or task.get("title", "")
    prompt = data.get("prompt") or task.get("prompt", "")
    draft = _draft_from_agent(data)
    try:
        return normalize_task(
            task_id=task.get("task_id"),
            title=str(title),
            prompt=str(prompt),
            user=user,
            type=draft["type"],
            interval_seconds=draft.get("interval_seconds"),
            time=draft.get("time"),
            next_run_at=draft["next_run_at"],
            latest_run_at=str(task.get("latest_run_at") or ""),
            status=str(task.get("status") or "enabled"),
            created_at=str(task.get("created_at") or ""),
            exec_mode=str(task.get("exec_mode") or "agent"),
        )
    except CronValidationError as exc:
        raise CronGenerationError(f"任务校验失败：{exc}") from exc
