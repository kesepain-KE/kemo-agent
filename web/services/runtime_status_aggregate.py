"""Runtime status aggregation extracted from :mod:`web.services.runtime_status`."""

from __future__ import annotations

def runtime_status(
    service,
    user: Any,
    *,
    session_id: Any = "",
    source: Any = "web",
    sections: Any = None,
) -> dict[str, Any]:
    import importlib
    _status = importlib.import_module("web.services.runtime_status")
    Any = _status.Any
    MemoryStore = _status.MemoryStore
    _BEIJING = _status._BEIJING
    _nonnegative_int = _status._nonnegative_int
    _parse_datetime = _status._parse_datetime
    build_prompt_bundle = _status.build_prompt_bundle
    datetime = _status.datetime
    estimate_text_tokens = _status.estimate_text_tokens
    load_config = _status.load_config
    normalize_kemo_reasoning_effort = _status.normalize_kemo_reasoning_effort
    normalize_reasoning_effort = _status.normalize_reasoning_effort
    timezone = _status.timezone
    name = service.require_user(user)
    normalized_source = service.require_source(source)
    normalized_session = service.require_session_id(session_id) if session_id else ""
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

    config = load_config(name, service.root)
    settings = service.settings(name)
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
        bundle = build_prompt_bundle(
            service.root,
            name,
            config,
            source=normalized_source,
            # An empty session is intentionally passed as an explicit
            # scope.  Passing None would mean "all sessions" to
            # select_prompt_plans(), which is unsafe for a new-session
            # status request.
            session_id=normalized_session,
        )

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
        context = service._current_context_status(
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
        tokens = service._today_token_statistics(name, now=now)

    message_data = None
    if requested_sections & {"summary", "external"}:
        message_data = service.message_status(name)
        message_routes = {
            "summary": message_data["summary"],
            "routes": [],
        }

    if "external" in requested_sections:
        sense_data = service.sense(name)
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
        expand_data = service.expands(name)
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
        message_source = message_data or service.message_status(name)
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
        runtime_host = service._runtime_status()

    if "maintenance" in requested_sections:
        system_cron = service._system_cron_status(name, now=now)
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
        store = MemoryStore(service.root, name, config)
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
        important_path = service.root / "users" / name / "memory_temporary_important.md"
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

        task_data = service.tasks(
            name,
            source=normalized_source,
            session_id=normalized_session,
        )
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
            web_congestion = service._get_chat_gate(name).status()
        except Exception:
            web_congestion = congestion["web"]
        try:
            message_congestion = (
                service._router_ref.queue_status()
                if service._router_ref is not None
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



