"""运行状态、用量统计与后台调度聚合。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from web.service import WebRunService

from pathlib import Path

from datetime import datetime, timezone
from typing import Any

from run.context import context_status
from run.cards import CardStore
from run.scheduler import CronStore
from run.history import find_record, integration_usage_rows
from run.infra import LogStore
from web.constants import _BEIJING


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _usage_cache_tokens(usage: dict[str, Any]) -> int:
    # Prefer the normalized cumulative field written by the runtime.  This
    # keeps today's totals correct for both unified Provider usage and legacy
    # records, and preserves an explicitly reported zero.
    for key in (
        "cached_input_tokens",
        "cached_prompt_tokens",
        "cache_hit_tokens",
        "cached_tokens",
        "cache_read_input_tokens",
        "prompt_cache_hit_tokens",
    ):
        if usage.get(key) is not None:
            return _nonnegative_int(usage.get(key))
    raw = usage.get("provider_raw")
    values = raw if isinstance(raw, list) else [raw]
    total = 0
    for item in values:
        if not isinstance(item, dict):
            continue
        direct = next(
            (
                item.get(key)
                for key in (
                    "prompt_cache_hit_tokens",
                    "cached_tokens",
                    "cached_prompt_tokens",
                    "cached_input_tokens",
                    "cache_hit_tokens",
                    "cache_read_input_tokens",
                )
                if item.get(key) is not None
            ),
            None,
        )
        details = item.get("prompt_tokens_details")
        nested = details.get("cached_tokens") if isinstance(details, dict) else None
        total += _nonnegative_int(direct if direct is not None else nested)
    if total:
        return total
    details = usage.get("prompt_tokens_details")
    return (
        _nonnegative_int(details.get("cached_tokens"))
        if isinstance(details, dict)
        else 0
    )


def _provider_response_time(response: dict[str, Any]) -> datetime | None:
    direct = _parse_datetime(response.get("created_at"))
    if direct is not None:
        return direct
    timestamps = [
        parsed
        for item in response.get("output") or []
        if isinstance(item, dict)
        and (parsed := _parse_datetime(item.get("created_at"))) is not None
    ]
    return max(timestamps, default=None)


class RuntimeStatusServiceMixin:
    if TYPE_CHECKING:
        _cron_summary = WebRunService._cron_summary
        root: Path

    def runtime_logs(self, user: str, *, category: str = "all", page: int = 1,
                     page_size: int = 25, refresh: bool = False) -> dict[str, Any]:
        from web.services.runtime_logs import runtime_logs
        return runtime_logs(self, user, category=category, page=page,
                            page_size=page_size, refresh=refresh)

    def _current_context_status(self, user, session_id, *, config, token_limit,
                                configured_ratio=.3, source="web", prompt_bundle=None):
        status = context_status({"user": user, "source": source,
                                 "session_id": session_id or "__new_session__"}, root=self.root,
                                prompt_bundle=prompt_bundle)
        value = status["context"]
        record = find_record(self.root, user, source, session_id) if session_id else None
        store = CardStore(self.root, user)
        history = store.snapshot(source, session_id) if record else None
        active_integrations = len({card.integration_id for card in history.cards
                                   if card.view_kind != "summary"}) if history else 0
        summary_tokens = sum(card.view_tokens for card in history.cards
                             if card.view_kind == "summary") if history else 0
        used, capacity = value["estimated_tokens_before"], value["effective_token_limit"]
        snapshot = {
            "available": True, "source": "cardstream", "measurement": "local_estimate",
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "system_prompt_tokens": value["fixed_tokens"], "tool_schema_tokens": value["schema_tokens"],
            "conversation_tokens": value["history_tokens"] - summary_tokens,
            "summary_tokens": summary_tokens, "other_tokens": value["dynamic_tokens"],
            "total_tokens": used, "capacity_tokens": capacity,
            "percent": round(100 * used / capacity, 4), "cards_active": value["cards_active"],
            "last_seq": value["last_seq"], "view_epoch": value["view_epoch"],
            "active_integrations": active_integrations,
        }
        return {"selected": bool(session_id), "available": not session_id or bool(record),
                "engine": "cardstream", "used_tokens": used, "max_tokens": capacity,
                "percent": snapshot["percent"], "active_integrations": active_integrations,
                "integration_count": record["integration_count"] if record else 0,
                "cards_active": value["cards_active"], "last_seq": value["last_seq"],
                "compression_threshold": value["compression_threshold"], "source": "cardstream",
                "view_epoch": value["view_epoch"], "context_snapshot": snapshot}

    def _today_token_statistics(self, user, *, now):
        today = now.astimezone(_BEIJING).date()
        sent = received = cached = requests = 0
        trend = [0] * 24
        estimated = False
        for row in integration_usage_rows(self.root, user):
            stamp = _parse_datetime(row["occurred_at"])
            if stamp is None or stamp.astimezone(_BEIJING).date() != today:
                continue
            usage = row["usage"]
            if not isinstance(usage, dict) or not usage:
                continue
            incoming = _nonnegative_int(usage.get("input_tokens") or usage.get("prompt_tokens"))
            outgoing = _nonnegative_int(usage.get("output_tokens") or usage.get("completion_tokens"))
            sent += incoming
            received += outgoing
            cached += _usage_cache_tokens(usage)
            requests += _nonnegative_int(usage.get("provider_request_count"))
            trend[stamp.astimezone(_BEIJING).hour] += incoming + outgoing
            estimated = True  # Integration aggregate timestamp, not fabricated per-request timing.
        return {"date": today.isoformat(), "timezone": "Asia/Shanghai", "sent_tokens": sent,
                "received_tokens": received, "total_tokens": sent + received, "cached_tokens": cached,
                "cache_rate": round(cached * 100 / sent, 2) if sent else 0.0,
                "request_count": requests, "estimated": estimated, "trend": trend}

    def _system_cron_status(self, user: str, *, now: datetime) -> dict[str, Any]:
        tasks = [
            self._cron_summary(item)
            for item in CronStore(self.root, "__system__", system=True).list_tasks()
        ]
        task_titles = {item["task_id"]: item["title"] for item in tasks}
        executions: list[dict[str, Any]] = []
        log_available = True
        try:
            structured = LogStore(self.root).list_cron(user, limit=1000)
        except Exception:
            structured = []
            log_available = False
        for item in structured:
            task_id = str(item.get("task_id") or "")
            executions.append(
                {
                    **item,
                    "title": task_titles.get(task_id, task_id),
                }
            )
        executions.sort(key=lambda item: item["executed_at"], reverse=True)
        if not executions:
            for task in tasks:
                if not task.get("latest_run_at"):
                    continue
                executions.append(
                    {
                        "id": f"{task['task_id']}:{task['latest_run_at']}",
                        "task_id": task["task_id"],
                        "title": task["title"],
                        "executed_at": task["latest_run_at"],
                        "status": "recorded",
                        "duration_ms": 0,
                        "result": {},
                        "error": None,
                        "source": "task_state",
                    }
                )
            executions.sort(key=lambda item: item["executed_at"], reverse=True)
        pending_confirmation_count = sum(
            item.get("status") == "paused"
            and item.get("last_stop_reason") == "interrupted_pending_confirmation"
            for item in tasks
        )
        return {
            "tasks": tasks,
            "executions": executions[:100],
            "tracking": "execution_log" if log_available else "task_state",
            "pending_confirmation_count": pending_confirmation_count,
        }

    def runtime_status(
        self,
        user: Any,
        *,
        session_id: Any = "",
        source: Any = "web",
        sections: Any = None,
    ) -> dict[str, Any]:
        from web.services.runtime_status_aggregate import runtime_status

        return runtime_status(
            self,
            user,
            session_id=session_id,
            source=source,
            sections=sections,
        )
