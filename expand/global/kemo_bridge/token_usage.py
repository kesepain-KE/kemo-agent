"""Read-only aggregates over the framework's existing CardStream metadata."""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from artifact_store import is_link
from peer_store import BridgeError

LOCAL_ZONE = ZoneInfo("Asia/Shanghai")
ZERO = {"input": 0, "output": 0, "cached": 0, "requests": 0}
QUERY = """
    SELECT i.updated_at, s.source, s.session_id,
           coalesce(json_extract(m.value_json,'$.usage.input_tokens'),
                    json_extract(m.value_json,'$.usage.prompt_tokens'), 0),
           coalesce(json_extract(m.value_json,'$.usage.output_tokens'),
                    json_extract(m.value_json,'$.usage.completion_tokens'), 0),
           coalesce(json_extract(m.value_json,'$.usage.cached_input_tokens'),
                    json_extract(m.value_json,'$.usage.cached_prompt_tokens'),
                    json_extract(m.value_json,'$.usage.cache_hit_tokens'),
                    json_extract(m.value_json,'$.usage.cached_tokens'), 0),
           coalesce(json_extract(m.value_json,'$.usage.provider_request_count'), 0)
    FROM card_integrations i
    JOIN card_integration_metadata m USING(integration_id)
    JOIN card_sessions s USING(session_key)
    WHERE s.lifecycle != 'deleted' AND i.updated_at >= ? AND i.updated_at < ?
      AND json_type(m.value_json,'$.usage') = 'object'
"""


def _start(day: date) -> str:
    return datetime.combine(day, time.min, LOCAL_ZONE).astimezone(timezone.utc).isoformat()


def summarize(user_dir: Path, since: date | None, until: date | None, group: str) -> dict:
    today = datetime.now(LOCAL_ZONE).date()
    first, last = since or until or today, until or today
    if first > last or last == date.max:
        raise BridgeError(400, "invalid_date_range")
    db_path = user_dir / "history" / "cards.sqlite3"
    if is_link(db_path.parent) or is_link(db_path):
        raise BridgeError(409, "usage_storage_unavailable")
    total = ZERO.copy()
    series: dict[str, dict] = defaultdict(ZERO.copy)
    if db_path.is_file():
        try:
            with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True, timeout=2.5)) as db:
                db.execute("PRAGMA query_only=ON")
                for when, source, session, *values in db.execute(QUERY, (_start(first), _start(last + timedelta(days=1)))):
                    stamp = datetime.fromisoformat(when).astimezone(LOCAL_ZONE).date()
                    if not first <= stamp <= last:
                        continue
                    counts = {key: max(0, value) if type(value) is int else 0
                              for key, value in zip(ZERO, values)}
                    if not any(counts.values()):
                        continue
                    bucket = (stamp.isoformat() if group == "day" else
                              f"{source}:{session}" if group == "session" else "unknown")
                    for key, count in counts.items():
                        total[key] += count
                        series[bucket][key] += count
        except (sqlite3.Error, ValueError, OverflowError) as exc:
            raise BridgeError(503, "usage_storage_unavailable") from exc
    # CardStream persists per-integration totals, not a stable model dimension
    # or a daily token limit. Never attribute old usage to today's model.
    by_model = [{"model": "unknown", "input": total["input"], "output": total["output"]}] if series else []
    return {"total": total, "series": [{"bucket": key, **value} for key, value in sorted(series.items())],
            "by_model": by_model, "budget": {"daily_limit": None, "used_ratio": 0.0}}
