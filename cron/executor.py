"""Cron-to-Run 直接执行适配器。

通过直接调用Run核心(handle_request)来执行cron任务，
收集结果和错误，并将它们写回 CronStore。
从不通过 cli.py 进行路由。"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from provider.factory import create_provider
from run.config import load_config
from run.cron_store import CronError, CronStore, CronValidationError
from run.engine import EngineError, handle_request
from run.tools import ToolRegistry, discover_tools


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def execute_cron_task(
    *,
    root: Path,
    user: str,
    task_id: str,
    config: dict[str, Any] | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
    tool_registry_factory: Callable[[Path, str], ToolRegistry] = discover_tools,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Execute a single cron task and persist the result.

    1. Atomically claim the task (enabled → running).
    2. Call handle_request with the task's prompt.
    3. Persist result or error, update next_run_at, increment run_count.

    Returns the final task dict.
    """
    cfg = config or load_config(user, root)
    store = CronStore(root, user)

        # 1. 原子声明：启用→运行
    def _claim(t: dict) -> dict:
        if t["status"] not in ("enabled", "failed"):
            raise CronError(
                f"任务 {task_id} 当前状态为 {t['status']!r}，无法领取"
            )
        t["status"] = "running"
        t["last_run_at"] = _now()
        return t

    try:
        task = store.update(task_id, _claim)
    except (CronError, CronValidationError) as exc:
        raise CronError(f"领取任务失败：{exc}") from exc

    prompt = task["prompt"]
    source = task.get("source", "cron")
    session_id = task.get("session_id", "cron")

        # 2.通过Run core执行
    request = {
        "user": user,
        "prompt": prompt,
        "source": source,
        "session_id": session_id,
    }

    result_payload: dict[str, Any] | None = None
    error_payload: dict[str, Any] | None = None

    if cancel_event is not None and cancel_event.is_set():
                # 执行前取消 — 恢复为启用
        def _revert(t: dict) -> dict:
            t["status"] = "enabled"
            return t
        try:
            store.update(task_id, _revert)
        except (CronError, CronValidationError):
            pass
        return store.read(task_id)

    try:
        result_payload = handle_request(
            request,
            root=root,
            provider_factory=provider_factory,
            tool_registry_factory=tool_registry_factory,
        )
    except EngineError as exc:
        error_payload = {
            "message": str(exc),
            "exception_type": type(exc).__name__,
            "phase": "run",
        }
    except Exception as exc:
        error_payload = {
            "message": str(exc),
            "exception_type": type(exc).__name__,
            "phase": "run",
        }

        # 3. 保存结果并更新时间表
    run_count = int(task.get("run_count", 0)) + 1
    schedule = task.get("schedule") or {}
    stype = schedule.get("type", "recurring")

    def _finish(t: dict) -> dict:
        t["run_count"] = run_count
        t["last_result"] = (
            {
                "text": str(result_payload.get("text", ""))[:500],
                "model": result_payload.get("model", ""),
                "usage": result_payload.get("usage"),
            }
            if result_payload
            else None
        )
        t["last_error"] = error_payload

        if error_payload is not None:
            t["status"] = "failed"
        elif stype == "once":
            t["status"] = "completed"
            t["next_run_at"] = ""
        else:
            t["status"] = "enabled"
                        # 从现在开始计算下一次运行
            from cron.schedule import compute_next_run
            now = datetime.now(timezone.utc)
            try:
                t["next_run_at"] = compute_next_run(schedule, after=now)
            except Exception:
                t["next_run_at"] = _now()
        return t

    try:
        task = store.update(task_id, _finish)
    except (CronError, CronValidationError) as exc:
        raise CronError(f"持久化结果失败：{exc}") from exc

    return task
