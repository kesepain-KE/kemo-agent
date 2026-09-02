"""运行状态、用量统计与后台调度聚合。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from provider.protocol.models import (
    normalize_kemo_reasoning_effort,
    normalize_reasoning_effort,
)
from run.config import load_config
from run.context import (
    ContextPolicy,
    build_context_snapshot,
    estimate_text_tokens,
    select_context,
)
from run.context import build_summary_message, read_summary_cache
from run.scheduler import CronStore
from run.history import empty_window, find_window, load_window, runtime_window_path
from run.history import list_windows as list_history_windows, window_exists
from run.infra import LogStore
from run.memory import MemoryStore
from run.config import build_prompt_bundle
from run.tools import apply_runtime_tool_policy, discover_tools
from web.constants import _BEIJING
from web.services.runtime_status_aggregate import runtime_status as _runtime_status_aggregate


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
    def _current_context_status(
        self,
        user: str,
        session_id: str,
        *,
        config: dict[str, Any],
        token_limit: int,
        round_limit: int,
        configured_ratio: float,
        source: str = "web",
        prompt_bundle: Any | None = None,
    ) -> dict[str, Any]:
        requested_source = str(source or "web").strip() or "web"
        requested_session_id = str(session_id or "").strip()
        unavailable = {
            "selected": False,
            "available": False,
            "used_tokens": 0,
            "max_tokens": token_limit,
            "percent": 0.0,
            "rounds": 0,
            "round_limit": round_limit,
            "compression_threshold": max(0, round(token_limit * configured_ratio)),
            "source": "unavailable",
        }
        selected = bool(requested_session_id)
        directory = (
            find_window(
                self.root,
                user,
                requested_source,
                requested_session_id,
            )
            if selected
            else None
        )
        archive: dict[str, Any]
        if directory is None:
            if selected:
                return unavailable
            directory = self.root / "users" / user / "history" / "__new_session__"
            archive = empty_window(user, requested_source, "__new_session__")
        else:
            try:
                archive = load_window(directory)
            except Exception:
                return unavailable
        try:
            runtime_path = (
                runtime_window_path(directory)
                if selected
                else directory / "temp" / "__new_session__"
            )
            runtime_window = archive
            calculation_source = "new_session_recalculated"
            if selected and window_exists(runtime_path):
                runtime_window = load_window(runtime_path)
                calculation_source = "runtime_recalculated"
            elif selected:
                calculation_source = "archive_recalculated"
            policy = ContextPolicy.from_config(config)
            if prompt_bundle is None:
                prompt_bundle = build_prompt_bundle(
                    self.root,
                    user,
                    config,
                    source=requested_source,
                    session_id=requested_session_id,
                )
            registry = apply_runtime_tool_policy(
                discover_tools(self.root, user), config
            )
            system_message = (
                {"role": "system", "content": prompt_bundle.text}
                if prompt_bundle.text
                else None
            )
            summary_message = None
            summary_message = build_summary_message(
                read_summary_cache(runtime_path)
            )
            selection = select_context(
                window=runtime_window,
                policy=policy,
                system_message=system_message,
                summary_message=summary_message,
                current_user_message=None,
                tools=registry.schemas() or None,
            )
            snapshot = build_context_snapshot(
                selection,
                system_prompt=prompt_bundle.text,
                summary_message=summary_message,
                capacity_tokens=token_limit,
                source=calculation_source,
            )
        except Exception:
            return unavailable
        archive_data = archive.get("data") or {}
        total_rounds = _nonnegative_int(archive_data.get("rounds"))
        foreground_rounds = _nonnegative_int(snapshot.get("foreground_rounds"))
        effective_limit = max(0, token_limit)
        threshold = _nonnegative_int(selection.input_budget) or max(
            0, round(effective_limit * configured_ratio)
        )
        return {
            "selected": selected,
            "available": True,
            "used_tokens": _nonnegative_int(snapshot.get("total_tokens")),
            "max_tokens": effective_limit,
            "percent": float(snapshot.get("percent") or 0.0),
            "rounds": foreground_rounds,
            "round_limit": max(0, round_limit),
            "compression_threshold": threshold,
            "source": str(snapshot.get("source") or calculation_source),
            "context_snapshot": snapshot,
            "session_total_rounds": total_rounds,
            "background_archived_rounds": max(0, total_rounds - foreground_rounds),
        }

    def _today_token_statistics(self, user: str, *, now: datetime) -> dict[str, Any]:
        today = now.astimezone(_BEIJING).date()
        sent_tokens = 0
        received_tokens = 0
        cached_tokens = 0
        request_count = 0
        trend = [0 for _ in range(24)]
        estimated = False

        def add_usage(usage: dict[str, Any], occurred_at: datetime | None) -> None:
            nonlocal sent_tokens, received_tokens, cached_tokens, request_count
            sent = _nonnegative_int(
                usage.get("input_tokens") or usage.get("prompt_tokens")
            )
            received = _nonnegative_int(
                usage.get("output_tokens") or usage.get("completion_tokens")
            )
            cached = _usage_cache_tokens(usage)
            declared_requests = _nonnegative_int(usage.get("provider_request_count"))
            if not any(
                (
                    sent,
                    received,
                    cached,
                    declared_requests,
                    _nonnegative_int(usage.get("total_tokens")),
                )
            ):
                return
            sent_tokens += sent
            received_tokens += received
            cached_tokens += cached
            request_count += declared_requests or 1
            if occurred_at is not None:
                trend[occurred_at.astimezone(_BEIJING).hour] += sent + received

        for stored in list_history_windows(self.root, user):
            data = stored.get("data") or {}
            fallback_time = _parse_datetime(data.get("updated_at"))
            metrics = data.get("round_metrics") or []
            metric_usage_found = False
            for metric in metrics if isinstance(metrics, list) else []:
                if not isinstance(metric, dict):
                    continue
                responses = metric.get("provider_responses") or []
                response_found = False
                response_has_timestamp = False
                for response in responses if isinstance(responses, list) else []:
                    if not isinstance(response, dict):
                        continue
                    occurred_at = _provider_response_time(response)
                    response_has_timestamp = (
                        response_has_timestamp or occurred_at is not None
                    )
                    if (
                        occurred_at is None
                        or occurred_at.astimezone(_BEIJING).date() != today
                    ):
                        continue
                    usage = response.get("usage") or {}
                    if not isinstance(usage, dict):
                        continue
                    add_usage(usage, occurred_at)
                    response_found = True
                    metric_usage_found = True
                if response_found or response_has_timestamp:
                    continue
                usage = metric.get("usage") or {}
                if (
                    isinstance(usage, dict)
                    and fallback_time is not None
                    and fallback_time.astimezone(_BEIJING).date() == today
                ):
                    add_usage(usage, fallback_time)
                    metric_usage_found = True
                    estimated = True
            if metric_usage_found:
                continue
            usage = data.get("token_usage") or {}
            if (
                isinstance(usage, dict)
                and fallback_time is not None
                and fallback_time.astimezone(_BEIJING).date() == today
                and (_nonnegative_int(usage.get("total_tokens")) or usage)
            ):
                add_usage(usage, fallback_time)
                estimated = True
        total_tokens = sent_tokens + received_tokens
        return {
            "date": today.isoformat(),
            "timezone": "Asia/Shanghai",
            "sent_tokens": sent_tokens,
            "received_tokens": received_tokens,
            "total_tokens": total_tokens,
            "cached_tokens": cached_tokens,
            "cache_rate": round(cached_tokens * 100 / sent_tokens, 2)
            if sent_tokens
            else 0.0,
            "request_count": request_count,
            "estimated": estimated,
            "trend": trend,
        }

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
        return {
            "tasks": tasks,
            "executions": executions[:100],
            "tracking": "execution_log" if log_available else "task_state",
        }

    def runtime_status(
        self,
        user: Any,
        *,
        session_id: Any = "",
        source: Any = "web",
        sections: Any = None,
    ) -> dict[str, Any]:
        """Compatibility entry point delegated to the aggregation module."""

        return _runtime_status_aggregate(
            self,
            user,
            session_id=session_id,
            source=source,
            sections=sections,
        )
