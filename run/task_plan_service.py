"""任务计划生成服务：通过AgentRunner调用task_plan子代理。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from provider.factory import create_provider
from run.agent_runner import AgentRunError, AgentRunner
from run.config import load_config
from run.memory import MemoryStore
from run.task_plan_store import (
    PlanConflictError,
    PlanValidationError,
    normalize_plan,
)
from run.tools import ToolRegistry, apply_runtime_tool_policy, discover_tools


class PlanGenerationError(RuntimeError):
    pass


class PlanSkipped(PlanGenerationError):
    """The sub-agent decided a plan is unnecessary."""
    pass


def _tool_summary(registry: ToolRegistry, max_tools: int = 50) -> list[dict[str, str]]:
    tools: list[dict[str, str]] = []
    for tool in registry.enabled_tools()[:max_tools]:
        tools.append({"name": tool.name, "description": tool.description})
    return tools


def _relevant_memory(root: Path, user: str, config: dict[str, Any], goal: str) -> str:
    store = MemoryStore(root, user, config)
    lines: list[str] = []
    used = 0
    for item in store.search(goal, limit=5):
        line = (
            f"- [{item['filename']}] ({item['tier']}, weight={item['weight']}) "
            f"{item['content']}"
        )
        extra = len(line) + (1 if lines else 0)
        if used + extra > 1000:
            continue
        lines.append(line)
        used += extra
    if not lines:
        return ""
    return "以下是按文件名匹配的相关用户记忆，当前计划目标优先：\n" + "\n".join(lines)


def generate_plan(
    *,
    root: Path,
    user: str,
    goal: str,
    source: str = "cli",
    session_id: str = "default",
    config: dict[str, Any] | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
    tool_registry: ToolRegistry | None = None,
    existing_plan: dict[str, Any] | None = None,
    edit_request: str | None = None,
) -> dict[str, Any]:
    """Call the task_plan sub-agent to produce a structured plan draft, then
    normalize and persist it.  Returns the stored plan dict.

    Raises ``PlanSkipped`` when the sub-agent decides a plan is unnecessary.
    Raises ``PlanGenerationError`` on sub-agent timeout, non-JSON output,
    unknown tools, or invalid dependencies.
    """
    cfg = config or load_config(user, root)
    if tool_registry is None:
        tool_config = cfg.get("tools") or {}
        tool_registry = (
            apply_runtime_tool_policy(discover_tools(root, user), cfg)
            if bool(tool_config.get("enabled", True))
            else ToolRegistry({})
        )
    tool_names = set(tool_registry.tools.keys())
    task_plan_config = cfg.get("task_plan") or {}
    max_steps = int(task_plan_config.get("max_steps", 10))
    auto_accept = bool(task_plan_config.get("auto_accept", False))

    runner = AgentRunner(root, user, config=cfg, provider_factory=provider_factory)
    memory_text = _relevant_memory(root, user, cfg, goal)

    input_data: dict[str, Any] = {
        "action": "edit" if existing_plan is not None else "create",
        "goal": goal,
        "available_tools": _tool_summary(tool_registry),
        "max_steps": max_steps,
        "relevant_memory": memory_text,
    }
    if existing_plan is not None:
        input_data["existing_plan"] = existing_plan
    if edit_request is not None:
        input_data["edit_request"] = edit_request

    try:
        result = runner.run("task_plan", input_data)
    except AgentRunError as exc:
        raise PlanGenerationError(f"task_plan 子代理执行失败：{exc}") from exc

    data = result.data
    action = data.get("action")

    if action == "skip":
        raise PlanSkipped(str(data.get("message") or "问题过于简单，可以直接执行"))

    if action not in ("create", "edit"):
        raise PlanGenerationError(f"task_plan 子代理返回未知 action：{action!r}")

    steps = data.get("steps")
    if not isinstance(steps, list) or len(steps) == 0:
        raise PlanGenerationError("task_plan 子代理未返回步骤列表")

        # 验证最大步数
    if len(steps) > max_steps:
        raise PlanGenerationError(
            f"步骤数量 {len(steps)} 超过最大限制 {max_steps}"
        )

    try:
        plan = normalize_plan(
            title=data.get("title") or goal[:100],
            description=data.get("description") or goal,
            user=user,
            source=source,
            session_id=session_id,
            steps=steps,
            auto_accept=auto_accept,
            tool_names=tool_names,
        )
    except PlanValidationError as exc:
        raise PlanGenerationError(f"计划校验失败：{exc}") from exc

    return plan


def edit_plan(
    *,
    root: Path,
    user: str,
    plan: dict[str, Any],
    edit_request: str,
    config: dict[str, Any] | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
    tool_registry: ToolRegistry | None = None,
) -> dict[str, Any]:
    """Call the task_plan sub-agent to edit an existing plan."""
    cfg = config or load_config(user, root)
    if tool_registry is None:
        tool_config = cfg.get("tools") or {}
        tool_registry = (
            apply_runtime_tool_policy(discover_tools(root, user), cfg)
            if bool(tool_config.get("enabled", True))
            else ToolRegistry({})
        )
    tool_names = set(tool_registry.tools.keys())
    task_plan_config = cfg.get("task_plan") or {}
    max_steps = int(task_plan_config.get("max_steps", 10))

    runner = AgentRunner(root, user, config=cfg, provider_factory=provider_factory)
    memory_text = _relevant_memory(root, user, cfg, edit_request)

    input_data = {
        "action": "edit",
        "goal": plan.get("description") or plan.get("title", ""),
        "available_tools": _tool_summary(tool_registry),
        "max_steps": max_steps,
        "existing_plan": plan,
        "edit_request": edit_request,
        "relevant_memory": memory_text,
    }

    try:
        result = runner.run("task_plan", input_data)
    except AgentRunError as exc:
        raise PlanGenerationError(f"task_plan 子代理执行失败：{exc}") from exc

    data = result.data
    action = data.get("action")
    if action not in ("create", "edit"):
        raise PlanGenerationError(f"task_plan 子代理返回未知 action：{action!r}")

    steps = data.get("steps")
    if not isinstance(steps, list) or len(steps) == 0:
        raise PlanGenerationError("task_plan 子代理未返回步骤列表")

    if len(steps) > max_steps:
        raise PlanGenerationError(
            f"步骤数量 {len(steps)} 超过最大限制 {max_steps}"
        )

    try:
        return normalize_plan(
            plan_id=plan.get("plan_id"),
            title=data.get("title") or plan.get("title", ""),
            description=data.get("description") or plan.get("description", ""),
            user=user,
            source=plan.get("source", "cli"),
            session_id=plan.get("session_id", "default"),
            steps=steps,
            auto_accept=plan.get("auto_accept", False),
            tool_names=tool_names,
        )
    except PlanValidationError as exc:
        raise PlanGenerationError(f"计划校验失败：{exc}") from exc
