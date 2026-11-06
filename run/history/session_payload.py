"""Transport projection: seq and integration metadata, not round windows."""
from __future__ import annotations

import copy


def session_payload(record: dict, *, chain_default="") -> dict:
    fields = (
        "source", "session_id", "conversation_id", "title", "title_source", "summary", "summary_status",
        "summary_target_seq", "summary_completed_seq", "summary_retry_at",
        "summary_attempt_count", "summary_max_attempts", "summary_last_error",
        "memory_status", "memory_processed_seq", "memory_target_seq", "memory_queue_reason",
        "memory_queued_at", "memory_last_error", "last_seq", "integration_count", "active_tokens",
        "updated_at", "created_at", "pinned_at", "favorite", "bound_platform",
    )
    result = {key: copy.deepcopy(record[key]) for key in fields if key in record}
    result.setdefault("title", "")
    return {**result, "state": record["lifecycle"], "run_state": record["run_state"],
            "chain": record.get("chain", chain_default), "pinned": bool(record.get("pinned_at")),
            "favorite": bool(record.get("favorite")), "engine": "cardstream"}
