"""Cron 三路执行适配器：主智能体、内部子代理和注册函数。"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from provider.factory import create_provider
from run.agent_runner import AgentRunner
from run.config import load_config
from run.cron_store import CronError, CronStore, CronValidationError, now_beijing
from run.engine import handle_request
from run.history_index import new_conversation_id
from run.expand_runtime import record_expand_runtime
from run.module_runtime import (
    module_update_timeout as _module_update_timeout,
    record_module_health as _record_module_health,
    run_module_updater,
)
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
    transport_registry: Any | None = None,
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
            background_session_id = str(task.get("session_id") or "").strip()
            if not background_session_id or background_session_id == "cron":
                background_session_id = new_conversation_id()
            request_payload: dict[str, Any] = {
                "user": task["user"],
                "prompt": task["prompt"],
                "source": f"background:cron:{task_id}",
                "session_id": background_session_id,
            }
            if transport_registry is not None:
                request_payload["_transport_registry"] = transport_registry
            handle_request(
                request_payload,
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
    response = {
        "status": "completed",
        "action": trigger,
        "user": user,
        "data": result.data if isinstance(getattr(result, "data", None), dict) else {},
    }
    update = (
        result.metadata.get("important_memory_update")
        if isinstance(getattr(result, "metadata", None), dict)
        else None
    )
    if isinstance(update, dict):
        response["memory_update"] = update
    return response


def _is_link_or_junction(path: Path) -> bool:
    """Return true for symbolic links and Windows directory junctions."""

    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(callable(is_junction) and is_junction())
    except OSError:
        return True


def _run_module_updater(
    update_path: Path,
    module_root: Path,
    *,
    timeout: float,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Backward-compatible public seam for module updater tests and callers."""

    return run_module_updater(
        update_path,
        module_root,
        timeout=timeout,
        cancel_event=cancel_event,
    )


def _update_modules(
    modules_dir: Path,
    category: str,
    *,
    config: dict[str, Any],
    cancel_event: threading.Event | None,
    module_prefix: str = "",
) -> dict[str, Any]:
    """Run declared sense/expand updaters in bounded child processes."""

    manifest_name = "sense.json" if category == "sense" else "expand.json"
    if _is_link_or_junction(modules_dir):
        return {
            "status": "failed",
            "category": category,
            "reason": f"{modules_dir.name} 目录不能是符号链接或目录联接",
            "updated": [],
            "failed": [module_prefix.rstrip("/") or modules_dir.name],
            "errors": [
                {
                    "module": module_prefix.rstrip("/") or modules_dir.name,
                    "reason": "模块根目录不能是符号链接或目录联接",
                }
            ],
        }
    if not modules_dir.is_dir():
        return {
            "status": "skipped",
            "category": category,
            "reason": f"{modules_dir.name} 目录不存在",
            "updated": [],
            "failed": [],
            "errors": [],
        }

    updated: list[str] = []
    failed: list[str] = []
    errors: list[dict[str, str]] = []

    def module_id(name: str) -> str:
        return f"{module_prefix}{name}" if module_prefix else name

    def reject(
        module: str,
        reason: str,
        exc: BaseException | None = None,
        *,
        exception_type: str = "",
    ) -> None:
        failed.append(module)
        detail = {"module": module, "reason": reason}
        if exc is not None:
            detail["exception_type"] = type(exc).__name__
        elif exception_type:
            detail["exception_type"] = exception_type
        errors.append(detail)

    for entry in sorted(modules_dir.iterdir(), key=lambda item: item.name):
        if cancel_event is not None and cancel_event.is_set():
            return {
                "status": "cancelled",
                "category": category,
                "updated": updated,
                "failed": failed,
                "errors": errors,
            }
        if entry.name.startswith(".") or entry.name == "__pycache__":
            continue
        logical_name = module_id(entry.name)
        if _is_link_or_junction(entry):
            reject(logical_name, "模块目录不能是符号链接或目录联接")
            continue
        if not entry.is_dir():
            continue

        manifest_path = entry / manifest_name
        if not manifest_path.is_file():
            continue
        if _is_link_or_junction(manifest_path):
            reject(logical_name, f"{manifest_name} 不能是符号链接或目录联接")
            continue
        try:
            metadata = json.loads(manifest_path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            reject(logical_name, f"无法读取有效的 {manifest_name}: {exc}", exc)
            continue
        if not isinstance(metadata, dict):
            reject(logical_name, f"{manifest_name} 顶层必须是 JSON 对象")
            continue

        start_update = metadata.get("start_update")
        if not isinstance(start_update, str) or not start_update.strip():
            reject(logical_name, f"{manifest_name} 缺少非空 start_update")
            continue
        relative = Path(start_update.strip())
        if relative.is_absolute() or ".." in relative.parts:
            reject(logical_name, "start_update 必须是模块目录内的相对路径")
            continue

        module_root = entry.resolve()
        candidate = entry / relative
        current = entry
        unsafe_component = False
        for part in relative.parts:
            if part in {"", "."}:
                continue
            current = current / part
            if _is_link_or_junction(current):
                unsafe_component = True
                break
        if unsafe_component:
            reject(logical_name, "start_update 路径不能经过符号链接或目录联接")
            continue

        update_path = candidate.resolve()
        try:
            update_path.relative_to(module_root)
        except ValueError:
            reject(logical_name, "start_update 解析后越出模块目录")
            continue
        if not update_path.is_file():
            reject(logical_name, f"start_update 文件不存在：{start_update}")
            continue

        run_started = time.monotonic()
        run_result = _run_module_updater(
            update_path,
            module_root,
            timeout=_module_update_timeout(config),
            cancel_event=cancel_event,
        )
        duration_ms = round((time.monotonic() - run_started) * 1000)
        if run_result.get("ok") is not True:
            reason = str(run_result.get("reason") or "更新脚本执行失败")
            if category == "expand":
                try:
                    record_expand_runtime(
                        module_root,
                        "update",
                        ok=False,
                        duration_ms=duration_ms,
                        error=reason,
                    )
                except Exception as runtime_exc:
                    reason = f"{reason}；写回运行诊断失败：{runtime_exc}"
            try:
                _record_module_health(manifest_path, category, healthy=False)
            except Exception as health_exc:
                reason = f"{reason}；写回异常健康状态失败：{health_exc}"
            reject(
                logical_name,
                reason,
                exception_type=str(run_result.get("exception_type") or ""),
            )
            continue
        try:
            _record_module_health(manifest_path, category, healthy=True)
        except Exception as exc:
            reject(logical_name, f"更新成功但健康状态写回失败：{exc}", exc)
            continue
        if category == "expand":
            try:
                record_expand_runtime(
                    module_root,
                    "update",
                    ok=True,
                    duration_ms=duration_ms,
                    result=run_result.get("result"),
                )
            except Exception as exc:
                reject(logical_name, f"更新成功但运行诊断写回失败：{exc}", exc)
                continue
        updated.append(logical_name)

    status = "completed" if not failed else ("partial" if updated else "failed")
    return {
        "status": status,
        "category": category,
        "updated": updated,
        "failed": failed,
        "errors": errors,
    }


def _execute_perception_update(
    root: Path,
    *,
    config: dict[str, Any],
    cancel_event: threading.Event | None,
) -> dict[str, Any]:
    """Update every global perception module exactly once."""

    return _update_modules(
        root / "global_sense",
        "sense",
        config=config,
        cancel_event=cancel_event,
    )


def _merge_module_results(
    results: list[dict[str, Any]],
    *,
    category: str,
) -> dict[str, Any]:
    updated = [name for result in results for name in result.get("updated", [])]
    failed = [name for result in results for name in result.get("failed", [])]
    errors = [item for result in results for item in result.get("errors", [])]
    if any(result.get("status") == "cancelled" for result in results):
        status = "cancelled"
    elif failed:
        status = "partial" if updated else "failed"
    elif all(result.get("status") == "skipped" for result in results):
        status = "skipped"
    else:
        status = "completed"
    return {
        "status": status,
        "category": category,
        "updated": updated,
        "failed": failed,
        "errors": errors,
    }


def _execute_expand_update(
    root: Path,
    user: str,
    *,
    config: dict[str, Any],
    cancel_event: threading.Event | None,
) -> dict[str, Any]:
    """Refresh shared roots once and user roots under their own identity."""

    if user == "__system__":
        result = _merge_module_results(
            [
                _update_modules(
                    root / "global_expand",
                    "expand",
                    config=config,
                    cancel_event=cancel_event,
                    module_prefix="global/",
                ),
                _update_modules(
                    root / "shared_expand",
                    "expand",
                    config=config,
                    cancel_event=cancel_event,
                    module_prefix="shared/",
                ),
            ],
            category="expand",
        )
        result["scope"] = "global_shared"
        return result

    from run.users import user_dir

    users_root = root / "users"
    resolved_users_root = users_root.resolve()
    user_root = user_dir(user, root)
    if _is_link_or_junction(users_root) or _is_link_or_junction(user_root):
        return {
            "status": "failed",
            "category": "expand",
            "scope": "user",
            "user": user,
            "updated": [],
            "failed": [user],
            "errors": [{
                "module": user,
                "reason": "用户目录不能是符号链接或目录联接",
            }],
        }
    try:
        user_root.resolve().relative_to(resolved_users_root)
    except ValueError:
        return {
            "status": "failed",
            "category": "expand",
            "scope": "user",
            "user": user,
            "updated": [],
            "failed": [user],
            "errors": [{
                "module": user,
                "reason": "用户目录解析后越出 users 目录",
            }],
        }
    result = _update_modules(
        user_root / "expand",
        "expand",
        config=config,
        cancel_event=cancel_event,
    )
    result["scope"] = "user"
    result["user"] = user
    return result


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
    if action == "perception_update":
        return _execute_perception_update(
            root,
            config=config,
            cancel_event=cancel_event,
        )
    if action == "expand_update":
        return _execute_expand_update(
            root,
            user,
            config=config,
            cancel_event=cancel_event,
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
    transport_registry: Any | None = None,
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
        transport_registry=transport_registry,
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
