"""Cron 三路执行适配器：主智能体、内部子代理和注册函数。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from provider.factory import create_provider
from run.agent_runner import AgentRunner
from run.config import load_config
from run.cron_store import CronError, CronStore, CronValidationError, now_beijing
from run.engine import handle_request
from run.history_index import new_conversation_id
from run.process_utils import hidden_subprocess_kwargs
from run.tools import ToolRegistry, discover_tools


_DEFAULT_MODULE_UPDATE_TIMEOUT = 120.0
_MODULE_RESULT_PREFIX = "__KEMO_MODULE_UPDATE_RESULT__="
_MODULE_UPDATE_RUNNER = r'''
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

PREFIX = "__KEMO_MODULE_UPDATE_RESULT__="

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def emit(payload: dict, *, stream) -> None:
    print(PREFIX + json.dumps(payload, ensure_ascii=False), file=stream, flush=True)


try:
    update_path = Path(sys.argv[1]).resolve()
    module_root = Path(sys.argv[2]).resolve()
    sys.path.insert(0, str(module_root))
    if update_path.parent != module_root:
        sys.path.insert(0, str(update_path.parent))
    spec = importlib.util.spec_from_file_location("__kemo_module_update__", str(update_path))
    if spec is None or spec.loader is None:
        raise ImportError("无法创建模块加载器")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    updater = getattr(module, "update", None)
    if not callable(updater):
        updater = getattr(module, "main", None)
    if not callable(updater):
        raise AttributeError("更新脚本必须提供可调用的 update() 或 main()")
    result = updater()
    if result is False:
        raise RuntimeError("更新脚本返回 False")
    if isinstance(result, dict):
        status = str(result.get("status") or "").strip().casefold()
        if result.get("ok") is False or status in {"error", "failed", "failure"}:
            reason = result.get("error") or result.get("message") or result.get("reason")
            raise RuntimeError(str(reason or "更新脚本返回失败状态"))
    emit({"ok": True}, stream=sys.stdout)
except BaseException as exc:
    emit(
        {
            "ok": False,
            "reason": str(exc) or type(exc).__name__,
            "exception_type": type(exc).__name__,
        },
        stream=sys.stderr,
    )
    raise SystemExit(1)
'''


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
    return {
        "status": "completed",
        "action": trigger,
        "user": user,
        "data": result.data if isinstance(getattr(result, "data", None), dict) else {},
    }


def _is_link_or_junction(path: Path) -> bool:
    """Return true for symbolic links and Windows directory junctions."""

    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(callable(is_junction) and is_junction())
    except OSError:
        return True


def _module_update_timeout(config: dict[str, Any]) -> float:
    task_config = config.get("task_cron_system") or {}
    if not isinstance(task_config, dict):
        return _DEFAULT_MODULE_UPDATE_TIMEOUT
    raw = task_config.get("module_update_timeout", _DEFAULT_MODULE_UPDATE_TIMEOUT)
    if isinstance(raw, bool):
        return _DEFAULT_MODULE_UPDATE_TIMEOUT
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_MODULE_UPDATE_TIMEOUT
    if seconds <= 0:
        return _DEFAULT_MODULE_UPDATE_TIMEOUT
    return min(seconds, 3600.0)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _record_module_health(
    manifest_path: Path,
    category: str,
    *,
    healthy: bool,
) -> None:
    if _is_link_or_junction(manifest_path):
        raise OSError(f"{manifest_path.name} 不能是符号链接或目录联接")
    payload = json.loads(manifest_path.read_text("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{manifest_path.name} 顶层必须是 JSON 对象")
    payload["health" if category == "sense" else "input_health"] = (
        "正常" if healthy else "异常"
    )
    if healthy:
        payload["recent_update"] = datetime.fromisoformat(now_beijing()).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    _atomic_write_json(manifest_path, payload)


def _process_detail(completed: subprocess.CompletedProcess[str]) -> str:
    detail = (completed.stderr or completed.stdout or "").strip()
    return detail[-1000:] if detail else "更新子进程未返回可识别结果"


def _runner_payload(completed: subprocess.CompletedProcess[str]) -> dict[str, Any] | None:
    outputs = (
        (completed.stdout, completed.stderr)
        if completed.returncode == 0
        else (completed.stderr, completed.stdout)
    )
    for output in outputs:
        for line in reversed((output or "").splitlines()):
            if not line.startswith(_MODULE_RESULT_PREFIX):
                continue
            try:
                payload = json.loads(line[len(_MODULE_RESULT_PREFIX):])
            except json.JSONDecodeError:
                return None
            return payload if isinstance(payload, dict) else None
    return None


def _run_module_updater(
    update_path: Path,
    module_root: Path,
    *,
    timeout: float,
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                _MODULE_UPDATE_RUNNER,
                str(update_path),
                str(module_root),
            ],
            cwd=str(module_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            **hidden_subprocess_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "reason": f"更新脚本执行超时（{timeout:g} 秒）",
            "exception_type": "TimeoutExpired",
        }
    except OSError as exc:
        return {
            "ok": False,
            "reason": f"无法启动更新子进程：{exc}",
            "exception_type": type(exc).__name__,
        }
    payload = _runner_payload(completed)
    if completed.returncode != 0:
        if payload and payload.get("ok") is False:
            return payload
        return {
            "ok": False,
            "reason": _process_detail(completed),
            "exception_type": "ChildProcessError",
        }
    if not payload or payload.get("ok") is not True:
        return {
            "ok": False,
            "reason": _process_detail(completed),
            "exception_type": "ChildProcessError",
        }
    return {"ok": True}


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

        run_result = _run_module_updater(
            update_path,
            module_root,
            timeout=_module_update_timeout(config),
        )
        if run_result.get("ok") is not True:
            reason = str(run_result.get("reason") or "更新脚本执行失败")
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
