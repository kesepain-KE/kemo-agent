"""Idempotent per-session display marks, separate from immutable RAW events."""
from run.cards import CardStore
from run.history.index_core import _now
from run.history.registry_store import mutate_registry_record


def update_session_marks(root, user, source, session_id, *, pinned=None, favorite=None):
    def mutate(record):
        if pinned is not None:
            record["pinned_at"] = (record.get("pinned_at") or _now()) if pinned else ""
        if favorite is not None:
            record["favorite"] = bool(favorite)
        return record
    return mutate_registry_record(root, user, source, session_id, mutate)


def favorite_count(root, user):
    store = CardStore(root, user)
    if not store.path.is_file():
        return 0
    with store.db() as db:
        return db.execute("SELECT count(*) FROM card_sessions WHERE lifecycle!='deleted' "
                          "AND json_extract(record_json,'$.favorite')=1").fetchone()[0]
