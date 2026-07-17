"""Web-facing service adapter over existing Run, history and user APIs."""

from __future__ import annotations

import os
from pathlib import Path
import queue
import re
import threading
from typing import Any, Callable, Iterator

from events import RunEvent
from run.agents import discover_agents
from run.config import load_config
from run.cron_store import CronStore
from run.engine import iter_request_events
from run.history import find_window, list_sessions, load_window, session_messages
from run.knowledge import build_index
from run.task_plan_store import PlanStore
from run.tools import discover_tools
from run.users import list_users


_SESSION_RE = re.compile(r"^[^\x00-\x1f]{1,128}$")
_WORKER_DONE = object()


class WebServiceError(RuntimeError):
    code = "internal_error"
    status = 500


class InvalidRequestError(WebServiceError):
    code = "invalid_request"
    status = 400


class NotFoundError(WebServiceError):
    code = "not_found"
    status = 404


class WebRunService:
    """A thin, injectable boundary between HTTP routes and the Run core."""

    def __init__(
        self,
        root: Path,
        *,
        event_source: Callable[..., Iterator[RunEvent]] = iter_request_events,
    ) -> None:
        self.root = root.resolve()
        self.event_source = event_source

    def health(self) -> dict[str, Any]:
        return {"status": "ok", "service": "kemo-agent-web", "version": 2}

    def users(self) -> list[dict[str, str]]:
        return [{"name": user} for user in list_users(self.root)]

    def require_user(self, user: Any) -> str:
        if not isinstance(user, str) or not user.strip():
            raise InvalidRequestError("user 必须是非空字符串")
        name = user.strip()
        if name not in set(list_users(self.root)):
            raise NotFoundError(f"用户不存在：{name}")
        return name

    def require_source(self, source: Any = "web") -> str:
        if source != "web":
            raise InvalidRequestError("Web API 当前仅允许 source=web")
        return "web"

    def require_session_id(self, session_id: Any) -> str:
        if not isinstance(session_id, str):
            raise InvalidRequestError("session_id 必须是字符串")
        value = session_id.strip()
        if not _SESSION_RE.fullmatch(value):
            raise InvalidRequestError("session_id 必须是 1–128 字符且不能包含控制字符")
        return value

    def require_prompt(self, prompt: Any) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise InvalidRequestError("prompt 必须是非空字符串")
        return prompt.strip()

    def sessions(self, user: Any, *, source: Any = "web") -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        return {
            "user": name,
            "source": normalized_source,
            "sessions": list_sessions(self.root, name, normalized_source),
        }

    def history(
        self,
        user: Any,
        session_id: Any,
        *,
        source: Any = "web",
    ) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        normalized_session = self.require_session_id(session_id)
        if find_window(self.root, name, normalized_source, normalized_session) is None:
            raise NotFoundError(f"会话不存在：{normalized_session}")
        return {
            "user": name,
            "source": normalized_source,
            "session_id": normalized_session,
            "messages": session_messages(
                self.root, name, normalized_source, normalized_session
            ),
        }

    @staticmethod
    def _plan_summary(plan: dict[str, Any]) -> dict[str, Any]:
        steps = []
        for item in plan.get("steps") or []:
            if not isinstance(item, dict):
                continue
            steps.append(
                {
                    "step_id": str(item.get("step_id") or ""),
                    "title": str(item.get("title") or ""),
                    "description": str(item.get("description") or ""),
                    "status": str(item.get("status") or "pending"),
                    "critical": bool(item.get("critical", True)),
                    "tool_name": str(item.get("tool_name") or ""),
                    "started_at": str(item.get("started_at") or ""),
                    "finished_at": str(item.get("finished_at") or ""),
                }
            )
        completed = sum(item["status"] in {"completed", "skipped"} for item in steps)
        return {
            "plan_id": str(plan.get("plan_id") or ""),
            "title": str(plan.get("title") or ""),
            "description": str(plan.get("description") or ""),
            "status": str(plan.get("status") or "pending"),
            "source": str(plan.get("source") or ""),
            "session_id": str(plan.get("session_id") or ""),
            "current_step": str(plan.get("current_step") or ""),
            "revision": int(plan.get("revision") or 1),
            "created_at": str(plan.get("created_at") or ""),
            "updated_at": str(plan.get("updated_at") or ""),
            "progress": {
                "completed": completed,
                "total": len(steps),
                "percent": round(completed * 100 / len(steps)) if steps else 0,
            },
            "steps": steps,
        }

    @staticmethod
    def _cron_summary(task: dict[str, Any]) -> dict[str, Any]:
        schedule = task.get("schedule")
        return {
            "task_id": str(task.get("task_id") or ""),
            "title": str(task.get("title") or ""),
            "status": str(task.get("status") or "enabled"),
            "schedule": dict(schedule) if isinstance(schedule, dict) else {},
            "source": str(task.get("source") or ""),
            "session_id": str(task.get("session_id") or ""),
            "next_run_at": str(task.get("next_run_at") or ""),
            "last_run_at": str(task.get("last_run_at") or ""),
            "run_count": int(task.get("run_count") or 0),
            "revision": int(task.get("revision") or 1),
            "created_at": str(task.get("created_at") or ""),
            "updated_at": str(task.get("updated_at") or ""),
            "last_state": "failed" if task.get("last_error") else (
                "completed" if task.get("last_result") is not None else "never"
            ),
        }

    def tasks(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        plans = [self._plan_summary(item) for item in PlanStore(self.root, name).list_plans()]
        crons = [self._cron_summary(item) for item in CronStore(self.root, name).list_tasks()]
        plans.sort(key=lambda item: item["updated_at"], reverse=True)
        crons.sort(key=lambda item: item["updated_at"], reverse=True)
        active_statuses = {"approved", "running", "paused"}
        waiting_statuses = {"pending", "approved", "paused"}
        return {
            "user": name,
            "summary": {
                "active_plans": sum(item["status"] in active_statuses for item in plans),
                "waiting_plans": sum(item["status"] in waiting_statuses for item in plans),
                "enabled_crons": sum(item["status"] == "enabled" for item in crons),
                "completed_plans": sum(item["status"] == "completed" for item in plans),
            },
            "plans": plans,
            "cron_tasks": crons,
        }

    def knowledge(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        config = load_config(name, self.root)
        knowledge_config = config.get("knowledge") or {}
        max_file_chars = max(1000, int(knowledge_config.get("max_file_chars", 20000)))
        documents = []
        for document in build_index(self.root, name, max_file_chars=max_file_chars):
            try:
                stat = document.path.stat()
                size = stat.st_size
                updated_at = stat.st_mtime
            except OSError:
                size = 0
                updated_at = 0
            documents.append(
                {
                    "scope": document.scope,
                    "relative_path": document.relative_path,
                    "title": document.title,
                    "size": size,
                    "updated_at": updated_at,
                }
            )
        return {
            "user": name,
            "enabled": bool(knowledge_config.get("enabled", True)),
            "retrieval": {
                "max_items": int(knowledge_config.get("max_items", 4)),
                "max_chars": int(knowledge_config.get("max_chars", 4000)),
                "minimum_score": int(knowledge_config.get("minimum_score", 2)),
                "mode": "file_index",
            },
            "summary": {
                "documents": len(documents),
                "user_documents": sum(item["scope"] == "user" for item in documents),
                "global_documents": sum(item["scope"] == "global" for item in documents),
            },
            "documents": documents,
            "extensions": {"kemo_graph": "not_connected"},
        }

    def skills(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        registry = discover_tools(self.root, name)
        layer_by_source = {
            "agent_create": "user",
            "user_create": "user",
            "shared_skills": "shared",
            "plugins": "core",
        }
        tools = []
        for tool in sorted(registry.tools.values(), key=lambda item: item.name.casefold()):
            tools.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "version": tool.version,
                    "enabled": tool.enabled,
                    "source": tool.source,
                    "layer": layer_by_source.get(tool.source, "project"),
                    "overrides": len(tool.overrides),
                }
            )
        return {
            "user": name,
            "summary": {
                "registered": len(tools),
                "enabled": sum(item["enabled"] for item in tools),
                "user": sum(item["layer"] == "user" for item in tools),
                "shared": sum(item["layer"] == "shared" for item in tools),
                "core": sum(item["layer"] == "core" for item in tools),
            },
            "tools": tools,
        }

    def sense(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        config = load_config(name, self.root)
        sense_config = config.get("global_sense")
        raw_sources = sense_config.get("sources") if isinstance(sense_config, dict) else []
        sources = []
        if isinstance(raw_sources, list):
            for index, item in enumerate(raw_sources):
                if not isinstance(item, dict):
                    continue
                sources.append(
                    {
                        "id": str(item.get("id") or f"source_{index + 1}"),
                        "name": str(item.get("name") or item.get("id") or "未命名来源"),
                        "description": str(item.get("description") or ""),
                        "layer": str(item.get("layer") or "user"),
                        "enabled": bool(item.get("enabled", True)),
                        "status": str(item.get("status") or "registered"),
                    }
                )
        core_dir = self.root / "global_sense"
        core_files = 0
        if core_dir.is_dir():
            core_files = sum(
                path.is_file() and path.suffix.casefold() in {".md", ".txt", ".json"}
                for path in core_dir.rglob("*")
            )
        return {
            "user": name,
            "registry_available": isinstance(sense_config, dict),
            "injection_enabled": bool(
                sense_config.get("enabled", False) if isinstance(sense_config, dict) else False
            ),
            "core_available": core_files > 0,
            "core_files": core_files,
            "summary": {
                "registered": len(sources),
                "enabled": sum(item["enabled"] for item in sources),
                "user": sum(item["layer"] == "user" for item in sources),
                "shared": sum(item["layer"] == "shared" for item in sources),
                "project": sum(item["layer"] == "project" for item in sources),
            },
            "sources": sources,
            "decisions": [],
        }

    def settings(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        config = load_config(name, self.root)
        provider = config.get("provider") or {}
        env_name = str(provider.get("api_key_env") or "")
        inline_key = bool(str(provider.get("api_key") or "").strip())
        environment_key = bool(env_name and os.getenv(env_name, "").strip())
        credential_source = "inline" if inline_key else "environment" if environment_key else "missing"
        tools = config.get("tools") or {}
        knowledge = config.get("knowledge") or {}
        memory = config.get("memory") or {}
        task_plan = config.get("task_plan") or {}
        cron = config.get("cron") or {}
        agents = config.get("agents") or {}
        return {
            "user": name,
            "schema_version": int(config.get("schema_version") or 1),
            "provider": {
                "type": str(provider.get("type") or ""),
                "base_url": str(provider.get("base_url") or ""),
                "model": str(provider.get("model") or ""),
                "timeout": float(provider.get("timeout") or 0),
                "stream": bool(provider.get("stream", False)),
                "credential_source": credential_source,
                "configured": bool(provider.get("type") and provider.get("model") and provider.get("base_url")),
            },
            "features": {
                "tools": bool(tools.get("enabled", True)),
                "knowledge": bool(knowledge.get("enabled", True)),
                "memory_extraction": bool(memory.get("extraction_enabled", True)),
                "memory_injection": bool(memory.get("injection_enabled", True)),
                "task_plan_auto_accept": bool(task_plan.get("auto_accept", False)),
                "cron": bool(cron.get("enabled", False)),
                "cron_auto_start": bool(cron.get("auto_start", False)),
            },
            "limits": {
                "context_tokens": int(agents.get("n4_token_limit") or 120000),
                "compression_ratio": float(agents.get("n5_token_compression_ratio") or 0.6),
                "task_plan_steps": int(task_plan.get("max_steps") or agents.get("n8_task_plan_max_steps") or 10),
                "tool_iterations": int(tools.get("max_iterations") or 8),
                "tool_timeout": float(tools.get("timeout") or 60),
                "knowledge_items": int(knowledge.get("max_items") or 4),
                "knowledge_chars": int(knowledge.get("max_chars") or 4000),
                "memory_items": int(memory.get("injection_max_items") or 8),
                "memory_chars": int(memory.get("injection_max_chars") or 2000),
            },
            "users": [item["name"] for item in self.users()],
        }

    def overview(self, user: Any, *, session_id: Any = "") -> dict[str, Any]:
        name = self.require_user(user)
        normalized_session = ""
        if session_id:
            normalized_session = self.require_session_id(session_id)
        task_data = self.tasks(name)
        knowledge_data = self.knowledge(name)
        skill_data = self.skills(name)
        settings_data = self.settings(name)
        sessions = list_sessions(self.root, name, "web")

        usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "estimated": False,
        }
        if normalized_session:
            directory = find_window(self.root, name, "web", normalized_session)
            if directory is not None:
                data = load_window(directory).get("data") or {}
                stored_usage = data.get("token_usage")
                if isinstance(stored_usage, dict):
                    usage.update(
                        {
                            key: stored_usage.get(key, usage[key])
                            for key in usage
                        }
                    )
        token_limit = int(settings_data["limits"]["context_tokens"])
        total_tokens = max(0, int(usage.get("total_tokens") or 0))
        percent = min(100, round(total_tokens * 100 / token_limit)) if token_limit > 0 else 0

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
                    "updated_at": task["updated_at"],
                }
            )
        activities.sort(key=lambda item: item["updated_at"], reverse=True)

        agent_registry = discover_agents(self.root)
        return {
            "user": name,
            "session_id": normalized_session,
            "context": {
                "usage": usage,
                "limit": token_limit,
                "percent": percent,
            },
            "provider": settings_data["provider"],
            "counts": {
                "sessions": len(sessions),
                "knowledge_documents": knowledge_data["summary"]["documents"],
                "enabled_tools": skill_data["summary"]["enabled"],
                "enabled_agents": len(agent_registry.enabled_agents()),
                "active_tasks": task_data["summary"]["active_plans"] + task_data["summary"]["enabled_crons"],
            },
            "active_plan": active_plan,
            "activities": activities[:6],
        }

    def stream_chat(
        self,
        user: Any,
        session_id: Any,
        prompt: Any,
        *,
        cancel_event: threading.Event,
    ) -> Iterator[RunEvent]:
        name = self.require_user(user)
        normalized_session = self.require_session_id(session_id)
        normalized_prompt = self.require_prompt(prompt)
        request = {
            "user": name,
            "source": "web",
            "session_id": normalized_session,
            "prompt": normalized_prompt,
            "stream": True,
        }
        # The Run generator owns thread-affine RLocks.  Its next()/close()
        # calls must therefore stay on one dedicated worker thread instead
        # of hopping between asyncio.to_thread workers.
        output: queue.Queue[RunEvent | BaseException | object] = queue.Queue(maxsize=32)

        def put(value: RunEvent | BaseException | object) -> bool:
            while True:
                if cancel_event.is_set():
                    return False
                try:
                    output.put(value, timeout=0.1)
                    return True
                except queue.Full:
                    continue

        def run_source() -> None:
            iterator: Iterator[RunEvent] | None = None
            try:
                iterator = iter(
                    self.event_source(
                        request,
                        root=self.root,
                        cancel_event=cancel_event,
                    )
                )
                for event in iterator:
                    if not put(event):
                        break
            except BaseException as exc:
                put(exc)
            finally:
                if iterator is not None:
                    close = getattr(iterator, "close", None)
                    if callable(close):
                        try:
                            close()
                        except BaseException as exc:
                            put(exc)
                put(_WORKER_DONE)

        worker = threading.Thread(
            target=run_source,
            name=f"web-run-{name}-{normalized_session}",
            daemon=True,
        )
        worker.start()

        def events() -> Iterator[RunEvent]:
            try:
                while True:
                    value = output.get()
                    if value is _WORKER_DONE:
                        return
                    if isinstance(value, BaseException):
                        raise value
                    if isinstance(value, RunEvent):
                        yield value
            finally:
                cancel_event.set()
                worker.join(timeout=1.0)

        return events()
