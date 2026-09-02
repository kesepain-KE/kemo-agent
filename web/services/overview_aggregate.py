"""Overview aggregation extracted from :mod:`web.services.overview`."""

from __future__ import annotations

def build_overview(
    service,
    user: Any,
    *,
    session_id: Any = "",
    source: Any = "web",
) -> dict[str, Any]:
    import importlib
    _overview = importlib.import_module("web.services.overview")
    Any = _overview.Any
    Path = _overview.Path
    _nonnegative_int = _overview._nonnegative_int
    discover_agents = _overview.discover_agents
    find_window = _overview.find_window
    list_sessions = _overview.list_sessions
    load_config = _overview.load_config
    load_window = _overview.load_window
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
    }
    rounds = 0
    selected_directory: Path | None = None
    if normalized_session:
        selected_directory = find_window(
            service.root, name, normalized_source, normalized_session
        )
        if selected_directory is not None:
            data = load_window(selected_directory).get("data") or {}
            rounds = max(0, int(data.get("rounds") or 0))
            stored_usage = data.get("token_usage")
            if isinstance(stored_usage, dict):
                usage.update(
                    {
                        key: stored_usage.get(key, usage[key])
                        for key in usage
                    }
                )
    token_limit = int(settings_data["limits"]["context_tokens"])
    round_limit = int(settings_data["limits"]["context_rounds"])
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
        round_limit=round_limit,
        configured_ratio=compression_ratio,
        source=normalized_source,
    )
    active_context_rounds = (
        max(0, int(current_context.get("rounds") or 0))
        if current_context.get("available") is True
        else rounds
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
            "foreground_rounds": 0,
        }

    session_tool_calls = 0
    if selected_directory is not None:
        try:
            archive = load_window(selected_directory)
        except Exception:
            archive = {}
        selected_data = archive.get("data") or {}
        metrics = selected_data.get("round_metrics") or []
        if isinstance(metrics, list) and metrics:
            session_tool_calls = sum(
                _nonnegative_int(metric.get("tool_calls"))
                for metric in metrics
                if isinstance(metric, dict)
            )
        else:
            item_container = archive.get("items") or {}
            items = (
                item_container.get("items")
                if isinstance(item_container, dict)
                else []
            )
            session_tool_calls = sum(
                item.get("type") == "tool_call"
                for item in (items or [])
                if isinstance(item, dict)
            )
    foreground_rounds = _nonnegative_int(
        current_context.get("rounds")
    )
    session_total_rounds = _nonnegative_int(
        current_context.get("session_total_rounds")
    )
    background_archived_rounds = max(
        0, session_total_rounds - foreground_rounds
    )

    sense_data = service.sense(name)
    expand_data = service.expands(name)
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
    active_plan = next(
        (item for item in task_data["plans"] if item["status"] in active_statuses),
        None,
    )
    activities = []
    for session in sessions[:4]:
        activities.append(
            {
                "type": "session",
                "title": f"Web 对话已保存 · {int(session.get('rounds') or 0)} 轮",
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
            "rounds": active_context_rounds,
            "session_total_rounds": rounds,
            "archived_rounds": max(0, rounds - active_context_rounds),
            "round_limit": round_limit,
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
                "foreground_rounds": foreground_rounds,
                "archived_rounds": background_archived_rounds,
                "total_tool_calls": session_tool_calls,
                "session_total_rounds": session_total_rounds,
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
                    or "round"
                ),
                "perception": str(
                    ((settings_data.get("source_policy") or {}).get("perception") or {}).get(
                        "injection_mode"
                    )
                    or "round"
                ),
            },
        },
        "context_snapshot": context_snapshot,
        "session_context_stats": {
            "selected": bool(normalized_session),
            "foreground_rounds": foreground_rounds,
            "background_archived_rounds": background_archived_rounds,
            "session_total_rounds": session_total_rounds,
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



