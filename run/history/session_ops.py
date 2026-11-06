"""Active bindings, explicit lifecycle and non-context title/summary jobs."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

from run.cards import CardStore, session_key
from run.history.index_core import _now, find_record
from run.history.memory_claims import claim_job, finish_job
from run.history.registry_store import mutate_registry_record, read_active_binding, upsert_registry_record
from run.history.session_listing import query_session_records


def set_active(root, user, active_key, session_id, *, source):
    record = find_record(root, user, source, session_id)
    if record is None:
        raise KeyError(session_id)
    return upsert_registry_record(root, user, record,
        active_updates={active_key: {"source": source, "session_id": session_id}})


def get_active(root, user, active_key):
    return read_active_binding(root, user, active_key)


def close_session(root, user, source, session_id):
    return mutate_registry_record(root, user, source, session_id,
        lambda record: {**record, "lifecycle": "closed"}, updated_at=_now(), clear_active=True)


def reopen_session(root, user, source, session_id):
    return mutate_registry_record(root, user, source, session_id,
        lambda record: {**record, "lifecycle": "open", "reopened_at": _now()}, updated_at=_now())


def update_title(root, user, source, session_id, title):
    return mutate_registry_record(root, user, source, session_id,
                                  lambda record: {**record, "title": title, "title_source": "manual"})


def queue_summary(root, user, source, session_id, **_):
    def queue(record):
        if not record["last_seq"]:
            return None
        if record.get("summary_claim_id") or (
            record.get("summary_status") == "completed"
            and record.get("summary_completed_seq", 0) >= record["last_seq"]):
            return None
        return {**record, "summary_status": "queued", "summary_target_seq": record["last_seq"],
                "summary_retry_at": "", "summary_last_error": None}
    result = mutate_registry_record(root, user, source, session_id, queue)
    return {**result, "status": result.get("summary_status", "none")} if result else None


def retry_summary(root, user, source, session_id):
    record = find_record(root, user, source, session_id)
    if record is None or not record["last_seq"] or record.get("summary_claim_id"):
        return None
    mutate_registry_record(root, user, source, session_id,
                           lambda current: {**current, "summary_attempt_count": 0, "summary_status": "queued"})
    return queue_summary(root, user, source, session_id)


def claim_pending_summary(root, user, **kwargs):
    return claim_job(root, user, "summary", statuses={"queued", "processing", "failed"}, **kwargs)


def finish_summary_claim(root, user, source, session_id, *, claim_id, error=None,
                         title=None, summary=None, retry_delay=60, **changes):
    if title is not None:
        changes["title"] = title
        changes["title_source"] = "summary"
    if summary is not None:
        changes["summary"] = summary
    return finish_job(root, user, source, session_id, "summary", claim_id=claim_id,
                      error=error, changes=changes, retry_delay=retry_delay)


def defer_summary_claim(root, user, source, session_id, *, claim_id, error=None, **changes):
    return finish_job(root, user, source, session_id, "summary", claim_id=claim_id,
                      error=error, changes=changes, defer=True)


def update_summary_checkpoint(root, user, source, session_id, *, claim_id, **changes):
    def mutate(record):
        if record.get("summary_claim_id") != claim_id:
            return None
        expires = datetime.now(timezone.utc) + timedelta(seconds=record.get("summary_claim_ttl_seconds", 600))
        return {**record, **changes, "summary_claimed_at": _now(), "summary_claim_expires_at": expires.isoformat()}
    return mutate_registry_record(root, user, source, session_id, mutate)


def list_records(root, user, *, source=None, limit=50, **filters):
    return query_session_records(root, user, source=source, limit=limit, **filters)[0]


def list_records_page(root, user, *, source=None, limit=50, **filters):
    return query_session_records(root, user, source=source, limit=limit, **filters)
