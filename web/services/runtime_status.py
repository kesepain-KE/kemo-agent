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
            find_window(self.root, user, source, session_id) if selected else None
        )
        archive: dict[str, Any]
        if directory is None:
            if selected:
                return unavailable
            directory = self.root / "users" / user / "history" / "__new_session__"
            archive = empty_window(user, source, "__new_session__")
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
            if selected and window_exists(runtime_path):
                runtime_window = load_window(runtime_path)
                source = "runtime_recalculated"
            elif selected:
                source = "archive_recalculated"
            policy = ContextPolicy.from_config(config)
            if prompt_bundle is None:
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
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        normalized_session = self.require_session_id(session_id) if session_id else ""
        now = datetime.now(_BEIJING)
        available_sections = {
            "summary",
            "prompt",
            "tokens",
            "api",
            "external",
            "maintenance",
            "congestion",
        }
        if sections is None or sections == "":
            requested_sections = set(available_sections)
        else:
            raw_sections = (
                sections.split(",")
                if isinstance(sections, str)
                else sections
                if isinstance(sections, (list, tuple, set))
                else []
            )
            requested_sections = {
                str(item).strip().casefold()
                for item in raw_sections
                if str(item).strip().casefold() in available_sections
            }
            if not requested_sections:
                requested_sections = set(available_sections)

        config = load_config(name, self.root)
        settings = self.settings(name)
        provider_config = config.get("provider") or {}
        provider = settings["provider"]
        token_limit = _nonnegative_int(settings["limits"].get("context_tokens"))
        round_limit = _nonnegative_int(settings["limits"].get("context_rounds"))
        agents_config = config.get("agents") or {}
        try:
            compression_ratio = float(
                agents_config.get("token_compression_ratio") or 0.3
            )
        except (TypeError, ValueError):
            compression_ratio = 0.3
        compression_ratio = min(1.0, max(0.0, compression_ratio))

        api = {
            "type": "",
            "base_url": "",
            "model": "",
            "thinking_effort": "",
            "configured": False,
            "credential_source": "missing",
        }
        context = {
            "selected": bool(normalized_session),
            "available": False,
            "used_tokens": 0,
            "max_tokens": token_limit,
            "percent": 0.0,
            "rounds": 0,
            "round_limit": round_limit,
            "compression_threshold": max(0, round(token_limit * compression_ratio)),
            "source": "not_requested",
        }
        tokens = {
            "date": now.date().isoformat(),
            "timezone": "Asia/Shanghai",
            "sent_tokens": 0,
            "received_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
            "cache_rate": 0.0,
            "request_count": 0,
            "estimated": False,
            "trend": [0 for _ in range(24)],
        }
        prompt = {
            "content": "",
            "total_chars": 0,
            "estimated_tokens": 0,
            "components": [],
        }
        components: dict[str, list[dict[str, Any]]] = {"sense": [], "expand": []}
        memory = {
            "updated_today": 0,
            "upgraded_today": 0,
            "upgrade_tracking": "not_available",
            "updates": [],
        }
        tasks = {
            "summary": {
                "active_plans": 0,
                "waiting_plans": 0,
                "enabled_crons": 0,
                "completed_plans": 0,
            },
            "items": [],
        }
        system_cron = {"tasks": [], "executions": [], "tracking": "not_requested"}
        empty_message_summary = {
            "total_bindings": 0,
            "total_transports": 0,
            "running_transports": 0,
            "stopped_transports": 0,
            "error_transports": 0,
            "connected_transports": 0,
            "temporary_files": 0,
            "today_logs": 0,
        }
        message_routes = {"summary": empty_message_summary, "routes": []}
        runtime_host = {"state": "not_requested", "components": {}}
        congestion = {
            "provider": {
                "active_requests": 0,
                "max_requests": 0,
                "available_requests": 0,
                "waiting_estimate": 0,
            },
            "web": {
                "active_chats": 0,
                "max_chats": 0,
                "pending_chats": 0,
                "max_pending": 0,
            },
            "message_router": {
                "active_workers": 0,
                "max_workers": 0,
                "queued_messages": 0,
                "max_queued": 0,
            },
        }

        bundle = None
        if requested_sections & {"summary", "prompt"}:
            bundle = build_prompt_bundle(self.root, name, config)

        if requested_sections & {"summary", "api"}:
            api = {
                "type": provider["type"],
                "base_url": provider["base_url"],
                "model": provider["model"],
                "thinking_effort": (
                    normalize_kemo_reasoning_effort(
                        provider_config.get("reasoning_effort")
                    )
                    if str(provider.get("type") or "").strip().casefold() == "kemo"
                    else normalize_reasoning_effort(
                        provider_config.get("reasoning_effort")
                    )
                ),
                "configured": bool(
                    provider.get("configured")
                    and provider.get("credential_source") != "missing"
                ),
                "credential_source": provider.get("credential_source"),
            }

        if "summary" in requested_sections:
            context = self._current_context_status(
                name,
                normalized_session,
                config=config,
                token_limit=token_limit,
                round_limit=round_limit,
                configured_ratio=compression_ratio,
                source=normalized_source,
                prompt_bundle=bundle,
            )

        if "prompt" in requested_sections and bundle is not None:
            prompt_components = []
            for section in bundle.sections:
                disabled = section.mode == "disabled"
                empty = section.content.strip() in {"", "（无）"}
                prompt_components.append(
                    {
                        "id": section.name,
                        "name": section.name,
                        "state": (
                            "disabled"
                            if disabled
                            else "empty"
                            if empty
                            else "truncated"
                            if section.truncated
                            else "injected"
                        ),
                        "chars": len(section.content),
                        "tokens": estimate_text_tokens(section.content),
                        "source_files": list(section.source_files),
                        "injected_items": int(section.injected_items),
                        "original_items": int(section.original_items),
                    }
                )
            prompt = {
                "content": bundle.text,
                "total_chars": len(bundle.text),
                "estimated_tokens": estimate_text_tokens(bundle.text),
                "components": prompt_components,
            }

        if "tokens" in requested_sections:
            tokens = self._today_token_statistics(name, now=now)

        message_data = None
        if requested_sections & {"summary", "external"}:
            message_data = self.message_status(name)
            message_routes = {
                "summary": message_data["summary"],
                "routes": [],
            }

        if "external" in requested_sections:
            sense_data = self.sense(name)
            components["sense"] = [
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
            for scope in expand_data.get("expands") or []:
                if not isinstance(scope, dict):
                    continue
                for item in scope.get("items") or []:
                    if not isinstance(item, dict):
                        continue
                    components["expand"].append(
                        {
                            "id": str(item.get("id") or ""),
                            "name": str(item.get("display_name") or item.get("name") or ""),
                            "scope": str(item.get("scope") or scope.get("scope") or ""),
                            "health": (
                                "error"
                                if not item.get("valid")
                                or item.get("input_health") == "异常"
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
                            "description": str(
                                item.get("error") or item.get("description") or ""
                            ),
                            "updated_at": item.get("updated_at"),
                        }
                    )
            message_source = message_data or self.message_status(name)
            message_routes["routes"] = [
                {
                    "id": str(item.get("id") or ""),
                    "name": str(item.get("display_name") or item.get("name") or ""),
                    "platform": str(item.get("platform") or ""),
                    "health": (
                        "healthy"
                        if item.get("connection_status") == "connected"
                        else "error"
                        if item.get("connection_status") == "error"
                        or item.get("state") == "error"
                        else "offline"
                    ),
                    "state": str(item.get("state") or "stopped"),
                    "latency_ms": item.get("latency_ms"),
                    "last_check": item.get("last_check"),
                    "description": str(
                        item.get("health") or item.get("connection_status") or ""
                    ),
                }
                for item in message_source.get("transports") or []
                if isinstance(item, dict)
            ]
            runtime_host = self._runtime_status()

        if "maintenance" in requested_sections:
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
            memory = {
                "updated_today": len(memory_updates),
                "upgraded_today": sum(
                    item.get("upgraded") is True for item in memory_updates
                ),
                "upgrade_tracking": (
                    "system_cron_log" if promotion_tracking else "not_available"
                ),
                "updates": memory_updates,
            }

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
                        "updated_at": item.get("latest_run_at")
                        or item.get("created_at")
                        or "",
                    }
                )
            tasks = {
                "summary": task_data["summary"],
                "items": sorted(
                    [*current_plans, *current_crons],
                    key=lambda item: item["updated_at"],
                    reverse=True,
                ),
            }

        if "congestion" in requested_sections:
            from provider.factory import provider_semaphore_status

            try:
                web_congestion = self._get_chat_gate(name).status()
            except Exception:
                web_congestion = congestion["web"]
            try:
                message_congestion = (
                    self._router_ref.queue_status()
                    if self._router_ref is not None
                    else congestion["message_router"]
                )
            except Exception:
                message_congestion = congestion["message_router"]
            congestion = {
                "provider": provider_semaphore_status(config),
                "web": web_congestion,
                "message_router": message_congestion,
            }

        return {
            "schema_version": 1,
            "included_sections": sorted(requested_sections),
            "generated_at": now.isoformat(),
            "user": name,
            "session_id": normalized_session,
            "api": api,
            "context": context,
            "tokens": tokens,
            "prompt": prompt,
            "components": components,
            "memory": memory,
            "tasks": tasks,
            "system_cron": system_cron,
            "message_routes": message_routes,
            "runtime_host": runtime_host,
            "congestion": congestion,
        }
