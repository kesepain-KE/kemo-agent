"""Cron 三路执行适配器：主智能体、内部子代理和注册函数。"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from provider.factory import create_provider
from run.agent_runner import AgentRunner
from run.config import load_config
from run.cron_store import CronError, CronStore, CronValidationError, now_beijing
from run.engine import handle_request
from run.tools import ToolRegistry, discover_tools


def _parse_subagent_prompt(prompt: str) -> tuple[str, dict[str, Any]]:
    try:
        payload = json.loads(prompt)
    except json.JSONDecodeError as exc:
        raise CronValidationError("subagent 任务 prompt 必须是有效 JSON") from exc
    if not isinstance(payload, dict):
        raise CronValidationError("subagent 任务 prompt 必须是 JSON 对象")
    name = payload.get("subagent")
    input_data = payload.get("input")
    if not isinstance(name, str) or not name.strip():
        raise CronValidationError("subagent 任务缺少非空 subagent 字符串")
    if not isinstance(input_data, dict):
        raise CronValidationError("subagent 任务 input 必须是 JSON 对象")
    return name.strip(), input_data


def _parse_function_prompt(prompt: str) -> str:
    try:
        payload = json.loads(prompt)
    except json.JSONDecodeError as exc:
        raise CronValidationError("function 任务 prompt 必须是有效 JSON") from exc
    if not isinstance(payload, dict):
        raise CronValidationError("function 任务 prompt 必须是 JSON 对象")
    name = payload.get("function")
    if not isinstance(name, str) or not name.strip():
        raise CronValidationError("function 任务缺少非空 function 字符串")
    return name.strip()


def _execute_internal_function(
    function_name: str,
    *,
    root: Path,
    user: str,
    config: dict[str, Any],
    provider_factory: Callable[[dict[str, Any]], Any],
    cancel_event: threading.Event | None,
) -> dict[str, Any]:
    if function_name != "cron.review_due.scan_and_promote":
        raise CronValidationError(f"未注册的 cron 内部函数：{function_name}")
    from cron.review_due import scan_and_promote

    return scan_and_promote(
        root=root,
        user=user,
        config=config,
        provider_factory=provider_factory,
        cancel_event=cancel_event,
    )


def _claim_task(store: CronStore, task_id: str) -> dict[str, Any]:
    def _claim(task: dict[str, Any]) -> dict[str, Any]:
        if task["status"] not in ("enabled", "failed"):
            raise CronError(f"任务 {task_id} 当前状态为 {task['status']!r}，无法领取")
        task["status"] = "running"
        task["latest_run_at"] = now_beijing()
        return task

    try:
        return store.update(task_id, _claim)
    except (CronError, CronValidationError) as exc:
        raise CronError(f"领取任务失败：{exc}") from exc


def _finish_task(store: CronStore, task: dict[str, Any], *, failed: bool) -> dict[str, Any]:
    task_id = task["task_id"]

    def _finish(current: dict[str, Any]) -> dict[str, Any]:
        current["latest_run_at"] = now_beijing()
        if failed:
            current["status"] = "failed"
            return current
        if current["type"] == "once":
            current["status"] = "completed"
            current["next_run_at"] = ""
            return current
        from cron.schedule import compute_next_run

        current["status"] = "enabled"
        current["next_run_at"] = compute_next_run(current, after=datetime.now().astimezone())
        return current

    try:
        return store.update(task_id, _finish)
    except (CronError, CronValidationError) as exc:
        raise CronError(f"持久化执行状态失败：{exc}") from exc


def _revert_claim(store: CronStore, task_id: str) -> dict[str, Any]:
    def _revert(task: dict[str, Any]) -> dict[str, Any]:
        task["status"] = "enabled"
        return task

    try:
        return store.update(task_id, _revert)
    except (CronError, CronValidationError):
        return store.read(task_id)


def _execute_claimed_task(
    *,
    root: Path,
    user: str,
    task: dict[str, Any],
    config: dict[str, Any],
    provider_factory: Callable[[dict[str, Any]], Any],
    tool_registry_factory: Callable[[Path, str], ToolRegistry],
    cancel_event: threading.Event | None,
) -> dict[str, Any]:
    store = CronStore(root, user)
    task_id = task["task_id"]
    if cancel_event is not None and cancel_event.is_set():
        return _revert_claim(store, task_id)

    failed = False
    try:
        exec_mode = task.get("exec_mode", "agent")
        if exec_mode == "subagent":
            name, input_data = _parse_subagent_prompt(task["prompt"])
            AgentRunner(
                root,
                user,
                config=config,
                provider_factory=provider_factory,
            ).run(
                name,
                input_data,
                cancel_event=cancel_event,
                task_id=task_id,
            )
        elif exec_mode == "function":
            _execute_internal_function(
                _parse_function_prompt(task["prompt"]),
                root=root,
                user=user,
                config=config,
                provider_factory=provider_factory,
                cancel_event=cancel_event,
            )
        else:
            handle_request(
                {
                    "user": task["user"],
                    "prompt": task["prompt"],
                    "source": "cron",
                    "session_id": "cron",
                },
                root=root,
                provider_factory=provider_factory,
                tool_registry_factory=tool_registry_factory,
                cancel_event=cancel_event,
            )
    except Exception:
        failed = True
    return _finish_task(store, task, failed=failed)


def _execute_memory_promotion(
    root: Path,
    user: str,
    *,
    config: dict[str, Any],
    provider_factory: Callable[[dict[str, Any]], Any],
    cancel_event: threading.Event | None,
) -> dict[str, Any]:
    from cron.review_due import scan_and_promote

    return scan_and_promote(
        root=root,
        user=user,
        config=config,
        provider_factory=provider_factory,
        cancel_event=cancel_event,
    )


def _execute_memory_review(
    root: Path,
    user: str,
    *,
    trigger: str,
    config: dict[str, Any],
    provider_factory: Callable[[dict[str, Any]], Any],
    cancel_event: threading.Event | None,
    task_id: str,
) -> dict[str, Any]:
    if cancel_event is not None and cancel_event.is_set():
        return {"status": "cancelled", "action": trigger, "user": user}
    result = AgentRunner(
        root,
        user,
        config=config,
        provider_factory=provider_factory,
    ).run(
        "memory_temporary_important",
        {"trigger": trigger},
        cancel_event=cancel_event,
        task_id=task_id,
    )
    return {
        "status": "completed",
        "action": trigger,
        "user": user,
        "data": result.data if isinstance(getattr(result, "data", None), dict) else {},
    }


def _execute_system_task(
    *,
    root: Path,
    user: str,
    task: dict[str, Any],
    config: dict[str, Any],
    provider_factory: Callable[[dict[str, Any]], Any],
    cancel_event: threading.Event | None,
) -> dict[str, Any]:
    if task.get("exec_mode") != "system":
        raise CronValidationError("system_task 必须使用 system 执行模式")
    action = str(task.get("action") or task.get("task_id") or "")
    if action == "memory_promotion":
        return _execute_memory_promotion(
            root,
            user,
            config=config,
            provider_factory=provider_factory,
            cancel_event=cancel_event,
        )
    if action in {"periodic_scan", "daily_consolidate"}:
        return _execute_memory_review(
            root,
            user,
            trigger=action,
            config=config,
            provider_factory=provider_factory,
            cancel_event=cancel_event,
            task_id=str(task.get("task_id") or action),
        )
    raise CronValidationError(f"未注册的系统 cron 动作：{action}")


def execute_cron_task(
    *,
    root: Path,
    user: str,
    task_id: str,
    config: dict[str, Any] | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
    tool_registry_factory: Callable[[Path, str], ToolRegistry] = discover_tools,
    cancel_event: threading.Event | None = None,
    system_task: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """根据 ``exec_mode`` 领取并执行一个 cron 任务。"""
    cfg = config if config is not None else load_config(user, root)
    if system_task is not None:
        return _execute_system_task(
            root=root,
            user=user,
            task=system_task,
            config=cfg,
            provider_factory=provider_factory,
            cancel_event=cancel_event,
        )
    task = _claim_task(CronStore(root, user), task_id)
    return _execute_claimed_task(
        root=root,
        user=user,
        task=task,
        config=cfg,
        provider_factory=provider_factory,
        tool_registry_factory=tool_registry_factory,
        cancel_event=cancel_event,
    )


def execute_subagent_task(
    *,
    root: Path,
    user: str,
    task_id: str,
    config: dict[str, Any] | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """显式执行 subagent 模式任务，并拒绝其他模式。"""
    store = CronStore(root, user)
    task = store.read(task_id)
    if task.get("exec_mode") != "subagent":
        raise CronValidationError(f"任务 {task_id} 不是 subagent 执行模式")
    cfg = config if config is not None else load_config(user, root)
    return _execute_claimed_task(
        root=root,
        user=user,
        task=_claim_task(store, task_id),
        config=cfg,
        provider_factory=provider_factory,
        tool_registry_factory=discover_tools,
        cancel_event=cancel_event,
    )


def execute_function_task(
    *,
    root: Path,
    user: str,
    task_id: str,
    config: dict[str, Any] | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """显式执行 function 模式任务，并拒绝其他模式。"""
    store = CronStore(root, user)
    task = store.read(task_id)
    if task.get("exec_mode") != "function":
        raise CronValidationError(f"任务 {task_id} 不是 function 执行模式")
    cfg = config if config is not None else load_config(user, root)
    return _execute_claimed_task(
        root=root,
        user=user,
        task=_claim_task(store, task_id),
        config=cfg,
        provider_factory=provider_factory,
        tool_registry_factory=discover_tools,
        cancel_event=cancel_event,
    )
