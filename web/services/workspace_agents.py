"""Authenticated projections and explicit controls for durable agent tasks."""
from __future__ import annotations

from run.agents import TaskLedger, find_agent_scheduler
from run.agents import guide_task as enqueue_guidance
from web.errors import ConflictError, InvalidRequestError, NotFoundError
from run.agents import AgentRunError
from run.agents import AgentTaskNotFoundError


def _summary(record):
    return {k: v for k, v in TaskLedger.public(record).items()
            if k not in {"events", "result", "usage", "error", "round_history"}}


def tasks(backend, user, *, source="", session_id="", page=1, page_size=20):
    user = backend.require_user(user)
    ledger = TaskLedger(backend.root, user)
    if not ledger.path.exists():
        return {"tasks": [], "total": 0, "page": page, "total_pages": 1}
    records, total = ledger.records(source, session_id, parent="", offset=(page - 1) * page_size, limit=page_size)
    result = []
    for parent in records:
        children, child_total = ledger.records(parent["source"], parent["session_id"], parent=parent["id"], limit=1000)
        result.append({**_summary(parent), "children": [_summary(r) for r in children],
                       "children_total": child_total})
    return {"tasks": result, "total": total, "page": page,
            "total_pages": max(1, (total + page_size - 1) // page_size)}


def task(backend, user, task_id, *, source, session_id):
    user = backend.require_user(user)
    if not source or not session_id:
        raise InvalidRequestError("查看智能体执行需要完整对话身份")
    ledger = TaskLedger(backend.root, user)
    record = ledger.get(task_id, source, session_id) if ledger.path.exists() else None
    if not record:
        raise NotFoundError("智能体任务不存在")
    controls = _control_availability(backend, user, ledger, record)
    artifacts = []
    result = record.get("result")
    candidates = result.get("artifacts", []) if isinstance(result, dict) else []
    for item in candidates[:40] if isinstance(candidates, list) else []:
        if not isinstance(item, dict) or item.get("scope") not in {"file_upload", "download"}:
            continue
        relative = item.get("relative_path")
        if not isinstance(relative, str) or not relative or len(relative) > 1024:
            continue
        try:
            target = backend.file_download(user, item["scope"], relative)
            artifacts.append({"scope": item["scope"], "relative_path": relative,
                              "name": target.name, "size": target.stat().st_size})
        except Exception:
            # Only validated existing files in this user's file areas get links.
            continue
    return {"task": {**ledger.public(record), "artifacts": artifacts, "controls": controls}}


def _control_availability(backend, user, ledger, record):
    if record.get("agent") == "main" or not record.get("root_task_id"):
        return {"guide": False, "cancel": False}
    parent = ledger.get(record.get("root_task_id", ""), record["source"], record["session_id"])
    parent_closed = bool(
        parent
        and parent.get("response_closed")
        and parent.get("execution_live")
        and parent.get("status") in {
            "completed", "limited", "partial", "failed", "cancelled", "interrupted",
        }
    )
    scheduler = find_agent_scheduler(backend.root, user)
    local_live = bool(
        parent_closed
        and scheduler
        and scheduler.has_local_task(
            record["id"], source=record["source"], session_id=record["session_id"]
        )
    )
    state = record.get("status")
    return {
        "guide": local_live and state in {"queued", "running"},
        "cancel": local_live and state in {"queued", "running", "timed_out_running"},
    }


def _control_target(backend, user, task_id, *, source, session_id):
    user = backend.require_user(user)
    source = backend.require_source(source)
    session_id = backend.require_session_id(session_id)
    ledger = TaskLedger(backend.root, user)
    record = ledger.get(task_id, source, session_id) if ledger.path.exists() else None
    if not record or record.get("agent") == "main":
        raise NotFoundError("智能体任务不存在")
    controls = _control_availability(backend, user, ledger, record)
    if not record.get("root_task_id") or not ledger.get(
        record["root_task_id"], source, session_id
    ):
        raise NotFoundError("智能体任务不存在")
    if not any(controls.values()):
        scheduler = find_agent_scheduler(backend.root, user)
        has_local_task = bool(
            scheduler
            and scheduler.has_local_task(task_id, source=source, session_id=session_id)
        )
        if (
            not has_local_task
            and record.get("status") in {"queued", "running", "timed_out_running"}
            and record.get("execution_live")
        ):
            raise ConflictError("子任务执行句柄不在当前进程，无法安全控制；状态未被改写")
        if not has_local_task:
            raise ConflictError("当前子任务已退出或不处于可控制状态")
        raise ConflictError("仅能控制主响应已关闭且仍在执行的子任务")
    return user, source, session_id, ledger, record, controls


def guide(backend, user, task_id, *, source, session_id, guidance):
    _, source, session_id, _, _, controls = _control_target(
        backend, user, task_id, source=source, session_id=session_id
    )
    if not controls["guide"]:
        raise ConflictError("当前子任务不能接收运行中指导")
    try:
        return enqueue_guidance(
            backend.root, user, task_id, source, session_id, None, guidance,
            allow_closed_parent=True,
        )
    except Exception as exc:
        if isinstance(exc, AgentRunError):
            raise ConflictError(str(exc)) from None
        raise


def cancel(backend, user, task_id, *, source, session_id):
    _, source, session_id, ledger, record, controls = _control_target(
        backend, user, task_id, source=source, session_id=session_id
    )
    if not controls["cancel"]:
        raise ConflictError("当前子任务不能取消")
    scheduler = find_agent_scheduler(backend.root, user)
    if scheduler is None:
        raise ConflictError("子任务执行句柄不在当前进程，无法安全取消")
    try:
        requested = scheduler.cancel(task_id, source=source, session_id=session_id)
    except Exception as exc:
        if isinstance(exc, AgentTaskNotFoundError):
            raise ConflictError("子任务执行句柄已退出或不在当前进程，未改写任务状态") from None
        raise
    if not requested:
        current = ledger.get(task_id, source, session_id) or record
        return {
            "ok": True,
            "task_id": task_id,
            "status": current["status"],
            "cancel_requested": False,
            "process_terminated": False,
        }
    queued = requested and record["status"] == "queued"
    return {
        "ok": True,
        "task_id": task_id,
        "status": "cancelled" if queued else "cancelling" if requested else record["status"],
        "cancel_requested": requested,
        "process_terminated": queued,
    }
