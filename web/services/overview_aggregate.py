"""Overview aggregation extracted from :mod:`web.services.overview`."""

from __future__ import annotations

import math
from typing import Any
from run.agents import discover_agents
from run.config import load_config
from run.history import find_record, list_sessions, session_statistics
from web.services.runtime_status import _nonnegative_int


def _session_cache_hit_rate(stored_usage: Any) -> float | None:
    if not isinstance(stored_usage, dict):
        return None
    raw_rate = stored_usage.get("cache_hit_rate")
    if isinstance(raw_rate, (str, int, float)) and not isinstance(raw_rate, bool):
        try:
            rate = float(raw_rate)
        except (TypeError, ValueError):
            rate = math.nan
        if math.isfinite(rate) and 0.0 <= rate <= 1.0:
            return rate

    cached_raw = stored_usage.get(
        "cached_prompt_tokens", stored_usage.get("cached_input_tokens")
    )
    missed_raw = stored_usage.get("cache_miss_tokens")
    if cached_raw is None or missed_raw is None:
        return None
    try:
        if isinstance(cached_raw, bool) or isinstance(missed_raw, bool):
            return None
        cached = max(0, int(cached_raw))
        missed = max(0, int(missed_raw))
    except (TypeError, ValueError, OverflowError):
        return None
    total = cached + missed
    return round(cached / total, 6) if total else None


def build_overview(
    service,
    user: Any,
    *,
    session_id: Any = "",
    source: Any = "web",
) -> dict[str, Any]:
    name = service.require_user(user)
    normalized_source = service.require_source(source)
    normalized_session = ""
    if session_id:
        normalized_session = service.require_session_id(session_id)
    task_data = service.tasks(
        name,
        source=normalized_source,
        session_id=normalized_session,
    )
    knowledge_data = service.knowledge(name)
    skill_data = service.skills(name)
    settings_data = service.settings(name)
    sessions = list_sessions(service.root, name, normalized_source)
    config = load_config(name, service.root)

    usage = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "estimated": False,
        "cache_hit_rate": None,
    }
    selected_record = find_record(service.root, name, normalized_source, normalized_session) if normalized_session else None
    statistics = session_statistics(service.root, name, normalized_source, normalized_session) if selected_record else {}
    integration_count = statistics.get("integration_count", 0)
    usage.update(statistics.get("usage", {}))
    usage["cache_hit_rate"] = _session_cache_hit_rate(usage)
    token_limit = int(settings_data["limits"]["context_tokens"])
    total_tokens = max(0, int(usage.get("total_tokens") or 0))
    percent = min(100, round(total_tokens * 100 / token_limit)) if token_limit > 0 else 0

    agents_config = config.get("agents") or {}
    try:
        compression_ratio = float(agents_config.get("token_compression_ratio") or 0.3)
    except (TypeError, ValueError):
        compression_ratio = 0.3
    compression_ratio = min(1.0, max(0.0, compression_ratio))
    current_context = service._current_context_status(
        name,
        normalized_session,
        config=config,
        token_limit=token_limit,
        configured_ratio=compression_ratio,
        source=normalized_source,
    )
    context_snapshot = current_context.get("context_snapshot")
    if not isinstance(context_snapshot, dict):
        context_snapshot = {
            "available": False,
            "source": "unavailable",
            "measurement": "unknown",
            "captured_at": "",
            "system_prompt_tokens": 0,
            "tool_schema_tokens": 0,
            "conversation_tokens": 0,
            "summary_tokens": 0,
            "other_tokens": 0,
            "total_tokens": 0,
            "capacity_tokens": token_limit,
            "percent": 0.0,
            "cards_active": 0, "last_seq": 0, "view_epoch": 0, "active_integrations": 0,
        }

    session_tool_calls = statistics.get("tool_calls", 0)
    cards_active = _nonnegative_int(current_context.get("cards_active"))
    last_seq = _nonnegative_int(current_context.get("last_seq"))
    active_integrations = _nonnegative_int(current_context.get("active_integrations"))

    sense_data = service.sense(name)
    expand_data = service.expands(name, include_files=False)
    message_data = service.message_status(name)
    knowledge_documents = knowledge_data.get("documents") or []
    enabled_knowledge = sum(
        bool(item.get("active_for_main_agent"))
        for item in knowledge_documents
        if isinstance(item, dict)
    )
    skill_catalog = skill_data.get("catalog_summary") or {}
    registered_tools = _nonnegative_int(skill_catalog.get("total"))
    enabled_tools = _nonnegative_int(skill_catalog.get("enabled"))

    active_statuses = {"running", "approved", "paused"}
    active_plan = (
        next(
            (item for item in task_data["plans"] if item["status"] in active_statuses),
            None,
        )
        if selected_record is not None
        else None
    )
    activities = []
    for session in sessions[:4]:
        activities.append(
            {
                "type": "session",
                "title": f"Web 对话已保存 · {int(session.get('integration_count') or 0)} 次整合",
                "detail": str(session.get("session_id") or ""),
                "status": "saved",
                "updated_at": str(session.get("updated_at") or ""),
            }
        )
    for plan in task_data["plans"][:3]:
        activities.append(
            {
                "type": "plan",
                "title": plan["title"],
                "detail": plan["description"],
                "status": plan["status"],
                "updated_at": plan["updated_at"],
            }
        )
    for task in task_data["cron_tasks"][:3]:
        activities.append(
            {
                "type": "cron",
                "title": task["title"],
                "detail": "定时任务",
                "status": task["status"],
                "updated_at": task.get("latest_run_at") or task.get("created_at") or "",
            }
        )
    activities.sort(key=lambda item: item["updated_at"], reverse=True)

    agent_registry = discover_agents(service.root, name)
    agents = [
        {
            "name": definition.name,
            "description": definition.description,
            "enabled": definition.enabled,
            "source": definition.source,
            "execution": definition.execution,
            "model_profile": definition.model_profile,
            "exposure": definition.capabilities.exposure,
        }
        for definition in sorted(
            agent_registry.agents.values(), key=lambda item: item.name.casefold()
        )
    ]
    return {
        "user": name,
        "session_id": normalized_session,
        "context": {
            "usage": usage,
            "limit": token_limit,
            "percent": percent,
            "engine": "cardstream", "cards_active": cards_active,
            "last_seq": last_seq, "integration_count": integration_count,
            "active_integrations": active_integrations,
        },
        "provider": settings_data["provider"],
        "counts": {
            "sessions": len(sessions),
            "knowledge_documents": knowledge_data["summary"]["documents"],
            "enabled_tools": skill_data["summary"]["enabled"],
            "enabled_agents": len(agent_registry.enabled_agents()),
            "active_tasks": task_data["summary"]["active_plans"] + task_data["summary"]["enabled_crons"],
        },
        "context_window": {
            "tokens": {
                "system_prompt_tokens": _nonnegative_int(
                    context_snapshot.get("system_prompt_tokens")
                ),
                "tool_schema_tokens": _nonnegative_int(
                    context_snapshot.get("tool_schema_tokens")
                ),
                "conversation_tokens": _nonnegative_int(
                    context_snapshot.get("conversation_tokens")
                ),
                "summary_tokens": _nonnegative_int(
                    context_snapshot.get("summary_tokens")
                ),
                "other_tokens": _nonnegative_int(
                    context_snapshot.get("other_tokens")
                ),
                "context_tokens": _nonnegative_int(
                    context_snapshot.get("total_tokens")
                )
                - _nonnegative_int(context_snapshot.get("system_prompt_tokens")),
                "total_tokens": _nonnegative_int(
                    context_snapshot.get("total_tokens")
                ),
                "capacity_tokens": _nonnegative_int(
                    context_snapshot.get("capacity_tokens")
                ),
                "percent": min(
                    100.0, float(context_snapshot.get("percent") or 0.0)
                ),
                "source": str(context_snapshot.get("source") or "unavailable"),
                "measurement": str(
                    context_snapshot.get("measurement") or "unknown"
                ),
                "captured_at": str(context_snapshot.get("captured_at") or ""),
            },
            "conversation": {
                "cards_active": cards_active, "last_seq": last_seq,
                "active_integrations": active_integrations,
                "total_tool_calls": session_tool_calls,
                "integration_count": integration_count,
                "session_tool_calls": session_tool_calls,
            },
            "tasks": {
                "active_plans": _nonnegative_int(task_data["summary"].get("active_plans")),
                "waiting_crons": _nonnegative_int(task_data["summary"].get("enabled_crons")),
            },
            "capabilities": {
                "tools_enabled": enabled_tools,
                "tools_disabled": max(0, registered_tools - enabled_tools),
                "agents_enabled": len(agent_registry.enabled_agents()),
            },
            "knowledge": {
                "enabled": enabled_knowledge,
                "disabled": max(0, len(knowledge_documents) - enabled_knowledge),
            },
            "messages": {
                "connected": _nonnegative_int(
                    (message_data.get("summary") or {}).get("connected_transports")
                ),
            },
            "integrations": {
                "expands": _nonnegative_int(
                    (expand_data.get("status_summary") or {}).get("enabled")
                ),
                "senses": _nonnegative_int(
                    (sense_data.get("summary") or {}).get("enabled")
                ),
            },
            "injection_policy": {
                "expand": str(
                    ((settings_data.get("source_policy") or {}).get("expand") or {}).get(
                        "injection_mode"
                    )
                    or "dynamic"
                ),
                "perception": str(
                    ((settings_data.get("source_policy") or {}).get("perception") or {}).get(
                        "injection_mode"
                    )
                    or "dynamic"
                ),
            },
        },
        "context_snapshot": context_snapshot,
        "session_context_stats": {
            "selected": bool(normalized_session),
            "cards_active": cards_active, "last_seq": last_seq,
            "active_integrations": active_integrations,
            "integration_count": integration_count,
            "session_tool_calls": session_tool_calls,
        },
        "agents": agents,
        "summary_cache": service._summary_cache_status(
            name,
            normalized_session,
            source=normalized_source,
        ),
        "runtime_host": service._runtime_status(),
        "active_plan": active_plan,
        "activities": activities[:6],
    }
