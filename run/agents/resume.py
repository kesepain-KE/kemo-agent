"""Explicit, scope-bound and transactionally claimed continuation of a task."""
from __future__ import annotations

import copy
import os
import queue

from run.agents.continuation import MAX_RESUME_ROUNDS, load_resume_state
from run.agents.queue_models import AgentTask
from run.agents.task_ledger import TaskLedger, _OPEN_PARENT, _OWNER, now
from run.agents.budgets import (
    BUDGET_STOPS, effective_limit, requested_timeout, SegmentBudget, legacy_tool_stop, upgrade_legacy_budget,
)


def _output_truncation_stop(record):
    error = record.get("error") or {}
    return (
        record.get("status") == "failed"
        and error.get("code") == "output_truncated"
        and error.get("process_terminated") is not False
        and not record.get("execution_live")
    )


def resume_task(scheduler, task_id, *, source, session_id, guidance="", timeout=None,
                ownership=None, config=None, event_callback=None, max_tool_calls=None) -> str:
    from run.agents.queue import AgentQueueError, AgentTaskNotFoundError, AgentQueueClosedError
    from run.agents.runner import AgentRunner
    if not isinstance(scheduler.runner, AgentRunner) or not source or not session_id:
        raise AgentQueueError("续跑需要完整对话身份和本地子代理执行器")
    if not isinstance(guidance, str) or len(guidance) > 8000:
        raise AgentQueueError("续跑指导必须是最多 8000 字符的文本")
    requested_timeout(timeout, field="续跑 timeout")
    runner = scheduler.runner
    ledger = TaskLedger(runner.root, runner.user)
    current_config = copy.deepcopy(config if config is not None else runner.config)
    with scheduler._lock:
        if scheduler._closed:
            raise AgentQueueClosedError("子代理调度队列已关闭")
        old_task = scheduler._tasks.get(task_id)
        if old_task is not None:
            scheduler._require_task_scope_locked(old_task, source=source, session_id=session_id)
            old_snapshot = old_task.snapshot()
            if ((old_task.status not in BUDGET_STOPS
                 and not legacy_tool_stop(old_snapshot)
                 and not _output_truncation_stop(old_snapshot))
                    or (old_task.error or {}).get("process_terminated") is False):
                raise AgentQueueError("子代理尚未超时停止或仍有执行存活，不能续跑")
        task: AgentTask | None = None
        enqueued = False
        try:
            with ledger.dependency_admission(source), ledger.db(write=True) as db:
                record = ledger.decode(db.execute(
                    "SELECT * FROM agent_tasks WHERE id=? AND source=? AND session=?",
                    (task_id, source, session_id)).fetchone())
                if record is None:
                    raise AgentTaskNotFoundError(f"未知子代理任务：{task_id}")
                legacy = legacy_tool_stop(record)
                truncated = _output_truncation_stop(record)
                if ((record["status"] not in BUDGET_STOPS and not legacy and not truncated)
                        or record.get("execution_live")
                        or (not record.get("resumable") and not legacy and not truncated)):
                    raise AgentQueueError("任务不是可续跑的预算停止或输出截断状态")
                round_number = record.get("run_count", 1)
                if not isinstance(round_number, int) or round_number >= MAX_RESUME_ROUNDS:
                    raise AgentQueueError("子代理续跑轮次已达安全上限")
                definition = runner.refresh_registry().get(record["agent"])
                if (definition not in runner.registry.public_agents("main_agent")
                        or definition.executor != "builtin:llm"
                        or definition.execution == "background_serial"):
                    raise AgentQueueError("当前子代理不支持恢复或已不再公开")
                restored = load_resume_state(
                    runner.root, runner.user, task_id,
                    source=source, session_id=session_id, definition=definition,
                    run_count=round_number, version=record.get("_checkpoint_version"),
                )
                upgrade_legacy_budget(restored)
                if not isinstance(ownership, dict):
                    raise AgentQueueError("续跑必须由当前主任务显式调度")
                parent = ledger.decode(db.execute("SELECT * FROM agent_tasks WHERE id=?",
                                       (ownership.get("root_task_id"),)).fetchone())
                if (not parent or parent["status"] not in _OPEN_PARENT
                        or parent["source"] != source or parent["session_id"] != session_id
                        or parent["attempt_id"] != ownership.get("attempt_id")):
                    raise AgentQueueError("主任务已收束或续跑归属无效")
                ledger.require_dependency_owner(parent)
                if (source == "web" and record.get("cardstream_integration_id")
                        and not record.get("card_result_seq")):
                    raise AgentQueueError("上一轮结果卡待提交，不能覆盖原回执")
                if record.get("root_task_id") != parent["id"]:
                    previous_parent = ledger.decode(db.execute("SELECT * FROM agent_tasks WHERE id=?",
                                               (record.get("root_task_id"),)).fetchone())
                    if previous_parent and (previous_parent.get("execution_live") or previous_parent["status"] not in {"completed", "limited", "partial", "failed", "cancelled", "interrupted"}):
                        raise AgentQueueError("原主任务仍在运行，不能由另一主任务接管")
                    record["submission_order"] = parent.get("last_submission_order", 0) + 1
                    parent["last_submission_order"] = record["submission_order"]
                    ledger.put(db, parent)
                restored["guidance"] = guidance
                previous_budget = restored.get("budget") or {}
                requested = max_tool_calls if max_tool_calls is not None else previous_budget.get("max_tool_calls")
                if requested is not None:
                    effective_limit(definition, requested)
                task = AgentTask(
                    id=task_id, agent=record["agent"], input_data=restored["input_data"],
                    source=source, session_id=session_id,
                    submission_order=record.get("submission_order", 0),
                    created_at=record["created_at"], timeout=None, survival_seconds=0,
                    config=current_config, serial=False, event_callback=event_callback,
                    ledger=ledger, run_count=round_number + 1, resume_state=restored,
                    previous_elapsed_ms=record.get("active_elapsed_ms", 0),
                    max_tool_calls=requested,
                )
                task.progress = {"status": "queued", "budget": SegmentBudget(
                    definition, requested, task.timeout, restored).snapshot()}
                task.progress["budget"]["elapsed_seconds"] = 0
                history = list(record.get("round_history") or [])[-(MAX_RESUME_ROUNDS - 1):]
                history.append({
                    "run_count": round_number, "status": record["status"],
                    "started_at": record.get("started_at"), "finished_at": record.get("finished_at"),
                    "root_task_id": record.get("root_task_id"),
                    "result": record.get("result"), "usage": record.get("usage"),
                    "budget": record.get("budget"), "stop_reason": record.get("stop_reason"),
                })
                record.update(
                    **ownership, status="queued", execution_live=True,
                    run_count=task.run_count, resumable=False, delivered=False, handled=False,
                    result=None, error=None, started_at="", finished_at="", updated_at=now(),
                    round_history=history, owner=_OWNER, pid=os.getpid(),
                    process_started=parent["process_started"],
                    stop_reason=None, budget=task.progress["budget"], progress={},
                    timeout=None, _heartbeat=None,
                    _guidance=[], _checkpoint_version=2,
                    cardstream_integration_id=parent.get("cardstream_integration_id", ""),
                    cardstream_generation=parent.get("cardstream_generation", ""),
                    cardstream_meter_key=parent.get("cardstream_meter_key", ""),
                    card_result_seq=None, card_result_error_type="",
                )
                ledger.put(db, record)
                scheduler._ensure_worker_locked()
                # Nonblocking put while holding both locks: rollback remains possible.
                scheduler._queue.put(task, block=False)
                enqueued = True
            scheduler._tasks[task_id] = task
            scheduler._concurrency_config = current_config
            scheduler._wake.set()
        except BaseException as exc:
            if enqueued and task is not None:
                scheduler._queue.discard(task)
            if isinstance(exc, queue.Full):
                raise AgentQueueError(f"{str(exc) or '子代理队列已满'}；原超时结果保持不变") from exc
            raise
    assert task is not None
    scheduler._emit(task, "queued")
    return task_id
