"""Cron list retention hides terminal sessions; RAW and old files stay intact."""
from datetime import datetime, timedelta, timezone
from run.cards import CardConflictError, CardStore, session_key
from run.history.index_core import _timestamp
from run.history.memory_claims import maintenance_claim_active
from run.history.registry_store import session_record
from run.history.session_api import _hide
from run.history.session_ops import list_records


def cleanup_cron_history(root, user, *, retention_days=7, now=None, limit=100):
    if type(retention_days) is not int or retention_days < 0:
        raise ValueError("retention_days must be a non-negative integer")
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")
    result = {"deleted_sessions": 0, "skipped_busy": 0, "raw_preserved": True}
    if retention_days == 0:
        return result
    current = now if isinstance(now, datetime) else datetime.now(timezone.utc)
    current = current.replace(tzinfo=timezone.utc) if current.tzinfo is None else current
    cutoff, store = current - timedelta(days=retention_days), CardStore(root, user)
    from run.conversation import session_lock
    for record in list_records(root, user, limit=None):
        source = record["source"]
        finished = _timestamp(record.get("cron_finished_at", record["updated_at"]))
        if (not source.startswith("background:cron:") or not source.removeprefix("background:cron:")
                or record["lifecycle"] != "closed" or finished is None or finished > cutoff):
            continue
        lock = session_lock(root, user, source, record["session_id"])
        if not lock.acquire(blocking=False):
            result["skipped_busy"] += 1
            continue
        try:
            with store.db(write=True) as db:
                key = session_key(source, record["session_id"])
                row = db.execute("SELECT * FROM card_sessions WHERE session_key=?", (key,)).fetchone()
                fresh = session_record(db, row)
                finished = _timestamp(fresh.get("cron_finished_at", fresh["updated_at"]))
                if row["lifecycle"] != "closed" or finished is None or finished > cutoff:
                    continue
                if fresh["run_state"] != "idle" or maintenance_claim_active(fresh, current):
                    result["skipped_busy"] += 1
                    continue
                _hide(db, key)
                result["deleted_sessions"] += 1
        except CardConflictError:
            result["skipped_busy"] += 1
        finally:
            lock.release()
        if result["deleted_sessions"] >= limit:
            break
    return result
