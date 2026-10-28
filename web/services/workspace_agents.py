"""Authenticated read projections of durable business agent tasks."""
from __future__ import annotations

from run.agents import TaskLedger
from web.errors import InvalidRequestError, NotFoundError


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
    return {"task": {**ledger.public(record), "artifacts": artifacts}}
