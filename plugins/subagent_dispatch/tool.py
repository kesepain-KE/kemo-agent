from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

from agents._runtime.user_packages import create_user_agent_package
from run.agents import get_agent_scheduler
from run.agents import (
    AgentError,
    AgentCancelledError,
    AgentQueueError,
    AgentTaskWaitTimeout,
    ExternalAgentError,
    call_external_agent,
    discover_agents,
    discover_external_agents,
    resolve_external_agent,
)
from run.config import load_config
from run.agents import (
    persist_main_agent_result,
    prepare_main_agent_invocation,
)


_DEFAULT_DETACHED_SURVIVAL_SECONDS = 120.0
_MAX_DETACHED_SURVIVAL_SECONDS = 3_600.0
_MAX_EXTERNAL_REQUEST_TIMEOUT_SECONDS = 3_600.0


def _scope(context: dict[str, Any]) -> tuple[str, str]:
    return (
        str(context.get("source") or "").strip(),
        str(context.get("session_id") or "").strip(),
    )


def _require_control_scope(context: dict[str, Any]) -> tuple[str, str]:
    source, session_id = _scope(context)
    if not source or not session_id:
        raise AgentError("子代理控制操作缺少 source 或 session_id 对话身份")
    return source, session_id


def _scheduler_get(
    scheduler: Any,
    task_id: str,
    *,
    source: str = "",
    session_id: str = "",
) -> Any:
    if source and session_id:
        try:
            return scheduler.get(task_id, source=source, session_id=session_id)
        except TypeError as exc:
            if "unexpected keyword argument" in str(exc):
                raise AgentError(
                    "子代理调度器不支持对话空间作用域，已拒绝无作用域读取"
                ) from exc
            raise
    return scheduler.get(task_id)


def _scheduler_wait(
    scheduler: Any,
    task_id: str,
    *,
    timeout: float | None = None,
    source: str = "",
    session_id: str = "",
) -> Any:
    if source and session_id:
        try:
            return scheduler.wait(
                task_id,
                timeout=timeout,
                source=source,
                session_id=session_id,
            )
        except TypeError as exc:
            if "unexpected keyword argument" in str(exc):
                raise AgentError(
                    "子代理调度器不支持对话空间作用域，已拒绝无作用域等待"
                ) from exc
            raise
    return scheduler.wait(task_id, timeout=timeout)


def _scheduler_cancel(
    scheduler: Any,
    task_id: str,
    *,
    source: str = "",
    session_id: str = "",
) -> Any:
    if source and session_id:
        try:
            return scheduler.cancel(task_id, source=source, session_id=session_id)
        except TypeError as exc:
            if "unexpected keyword argument" in str(exc):
                raise AgentError(
                    "子代理调度器不支持对话空间作用域，已拒绝无作用域取消"
                ) from exc
            raise
    return scheduler.cancel(task_id)


def _detached_survival_seconds(config: dict[str, Any]) -> float:
    runtime = config.get("agent_runtime") or {}
    if not isinstance(runtime, dict):
        runtime = {}
    raw = runtime.get("timeout_survival_seconds", _DEFAULT_DETACHED_SURVIVAL_SECONDS)
    if isinstance(raw, bool):
        return _DEFAULT_DETACHED_SURVIVAL_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_DETACHED_SURVIVAL_SECONDS
    if not math.isfinite(value) or value <= 0:
        return _DEFAULT_DETACHED_SURVIVAL_SECONDS
    return min(_MAX_DETACHED_SURVIVAL_SECONDS, max(120.0, value))


def _positive_timeout(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise AgentError(f"{field} 必须是正数")
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise AgentError(f"{field} 必须是正数") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise AgentError(f"{field} 必须是正数")
    return timeout


def _wait_for_task(
    scheduler: Any,
    task_id: str,
    *,
    timeout: float,
    cancel_event: Any = None,
    source: str = "",
    session_id: str = "",
) -> tuple[bool, Any]:
    """Wait up to the caller deadline without cancelling a live task."""

    deadline = time.monotonic() + timeout
    while True:
        if cancel_event is not None and cancel_event.is_set():
            _scheduler_cancel(scheduler, task_id, source=source, session_id=session_id)
            raise AgentCancelledError("子代理调用已取消")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            snapshot = _scheduler_get(scheduler, task_id, source=source, session_id=session_id)
            status = snapshot.get("status")
            if status in {"queued", "running", "timed_out_running"}:
                return False, snapshot
            try:
                return True, _scheduler_wait(
                    scheduler,
                    task_id,
                    timeout=0,
                    source=source,
                    session_id=session_id,
                )
            except AgentTaskWaitTimeout:
                continue
        try:
            return True, _scheduler_wait(
                scheduler,
                task_id,
                timeout=min(0.1, remaining),
                source=source,
                session_id=session_id,
            )
        except AgentTaskWaitTimeout:
            continue
        except AgentQueueError:
            snapshot = _scheduler_get(scheduler, task_id, source=source, session_id=session_id)
            if snapshot.get("status") in {"queued", "running", "timed_out_running"}:
                return False, snapshot
            raise


def _running_task_response(
    snapshot: dict[str, Any],
    *,
    agent: str,
    timeout: float,
) -> dict[str, Any]:
    status = str(snapshot.get("status") or "running")
    if status not in {"queued", "running", "timed_out_running"}:
        status = "running"
    return {
        "status": status,
        "agent": agent,
        "task_id": str(snapshot.get("id") or ""),
        "timeout": timeout,
        "detached": True,
        "message": (
            "子代理仍在运行；可用 action=status 追踪状态，"
            "或用 action=cancel 停止任务"
        ),
    }


def _public(root: Path, user: str):
    return discover_agents(root, user).public_agents("main_agent")


def run(
    action: str,
    agent: str = "",
    input: dict[str, Any] | None = None,
    definition: dict[str, Any] | None = None,
    wait: bool = True,
    task_id: str = "",
    timeout: float | None = None,
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    root = Path(context["root"]).resolve()
    user = str(context["user"])
    scope_source, scope_session = _scope(context)
    public = {definition.name: definition for definition in _public(root, user)}
    if action == "list":
        agents = [
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
        for binding in discover_external_agents(root, user):
            agents.append(
                {
                    "name": binding.handle,
                    "description": binding.description,
                    "version": "external",
                    "source": "external",
                    "execution": "sync",
                    "input_schema": binding.input_schema,
                    "output_schema": binding.output_schema,
                }
            )
        return {"agents": agents}
    if action == "create":
        return {
            "status": "created",
            "agent": create_user_agent_package(root, user, definition or {}),
        }
    config = load_config(user, root)
    if action == "call":
        # Every queued child agent must have a complete conversation scope so
        # status/wait/cancel cannot later fall back to a user-wide wildcard.
        _require_control_scope(context)
        if timeout is not None:
            if isinstance(timeout, bool):
                raise AgentError("timeout 必须是正数")
            try:
                timeout_value = float(timeout)
            except (TypeError, ValueError) as exc:
                raise AgentError("timeout 必须是正数") from exc
            if not math.isfinite(timeout_value) or timeout_value <= 0:
                raise AgentError("timeout 必须是正数")
            timeout = timeout_value
        if str(agent or "").startswith("external:"):
            if not wait:
                raise AgentError("外部子代理当前只支持同步调用；请使用 wait=true")
            try:
                binding = resolve_external_agent(root, user, agent)
                requested_timeout = _positive_timeout(
                    timeout if timeout is not None else binding.timeout,
                    field="外部子代理 timeout",
                )
                if requested_timeout > _MAX_EXTERNAL_REQUEST_TIMEOUT_SECONDS:
                    raise AgentError(
                        "外部子代理 timeout 不能超过 3600 秒"
                    )
                survival = _detached_survival_seconds(config)
                execution_timeout = requested_timeout + survival
                external_input = {} if input is None else input
                if not isinstance(external_input, dict):
                    raise AgentError("外部子代理 input 必须是对象")
                scheduler = get_agent_scheduler(root, user, config=config)
                task_id = scheduler.submit_callable(
                    agent,
                    external_input,
                    lambda task_cancel: call_external_agent(
                        root,
                        user,
                        agent,
                        external_input,
                        timeout=execution_timeout,
                        cancel_event=task_cancel,
                    ),
                    timeout=execution_timeout,
                    timeout_survival_seconds=survival,
                    source=scope_source,
                    session_id=scope_session,
                )
                completed, value = _wait_for_task(
                    scheduler,
                    task_id,
                    timeout=requested_timeout,
                    cancel_event=context.get("cancel_event"),
                    source=scope_source,
                    session_id=scope_session,
                )
                if not completed:
                    return _running_task_response(
                        value,
                        agent=agent,
                        timeout=requested_timeout,
                    )
                return value
            except AgentQueueError as exc:
                raise AgentError(str(exc)) from exc
            except ExternalAgentError as exc:
                raise AgentError(str(exc)) from exc
        definition = public.get(agent)
        if definition is None:
            raise AgentError(f"子代理未公开或不存在：{agent}")
        invocation = prepare_main_agent_invocation(
            root=root,
            user=user,
            agent=agent,
            input_data=input,
            config=config,
        )
        if invocation.synchronous_only and not wait:
            raise ValueError(f"{agent} 必须同步调用，以便完成校验和持久化")
        payload = invocation.payload
        if wait:
            requested_timeout = _positive_timeout(
                timeout if timeout is not None else definition.timeout,
                field="子代理 timeout",
            )
            persisted_holder: dict[str, Any] = {}

            def persist_result(result: Any) -> None:
                data = getattr(result, "data", None)
                if isinstance(data, dict):
                    persisted_holder["plan"] = persist_main_agent_result(
                        root=root,
                        user=user,
                        agent=agent,
                        payload=payload,
                        result_data=data,
                        source=str(context.get("source") or "web"),
                        session_id=str(context.get("session_id") or "web"),
                        config=config,
                    )

            scheduler = get_agent_scheduler(root, user, config=config)
            task_id = scheduler.submit(
                agent,
                payload,
                timeout=timeout,
                timeout_survival_seconds=_detached_survival_seconds(config),
                result_handler=persist_result if agent == "task_plan" else None,
                allow_sync=True,
                config=config,
                source=scope_source,
                session_id=scope_session,
            )
            completed, value = _wait_for_task(
                scheduler,
                task_id,
                timeout=requested_timeout,
                cancel_event=context.get("cancel_event"),
                source=scope_source,
                session_id=scope_session,
            )
            if not completed:
                return _running_task_response(
                    value,
                    agent=agent,
                    timeout=requested_timeout,
                )
            result = value
            persisted = persisted_holder.get("plan")
            response = {
                "status": "completed",
                "agent": result.agent,
                "data": result.data,
                "usage": result.usage,
                "model": result.model,
                "metadata": result.metadata,
            }
            if agent == "task_plan":
                response["plan"] = persisted
            return response
        scheduler = get_agent_scheduler(root, user, config=config)
        submit_kwargs = {"timeout": timeout, "config": config} if timeout is not None else {"config": config}
        submitted = scheduler.submit(
            agent,
            payload,
            **submit_kwargs,
            source=scope_source,
            session_id=scope_session,
        )
        return {"status": "queued", "agent": agent, "task_id": submitted}
    scheduler = get_agent_scheduler(root, user, config=config)
    if action == "status":
        if not task_id:
            raise ValueError("status 需要 task_id")
        source, session_id = _require_control_scope(context)
        return _scheduler_get(scheduler, task_id, source=source, session_id=session_id)
    if action == "cancel":
        if not task_id:
            raise ValueError("cancel 需要 task_id")
        source, session_id = _require_control_scope(context)
        return {
            "task_id": task_id,
            "cancelled": _scheduler_cancel(
                scheduler,
                task_id,
                source=source,
                session_id=session_id,
            ),
        }
    raise ValueError(f"未知 action：{action}")
