"""Visible, retryable skill conversion work independent of promotion success."""
from __future__ import annotations

from run.memory.generated_skills import is_work_memory, reconcile
from run.memory.work import WorkLedger, digest
from run.memory.store import connection


def queue_conversion(store, requested):
    if requested["to_tier"] != "permanent" or not is_work_memory(
            requested["filename"] + "\n" + requested["content"]):
        return None
    entry = store.get_entry(requested["from_tier"], requested["filename"])
    if entry is None:
        return None
    key = "skill:" + digest([requested["filename"], entry["created_at"],
                             requested["content"]])
    with connection(store.root, store.user) as db:
        row = store._row_by_filename(db, requested["filename"])
    return WorkLedger(store.root, store.user).enqueue(
        "skill", key, {"promotion": requested, "content_hash": digest(requested["content"]),
                      "current_sources": [{"fragment_id": row["id"], "filename": row["filename"],
                                           "content_hash": row["content_hash"]}]})


def settle_conversion(store, job, decision=None):
    if not job:
        return True
    ledger = WorkLedger(store.root, store.user)
    state = ledger.get(job["work_key"])["payload"]
    requested = state["promotion"]
    sources = reconcile(store.root, store.user)
    # A checked source binding is authoritative, never the model's skill_created flag.
    expected_ids = {s["fragment_id"] for s in state.get("current_sources", [])}
    for name, metadata in sources.items():
        if metadata["status"] == "active" and expected_ids and expected_ids.issubset(
                {item["fragment_id"] for item in metadata["sources"]}):
            ledger.save(job["work_key"], {**state, "skill": name}, completed=True)
            return True
    if decision and decision.get("skill_applicable") is False and str(
            decision.get("skill_skipped_reason") or "").strip():
        ledger.save(job["work_key"], {**state, "not_applicable": decision["skill_skipped_reason"]},
                    completed=True)
        return True
    ledger.save(job["work_key"], state, error="skill_conversion_not_completed")
    return False


def retry_conversions(store, runner, cancel_event=None, *, limit=8):
    ledger = WorkLedger(store.root, store.user)
    errors = []
    for job in ledger.pending("skill", limit):
        if cancel_event is not None and cancel_event.is_set():
            break
        if settle_conversion(store, job):
            continue
        state = ledger.get(job["work_key"])["payload"]
        original = state["promotion"]
        with connection(store.root, store.user) as db:
            entries = [db.execute("SELECT * FROM memory_fragments WHERE id=?",
                                  (s["fragment_id"],)).fetchone()
                       for s in state.get("current_sources", [])]
        if not entries or any(e is None or e["content_hash"] != s["content_hash"]
                              for e, s in zip(entries, state["current_sources"])):
            ledger.save(job["work_key"], {**state, "cancelled": "source_changed_or_removed"},
                        completed=True)
            continue
        if any(e["tier"] != "permanent" for e in entries):
            # A failed promotion keeps owning this conversion. Its next normal
            # promotion batch handles it; don't duplicate that model work here.
            continue
        try:
            result = runner.run("self_improve", {
                "trigger": "memory_promotion", "skill_retry_only": True,
                "conversion_sources": [s["filename"] for s in state["current_sources"]],
                "promotions": [{**original, "from_tier": entries[0]["tier"],
                                "filename": entries[0]["filename"]}]}, cancel_event=cancel_event)
            decision = next((d for d in result.data.get("promotions", [])
                             if isinstance(d, dict) and d.get("filename") == entries[0]["filename"]), {})
            if not settle_conversion(store, job, decision):
                errors.append({"filename": original["filename"], "stage": "skill_conversion",
                               "message": "技能转化未完成，已保留待办供下一维护周期重试"})
        except Exception as exc:
            ledger.save(job["work_key"], state, error=type(exc).__name__)
            errors.append({"filename": original["filename"], "stage": "skill_conversion",
                           "type": type(exc).__name__, "message": str(exc)})
    return errors
