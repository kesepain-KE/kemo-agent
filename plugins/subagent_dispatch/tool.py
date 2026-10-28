from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any, cast

from agents._runtime.user_packages import create_user_agent_package
from run.agents import get_agent_scheduler
from run.agents import (
    AgentError,
    AgentCancelledError,
    AgentQueueError,
    AgentTaskWaitTimeout,
    AgentRunResult,
    ExternalAgentError,
    call_external_agent,
    discover_agents,
    discover_external_agents,
    resolve_external_agent,
)
from run.config import load_config
from run.agents import conclusion_data, delivery, describe_agent, guide_task, split_task, numeric_usage
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
            if status in {"failed", "interrupted"}:
                return True, delivery(snapshot)
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
            if snapshot.get("status") in {"failed", "interrupted"}:
                return True, delivery(snapshot)
            raise


def _running_task_response(
    snapshot: dict[str, Any],
    *,
    agent: str,
    timeout: float,
    managed: bool = False,
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
        "budget": snapshot.get("budget", {}),
        "progress": snapshot.get("progress", {}),
        "usage": snapshot.get("usage", {}),
        "message": (
            "子代理仍在运行；框架会自动续接结果，主任务等待必要依赖后才会完成，"
            "无需反复查询状态。"
            if managed else
            "子代理仍在运行；可用 action=status 追踪状态，"
            "或用 action=cancel 停止任务"
        ),
    }


def _public(root: Path, user: str):
    return discover_agents(root, user).public_agents("main_agent")


def _effective_wait(value: bool | None, definition: Any, *, synchronous_only: bool = False) -> bool:
    """Only ordinary local LLM business agents default to background submission."""
    if value is not None:
        return value
    return (
        synchronous_only
        or getattr(definition, "execution", "") == "background_serial"
        or getattr(definition, "executor", "") != "builtin:llm"
    )


def run(
    action: str,
    agent: str = "",
    input: dict[str, Any] | None = None,
    definition: dict[str, Any] | None = None,
    wait: bool | None = None,
    task_id: str = "",
    timeout: float | None = None,
    guidance: str = "",
    max_tool_calls: int | None = None,
    tasks: list[dict[str, Any]] | None = None,
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    if context.get("caller") == "subagent":
        raise AgentError("子智能体不能创建、调度或管理其他子智能体")
    root = Path(context["root"]).resolve()
    user = str(context["user"])
    scope_source, scope_session = _scope(context)
    event_options = (
        {"event_callback": context["event_callback"]}
        if callable(context.get("event_callback")) else {}
    )
    # Ownership is injected by the main runtime, never accepted as model args.
    if isinstance(context.get("task_ownership"), dict):
        event_options["ownership"] = context["task_ownership"]
    public = {definition.name: definition for definition in _public(root, user)}
    config = load_config(user, root)
    if action == "list":
        agents = [
            {
                "name": definition.name,
                "description": definition.description,
                "version": definition.version,
                "source": definition.source,
                "execution": definition.execution,
                "input_schema": definition.input_schema,
                "default_wait": _effective_wait(None, definition),
                **describe_agent(root, user, definition, config),
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
                    "default_wait": True,
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
    if action == "guide":
        source, session_id = _require_control_scope(context)
        return guide_task(root, user, task_id, source, session_id, context.get("task_ownership"), guidance)
    if action == "split":
        source, session_id = _require_control_scope(context)
        return split_task(get_agent_scheduler(root, user, config=config), task_id,
            source=source, session_id=session_id, tasks=tasks, timeout=timeout,
            max_tool_calls=max_tool_calls, config=config, **event_options)
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
            if max_tool_calls is not None:
                raise AgentError("外部代理不支持覆盖本地工具预算")
            if wait is False:
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
                    **event_options,
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
                if completed and isinstance(value, dict) and value.get("ok") is False:
                    return value
                if not completed:
                    return _running_task_response(
                        value,
                        agent=agent,
                        timeout=requested_timeout,
                        managed=bool(context.get("task_ownership")),
                    )
                return conclusion_data({**value, "task_id": task_id}) if isinstance(value, dict) else conclusion_data(value)
            except AgentQueueError as exc:
                raise AgentError(str(exc)) from exc
            except ExternalAgentError as exc:
                raise AgentError(str(exc)) from exc
        agent_definition = public.get(agent)
        if agent_definition is None:
            raise AgentError(f"子代理未公开或不存在：{agent}")
        invocation = prepare_main_agent_invocation(
            root=root,
            user=user,
            agent=agent,
            input_data=input,
            config=config,
        )
        wait = _effective_wait(wait, agent_definition, synchronous_only=invocation.synchronous_only)
        if invocation.synchronous_only and not wait:
            raise ValueError(f"{agent} 必须同步调用，以便完成校验和持久化")
        payload = invocation.payload
        if max_tool_calls is not None:
            event_options["max_tool_calls"] = max_tool_calls
        if wait:
            requested_timeout = _positive_timeout(
                timeout if timeout is not None else agent_definition.timeout,
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
                **event_options,
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
            if completed and isinstance(value, dict) and value.get("ok") is False:
                return value
            if not completed:
                return _running_task_response(
                    value,
                    agent=agent,
                    timeout=requested_timeout,
                    managed=bool(context.get("task_ownership")),
                )
            if not isinstance(value, AgentRunResult):
                raise AgentError("本地子代理返回了无效的执行结果类型")
            result = cast(Any, value)  # Public facade is lazy; runtime type was checked above.
            persisted = persisted_holder.get("plan")
            reason = str(result.metadata.get("stop_reason") or "") if result.metadata.get("partial") else ""
            response = {
                "task_id": task_id, "agent": result.agent,
                "status": {"timeout": "timed_out", "tool_budget_exhausted": "tool_limit"}.get(reason, "completed"),
                "data": conclusion_data(result.data), "model": result.model, "usage": numeric_usage(result.usage),
                "resumable": bool(result.metadata.get("resumable")),
                "run_count": result.metadata.get("run_count", 1),
                "budget": result.metadata.get("budget", {}), "stop_reason": reason or None,
            }
            if result.metadata.get("background_jobs") is not None:
                response["background_jobs"] = result.metadata["background_jobs"]
            if agent == "task_plan":
                response["plan"] = persisted
            return response
        scheduler = get_agent_scheduler(root, user, config=config)
        submit_kwargs = {"timeout": timeout, "config": config} if timeout is not None else {"config": config}
        submitted = scheduler.submit(
            agent,
            payload,
            **submit_kwargs,
            **event_options,
            allow_sync=True,
            source=scope_source,
            session_id=scope_session,
        )
        budget = {}
        if hasattr(agent_definition, "capabilities"):
            from run.agents import SegmentBudget
            budget = SegmentBudget(agent_definition, config, max_tool_calls,
                                   timeout if timeout is not None else agent_definition.timeout).snapshot()
            budget["elapsed_seconds"] = 0
        return {"status": "queued", "agent": agent, "task_id": submitted,
                "budget": budget}
    scheduler = get_agent_scheduler(root, user, config=config)
    if action == "resume":
        if not task_id:
            raise AgentError("resume 需要 task_id")
        source, session_id = _require_control_scope(context)
        snapshot = _scheduler_get(scheduler, task_id, source=source, session_id=session_id)
        if snapshot.get("agent") not in public:
            raise AgentError("子代理未公开或不存在")
        wait = _effective_wait(wait, public[snapshot["agent"]])
        scheduler.resume(
            task_id, source=source, session_id=session_id, guidance=guidance, timeout=timeout,
            config=config, **event_options,
            **({"max_tool_calls": max_tool_calls} if max_tool_calls is not None else {}),
        )
        if wait:
            completed, value = _wait_for_task(
                scheduler, task_id,
                timeout=_positive_timeout(timeout if timeout is not None else public[snapshot["agent"]].timeout,
                                          field="续跑 timeout"),
                cancel_event=context.get("cancel_event"), source=source, session_id=session_id,
            )
            if completed:
                return delivery(_scheduler_get(scheduler, task_id, source=source, session_id=session_id))
        return delivery(_scheduler_get(scheduler, task_id, source=source, session_id=session_id))
    if action == "status":
        if not task_id:
            raise ValueError("status 需要 task_id")
        source, session_id = _require_control_scope(context)
        return delivery(_scheduler_get(scheduler, task_id, source=source, session_id=session_id))
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
