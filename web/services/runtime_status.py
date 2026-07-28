"""运行状态、用量统计与后台调度聚合。"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from provider.protocol.models import normalize_reasoning_effort
from run.config import load_config
from run.context import (
    ContextPolicy,
    build_context_snapshot,
    estimate_text_tokens,
    select_context,
)
from run.context_summary import build_summary_message
from run.cron_store import CronStore
from run.history import empty_window, find_window, load_window, runtime_window_path
from run.log_store import LogStore
from run.memory import MemoryStore
from run.prompt import build_prompt_bundle
from run.tools import apply_runtime_tool_policy, discover_tools
from web.constants import _BEIJING
from web.services._paths import _visible_children


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
    return _nonnegative_int(details.get("cached_tokens")) if isinstance(details, dict) else 0


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
    ) -> dict[str, Any]:
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
        selected = bool(session_id)
        directory = (
            find_window(self.root, user, "web", session_id) if selected else None
        )
        archive: dict[str, Any]
        if directory is None:
            if selected:
                return unavailable
            directory = self.root / "users" / user / "history" / "__new_session__"
            archive = empty_window(user, "web", "__new_session__")
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
            source = "new_session_recalculated"
            if runtime_path.is_dir() and (runtime_path / "data.json").is_file():
                runtime_window = load_window(runtime_path)
                source = "runtime_recalculated"
            elif selected:
                source = "archive_recalculated"
            policy = ContextPolicy.from_config(config)
            prompt_bundle = build_prompt_bundle(self.root, user, config)
            registry = apply_runtime_tool_policy(
                discover_tools(self.root, user), config
            )
            system_message = (
                {"role": "system", "content": prompt_bundle.text}
                if prompt_bundle.text
                else None
            )
            summary_message = None
            summary_path = runtime_path / "context_summary.json"
            if summary_path.is_file():
                try:
                    summary_message = build_summary_message(
                        json.loads(summary_path.read_text("utf-8"))
                    )
                except (OSError, json.JSONDecodeError):
                    summary_message = None
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
                source=source,
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
            "source": str(snapshot.get("source") or source),
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
        history = self.root / "users" / user / "history"

        def add_usage(usage: dict[str, Any], occurred_at: datetime | None) -> None:
            nonlocal sent_tokens, received_tokens, cached_tokens, request_count
            sent = _nonnegative_int(usage.get("input_tokens") or usage.get("prompt_tokens"))
            received = _nonnegative_int(
                usage.get("output_tokens") or usage.get("completion_tokens")
            )
            cached = _usage_cache_tokens(usage)
            declared_requests = _nonnegative_int(
                usage.get("provider_request_count")
            )
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

        for directory in _visible_children(history):
            if not directory.is_dir() or directory.name == "temp":
                continue
            try:
                data = load_window(directory).get("data") or {}
            except Exception:
                continue
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
                    response_has_timestamp = response_has_timestamp or occurred_at is not None
                    if occurred_at is None or occurred_at.astimezone(_BEIJING).date() != today:
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
            "cache_rate": round(cached_tokens * 100 / sent_tokens, 2) if sent_tokens else 0.0,
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
        log_path = (
            self.root
            / "cron"
            / "task_cron_system"
            / "log"
            / f"{now.astimezone(_BEIJING):%Y-%m-%d}.jsonl"
        )
        executions: list[dict[str, Any]] = []
        structured: list[dict[str, Any]] = []
        try:
            store = LogStore(self.root)
            store.migrate_cron_logs(log_path.parent)
            structured = store.list_cron(user, limit=1000)
        except Exception:
            structured = []
        if structured:
            for item in structured:
                task_id = str(item.get("task_id") or "")
                executions.append(
                    {
                        **item,
                        "title": task_titles.get(task_id, task_id),
                    }
                )
        elif log_path.is_file() and not log_path.is_symlink():
            try:
                lines = log_path.read_text("utf-8").splitlines()
            except (OSError, UnicodeError):
                lines = []
            for index, line in enumerate(lines[-1000:]):
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(item, dict) or item.get("user") != user:
                    continue
                task_id = str(item.get("task_id") or "")
                executions.append(
                    {
                        "id": f"{task_id}:{item.get('executed_at') or index}",
                        "task_id": task_id,
                        "title": task_titles.get(task_id, task_id),
                        "executed_at": str(item.get("executed_at") or ""),
                        "status": str(item.get("status") or "unknown"),
                        "duration_ms": _nonnegative_int(item.get("duration_ms")),
                        "result": item.get("result") if isinstance(item.get("result"), dict) else {},
                        "error": item.get("error") if isinstance(item.get("error"), dict) else None,
                        "source": "execution_log",
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
            "tracking": "execution_log" if structured or log_path.is_file() else "task_state",
        }

    def runtime_status(self, user: Any, *, session_id: Any = "") -> dict[str, Any]:
        name = self.require_user(user)
        normalized_session = self.require_session_id(session_id) if session_id else ""
        now = datetime.now(_BEIJING)
        config = load_config(name, self.root)
        settings = self.settings(name)
        agents_config = config.get("agents") or {}
        token_limit = _nonnegative_int(settings["limits"].get("context_tokens"))
        round_limit = _nonnegative_int(settings["limits"].get("context_rounds"))
        try:
            compression_ratio = float(agents_config.get("token_compression_ratio") or 0.3)
        except (TypeError, ValueError):
            compression_ratio = 0.3
        compression_ratio = min(1.0, max(0.0, compression_ratio))

        bundle = build_prompt_bundle(self.root, name, config)
        prompt_components = []
        for section in bundle.sections:
            empty = section.content.strip() in {"", "（无）"}
            prompt_components.append(
                {
                    "id": section.name,
                    "name": section.name,
                    "state": (
                        "empty" if empty else "truncated" if section.truncated else "injected"
                    ),
                    "chars": len(section.content),
                    "tokens": estimate_text_tokens(section.content),
                    "source_files": list(section.source_files),
                    "injected_items": int(section.injected_items),
                    "original_items": int(section.original_items),
                }
            )

        sense_data = self.sense(name)
        sense_components = [
            {
                "id": str(item.get("id") or item.get("name") or ""),
                "name": str(item.get("display_name") or item.get("name") or ""),
                "health": (
                    "error"
                    if not item.get("valid") or item.get("health") == "异常"
                    else "healthy"
                    if item.get("health") == "正常"
                    else "warning"
                ),
                "state": (
                    "error"
                    if not item.get("valid")
                    else "injected"
                    if item.get("injected_markdown")
                    else "loaded"
                    if item.get("enabled")
                    else "disabled"
                ),
                "description": str(item.get("error") or item.get("description") or ""),
                "updated_at": item.get("updated_at"),
            }
            for item in sense_data.get("sources") or []
            if isinstance(item, dict)
        ]

        expand_data = self.expands(name)
        expand_components = []
        for scope in expand_data.get("expands") or []:
            if not isinstance(scope, dict):
                continue
            for item in scope.get("items") or []:
                if not isinstance(item, dict):
                    continue
                expand_components.append(
                    {
                        "id": str(item.get("id") or ""),
                        "name": str(item.get("display_name") or item.get("name") or ""),
                        "scope": str(item.get("scope") or scope.get("scope") or ""),
                        "health": (
                            "error"
                            if not item.get("valid") or item.get("input_health") == "异常"
                            else "healthy"
                            if item.get("input_health") == "正常"
                            else "warning"
                        ),
                        "state": (
                            "error"
                            if not item.get("valid")
                            else "injected"
                            if item.get("injected_markdown")
                            else "loaded"
                            if item.get("active_for_main_agent")
                            else "disabled"
                        ),
                        "description": str(item.get("error") or item.get("description") or ""),
                        "updated_at": item.get("updated_at"),
                    }
                )

        system_cron = self._system_cron_status(name, now=now)
        promotion_by_file: dict[str, dict[str, Any]] = {}
        for execution in system_cron["executions"]:
            if execution.get("task_id") != "memory_promotion":
                continue
            result = execution.get("result") or {}
            for promotion in result.get("promotions") or []:
                if isinstance(promotion, dict) and promotion.get("filename"):
                    promotion_by_file[str(promotion["filename"])] = promotion
        promotion_tracking = system_cron["tracking"] == "execution_log"
        memory_updates = []
        store = MemoryStore(self.root, name, config)
        for item in store.list_items():
            updated = _parse_datetime(item.get("updated_at"))
            if updated is None or updated.astimezone(_BEIJING).date() != now.date():
                continue
            promotion = promotion_by_file.get(str(item.get("filename") or ""))
            memory_updates.append(
                {
                    "id": f"{item.get('tier')}:{item.get('filename')}",
                    "filename": str(item.get("filename") or ""),
                    "tier": str(item.get("tier") or ""),
                    "weight": _nonnegative_int(item.get("weight")),
                    "updated_at": str(item.get("updated_at") or ""),
                    "upgraded": bool(promotion) if promotion_tracking else None,
                    "from_tier": str((promotion or {}).get("from_tier") or ""),
                    "to_tier": str((promotion or {}).get("to_tier") or ""),
                }
            )
        important_path = self.root / "users" / name / "memory_temporary_important.md"
        if important_path.is_file() and not important_path.is_symlink():
            important_time = datetime.fromtimestamp(
                important_path.stat().st_mtime, timezone.utc
            ).astimezone(_BEIJING)
            if important_time.date() == now.date():
                memory_updates.append(
                    {
                        "id": "important:memory_temporary_important.md",
                        "filename": "memory_temporary_important.md",
                        "tier": "important",
                        "weight": 0,
                        "updated_at": important_time.isoformat(),
                        "upgraded": None,
                        "from_tier": "",
                        "to_tier": "",
                    }
                )
        memory_updates.sort(key=lambda item: item["updated_at"], reverse=True)

        task_data = self.tasks(name)
        current_plans = [
            {
                "id": item["plan_id"],
                "kind": "plan",
                "title": item["title"],
                "status": item["status"],
                "next_run_at": "",
                "trigger": f"进度 {item['progress']['completed']} / {item['progress']['total']}",
                "updated_at": item["updated_at"],
            }
            for item in task_data["plans"]
            if item["status"] not in {"completed", "cancelled", "failed"}
        ]
        current_crons = []
        for item in task_data["cron_tasks"]:
            if item["status"] in {"completed", "cancelled"}:
                continue
            trigger = (
                f"每日 {item.get('time')}"
                if item.get("type") == "daily"
                else f"每 {item.get('interval_seconds')} 秒"
                if item.get("type") == "recurring"
                else "单次执行"
            )
            current_crons.append(
                {
                    "id": item["task_id"],
                    "kind": "cron",
                    "title": item["title"],
                    "status": item["status"],
                    "next_run_at": item["next_run_at"],
                    "trigger": trigger,
                    "updated_at": item.get("latest_run_at") or item.get("created_at") or "",
                }
            )

        message_data = self.message_status(name)
        message_routes = [
            {
                "id": str(item.get("id") or ""),
                "name": str(item.get("display_name") or item.get("name") or ""),
                "platform": str(item.get("platform") or ""),
                "health": (
                    "healthy"
                    if item.get("connection_status") == "connected"
                    else "error"
                    if item.get("connection_status") == "error" or item.get("state") == "error"
                    else "offline"
                ),
                "state": str(item.get("state") or "stopped"),
                "latency_ms": item.get("latency_ms"),
                "last_check": item.get("last_check"),
                "description": str(item.get("health") or item.get("connection_status") or ""),
            }
            for item in message_data.get("transports") or []
            if isinstance(item, dict)
        ]

        provider_config = config.get("provider") or {}
        provider = settings["provider"]
        context = self._current_context_status(
            name,
            normalized_session,
            config=config,
            token_limit=token_limit,
            round_limit=round_limit,
            configured_ratio=compression_ratio,
        )
        from provider.factory import provider_semaphore_status

        try:
            web_congestion = self._get_chat_gate(name).status()
        except Exception:
            web_congestion = {
                "active_chats": 0,
                "max_chats": 0,
                "pending_chats": 0,
                "max_pending": 0,
            }
        try:
            message_congestion = (
                self._router_ref.queue_status()
                if self._router_ref is not None
                else {
                    "active_workers": 0,
                    "max_workers": 0,
                    "queued_messages": 0,
                    "max_queued": 0,
                }
            )
        except Exception:
            message_congestion = {
                "active_workers": 0,
                "max_workers": 0,
                "queued_messages": 0,
                "max_queued": 0,
            }
        congestion = {
            "provider": provider_semaphore_status(config),
            "web": web_congestion,
            "message_router": message_congestion,
        }
        return {
            "schema_version": 1,
            "generated_at": now.isoformat(),
            "user": name,
            "session_id": normalized_session,
            "api": {
                "type": provider["type"],
                "base_url": provider["base_url"],
                "model": provider["model"],
                "thinking_effort": normalize_reasoning_effort(
                    provider_config.get("reasoning_effort")
                ),
                "configured": bool(
                    provider.get("configured")
                    and provider.get("credential_source") != "missing"
                ),
                "credential_source": provider.get("credential_source"),
            },
            "context": context,
            "tokens": self._today_token_statistics(name, now=now),
            "prompt": {
                "content": bundle.text,
                "total_chars": len(bundle.text),
                "estimated_tokens": estimate_text_tokens(bundle.text),
                "components": prompt_components,
            },
            "components": {
                "sense": sense_components,
                "expand": expand_components,
            },
            "memory": {
                "updated_today": len(memory_updates),
                "upgraded_today": sum(item.get("upgraded") is True for item in memory_updates),
                "upgrade_tracking": (
                    "system_cron_log" if promotion_tracking else "not_available"
                ),
                "updates": memory_updates,
            },
            "tasks": {
                "summary": task_data["summary"],
                "items": sorted(
                    [*current_plans, *current_crons],
                    key=lambda item: item["updated_at"],
                    reverse=True,
                ),
            },
            "system_cron": system_cron,
            "message_routes": {
                "summary": message_data["summary"],
                "routes": message_routes,
            },
            "runtime_host": self._runtime_status(),
            "congestion": congestion,
        }


