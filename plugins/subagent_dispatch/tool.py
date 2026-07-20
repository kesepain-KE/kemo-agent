from __future__ import annotations

from pathlib import Path
from typing import Any

from agents._runtime.user_packages import create_user_agent_package
from run.agent_runner import AgentRunner
from run.agent_service import get_agent_scheduler
from run.agents import AgentError, discover_agents
from run.config import load_config
from run.task_plan_service import persist_agent_result


def _public(root: Path, user: str):
    return discover_agents(root, user).public_agents("main_agent")


def run(
    action: str,
    agent: str = "",
    input: dict[str, Any] | None = None,
    definition: dict[str, Any] | None = None,
    wait: bool = True,
    task_id: str = "",
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    root = Path(context["root"]).resolve()
    user = str(context["user"])
    public = {definition.name: definition for definition in _public(root, user)}
    if action == "list":
        return {
            "agents": [
                {
                    "name": definition.name,
                    "description": definition.description,
                    "version": definition.version,
                    "source": definition.source,
                    "execution": definition.execution,
                    "input_schema": definition.input_schema,
                }
                for definition in public.values()
            ]
        }
    if action == "create":
        return {
            "status": "created",
            "agent": create_user_agent_package(root, user, definition or {}),
        }
    config = load_config(user, root)
    if action == "call":
        definition = public.get(agent)
        if definition is None:
            raise AgentError(f"子代理未公开或不存在：{agent}")
        payload = input or {}
        if wait:
            result = AgentRunner(root, user, config=config).run(agent, payload)
            plan = None
            if agent == "task_plan" and isinstance(result.data, dict):
                plan = persist_agent_result(
                    root=root,
                    user=user,
                    input_data=payload,
                    result_data=result.data,
                    source=str(context.get("source") or "web"),
                    session_id=str(context.get("session_id") or "web"),
                    config=config,
                )
            return {
                "status": "completed",
                "agent": result.agent,
                "data": result.data,
                "plan": plan,
                "usage": result.usage,
                "model": result.model,
            }
        scheduler = get_agent_scheduler(root, user, config=config)
        submitted = scheduler.submit(agent, payload)
        return {"status": "queued", "agent": agent, "task_id": submitted}
    scheduler = get_agent_scheduler(root, user, config=config)
    if action == "status":
        if not task_id:
            raise ValueError("status 需要 task_id")
        return scheduler.get(task_id)
    if action == "cancel":
        if not task_id:
            raise ValueError("cancel 需要 task_id")
        return {"task_id": task_id, "cancelled": scheduler.cancel(task_id)}
    raise ValueError(f"未知 action：{action}")
