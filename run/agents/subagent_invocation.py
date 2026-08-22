"""Authoritative main-agent invocation rules for built-in subagents.

Generic user subagents keep their package-defined input contract.  Built-in
agents whose input contains runtime authority (time, permissions, config, or
private trigger modes) are adapted here before AgentRunner sees the payload.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from cron.schedule import BEIJING
from run.config import load_config
from run.tasks import persist_agent_result, prepare_task_plan_input


class SubagentInvocationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PreparedSubagentInvocation:
    payload: dict[str, Any]
    synchronous_only: bool = False


def _copy_input(input_data: dict[str, Any] | None) -> dict[str, Any]:
    if input_data is None:
        return {}
    if not isinstance(input_data, dict):
        raise SubagentInvocationError("子代理 input 必须是对象")
    return dict(input_data)


def _prepare_task_plan(
    root: Path,
    user: str,
    payload: dict[str, Any],
    config: dict[str, Any],
) -> PreparedSubagentInvocation:
    return PreparedSubagentInvocation(
        prepare_task_plan_input(
            root=root,
            user=user,
            input_data=payload,
            config=config,
        ),
        synchronous_only=True,
    )


def _prepare_time_plan(
    root: Path,
    user: str,
    payload: dict[str, Any],
    config: dict[str, Any],
) -> PreparedSubagentInvocation:
    del root, user, config
    action = payload.get("action")
    if action not in {"create", "edit", "delete"}:
        raise SubagentInvocationError("time_plan action 必须是 create、edit 或 delete")
    # Current time is runtime authority.  Never trust or require a model caller
    # to copy it from another tool invocation.
    if action in {"create", "edit"}:
        payload["current_time_beijing"] = datetime.now(BEIJING).isoformat()
    else:
        payload.pop("current_time_beijing", None)
    return PreparedSubagentInvocation(payload)


def _prepare_self_improve(
    root: Path,
    user: str,
    payload: dict[str, Any],
    config: dict[str, Any],
) -> PreparedSubagentInvocation:
    del root, user, config
    if payload.get("trigger") != "manual_review":
        raise SubagentInvocationError(
            "主智能体只能以 manual_review 模式调用 self_improve；"
            "context_compression 和 memory_promotion 仅供引擎或调度器使用"
        )
    if not str(payload.get("request") or "").strip():
        raise SubagentInvocationError("self_improve manual_review 需要非空 request")
    return PreparedSubagentInvocation(payload)


def _prepare_important_memory(
    root: Path,
    user: str,
    payload: dict[str, Any],
    config: dict[str, Any],
) -> PreparedSubagentInvocation:
    del root, user, config
    if payload.get("trigger") not in {"periodic_scan", "daily_consolidate"}:
        raise SubagentInvocationError(
            "memory_temporary_important trigger 必须是 periodic_scan 或 daily_consolidate"
        )
    return PreparedSubagentInvocation(payload)


_PREPARERS: dict[
    str,
    Callable[
        [Path, str, dict[str, Any], dict[str, Any]],
        PreparedSubagentInvocation,
    ],
] = {
    "task_plan": _prepare_task_plan,
    "time_plan": _prepare_time_plan,
    "self_improve": _prepare_self_improve,
    "memory_temporary_important": _prepare_important_memory,
}


def prepare_main_agent_invocation(
    *,
    root: Path,
    user: str,
    agent: str,
    input_data: dict[str, Any] | None,
    config: dict[str, Any] | None = None,
) -> PreparedSubagentInvocation:
    """Prepare one public subagent call without widening its capabilities."""

    payload = _copy_input(input_data)
    preparer = _PREPARERS.get(agent)
    if preparer is None:
        return PreparedSubagentInvocation(payload)
    cfg = config if config is not None else load_config(user, root)
    return preparer(root, user, payload, cfg)


def persist_main_agent_result(
    *,
    root: Path,
    user: str,
    agent: str,
    payload: dict[str, Any],
    result_data: dict[str, Any],
    source: str,
    session_id: str,
    config: dict[str, Any],
) -> dict[str, Any] | None:
    """Run built-in post-processing required by a synchronous tool call."""

    if agent != "task_plan":
        return None
    return persist_agent_result(
        root=root,
        user=user,
        input_data=payload,
        result_data=result_data,
        source=source,
        session_id=session_id,
        config=config,
    )
