"""Canonical user-facing projection for one history session record.

The durable registry is shared by the Web, App, CLI and background channels.
Keeping this projection in the history domain prevents each transport service
from inventing a slightly different lifecycle/memory/summary payload.
"""

from __future__ import annotations

import copy
from typing import Any


def _nonnegative_int(value: Any, *, minimum: int = 0) -> int:
    """Normalize legacy/malformed numeric metadata without breaking reads.

    Archive metadata predates the SQLite registry and is user/workspace data;
    a partially-written or hand-edited value must not turn a history listing
    into a 500 response.  Callers can request a higher lower-bound for fields
    such as ``summary_max_attempts``.
    """

    try:
        return max(minimum, int(value or 0))
    except (TypeError, ValueError):
        return minimum


def session_payload(
    record: dict[str, Any],
    *,
    chain_default: str = "",
) -> dict[str, Any]:
    """Return the stable API projection for a registry session record.

    ``chain_default`` preserves the historical distinction between the raw
    history API (empty when older records lack the field) and the Web service
    (``interactive`` for newly-created sessions).  The field itself is still
    read from the record whenever present.
    """

    return {
        "source": str(record.get("source") or ""),
        "bound_platform": str(record.get("bound_platform") or ""),
        "session_id": str(record.get("session_id") or ""),
        "conversation_id": str(record.get("conversation_id") or ""),
        "window": str(record.get("archive_window") or ""),
        "title": str(record.get("title") or ""),
        "summary": str(record.get("summary") or ""),
        "summary_status": str(record.get("summary_status") or "none"),
        "summary_target_round": _nonnegative_int(record.get("summary_target_round")),
        "summary_completed_round": _nonnegative_int(record.get("summary_completed_round")),
        "summary_retry_at": str(record.get("summary_retry_at") or ""),
        "summary_retry_count": _nonnegative_int(record.get("summary_retry_count")),
        "summary_attempt_count": _nonnegative_int(record.get("summary_attempt_count")),
        "summary_consecutive_failures": _nonnegative_int(
            record.get("summary_consecutive_failures")
        ),
        "summary_max_attempts": max(
            1, _nonnegative_int(record.get("summary_max_attempts")) or 5
        ),
        "summary_last_attempt_at": str(record.get("summary_last_attempt_at") or ""),
        "summary_recovered_at": str(record.get("summary_recovered_at") or ""),
        "summary_last_error": copy.deepcopy(
            record.get("summary_last_error")
            if isinstance(record.get("summary_last_error"), dict)
            else None
        ),
        "summary_checkpoint_next_chunk": max(
            0, int(record.get("summary_checkpoint_next_chunk") or 0)
        ),
        "summary_checkpoint_total_chunks": max(
            0, int(record.get("summary_checkpoint_total_chunks") or 0)
        ),
        "state": str(record.get("lifecycle") or "open"),
        "run_state": str(record.get("run_state") or "idle"),
        "chain": str(record.get("chain") or chain_default),
        "memory_status": str(record.get("memory_status") or "unknown"),
        "memory_processed_round": _nonnegative_int(record.get("memory_processed_round")),
        "memory_target_round": _nonnegative_int(record.get("memory_target_round")),
        "memory_queue_reason": str(record.get("memory_queue_reason") or ""),
        "memory_queued_at": str(record.get("memory_queued_at") or ""),
        "memory_last_error": copy.deepcopy(
            record.get("memory_last_error")
            if isinstance(record.get("memory_last_error"), dict)
            else None
        ),
        "rounds": _nonnegative_int(record.get("rounds")),
        "updated_at": str(record.get("updated_at") or ""),
    }
