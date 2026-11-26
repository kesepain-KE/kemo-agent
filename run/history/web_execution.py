"""Durable Web dependencies, independent of page presence and the SSE journal."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import time

from run.cards import CardStore, IntegrationStore, card_read_connection, session_key


_NO_PROGRESS_SECONDS = 6 * 3600


def integration_scope(db, key, generation):
    return db.execute(
        "SELECT rowid,integration_id,response_closed,updated_at,"
        "json_extract(record_json,'$.recovery.reason') AS recovery_reason,"
        "json_extract(record_json,'$.recovery.unknown_executions') AS unknown_executions "
        "FROM card_integrations WHERE session_key=? AND generation=? ORDER BY rowid DESC",
        (key, generation),
    ).fetchall()


def integration_marker(db, key, generation):
    row = db.execute(
        "SELECT rowid,integration_id FROM card_integrations WHERE session_key=? AND generation=? "
        "ORDER BY rowid DESC LIMIT 1", (key, generation),
    ).fetchone()
    return tuple(row) if row else None


def web_dependencies(root, user, sid, generation, integrations):
    """Read task and Shell facts without settling, publishing or signalling jobs."""
    from run.agents import FINAL_TASK_STATUSES, TaskLedger
    from run.tools import ShellInbox, required_shell_state

    ids = {row["integration_id"] for row in integrations}
    ledger = TaskLedger(root, user)
    if not ids:
        return ledger, []
    tasks = ledger.execution_records("web", sid) if ids else []
    roots = {task["id"]: task for task in tasks if not task.get("root_task_id")}
    blocks = []

    def binding(record):
        own_generation = record.get("cardstream_generation")
        if own_generation and own_generation != generation:
            return ""
        iid = record.get("cardstream_integration_id")
        if not iid:
            parent = roots.get(record.get("root_task_id"), {})
            if not record.get("attempt_id") or record["attempt_id"] == parent.get("attempt_id"):
                iid = parent.get("cardstream_integration_id")
        if iid in ids:
            return "bound" if own_generation and record.get("cardstream_integration_id") else "unknown"
        return "unknown" if iid and own_generation == generation else ""

    for task in tasks:
        if not task.get("root_task_id") or not task.get("required", True):
            continue
        owner = binding(task)
        live = task.get("execution_live") or task["status"] not in FINAL_TASK_STATUSES
        if owner and (live or not task.get("card_result_seq")):
            reason = ("owner_unknown" if owner == "unknown" or task.get("reconcile_unverified") else
                      "child_live" if live else "result_pending")
            blocks.append((reason, "child", task))
    for job in ShellInbox(root, user, "web", sid).records():
        owner = binding(job)
        reason = required_shell_state(job, expects_receipt=bool(owner))
        if reason and owner:
            blocks.append((reason if owner == "bound" else "owner_unknown", "shell", job))
    return ledger, blocks


def repair_web_receipts(ledger, sid, blocks):
    """Idempotent publication of terminal facts only; never reconcile an active Shell job."""
    from run.tools import publish_background_result

    for reason, kind, record in blocks:
        if kind == "shell" and reason == "result_pending":
            publish_background_result(ledger.root, ledger.user, record)
        elif kind == "child" and reason == "result_pending":
            ledger._append_card_result(record["id"], "web", sid)


def dependency_recovery_sessions(root, user):
    from run.agents import TaskLedger
    from run.tools import web_shell_sessions

    return TaskLedger(root, user).web_dependency_sessions() | web_shell_sessions(root, user)


def _execution_snapshot(root, user, sid):
    key = session_key("web", sid)
    with card_read_connection(root, user) as db:
        if db is None:
            return None
        row = db.execute("SELECT generation,lifecycle,last_seq FROM card_sessions "
                         "WHERE session_key=? AND lifecycle!='deleted'", (key,)).fetchone()
        if row is None:
            return None
        integrations = integration_scope(db, key, row["generation"])
        if not integrations:
            return None
        reasons = Counter({"integration_open": sum(not i["response_closed"] for i in integrations)})
        reasons["tool_live"] = db.execute(
            "SELECT count(*) FROM card_tool_executions t JOIN card_integrations i USING(integration_id) "
            "WHERE i.session_key=? AND i.generation=? AND t.execution_live=1 AND NOT "
            "(t.status='interrupted_unknown' AND "
            "coalesce(json_extract(i.record_json,'$.recovery.tools_detached'),0)=1)",
            (key, row["generation"]),
        ).fetchone()[0]
        card = db.execute("SELECT created_at FROM context_cards WHERE session_key=? AND seq=?",
                          (key, row["last_seq"])).fetchone()
    _, blocks = web_dependencies(root, user, sid, row["generation"], integrations)
    return row, integrations, reasons, blocks, card["created_at"] if card else ""


def _no_progress(now, integrations, blocks, card_at):
    progress = [card_at, *(i["updated_at"] for i in integrations)]
    for _, _, record in blocks:
        progress.extend(record.get(k) for k in ("created_at", "started_at", "finished_at"))
        progress.extend(event.get("at") for event in record.get("events", ()))
    stamps = (datetime.fromisoformat(value).timestamp() for value in progress if value)
    return now - max(stamps) >= _NO_PROGRESS_SECONDS


def reconcile_web_execution(root, user, sid, *, now):
    """Progress existing execution records without a page lease or an archive attempt."""
    from run.agents import FINAL_TASK_STATUSES, TaskLedger
    from run.infra import record_runtime_event
    from run.tools import required_shell_state, settle_required_shell_exit

    store = CardStore(root, user)
    integration = IntegrationStore(store, "web", sid, "")
    recovered = len(integration.recover_interrupted())
    snapshot = _execution_snapshot(root, user, sid)
    if not snapshot or snapshot[0]["lifecycle"] != "open":
        return recovered
    row, integrations, reasons, blocks, card_at = snapshot
    ledger = TaskLedger(root, user)
    receipts = []
    for reason, kind, record in blocks:
        if kind == "child":
            if reason != "result_pending":
                record = ledger.reconcile_execution(record)
                if record is None or record.get("execution_live"):
                    continue
                if (record["status"] in FINAL_TASK_STATUSES and not record.get("card_result_seq")
                        and record.get("cardstream_generation") == row["generation"]
                        and record.get("cardstream_integration_id") in {i["integration_id"] for i in integrations}):
                    reason = "result_pending"
        elif reason in {"required_shell", "owner_unknown"} and (
            record.get("cardstream_generation") == row["generation"]
            and record.get("cardstream_integration_id") in {i["integration_id"] for i in integrations}
        ):
            record = settle_required_shell_exit(root, user, record, now=now)
            reason = required_shell_state(record)
        if reason == "result_pending":
            receipts.append((reason, kind, record))
    repair_web_receipts(ledger, sid, receipts)
    if any(reasons.values()) or blocks:
        latest = integrations[0]
        if not latest["no_progress_reported"] and _no_progress(now, integrations, blocks, card_at):
            with store.db(write=True) as db:
                reported = db.execute(
                    "UPDATE card_integrations SET record_json=json_set(record_json,'$.no_progress_reported_at',?) "
                    "WHERE integration_id=? AND updated_at=? AND generation=? "
                    "AND json_extract(record_json,'$.no_progress_reported_at') IS NULL "
                    "AND EXISTS(SELECT 1 FROM card_sessions s WHERE s.session_key=card_integrations.session_key "
                    "AND s.lifecycle='open' AND s.generation=card_integrations.generation AND s.last_seq=?)",
                    (datetime.fromtimestamp(now, timezone.utc).isoformat(), latest["integration_id"],
                     latest["updated_at"], row["generation"], row["last_seq"]),
                ).rowcount
            if reported:
                record_runtime_event(root, user, category="backend", name="web_session_no_visible_progress",
                                     status="running", session_id=sid, error_type="NoVisibleProgress")
    return recovered


def web_execution_state(root, user, sid):
    """Small, read-only fallback when this server has no in-memory Run."""
    snapshot = _execution_snapshot(root, user, sid)
    if snapshot is None:
        return None
    _, integrations, reasons, blocks, card_at = snapshot
    reasons.update(reason for reason, _, _ in blocks)
    latest = integrations[0]
    if any(reasons.values()):
        status = ("owner_unknown" if reasons["owner_unknown"] else
                  "result_pending" if reasons["result_pending"] else "active")
        return {"status": status, "blockers": dict(+reasons),
                "no_visible_progress": _no_progress(time.time(), integrations, blocks, card_at)}
    if latest["recovery_reason"] == "owner_lost":
        return {"status": "interrupted", "unknown_tool_count": latest["unknown_executions"] or 0}
    return None
