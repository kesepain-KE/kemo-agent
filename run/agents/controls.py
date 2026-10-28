"""Explicit main-agent controls and small read projections, never tool traces."""
from __future__ import annotations

import math
import uuid

from agents._runtime.resources import build_agent_tool_registry, effective_knowledge_scopes
from provider.protocol.enums import MessageRole
from provider.protocol.models import MessageItem
from run.agents.budgets import effective_limit, overridable, BUDGET_STOPS
from run.agents.contracts import AgentRunError
from run.agents.task_ledger import TaskLedger, now


def describe_agent(root, user, definition, config):
    return {
        "default_timeout": definition.timeout,
        "max_iterations": effective_limit(definition, config),
        "supports_budget_override": overridable(definition),
        "supports_resume": overridable(definition),
        "authorized_plugins": sorted(build_agent_tool_registry(root, user, definition, config).tools),
        "knowledge_scopes": list(effective_knowledge_scopes(definition, config)),
    }


def _owned_record(ledger, db, task_id, source, session_id, ownership, *, active=False):
    record = ledger.decode(db.execute(
        "SELECT * FROM agent_tasks WHERE id=? AND source=? AND session=?",
        (task_id, source, session_id)).fetchone())
    if not record or not isinstance(ownership, dict):
        raise AgentRunError("任务不存在或当前主任务无控制权限")
    parent = ledger.decode(db.execute("SELECT * FROM agent_tasks WHERE id=?",
                                     (ownership.get("root_task_id"),)).fetchone())
    if (not parent or parent["source"] != source or parent["session_id"] != session_id
            or parent["status"] not in {"running", "merging"}
            or parent["attempt_id"] != ownership.get("attempt_id")):
        raise AgentRunError("当前主任务归属无效")
    if record.get("root_task_id") != parent["id"]:
        old = ledger.decode(db.execute("SELECT * FROM agent_tasks WHERE id=?",
                                      (record.get("root_task_id"),)).fetchone())
        if active or (old and old["status"] not in {"completed", "failed", "cancelled", "interrupted"}):
            raise AgentRunError("不能控制其他活动主任务的子任务")
    return record


def guide_task(root, user, task_id, source, session_id, ownership, guidance):
    if not isinstance(guidance, str) or not guidance.strip() or len(guidance) > 8000:
        raise AgentRunError("运行中指导必须为 1..8000 字符的非空文本")
    ledger = TaskLedger(root, user)
    with ledger.db(write=True) as db:
        record = _owned_record(ledger, db, task_id, source, session_id, ownership, active=True)
        from run.agents import discover_agents
        if not overridable(discover_agents(root, user).get(record["agent"])):
            raise AgentRunError("此子代理不支持运行中指导")
        if record["status"] not in {"queued", "running"}:
            raise AgentRunError("仅能引导排队或运行中的子任务；停止任务请使用 resume")
        mailbox = list(record.get("_guidance") or [])
        if len(mailbox) >= 8:
            raise AgentRunError("未处理的指导已达上限，请等待安全点处理")
        mailbox.append(guidance)
        record.update(_guidance=mailbox, updated_at=now())
        ledger.put(db, record)
    return {"task_id": task_id, "status": "guidance_queued", "pending": len(mailbox),
            "message": "指导将在下一请求安全点交付，不扩权、不启动另一个执行器。"}


def take_guidance(context, items):
    if not context.task_id or not context.source or not context.session_id:
        return
    ledger = TaskLedger(context.runner.root, context.runner.user)
    if not ledger.path.exists():
        return
    record = ledger.get(context.task_id, context.source, context.session_id)
    if not record or not record.get("_guidance"):
        return
    with ledger.db(write=True) as db:
        record = ledger.decode(db.execute("SELECT * FROM agent_tasks WHERE id=?",
                                         (context.task_id,)).fetchone())
        if (not record or record.get("run_count", 1) != context.continuation.run_count
                or record["status"] != "running"):
            return
        messages = record.pop("_guidance", [])
        ledger.put(db, record)
    for text in messages:
        items.append(MessageItem.text(MessageRole.USER,
            "[主智能体运行中指导；不改变当前权限与用户授权]\n" + text,
            item_id=f"msg_guidance_{uuid.uuid4().hex}"))


def split_task(scheduler, task_id, *, source, session_id, ownership=None, tasks,
               timeout=None, max_tool_calls=None, config=None, event_callback=None):
    from run.agents.contracts import validate_json_schema
    if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                                or not math.isfinite(timeout) or timeout <= 0):
        raise AgentRunError("split timeout 必须是正数")
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 8:
        raise AgentRunError("split 需要 1..8 个显式子任务输入")
    ledger = TaskLedger(scheduler.runner.root, scheduler.runner.user)
    with ledger.db(write=True) as db:
        record = _owned_record(ledger, db, task_id, source, session_id, ownership)
        if record["status"] not in BUDGET_STOPS | {"failed"} or record.get("execution_live"):
            raise AgentRunError("只能拆分实际已退出的预算停止或失败任务")
    definition = scheduler.runner.refresh_registry().get(record["agent"])
    if not overridable(definition):
        raise AgentRunError("此代理不支持业务任务拆分")
    effective_limit(definition, config or scheduler.runner.config, max_tool_calls)
    for payload in tasks:
        if not isinstance(payload, dict):
            raise AgentRunError("split 每项必须是完整的子代理 input 对象")
        validate_json_schema(payload, definition.input_schema)
    accepted = []
    for payload in tasks:
        try:
            accepted.append(scheduler.submit(
                record["agent"], payload, source=source, session_id=session_id,
                ownership=ownership, allow_sync=True, timeout=timeout,
                max_tool_calls=max_tool_calls, config=config,
                event_callback=event_callback, origin_task_id=task_id))
        except Exception as exc:
            from provider.protocol.diagnostics import safe_provider_message
            return {"status": "partially_queued" if accepted else "rejected", "origin_task_id": task_id,
                    "ok": False,
                    "task_ids": accepted, "accepted_count": len(accepted),
                    "error": safe_provider_message(str(exc), "拆分入队失败"),
                    "message": "已接受任务不会回滚或重新执行；只补交尚未接受的输入。"}
    return {"status": "queued", "origin_task_id": task_id, "task_ids": accepted,
            "accepted_count": len(accepted)}


def failure_advice(records):
    groups = {}
    for record in records:
        reason = record.get("stop_reason") or {"tool_limit": "tool_budget_exhausted", "timed_out": "timeout"}.get(record["status"])
        if reason in {"tool_budget_exhausted", "timeout"}:
            groups.setdefault(reason, []).append(record["id"])
    return [{"stop_reason": reason, "count": len(ids), "task_ids": ids[:8],
             "recommendation": "多个子任务因同类预算停止；可显式 resume 调整额度/时长，或 split 拆分剩余工作。不会自动重派。"}
            for reason, ids in groups.items() if len(ids) >= 2]


def background_receipt(root, user, source, session_id, task_id):
    from run.tools import ShellInbox
    inbox = ShellInbox(root, user, source, session_id, owner_task_id=task_id)
    records = [r for r in inbox.records() if r.get("owner_task_id") == task_id]
    return {"jobs": [{k: r.get(k) for k in ("job_id", "status", "completion_mode", "finished_at", "exit_code")}
                     for r in records[-8:]], "total": len(records),
            "policy": "保留真实归属；停止后主智能体接管收集，可用 shell status/cancel 显式核对或取消。续跑先核对，勿重复启动。"}
