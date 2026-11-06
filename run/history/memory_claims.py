"""Durable bounded RAW-sequence claims, independent of VIEW compression."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextlib import nullcontext
import json
from uuid import uuid4

from run.cards import CardConflictError, CardStore, json_text, session_key
from run.history.index_core import _now, _timestamp
from run.history.registry_store import session_record


def maintenance_claim_active(record, now):
    for kind in ("memory", "summary"):
        if record.get(f"{kind}_status") != "processing":
            continue
        expires = _timestamp(record.get(f"{kind}_claim_expires_at"))
        claimed = _timestamp(record.get(f"{kind}_claimed_at"))
        if expires is None and claimed is not None:
            expires = claimed + timedelta(seconds=record.get(f"{kind}_claim_ttl_seconds", 600))
        if expires is None or expires > now:
            return True
    return False


def narrative_budget_end(db, key, start, target, input_tokens):
    from run.cards.models import Card
    from run.memory.evidence import narrative_record, narrative_rows
    from provider.tokenizer import count_value
    used, group_tokens, group_id, group_start = 0, 0, None, start
    for row in narrative_rows(db, key, start, target):
        item = narrative_record(Card.from_row(row))
        if item is None:
            continue
        if group_id != item["integration_id"]:
            if used and used + group_tokens > input_tokens:
                return max(start, group_start - 1)
            used += group_tokens
            group_id, group_tokens = item["integration_id"], 0
            group_start = db.execute(
                "SELECT MIN(seq) FROM context_cards WHERE session_key=? AND integration_id=?",
                (key, group_id)).fetchone()[0]
        group_tokens += count_value(item)
    if used and used + group_tokens > input_tokens:
        return max(start, group_start - 1)
    return target


def claim_job(root, user, kind, *, input_tokens=12000, statuses=None, stale_after_seconds=600,
              source=None, session_id=None, max_attempts=5):
    store = CardStore(root, user)
    if not store.path.is_file():
        return None
    now = datetime.now(timezone.utc)
    allowed = set(statuses or {"pending", "queued", "failed", "processing"})
    with store.db(write=True) as db:
        rows = db.execute("SELECT * FROM card_sessions WHERE lifecycle!='deleted' "
                          "ORDER BY updated_at,session_key").fetchall()
        for row in rows:
            if (source is not None and row["source"] != source
                    or session_id is not None and row["session_id"] != session_id):
                continue
            record = session_record(db, row)
            if record["run_state"] != "idle":
                continue
            state = str(record.get(f"{kind}_status") or "pending")
            if state not in allowed:
                continue
            if kind == "summary" and int(record.get("summary_attempt_count") or 0) >= max_attempts:
                continue
            retry_at = _timestamp(record.get(f"{kind}_retry_at"))
            if retry_at and retry_at > now:
                continue
            claimed_at = _timestamp(record.get(f"{kind}_claimed_at"))
            if record.get(f"{kind}_claim_id") and claimed_at and (
                now - claimed_at).total_seconds() < stale_after_seconds:
                continue
            start = row["memory_processed_seq"] + 1 if kind == "memory" else 1
            target = (row["last_seq"] if kind == "memory" and state == "pending" else
                      min(row["last_seq"], int(record.get(f"{kind}_target_seq") or row["last_seq"])))
            if start > target:
                continue
            end = (narrative_budget_end(db, row["session_key"], start, target, input_tokens)
                   if kind == "memory" else target)
            pending = record.get("memory_last_error") or {}
            if (kind == "memory" and isinstance(pending, dict)
                    and (pending.get("work_pending") or pending.get("capacity_blocked"))
                    and record.get("memory_claim_start_seq") == start
                    and start <= int(record.get("memory_claim_end_seq") or 0) <= target):
                # Preserve the durable work's source geometry after a budget
                # pause, even when the administrator changes the token budget.
                end = int(record["memory_claim_end_seq"])
            claim_id = f"{kind}_" + uuid4().hex
            changes = {f"{kind}_claim_id": claim_id, f"{kind}_claimed_at": _now(),
                       f"{kind}_claim_ttl_seconds": max(1, stale_after_seconds),
                       f"{kind}_claim_expires_at": (now + timedelta(seconds=max(1, stale_after_seconds))).isoformat(),
                       f"{kind}_claim_start_seq": start, f"{kind}_claim_end_seq": end,
                       f"{kind}_status": "processing"}
            if kind == "summary":
                changes[f"{kind}_claim_end_seq"] = target
                changes["summary_attempt_count"] = int(record.get("summary_attempt_count") or 0) + 1
                changes["summary_max_attempts"] = max_attempts
            metadata = {**json.loads(row["record_json"]), **changes}
            db.execute("UPDATE card_sessions SET record_json=? WHERE session_key=?",
                       (json_text(metadata), row["session_key"]))
            return {**record, **changes}
    return None


def finish_job(root, user, source, session_id, kind, *, claim_id, error=None,
               changes=None, defer=False, transaction=None, retry_delay=60):
    store = CardStore(root, user)
    with (nullcontext(transaction) if transaction is not None else store.db(write=True)) as db:
        row = db.execute("SELECT * FROM card_sessions WHERE session_key=? AND lifecycle!='deleted'",
                         (session_key(source, session_id),)).fetchone()
        if row is None:
            return None
        record = json.loads(row["record_json"])
        if record.get(f"{kind}_claim_id") != claim_id:
            return None
        expires = _timestamp(record.get(f"{kind}_claim_expires_at"))
        if not error and not defer and (expires is None or expires <= datetime.now(timezone.utc)):
            raise CardConflictError("认领已过期，拒绝提交迟到的维护结果")
        end = int(record[f"{kind}_claim_end_seq"])
        if kind == "memory" and not error and not defer:
            start = int(record[f"{kind}_claim_start_seq"])
            if row["memory_processed_seq"] != start - 1:
                raise CardConflictError("RAW memory cursor changed during extraction")
            db.execute("UPDATE card_sessions SET memory_processed_seq=? WHERE session_key=?",
                       (end, row["session_key"]))
        record.pop(f"{kind}_claim_id", None)
        changes = dict(changes or {})
        if record.get("title_source") == "manual":
            changes.pop("title", None)
            changes.pop("title_source", None)
        record.update(changes)
        queued_target = record.get(f"{kind}_target_seq")
        remaining_target = min(row["last_seq"], int(queued_target or row["last_seq"]))
        record[f"{kind}_status"] = "queued" if defer else "failed" if error else (
            ("queued" if queued_target is not None else "pending") if kind == "memory"
            and end < remaining_target else
            "pending" if kind == "memory" and end < row["last_seq"] else "completed")
        if kind == "memory" and not error and not defer and end >= remaining_target:
            record.pop("memory_target_seq", None)
        if kind == "summary" and defer:
            record["summary_attempt_count"] = max(0, int(record.get("summary_attempt_count") or 0) - 1)
        record[f"{kind}_last_error"] = error
        record[f"{kind}_retry_at"] = (
            (datetime.now(timezone.utc) + timedelta(seconds=retry_delay)).isoformat() if error or defer else "")
        if kind == "summary" and not error and not defer:
            record["summary_completed_seq"] = end
        db.execute("UPDATE card_sessions SET record_json=? WHERE session_key=?",
                   (json_text(record), row["session_key"]))
        return session_record(db, db.execute("SELECT * FROM card_sessions WHERE session_key=?",
                                             (row["session_key"],)).fetchone())


def claim_pending_memory(root, user, *, input_tokens=12000, statuses=None, stale_after_seconds=600,
                         source=None, session_id=None):
    if type(input_tokens) is not int or input_tokens < 1:
        raise ValueError("记忆正文 token 预算必须是正整数")
    return claim_job(root, user, "memory", input_tokens=input_tokens, statuses=statuses,
                     stale_after_seconds=stale_after_seconds, source=source, session_id=session_id)


def finish_memory_claim(root, user, source, session_id, *, claim_id, error=None,
                        transaction=None, **changes):
    return finish_job(root, user, source, session_id, "memory", claim_id=claim_id,
                      error=error, changes=changes, transaction=transaction)


def defer_memory_claim(root, user, source, session_id, *, claim_id, error=None,
                       transaction=None, **changes):
    return finish_job(root, user, source, session_id, "memory", claim_id=claim_id,
                      error=error, changes=changes, defer=True, transaction=transaction)
