"""Independent bounded log view; never runs the full runtime-status aggregator."""
from __future__ import annotations

from datetime import datetime, timezone
import threading
from typing import Any

from run.infra import (
    CACHE_TTL_SECONDS, LogStore, cached_runtime_log_records, runtime_event_snapshot,
)
from web.constants import _BEIJING

CATEGORIES = ("all", "backend", "threads", "terminal", "message")
TERMINAL_LIVE_WINDOW = 300


def runtime_logs(backend: Any, user: str, *, category: str = "all", page: int = 1,
                 page_size: int = 25, refresh: bool = False) -> dict[str, Any]:
    user = backend.require_user(user)  # Validate scope even when the cache is warm.
    if category not in CATEGORIES or page < 1 or not 1 <= page_size <= 100:
        raise ValueError("无效的日志分类或分页参数")
    errors: list[str] = []
    hit = False
    try:
        rows, hit = cached_runtime_log_records(
            backend.root, user, lambda: LogStore(backend.root).runtime_log_records(user), refresh=refresh,
        )
    except Exception:
        rows = []
        errors.append("持久化日志暂时不可用，请刷新重试。")
    rows.extend(runtime_event_snapshot(backend.root, user))
    now = datetime.now(timezone.utc).isoformat()
    # Do not expose thread names, stacks or locals: they can contain another user's data.
    threads = sorted(threading.enumerate(), key=lambda thread: thread.ident or 0)
    for thread in threads[:200]:
        rows.append({
            "id": f"thread:{thread.ident}", "category": "threads",
            "title": ("主线程" if thread is threading.main_thread() else "工作线程") + f" #{thread.ident}",
            "occurred_at": now, "status": "running" if thread.is_alive() else "stopped",
            "duration_ms": None, "source": "snapshot",
            "detail": "当前进程线程快照 · " + ("守护线程" if thread.daemon else "非守护线程"),
        })

    def order(row: dict[str, Any]) -> tuple[float, str]:
        try:
            parsed = datetime.fromisoformat(str(row["occurred_at"]).replace("Z", "+00:00"))
            stamp = parsed.replace(tzinfo=_BEIJING).timestamp() if parsed.tzinfo is None else parsed.timestamp()
        except (ValueError, TypeError, OverflowError, OSError):
            stamp = 0.0
        return stamp, row["id"]

    counts = {key: len(rows) if key == "all" else sum(row["category"] == key for row in rows)
              for key in CATEGORIES}
    if category == "terminal":
        # A terminal is a chronological, append-only view: the in-memory
        # snapshot already preserves arrival order, and timestamps tie at low
        # clock resolution, so keep arrival order instead of re-sorting (which
        # would shuffle same-instant lines).  Keep a bounded tail so the
        # browser never re-renders the entire in-memory buffer every 2s.
        selected = [row for row in rows if row["category"] == "terminal"]
        total = len(selected)
        selected = selected[-TERMINAL_LIVE_WINDOW:]
        displayed = len(selected)
        return {
            "user": user, "category": category, "entries": selected,
            "counts": counts, "generated_at": now,
            "pagination": {"page": 1, "page_size": max(1, displayed), "total_items": total,
                           "total_pages": 1, "has_previous": False, "has_next": False},
            "cache": {"hit": hit, "ttl_seconds": CACHE_TTL_SECONDS}, "source_errors": errors,
        }
    selected = sorted(
        (row for row in rows if category == "all" or row["category"] == category),
        key=order,
        reverse=True,
    )
    total = len(selected)
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, pages)
    return {
        "user": user, "category": category, "entries": selected[(page - 1) * page_size:page * page_size],
        "counts": counts, "generated_at": now,
        "pagination": {"page": page, "page_size": page_size, "total_items": total,
                       "total_pages": pages, "has_previous": page > 1, "has_next": page < pages},
        "cache": {"hit": hit, "ttl_seconds": CACHE_TTL_SECONDS}, "source_errors": errors,
    }
