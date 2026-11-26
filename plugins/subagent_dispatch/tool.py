from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from agents._runtime.user_packages import create_user_agent_package
from run.agents import (
    FINAL_TASK_STATUSES, SCHEDULER_TERMINAL_STATUSES,
    AgentCancelledError, AgentError, AgentInputError, AgentQueueError, AgentRunError,
    AgentTaskNotFoundError, AgentTaskWaitTimeout, ExternalAgentError,
    agent_input_hint, bounded_schema_hint, call_external_agent, cancellation_state,
    delivery, describe_agent, discover_agents, discover_external_agents,
    get_agent_scheduler, guide_task, main_agent_result_handler, prepare_main_agent_invocation,
    requested_timeout, resolve_external_agent, split_task, validate_json_schema,
)
from run.config import load_config
from run.retry import HOST_CONTROL_EXCEPTIONS
from run.tools import DEFAULT_QUERY_WAIT_SECONDS, query_wait_seconds


_MAX_EXTERNAL_REQUEST_TIMEOUT_SECONDS = 3600.0
_STATUS_TERMINAL = SCHEDULER_TERMINAL_STATUSES | FINAL_TASK_STATUSES


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


def _submission(scheduler, task_id, source, session_id, *, wait):
    snapshot = _scheduler_get(scheduler, task_id, source=source, session_id=session_id)
    # Even if execution has already finished, this receipt describes accepted
    # submission, not a second delivery of the terminal result.
    status = "running" if snapshot["status"] == "running" else "queued"
    return {
        "ok": True, "status": status, "agent": snapshot["agent"], "task_id": task_id,
        "detached": True, "run_count": snapshot.get("run_count", 1),
        "status_at": "submission",
        "budget": snapshot.get("budget") or {},
        "message": ("wait=true 仅作兼容，不再同步等待。 " if wait is True else "")
                   + "已提交；主对话仍等待必要依赖，由框架交付实际结果。",
    }


def _timeout(value, *, field="timeout"):
    try:
        return requested_timeout(value, field=field)
    except AgentRunError as exc:
        raise AgentError(str(exc)) from exc


def _public(root: Path, user: str):
    return discover_agents(root, user).public_agents("main_agent")


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
                "default_wait": False,
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
                    "default_wait": False,
                    "default_timeout": binding.timeout,
                    "input_schema": binding.input_schema,
                    "output_schema": binding.output_schema,
                }
            )
        return {"ok": True, "agents": agents}
    if action == "create":
        return {
            "ok": True,
            "status": "created",
            "agent": create_user_agent_package(root, user, definition or {}),
        }
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
        timeout = _timeout(timeout)
        if str(agent or "").startswith("external:"):
            if max_tool_calls is not None:
                raise AgentError("外部代理不支持覆盖本地工具预算")
            try:
                binding = resolve_external_agent(root, user, agent)
            except ExternalAgentError as exc:
                raise AgentError(str(exc)) from exc
            transport_timeout = binding.timeout if timeout is None or timeout == 0 else timeout
            if not 0 < transport_timeout <= _MAX_EXTERNAL_REQUEST_TIMEOUT_SECONDS:
                raise AgentError("外部子代理 timeout 必须大于 0 且不能超过 3600 秒")
            external_input = {} if input is None else input
            if not isinstance(external_input, dict):
                raise AgentError("外部子代理 input 必须是对象")
            if callable(context.get("task_ownership_factory")):
                event_options["ownership"] = context["task_ownership_factory"]()
            scheduler = get_agent_scheduler(root, user, config=config)
            task_id = scheduler.submit_callable(
                agent, external_input,
                lambda task_cancel: call_external_agent(
                    root, user, agent, external_input, timeout=transport_timeout,
                    cancel_event=task_cancel,
                ),
                timeout=transport_timeout, timeout_survival_seconds=0, **event_options,
                source=scope_source, session_id=scope_session,
            )
            return _submission(scheduler, task_id, scope_source, scope_session, wait=wait)
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
        payload = invocation.payload
        try:
            input_schema = agent_definition.input_schema
        except HOST_CONTROL_EXCEPTIONS:
            raise
        except Exception:
            input_schema = None  # Plugin/external schemas may be unavailable.
        if isinstance(input_schema, dict):
            try:
                validate_json_schema(payload, input_schema)
            except AgentInputError as exc:
                response = {
                    "ok": False, "status": "invalid_input", "agent": agent,
                    "error": {"code": "invalid_agent_input", "message": "input 不符合契约"},
                }
                available = 401 - len(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
                response["error"].update(
                    bounded_schema_hint(
                        agent_input_hint(
                            input_schema,
                            payload,
                            field_path=exc.field_path,
                            expected_type=exc.expected_type,
                            reason=exc.reason,
                        ),
                        limit=available,
                    )
                )
                return response
        if callable(context.get("task_ownership_factory")):
            event_options["ownership"] = context["task_ownership_factory"]()
        if max_tool_calls is not None:
            event_options["max_tool_calls"] = max_tool_calls
        scheduler = get_agent_scheduler(root, user, config=config)
        task_id = scheduler.submit(
            agent, payload, timeout=timeout, config=config, **event_options, allow_sync=True,
            result_handler=main_agent_result_handler(
                root=root, user=user, agent=agent, payload=payload, source=scope_source,
                session_id=scope_session, config=config,
            ),
            source=scope_source, session_id=scope_session,
        )
        return _submission(scheduler, task_id, scope_source, scope_session, wait=wait)
    scheduler = get_agent_scheduler(root, user, config=config)
    if action == "resume":
        if not task_id:
            raise AgentError("resume 需要 task_id")
        source, session_id = _require_control_scope(context)
        snapshot = _scheduler_get(scheduler, task_id, source=source, session_id=session_id)
        if snapshot.get("agent") not in public:
            raise AgentError("子代理未公开或不存在")
        scheduler.resume(
            task_id, source=source, session_id=session_id, guidance=guidance,
            timeout=_timeout(timeout, field="续跑 timeout"), config=config,
            **event_options, max_tool_calls=max_tool_calls,
        )
        return _submission(scheduler, task_id, source, session_id, wait=wait)
    if action == "status":
        if not task_id:
            raise ValueError("status 需要 task_id")
        source, session_id = _require_control_scope(context)
        snapshot = _scheduler_get(scheduler, task_id, source=source, session_id=session_id)
        if wait is not True:
            return delivery(snapshot)
        seconds = query_wait_seconds(DEFAULT_QUERY_WAIT_SECONDS if timeout is None else timeout)
        deadline = time.monotonic() + seconds
        wait_outcome = "finished"
        if snapshot["status"] not in _STATUS_TERMINAL:
            try:
                scheduler.wait(task_id, max(0.0, deadline - time.monotonic()),
                               source=source, session_id=session_id)
            except AgentTaskWaitTimeout:
                wait_outcome = "timeout"
            except AgentTaskNotFoundError:
                # wait 只认本进程；每次仍按现有作用域查，真正缺失时让 get 抛错。
                while True:
                    snapshot = _scheduler_get(scheduler, task_id, source=source, session_id=session_id)
                    if snapshot["status"] in _STATUS_TERMINAL:
                        return {**delivery(snapshot), "wait_outcome": "finished"}
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return {**delivery(snapshot), "wait_outcome": "timeout"}
                    time.sleep(min(2.0, remaining))
            except (AgentCancelledError, AgentQueueError):
                # 等待已结束；成功与否仍由刷新后的任务状态和错误信息表达。
                wait_outcome = "finished"
            snapshot = _scheduler_get(scheduler, task_id, source=source, session_id=session_id)
        return {**delivery(snapshot), "wait_outcome": wait_outcome}
    if action == "cancel":
        if not task_id:
            raise ValueError("cancel 需要 task_id")
        source, session_id = _require_control_scope(context)
        cancelled = _scheduler_cancel(
            scheduler, task_id, source=source, session_id=session_id,
        )
        snapshot = _scheduler_get(scheduler, task_id, source=source, session_id=session_id)
        state = cancellation_state(snapshot)
        return {
            "task_id": task_id, "status": snapshot["status"], **state,
            "ok": bool(cancelled), "cancel_requested": bool(cancelled),
            "cancelled": state.get("cancel_state") == "exited",
        }
    raise ValueError(f"未知 action：{action}")
