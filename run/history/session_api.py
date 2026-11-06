"""User-facing CardStream session operations; never fabricate an old window."""
from __future__ import annotations

import json
from run.cards import CardConflictError, CardStore, json_text, session_key
from run.config import load_config
from run.history.index_core import find_record, new_conversation_id, reserve_session, _now
from run.history.registry_store import mutate_registry_record
from run.history.session_listing import query_session_records
from run.history.session_ops import close_session, update_title
from run.history.session_payload import session_payload

HistoryError = CardConflictError


def ensure_session_appendable(root, user, source, session_id):
    record = CardStore(root, user).session(source, session_id) if CardStore(root, user).path.is_file() else None
    if record and record["lifecycle"] != "open":
        raise HistoryError("对话已经结束或删除，请创建新对话")


def queue_memory_extraction(root, user, source, session_id, *, target_seq=None, reason="session_closed"):
    record = find_record(root, user, source, session_id)
    if record is None:
        return {"status": "skipped", "reason": "no_history"}
    target = min(record["last_seq"], target_seq if target_seq is not None else record["last_seq"])
    if target <= record["memory_processed_seq"]:
        return {"status": "skipped", "reason": "already_processed", "target_seq": target}
    from run.memory import memory_extraction_mode
    if memory_extraction_mode(load_config(user, root)) == "disabled":
        return {"status": "skipped", "reason": "memory_extraction_disabled"}
    def queue(current):
        current.update(memory_target_seq=max(target, int(current.get("memory_target_seq") or 0)),
                       memory_queue_reason=reason, memory_queued_at=_now(), memory_retry_at="")
        if not current.get("memory_claim_id"):
            current["memory_status"] = "queued"
        return current
    updated = mutate_registry_record(root, user, source, session_id, queue)
    return {"status": updated["memory_status"], "target_seq": updated["memory_target_seq"],
            "processed_seq": updated["memory_processed_seq"]}


def list_sessions(root, user, source=None, **filters):
    return [session_payload(record) for record in query_session_records(
        root, user, source=source, **filters)[0]]


def list_sessions_page(root, user, source=None, **filters):
    records, more = query_session_records(root, user, source=source, **filters)
    return [session_payload(record) for record in records], more


def rename_session(root, user, source, session_id, title):
    return int(update_title(root, user, source, session_id, title) is not None)


def _hide(db, key):
    CardStore.require_idle(db, key)
    db.execute("UPDATE card_sessions SET lifecycle='deleted',updated_at=? WHERE session_key=?",
               (_now(), key))
    db.execute("DELETE FROM card_bindings WHERE session_key=?", (key,))
    db.execute("DELETE FROM card_leases WHERE session_key=?", (key,))


def delete_session(root, user, source, session_id):
    store = CardStore(root, user)
    if not store.path.is_file():
        return 0
    key = session_key(source, session_id)
    with store.db(write=True) as db:
        if not db.execute("SELECT 1 FROM card_sessions WHERE session_key=? AND lifecycle!='deleted'",
                          (key,)).fetchone():
            return 0
        _hide(db, key)
    return 1


def delete_all_sessions(root, user, source=None, *, client_id=""):
    import time
    store = CardStore(root, user)
    if not store.path.is_file():
        return 0, 0
    with store.db(write=True) as db:
        sql, args = "SELECT session_key FROM card_sessions WHERE lifecycle!='deleted'", []
        if source is not None:
            sql += " AND source=?"
            args.append(source)
        keys = [row[0] for row in db.execute(sql, args)]
        for key in keys:
            others = db.execute("SELECT 1 FROM card_leases WHERE session_key=? AND released=0 "
                                "AND expires_at>? AND client_id!=?", (key, time.time(), client_id)).fetchone()
            if others:
                raise HistoryError("仍有其他页面占用会话，未删除任何会话")
            CardStore.require_idle(db, key)
        for key in keys:
            _hide(db, key)
        # No physical historical records or old user files are removed.
        return len(keys), 0


def clear_session(root, user, source, session_id):
    """End this identity and create a fresh one; RAW is never erased/reused."""
    close_session(root, user, source, session_id)
    identity = new_conversation_id()
    active_key = f"{source}:{user}" if source == "cli" else None
    reserve_session(root, user, source, identity, active_key=active_key)
    return identity


def session_messages(root, user, source, session_id, *, limit=500):
    store = CardStore(root, user)
    if not store.path.is_file():
        return []
    return [{"role": "user" if card.raw_kind == "user_text" else "assistant",
             "content": card.raw_payload.get("text", ""), "seq": card.seq,
             "integration_id": card.integration_id, "level": card.raw_level}
            for card in store.raw_page(source, session_id, limit=limit)
            if card.raw_kind in {"user_text", "agent_text"}]


def session_statistics(root, user, source, session_id):
    from run.infra import merge_usage_dict, new_usage_total
    store = CardStore(root, user)
    if not store.path.is_file():
        return {"integration_count": 0, "tool_calls": 0, "usage": new_usage_total()}
    key, usage = session_key(source, session_id), new_usage_total()
    with store.db() as db:
        integrations = db.execute("SELECT record_json FROM card_integrations WHERE session_key=?",
                                  (key,)).fetchall()
        for row in integrations:
            merge_usage_dict(usage, json.loads(row[0]).get("usage", {}))
        calls = db.execute("SELECT count(*) FROM card_tool_executions t JOIN card_integrations i "
                           "USING(integration_id) WHERE i.session_key=?", (key,)).fetchone()[0]
    return {"integration_count": len(integrations), "tool_calls": calls, "usage": usage}


def integration_usage_rows(root, user):
    """Aggregates retain their integration timestamp; callers mark daily estimates."""
    store = CardStore(root, user)
    if not store.path.is_file():
        return
    with store.db() as db:
        for row in db.execute("SELECT i.updated_at,i.record_json FROM card_integrations i "
                              "JOIN card_sessions s USING(session_key) WHERE s.lifecycle!='deleted'"):
            yield {"occurred_at": row["updated_at"], "usage": json.loads(row["record_json"]).get("usage", {})}


def withdraw_last_integration(root, user, source, session_id, *, expected_integration_count, expected_prompt):
    """UI resend withdraws VIEW only; the original integration stays in RAW."""
    store = CardStore(root, user)
    key = session_key(source, session_id)
    with store.db(write=True) as db:
        CardStore.require_idle(db, key)
        state = db.execute("SELECT * FROM card_sessions WHERE session_key=? AND lifecycle='open'",
                           (key,)).fetchone()
        integrations = db.execute("SELECT * FROM card_integrations WHERE session_key=? "
                                  "ORDER BY created_at,integration_id", (key,)).fetchall()
        if state is None or not integrations or expected_integration_count != len(integrations):
            raise HistoryError("整合列表已改变，请刷新后重试")
        last = next(
            (row for row in reversed(integrations)
             if not json.loads(row["record_json"]).get("withdrawn")),
            None,
        )
        if last is None:
            raise HistoryError("该整合已经撤回，不能重复撤回")
        first = db.execute("SELECT raw_payload FROM context_cards WHERE integration_id=? "
                           "AND raw_kind='user_text' AND raw_level='initial' ORDER BY seq LIMIT 1",
                           (last["integration_id"],)).fetchone()
        if first is None or json.loads(first[0]).get("text") != expected_prompt:
            raise HistoryError("原始输入不匹配，不修改前台")
        # A separate suffix can be withdrawn without changing the earlier summary.
        if db.execute("SELECT 1 FROM context_cards WHERE session_key=? AND integration_id=? "
                      "AND (view_kind='summary' OR absorbed_by IS NOT NULL) LIMIT 1",
                      (key, last["integration_id"])).fetchone():
            raise HistoryError("该轮内容已合并进压缩摘要，无法单独撤回；可发送新消息修正")
        tokens = db.execute("SELECT coalesce(sum(view_tokens),0) FROM context_cards "
                            "WHERE integration_id=? AND token_active=1", (last["integration_id"],)).fetchone()[0]
        db.execute("UPDATE context_cards SET token_active=0,view_revision=view_revision+1 "
                   "WHERE integration_id=? AND token_active=1", (last["integration_id"],))
        db.execute("UPDATE card_sessions SET active_tokens=active_tokens-?,view_epoch=view_epoch+1,updated_at=? "
                   "WHERE session_key=?", (tokens, _now(), key))
        record = {**json.loads(last["record_json"]), "withdrawn": True}
        db.execute("UPDATE card_integrations SET record_json=? WHERE integration_id=?",
                   (json_text(record), last["integration_id"]))
        return {"withdrawn": True, "integration_id": last["integration_id"],
                "raw_preserved": True, "integration_count": len(integrations),
                "prompt": expected_prompt, "content": json.loads(first[0]).get("content") or []}
