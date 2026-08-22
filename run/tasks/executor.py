"""与条目无关的任务计划执行核心。

从磁盘读取最新的计划，根据选择下一个可运行的步骤
依赖项，通过现有的 ToolRunner 执行它，并持久化
状态以原子方式改变。  磁盘是唯一的事实来源——不
内存状态与之竞争。"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from events import RunEvent, error_event
from provider.factory import create_provider
from run.config import load_config
from run.tasks.store import (
    PlanError,
    PlanNotFoundError,
    PlanStore,
    PlanValidationError,
)
from run.tools import (
    ToolResultTooLargeError,
    ToolRegistry,
    apply_runtime_tool_policy,
    discover_tools,
    execute_tool,
)


class PlanExecutionError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_step(plan: dict[str, Any]) -> dict[str, Any] | None:
    """Select the next runnable step: pending with all deps completed."""
    steps_by_id = {s["step_id"]: s for s in plan["steps"]}
    for step in plan["steps"]:
        if step["status"] != "pending":
            continue
        deps = step.get("depends_on") or []
        if all(
            steps_by_id.get(dep, {}).get("status") == "completed"
            for dep in deps
        ):
            return step
    return None


def _failed_steps_needing_fix(plan: dict[str, Any]) -> list[str]:
    """Return every critical failed step that must be explicitly repaired."""

    return [
        str(step.get("step_id") or "")
        for step in (plan.get("steps") or [])
        if isinstance(step, dict)
        and str(step.get("step_id") or "")
        and step.get("status") == "failed"
        and bool(step.get("critical", True))
    ]


def _is_plan_active(status: str) -> bool:
    return status in ("approved", "running")


def execute_plan(
    *,
    root: Path,
    user: str,
    plan_id: str,
    config: dict[str, Any] | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
    tool_registry: ToolRegistry | None = None,
    cancel_event: threading.Event | None = None,
    event_callback: Callable[[RunEvent], None] | None = None,
    agent_event_source: Callable[[dict[str, Any]], Iterator[RunEvent]] | None = None,
) -> Iterator[RunEvent]:
    """Execute a plan step by step, yielding events.

    The plan must be in ``approved`` or ``running`` status.  Each step is
    persisted before and after execution.  Critical step failures pause the
    plan; non-critical failures are recorded and execution continues.

    On process restart, ``recover_interrupted`` (called at startup) converts
    leftover ``running`` steps to ``pending`` and pauses the plan.
    """
    cfg = config or load_config(user, root)
    if agent_event_source is None and tool_registry is None:
        tool_config = cfg.get("tools") or {}
        tool_registry = (
            apply_runtime_tool_policy(discover_tools(root, user), cfg)
            if bool(tool_config.get("enabled", True))
            else ToolRegistry({})
        )
    tool_timeout = float((cfg.get("tools") or {}).get("timeout", 240))
    agent_timeout = (cfg.get("agent_runtime") or {}).get("default_timeout", 600)

    store = PlanStore(root, user)

    try:
        plan = store.read(plan_id)
    except (PlanNotFoundError, PlanError) as exc:
        yield error_event(exc, phase="plan_read")
        return

    if plan["status"] != "approved":
        yield error_event(
            PlanExecutionError(
                f"计划 {plan_id} 当前状态为 {plan['status']!r}，无法由新的执行器领取"
            ),
            phase="plan_status",
        )
        return

    # A new executor may only claim an approved plan.  Treating an existing
    # ``running`` state as resumable lets the Web/App executor and background
    # scheduler execute the same plan concurrently.
    try:
        def _claim(p: dict[str, Any]) -> dict[str, Any]:
            if p.get("status") != "approved":
                raise PlanExecutionError(
                    f"计划 {plan_id} 已被其他执行器领取或状态已变化"
                )
            return {**p, "status": "running"}

        plan = store.update(plan_id, _claim)
    except (PlanError, PlanValidationError, PlanExecutionError) as exc:
        yield error_event(exc, phase="plan_status")
        return

    while True:
        if cancel_event is not None and cancel_event.is_set():
            return

                # 每次迭代从磁盘重新读取以获取外部编辑
        try:
            plan = store.read(plan_id)
        except (PlanNotFoundError, PlanError) as exc:
            yield error_event(exc, phase="plan_read")
            return

        if plan["status"] != "running":
                        # 计划被外部暂停或取消
            yield RunEvent(
                type="done",
                metadata={
                    "plan_id": plan_id,
                    "status": plan["status"],
                    "reason": "plan_no_longer_running",
                },
            )
            return

        step = _next_step(plan)
        if step is None:
            failed_step_ids = _failed_steps_needing_fix(plan)
            if failed_step_ids:
                try:
                    plan = store.update(
                        plan_id,
                        lambda p: {**p, "status": "paused"},
                        note="等待修正失败步骤",
                    )
                except (PlanError, PlanValidationError) as exc:
                    yield error_event(exc, phase="plan_pause")
                    return
                yield RunEvent(
                    type="done",
                    metadata={
                        "plan_id": plan_id,
                        "status": "paused",
                        "reason": "failed_step_needs_fix",
                        "failed_step_ids": failed_step_ids,
                        "message": "请先修正失败步骤后重试",
                    },
                )
                return

            all_succeeded = all(
                s.get("status") in {"completed", "skipped"}
                for s in plan["steps"]
            )
            if all_succeeded:
                try:
                    plan = store.update(
                        plan_id,
                        lambda p: {**p, "status": "completed"},
                        note="计划执行完成",
                    )
                except (PlanError, PlanValidationError) as exc:
                    yield error_event(exc, phase="plan_complete")
                    return
                yield RunEvent(
                    type="done",
                    metadata={
                        "plan_id": plan_id,
                        "status": "completed",
                        "steps_total": len(plan["steps"]),
                        "steps_completed": sum(
                            1 for s in plan["steps"] if s["status"] == "completed"
                        ),
                    },
                )
                return
            try:
                plan = store.update(
                    plan_id,
                    lambda p: {**p, "status": "failed"},
                    note="无可运行步骤",
                )
            except (PlanError, PlanValidationError) as exc:
                yield error_event(exc, phase="plan_fail")
                return
            yield RunEvent(
                type="done",
                metadata={
                    "plan_id": plan_id,
                    "status": "failed",
                    "reason": "no_runnable_step",
                },
            )
            return

        step_id = step["step_id"]
        tool_name = step.get("tool_name")
        tool_arguments = step.get("tool_arguments") or {}

        # 保持运行状态
        def _mark_running(p: dict) -> dict:
            if p.get("status") != "running":
                raise PlanExecutionError(
                    f"计划 {plan_id} 已停止，不能开始步骤 {step_id}"
                )
            for s in p["steps"]:
                if s["step_id"] == step_id:
                    if s.get("status") != "pending":
                        raise PlanExecutionError(
                            f"步骤 {step_id} 已被其他执行器领取或状态已变化"
                        )
                    s["status"] = "running"
                    s["started_at"] = _now()
                    break
            else:
                raise PlanExecutionError(f"步骤不存在: {step_id}")
            p["current_step"] = step_id
            return p

        try:
            plan = store.update(plan_id, _mark_running)
        except (PlanError, PlanValidationError, PlanExecutionError) as exc:
            yield error_event(exc, phase="step_start")
            return

        yield RunEvent(
            type="tool_call_start",
            tool_call_id=step_id,
            tool_name=tool_name or "",
            arguments=tool_arguments,
            metadata={"plan_id": plan_id, "step_id": step_id},
        )

        if cancel_event is not None and cancel_event.is_set():
            return

                # 执行工具
        status: str
        result_payload: Any
        error_payload: dict[str, Any] | None = None

        if agent_event_source is not None:
            control_prompt = (
                "【任务计划自动执行】\n"
                f"计划 ID：{plan_id}\n"
                f"当前步骤：{step_id} - {step.get('title', '')}\n"
                f"步骤说明：{step.get('description', '')}\n"
                f"计划工具：{tool_name or '由主智能体判断'}\n"
                f"计划参数：{json.dumps(tool_arguments, ensure_ascii=False)}\n\n"
                "完整活跃计划已注入系统提示词。只执行当前步骤，不要执行后续步骤，"
                "不要创建或编辑任务计划。本次为 executor-managed 模式，步骤状态由框架维护，"
                "禁止调用 task_plan.step_done 或 task_plan.step_fail。可以根据实际环境修正工具或参数；"
                "完成当前步骤后简要报告结果。"
            )
            agent_request = {
                "user": user,
                "source": str(plan.get("source") or "web"),
                "session_id": str(plan.get("session_id") or f"plan:{plan_id}"),
                "prompt": control_prompt,
                "stream": True,
                "run_id": f"plan_run_{uuid.uuid4().hex}",
                "_task_plan_id": plan_id,
                "_task_plan_step_id": step_id,
                "_task_plan_mode": "executor_managed",
            }
            assistant_text: list[str] = []
            agent_tools: list[dict[str, Any]] = []
            agent_error: dict[str, Any] | None = None
            terminal_done = False
            terminal_metadata: dict[str, Any] = {}
            for agent_event in agent_event_source(agent_request):
                agent_event.metadata.setdefault("plan_id", plan_id)
                agent_event.metadata.setdefault("step_id", step_id)
                agent_event.metadata.setdefault("background_plan_run", True)
                if agent_event.type == "text_delta":
                    assistant_text.append(agent_event.content)
                elif agent_event.type == "tool_call_result":
                    succeeded = bool(
                        (isinstance(agent_event.result, dict) and agent_event.result.get("ok"))
                        or agent_event.metadata.get("status") in {"completed", "success"}
                    )
                    agent_tools.append(
                        {
                            "name": agent_event.tool_name,
                            "status": "completed" if succeeded else "failed",
                        }
                    )
                elif agent_event.type == "error":
                    agent_error = dict(agent_event.error or {})
                elif agent_event.type == "done":
                    terminal_done = True
                    terminal_metadata = dict(agent_event.metadata)
                if agent_event.type not in {"done", "error"}:
                    yield agent_event
            if agent_error is not None:
                status = "failed"
                error_payload = agent_error
                result_payload = {"ok": False, "error": error_payload}
            elif not terminal_done:
                status = "failed"
                error_payload = {
                    "message": "主智能体执行未产生完成事件",
                    "exception_type": "PlanAgentRunIncomplete",
                }
                result_payload = {"ok": False, "error": error_payload}
            elif str(terminal_metadata.get("status") or "").casefold() not in {
                "",
                "completed",
                "success",
            }:
                terminal_status = str(terminal_metadata.get("status") or "failed").casefold()
                stop_reason = str(
                    terminal_metadata.get("stop_reason")
                    or terminal_metadata.get("reason")
                    or terminal_status
                )
                exception_type = {
                    "limited": "PlanAgentRunLimited",
                    "cancelled": "PlanAgentRunCancelled",
                    "failed": "PlanAgentRunFailed",
                }.get(terminal_status, "PlanAgentRunIncomplete")
                status = "failed"
                error_payload = {
                    "message": (
                        f"主智能体以非成功状态结束：{terminal_status}"
                        f"（{stop_reason}）"
                    ),
                    "exception_type": exception_type,
                    "agent_status": terminal_status,
                    "stop_reason": stop_reason,
                }
                failure = terminal_metadata.get("failure")
                if isinstance(failure, dict) and failure:
                    error_payload["failure"] = dict(failure)
                result_payload = {"ok": False, "error": error_payload}
            elif tool_name is not None and not any(
                item["status"] == "completed" for item in agent_tools
            ):
                status = "failed"
                error_payload = {
                    "message": "主智能体未成功执行任何工具",
                    "exception_type": "PlanAgentToolMissing",
                }
                result_payload = {"ok": False, "error": error_payload}
            else:
                status = "completed"
                result_payload = {
                    "ok": True,
                    "result": {
                        "assistant": "".join(assistant_text)[-4000:],
                        "tool_calls": agent_tools,
                    },
                }
        elif tool_name is None:
            status = "completed"
            result_payload = {"ok": True, "result": None}
        else:
            try:
                definition = tool_registry.get(tool_name)
                result = execute_tool(
                    definition,
                    tool_arguments,
                    context={
                        "root": str(root),
                        "user": user,
                        "source": f"plan:{plan_id}",
                        "session_id": plan.get("session_id", ""),
                        "window": "",
                        "tool_timeout": tool_timeout,
                        "agent_timeout": agent_timeout,
                        "plan_id": plan_id,
                        "step_id": step_id,
                    },
                    timeout=tool_timeout,
                    cancel_event=cancel_event,
                )
                status = "completed"
                result_payload = {"ok": True, "result": result}
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, GeneratorExit)):
                    raise
                oversized_result = isinstance(exc, ToolResultTooLargeError)
                status = "failed"
                result_payload = {
                    "ok": False,
                    "error": (
                        exc.error_payload()
                        if oversized_result
                        else {
                            "message": str(exc),
                            "exception_type": type(exc).__name__,
                        }
                    ),
                }
                error_payload = result_payload["error"]

        yield RunEvent(
            type="tool_call_result",
            tool_call_id=step_id,
            tool_name=tool_name or "",
            arguments=tool_arguments,
            result=result_payload,
            metadata={
                "plan_id": plan_id,
                "step_id": step_id,
                "status": status,
            },
        )

        # 保留步骤结果
        def _mark_step_result(p: dict) -> dict:
            if p.get("status") not in {"running", "paused"}:
                raise PlanExecutionError(
                    f"计划 {plan_id} 已停止，拒绝覆盖步骤 {step_id} 的最新状态"
                )
            for s in p["steps"]:
                if s["step_id"] == step_id:
                    if s.get("status") != "running":
                        raise PlanExecutionError(
                            f"步骤 {step_id} 已被外部修改，拒绝写入陈旧执行结果"
                        )
                    s["status"] = status
                    s["result"] = result_payload if status == "completed" else None
                    s["error"] = error_payload
                    s["finished_at"] = _now()
                    break
            else:
                raise PlanExecutionError(f"步骤不存在: {step_id}")
            return p

        try:
            plan = store.update(plan_id, _mark_step_result)
        except (PlanError, PlanValidationError, PlanExecutionError) as exc:
            yield error_event(exc, phase="step_persist")
            return

        if status == "failed":
            critical = step.get("critical", True)
            if critical:
                # 暂停计划
                try:
                    def _pause_failed(p: dict[str, Any]) -> dict[str, Any]:
                        if p.get("status") == "paused":
                            return p
                        if p.get("status") != "running":
                            raise PlanExecutionError(
                                f"计划 {plan_id} 已停止，不能由失败步骤改为暂停"
                            )
                        return {**p, "status": "paused"}

                    plan = store.update(plan_id, _pause_failed)
                except (PlanError, PlanValidationError, PlanExecutionError) as exc:
                    yield error_event(exc, phase="plan_pause")
                    return
                yield RunEvent(
                    type="done",
                    metadata={
                        "plan_id": plan_id,
                        "status": "paused",
                        "reason": "critical_step_failed",
                        "step_id": step_id,
                        **(
                            {
                                "stop_reason": error_payload.get("stop_reason"),
                                "failure": dict(error_payload),
                            }
                            if isinstance(error_payload, dict)
                            and error_payload.get("stop_reason")
                            else {}
                        ),
                    },
                )
                return
                        # 非关键：继续下一步
        if cancel_event is not None and cancel_event.is_set():
            return


def approve_plan(root: Path, user: str, plan_id: str) -> dict[str, Any]:
    """Transition a plan from pending to approved."""
    store = PlanStore(root, user)

    def _approve(plan: dict[str, Any]) -> dict[str, Any]:
        if plan.get("status") != "pending":
            raise PlanExecutionError(
                f"计划 {plan_id} 当前状态为 {plan.get('status')!r}，无法批准"
            )
        return {**plan, "status": "approved"}

    return store.update(plan_id, _approve)


def pause_plan(root: Path, user: str, plan_id: str) -> dict[str, Any]:
    """Pause a running or approved plan."""
    store = PlanStore(root, user)

    def _pause(plan: dict[str, Any]) -> dict[str, Any]:
        if plan.get("status") not in ("running", "approved"):
            raise PlanExecutionError(
                f"计划 {plan_id} 当前状态为 {plan.get('status')!r}，无法暂停"
            )
        return {**plan, "status": "paused"}

    return store.update(plan_id, _pause)


def resume_plan(root: Path, user: str, plan_id: str) -> dict[str, Any]:
    """Make a paused plan eligible for one executor to claim."""
    store = PlanStore(root, user)

    def _resume(plan: dict[str, Any]) -> dict[str, Any]:
        if plan.get("status") != "paused":
            raise PlanExecutionError(
                f"计划 {plan_id} 当前状态为 {plan.get('status')!r}，无法恢复"
            )
        return {**plan, "status": "approved"}

    return store.update(plan_id, _resume)


def cancel_plan(root: Path, user: str, plan_id: str) -> dict[str, Any]:
    """Cancel a plan and skip all pending steps."""
    store = PlanStore(root, user)

    def _cancel(p: dict) -> dict:
        p["status"] = "cancelled"
        for s in p["steps"]:
            if s["status"] == "pending":
                s["status"] = "cancelled"
            elif s["status"] == "running":
                s["status"] = "pending"
        return p

    return store.update(plan_id, _cancel)


def get_plan(root: Path, user: str, plan_id: str) -> dict[str, Any]:
    return PlanStore(root, user).read(plan_id)


def list_plans(root: Path, user: str) -> list[dict[str, Any]]:
    return PlanStore(root, user).list_plans()
