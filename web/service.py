"""基于现有运行、历史记录和用户 API 的面向 Web 的服务适配器。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
import queue
import re
import threading
import time
from typing import Any, Callable, Iterator
import uuid

from pydantic import TypeAdapter, ValidationError

from events import RunEvent
from provider.protocol.models import (
    AudioContent,
    ContentBlock,
    FileContent,
    ImageContent,
    VideoContent,
)
from run.agents import discover_agents
from run.config import (
    USER_ONLY_SECTIONS,
    load_config,
    read_json_object,
)
from run.context import estimate_text_tokens
from run.cron_store import CronStore
from run.engine import iter_request_events
from run.history import (
    delete_all_sessions as delete_all_history_sessions,
    delete_session as delete_history_session,
    find_window,
    list_sessions,
    load_window,
    rename_session as rename_history_session,
    runtime_window_path,
    session_messages,
)
from run.memory import MemoryStore
from run.prompt import (
    PROMPT_SECTION_ORDER,
    build_prompt_bundle,
    parse_prompt_settings,
)
from run.prompt_sources import iter_files, load_prompt_source_registry
from run.source_policy import MainAgentSourcePolicy
from run.task_plan_store import PlanStore
from run.tools import apply_runtime_tool_policy, discover_tools
from run.users import list_users


_SESSION_RE = re.compile(r"^[^\x00-\x1f]{1,128}$")
_SESSION_TITLE_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,80}$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_WORKER_DONE = object()
_REDACTED = "***"
_TOOL_TEXT_LIMIT = 5000
_CONTENT_LIST_ADAPTER = TypeAdapter(list[ContentBlock])
_SENSITIVE_CONFIG_KEYS = frozenset(
    {"api_key", "access_token", "password", "session_secret", "authorization"}
)
_CONFIG_SOURCE_PATHS = (
    "provider.type",
    "provider.base_url",
    "provider.model",
    "provider.stream",
    "tools.enabled",
    "tools.max_iterations",
    "tools.max_per_round",
    "tools.timeout",
    "memory.history_read_enabled",
    "memory.temporary_injection_limits.half_year",
    "memory.temporary_injection_limits.one_month",
    "memory.temporary_injection_limits.seven_days",
    "memory.important_memory_max_chars",
    "task_plan.auto_accept",
    "task_plan.max_steps",
    "cron.enabled",
    "runtime_host.enable_background_scheduler",
    "agents.max_rounds",
    "agents.token_limit",
    "agents.token_compression_ratio",
    "skills.shared_whitelist",
    "plugins.whitelist",
    "expand.global_whitelist",
    "expand.shared_whitelist",
    "perception.global_whitelist",
    "kemo_graph.kemo_graph_global_knowledge",
    "kemo_graph.kemo_graph_shared_knowledge",
    "kemo_graph.kemo_graph_user_knowledge",
    "kemo_graph.kemo_graph_temporary_memory",
)


def _tool_text_preview(value: Any) -> tuple[str, bool]:
    if isinstance(value, str):
        rendered = value
    else:
        try:
            rendered = json.dumps(value, ensure_ascii=False, indent=2, default=str)
        except (TypeError, ValueError):
            rendered = str(value)
    truncated = len(rendered) > _TOOL_TEXT_LIMIT
    return rendered[:_TOOL_TEXT_LIMIT], truncated


@dataclass(slots=True)
class ActiveRun:
    run_id: str
    user: str
    session_id: str
    guidance: queue.Queue[str] = field(default_factory=lambda: queue.Queue(maxsize=8))
    started_at: float = field(default_factory=time.monotonic)


class WebServiceError(RuntimeError):
    code = "internal_error"
    status = 500


class InvalidRequestError(WebServiceError):
    code = "invalid_request"
    status = 400


class NotFoundError(WebServiceError):
    code = "not_found"
    status = 404


class ConflictError(WebServiceError):
    code = "conflict"
    status = 409


def _is_sensitive_key(key: str) -> bool:
    lowered = key.casefold()
    return lowered in _SENSITIVE_CONFIG_KEYS or lowered.endswith("_secret")


def _redact_config(value: Any, path: tuple[str, ...] = ()) -> tuple[Any, list[str]]:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        redacted: list[str] = []
        for key, item in value.items():
            rendered = str(key)
            current = (*path, rendered)
            if _is_sensitive_key(rendered):
                result[rendered] = _REDACTED
                redacted.append(".".join(current))
                continue
            clean, nested = _redact_config(item, current)
            result[rendered] = clean
            redacted.extend(nested)
        return result, redacted
    if isinstance(value, list):
        result = []
        redacted: list[str] = []
        for index, item in enumerate(value):
            clean, nested = _redact_config(item, (*path, str(index)))
            result.append(clean)
            redacted.extend(nested)
        return result, redacted
    return value, []


class WebRunService:
    """A thin, injectable boundary between HTTP routes and the Run core."""

    def __init__(
        self,
        root: Path,
        *,
        event_source: Callable[..., Iterator[RunEvent]] = iter_request_events,
        runtime_status_provider: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.event_source = event_source
        self.runtime_status_provider = runtime_status_provider
        self._active_runs: dict[str, ActiveRun] = {}
        self._active_runs_lock = threading.RLock()

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

    def require_session_title(self, title: Any) -> str:
        if not isinstance(title, str):
            raise InvalidRequestError("title 必须是字符串")
        value = title.strip()
        if not _SESSION_TITLE_RE.fullmatch(value):
            raise InvalidRequestError("title 必须是 1–80 字符且不能包含控制字符")
        return value

    def require_prompt(self, prompt: Any) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise InvalidRequestError("prompt 必须是非空字符串")
        return prompt.strip()

    def require_content(self, content: Any) -> list[dict[str, Any]]:
        if content in (None, []):
            return []
        if not isinstance(content, list):
            raise InvalidRequestError("content 必须是 Content Block 数组")
        try:
            blocks = _CONTENT_LIST_ADAPTER.validate_python(content)
        except ValidationError as exc:
            raise InvalidRequestError("content 包含无效的多模态内容块") from exc
        media_types = (ImageContent, AudioContent, VideoContent, FileContent)
        for block in blocks:
            if isinstance(block, media_types):
                if block.source is not None:
                    raise InvalidRequestError(
                        "Web 多模态入口只接受 asset_id，不接受 URL、Base64 或本地路径"
                    )
                if not block.asset_id:
                    raise InvalidRequestError("Web 媒体内容必须提供 asset_id")
        return [block.model_dump(mode="json", exclude_none=True) for block in blocks]

    def require_run_id(self, run_id: Any) -> str:
        if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id.strip()):
            raise InvalidRequestError("run_id 必须是 8–128 位字母、数字、下划线或连字符")
        return run_id.strip()

    def submit_guidance(self, user: Any, run_id: Any, guidance: Any) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_run_id = self.require_run_id(run_id)
        text = self.require_prompt(guidance)
        with self._active_runs_lock:
            active = self._active_runs.get(normalized_run_id)
            if active is None:
                raise NotFoundError(f"运行不存在或已结束：{normalized_run_id}")
            if active.user != name:
                raise NotFoundError(f"运行不存在或已结束：{normalized_run_id}")
            try:
                active.guidance.put_nowait(text)
            except queue.Full as exc:
                raise ConflictError("运行中引导队列已满，请等待当前引导被处理") from exc
            queued = active.guidance.qsize()
        return {
            "run_id": normalized_run_id,
            "user": name,
            "session_id": active.session_id,
            "status": "queued",
            "queued": queued,
        }

    def _config_path(self, user: str) -> Path:
        return self.root / "users" / user / "user_config.json"

    @staticmethod
    def _has_path(value: dict[str, Any], dotted: str) -> bool:
        current: Any = value
        for part in dotted.split("."):
            if not isinstance(current, dict) or part not in current:
                return False
            current = current[part]
        return True

    def _config_provenance(self, user: str) -> dict[str, str]:
        global_config = read_json_object(self.root / "config" / "global_config.json")
        user_config = read_json_object(self._config_path(user), allow_empty=True)
        return {
            dotted: (
                "user"
                if self._has_path(user_config, dotted)
                else "global"
                if (
                    dotted.split(".", 1)[0] not in USER_ONLY_SECTIONS
                    and self._has_path(global_config, dotted)
                )
                else "default"
            )
            for dotted in _CONFIG_SOURCE_PATHS
        }

    def user_config(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        path = self._config_path(name)
        config = read_json_object(path, allow_empty=True)
        redacted, redacted_paths = _redact_config(config)
        return {
            "user": name,
            "config": redacted,
            "redacted_paths": redacted_paths,
        }

    def sessions(self, user: Any, *, source: Any = "web") -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        return {
            "user": name,
            "source": normalized_source,
            "sessions": list_sessions(self.root, name, normalized_source),
        }

    def rename_session(
        self,
        user: Any,
        session_id: Any,
        title: Any,
        *,
        source: Any = "web",
    ) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        normalized_session = self.require_session_id(session_id)
        normalized_title = self.require_session_title(title)
        changed = rename_history_session(
            self.root,
            name,
            normalized_source,
            normalized_session,
            normalized_title,
        )
        if changed == 0:
            raise NotFoundError(f"会话不存在：{normalized_session}")
        session = next(
            (
                item
                for item in list_sessions(self.root, name, normalized_source)
                if item.get("session_id") == normalized_session
            ),
            None,
        )
        return {
            "user": name,
            "source": normalized_source,
            "session": session,
        }

    def delete_session(
        self,
        user: Any,
        session_id: Any,
        *,
        source: Any = "web",
    ) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        normalized_session = self.require_session_id(session_id)
        with self._active_runs_lock:
            if any(
                active.user == name and active.session_id == normalized_session
                for active in self._active_runs.values()
            ):
                raise ConflictError("会话正在运行，结束当前响应后再删除")
            deleted = delete_history_session(
                self.root,
                name,
                normalized_source,
                normalized_session,
            )
        if deleted == 0:
            raise NotFoundError(f"会话不存在：{normalized_session}")
        return {
            "user": name,
            "source": normalized_source,
            "session_id": normalized_session,
            "deleted": True,
        }

    def delete_all_sessions(
        self,
        user: Any,
        *,
        source: Any = "web",
    ) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        with self._active_runs_lock:
            if any(active.user == name for active in self._active_runs.values()):
                raise ConflictError("存在正在运行的会话，结束当前响应后再全部删除")
            deleted_sessions, deleted_windows = delete_all_history_sessions(
                self.root,
                name,
                normalized_source,
            )
        return {
            "user": name,
            "source": normalized_source,
            "deleted": True,
            "deleted_sessions": deleted_sessions,
            "deleted_windows": deleted_windows,
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
        directory = find_window(self.root, name, normalized_source, normalized_session)
        if directory is None:
            raise NotFoundError(f"会话不存在：{normalized_session}")
        window = load_window(directory)
        raw_metrics = (window.get("data") or {}).get("round_metrics") or []
        round_metrics = []
        if isinstance(raw_metrics, list):
            for item in raw_metrics:
                if not isinstance(item, dict):
                    continue
                usage = item.get("usage") if isinstance(item.get("usage"), dict) else {}
                round_metrics.append(
                    {
                        "round": int(item.get("round") or 0),
                        "usage": dict(usage),
                        "elapsed_ms": max(0, int(item.get("elapsed_ms") or 0)),
                        "tool_calls": max(0, int(item.get("tool_calls") or 0)),
                        "guidance": [
                            str(value)
                            for value in item.get("guidance", [])
                            if isinstance(value, str)
                        ] if isinstance(item.get("guidance"), list) else [],
                        "tool_pause": (
                            dict(item["tool_pause"])
                            if isinstance(item.get("tool_pause"), dict)
                            else None
                        ),
                    }
                )
        reasoning_by_round: dict[int, str] = {}
        raw_reasoning = (window.get("think") or {}).get("rounds") or []
        if isinstance(raw_reasoning, list):
            for item in raw_reasoning:
                if not isinstance(item, dict):
                    continue
                round_number = int(item.get("round") or 0)
                if round_number > 0:
                    reasoning_by_round[round_number] = str(item.get("content") or "")

        tools_by_round: dict[int, list[dict[str, Any]]] = {}
        raw_tools = (window.get("tool") or {}).get("rounds") or []
        if isinstance(raw_tools, list):
            for item in raw_tools:
                if not isinstance(item, dict):
                    continue
                round_number = int(item.get("round") or 0)
                if round_number <= 0 or not isinstance(item.get("calls"), list):
                    continue
                calls = []
                for call in item["calls"]:
                    if not isinstance(call, dict):
                        continue
                    arguments_text, arguments_truncated = _tool_text_preview(call.get("arguments") or {})
                    result_text, result_truncated = _tool_text_preview(call.get("result"))
                    raw_status = str(call.get("status") or "completed").casefold()
                    status = (
                        "running"
                        if raw_status in {"running", "started", "pending", "deferred"}
                        else "error"
                        if raw_status
                        in {"failed", "error", "temporarily_unavailable"}
                        else "success"
                    )
                    calls.append(
                        {
                            "call_id": str(call.get("id") or ""),
                            "name": str(call.get("name") or "未知工具"),
                            "status": status,
                            "elapsed_ms": max(0, int(call.get("elapsed_ms") or 0)),
                            "arguments_text": arguments_text,
                            "arguments_truncated": arguments_truncated,
                            "result_text": result_text,
                            "result_truncated": result_truncated,
                        }
                    )
                tools_by_round[round_number] = calls

        round_traces = [
            {
                "round": round_number,
                "reasoning": reasoning_by_round.get(round_number, ""),
                "tools": tools_by_round.get(round_number, []),
            }
            for round_number in sorted(reasoning_by_round.keys() | tools_by_round.keys())
        ]
        return {
            "user": name,
            "source": normalized_source,
            "session_id": normalized_session,
            "messages": session_messages(
                self.root, name, normalized_source, normalized_session
            ),
            "round_metrics": round_metrics,
            "round_traces": round_traces,
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
        summary = {
            "task_id": str(task.get("task_id") or ""),
            "title": str(task.get("title") or ""),
            "status": str(task.get("status") or "enabled"),
            "type": str(task.get("type") or ""),
            "next_run_at": str(task.get("next_run_at") or ""),
            "latest_run_at": str(task.get("latest_run_at") or ""),
            "created_at": str(task.get("created_at") or ""),
        }
        if task.get("type") == "daily":
            summary["time"] = str(task.get("time") or "")
        elif task.get("type") == "recurring":
            summary["interval_seconds"] = int(task.get("interval_seconds") or 0)
        summary["last_state"] = (
            "never" if not task.get("latest_run_at") else str(task.get("status") or "completed")
        )
        return summary

    def tasks(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        plans = [self._plan_summary(item) for item in PlanStore(self.root, name).list_plans()]
        crons = [self._cron_summary(item) for item in CronStore(self.root, name).list_tasks()]
        plans.sort(key=lambda item: item["updated_at"], reverse=True)
        crons.sort(
            key=lambda item: item.get("latest_run_at") or item.get("created_at") or "",
            reverse=True,
        )
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
        source_policy = MainAgentSourcePolicy.from_config(config)
        policy_summary = source_policy.public_summary()
        documents = []
        for scope, base in (
            ("user", self.root / "users" / name / "knowledge"),
            ("shared", self.root / "shared_knowledge"),
            ("global", self.root / "global_knowledge"),
        ):
            for path in iter_files(base, suffixes={".md", ".txt", ".json"}):
                try:
                    stat = path.stat()
                    size = stat.st_size
                    updated_at = stat.st_mtime
                except OSError:
                    size = 0
                    updated_at = 0
                documents.append(
                    {
                        "scope": scope,
                        "relative_path": path.relative_to(base).as_posix(),
                        "title": path.stem,
                        "size": size,
                        "updated_at": updated_at,
                        "active_for_main_agent": scope
                        in source_policy.direct_knowledge_scopes(),
                    }
                )
        return {
            "user": name,
            "enabled": True,
            "retrieval": {
                "mode": "index_only",
                "full_index": True,
            },
            "summary": {
                "documents": len(documents),
                "user_documents": sum(item["scope"] == "user" for item in documents),
                "shared_documents": sum(item["scope"] == "shared" for item in documents),
                "global_documents": sum(item["scope"] == "global" for item in documents),
            },
            "documents": documents,
            "extensions": {"kemo_graph": policy_summary["kemo_graph"]["status"]},
            "source_policy": policy_summary,
        }

    def skills(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        config = load_config(name, self.root)
        source_policy = MainAgentSourcePolicy.from_config(config)
        registry = apply_runtime_tool_policy(discover_tools(self.root, name), config)
        layer_by_source = {"plugins": "core"}
        tools = []
        for tool in sorted(registry.tools.values(), key=lambda item: item.name.casefold()):
            tools.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "version": tool.version,
                    "enabled": tool.enabled,
                    "source": tool.source,
                    "layer": layer_by_source.get(tool.source, "core"),
                    "overrides": len(tool.overrides),
                }
            )
        prompt_skills = []
        prompt_sources = load_prompt_source_registry(self.root, name)
        for descriptor in prompt_sources.select_skills():
            base = (
                self.root / "shared_skills"
                if descriptor.scope == "shared"
                else self.root / "users" / name / "user_skills"
            )
            logical_name = descriptor.path.parent.relative_to(base).as_posix()
            allowed = (
                source_policy.shared_skills
                if descriptor.scope == "shared"
                else source_policy.user_skills
            )
            prompt_skills.append(
                {
                    "name": logical_name,
                    "title": descriptor.title,
                    "description": descriptor.description,
                    "scope": descriptor.scope,
                    "active_for_main_agent": allowed.allows(logical_name),
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
            "prompt_summary": {
                "registered": len(prompt_skills),
                "active": sum(item["active_for_main_agent"] for item in prompt_skills),
                "user": sum(item["scope"] == "user" for item in prompt_skills),
                "shared": sum(item["scope"] == "shared" for item in prompt_skills),
            },
            "prompt_skills": prompt_skills,
            "source_policy": source_policy.public_summary(),
        }

    def sense(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        config = load_config(name, self.root)
        source_policy = MainAgentSourcePolicy.from_config(config)
        core_dir = self.root / "global_sense"
        registry_available = (core_dir / "register.py").is_file()
        registry = load_prompt_source_registry(self.root, name)
        inventory = registry.perception_inventory(
            allow_modules=source_policy.global_perception.selector()
        )
        prompt_settings = parse_prompt_settings(config)
        selection = registry.select_perception(
            max_chars=prompt_settings.char_limits["perception"],
            mode=prompt_settings.injection_mode["perception"],
            allow_modules=source_policy.global_perception.selector(),
        )
        injected_files = set(selection.source_files)
        sources = [
            {
                "id": item["name"],
                "name": item["name"],
                "display_name": item["display_name"],
                "description": (
                    f"标准数据文件：{item['data_md']}"
                    if item["valid"]
                    else f"模块配置无效：{item['error']}"
                ),
                "layer": "global",
                "enabled": item["active"],
                "active_for_main_agent": item["active"],
                "status": item["status"],
                "data_md": item["data_md"],
                "recent_update": item["recent_update"],
                "health": item["health"],
                "valid": item["valid"],
                "error": item["error"],
                "start_update": item["start_update"],
                "files": item["files"],
                "registered_items": item["files"],
                "injected_items": sum(
                    path in injected_files
                    for path in (
                        f"{item['root']}/{item['name']}/{relative_path}"
                        for relative_path in item["data_items"]
                    )
                ),
                "data_items": item["data_items"],
                "updated_at": item["updated_at"],
            }
            for item in inventory
        ]
        core_files = sum(item["files"] for item in inventory)
        preview_limit = 4000
        preview = selection.text[:preview_limit]
        return {
            "user": name,
            "registry_available": registry_available,
            "injection_enabled": any(item["active_for_main_agent"] for item in sources),
            "core_available": bool(sources),
            "core_files": core_files,
            "summary": {
                "registered": len(sources),
                "enabled": sum(item["enabled"] for item in sources),
                "user": sum(item["layer"] == "user" for item in sources),
                "shared": sum(item["layer"] == "shared" for item in sources),
                "global": sum(item["layer"] == "global" for item in sources),
                "healthy": sum(item["valid"] and item["health"] == "正常" for item in sources),
                "unhealthy": sum(item["health"] == "异常" for item in sources),
                "invalid": sum(not item["valid"] for item in sources),
                "registered_data": core_files,
                "injected_data": selection.injected_items,
            },
            "sources": sources,
            "injection": {
                "enabled": bool(selection.text),
                "registered_items": core_files,
                "injected_items": selection.injected_items,
                "original_chars": selection.original_chars,
                "injected_chars": selection.injected_chars,
                "estimated_tokens": estimate_text_tokens(selection.text),
                "truncated": selection.truncated,
                "preview": preview,
                "preview_truncated": len(selection.text) > preview_limit,
                "source_files": list(selection.source_files),
                "prompt_section": "perception",
                "prompt_position": "System Prompt / Global Sense",
            },
            "decisions": [],
            "source_policy": source_policy.public_summary(),
        }

    def settings(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        config = load_config(name, self.root)
        source_policy = MainAgentSourcePolicy.from_config(config)
        provider = config.get("provider") or {}
        env_name = str(provider.get("api_key_env") or "")
        inline_key = bool(str(provider.get("api_key") or "").strip())
        environment_key = bool(env_name and os.getenv(env_name, "").strip())
        credential_source = "inline" if inline_key else "environment" if environment_key else "missing"
        tools = config.get("tools") or {}
        memory = config.get("memory") or {}
        temporary_memory_limits = memory.get("temporary_injection_limits") or {}
        task_plan = config.get("task_plan") or {}
        cron = config.get("cron") or {}
        runtime_host = config.get("runtime_host") or {}
        agents = config.get("agents") or {}
        return {
            "user": name,
            "schema_version": int(config.get("schema_version") or 1),
            "provider": {
                "type": str(provider.get("type") or ""),
                "base_url": str(provider.get("base_url") or ""),
                "model": str(provider.get("model") or ""),
                "timeout": 120.0,
                "stream": bool(provider.get("stream", True)),
                "credential_source": credential_source,
                "configured": bool(provider.get("type") and provider.get("model") and provider.get("base_url")),
            },
            "features": {
                "tools": bool(tools.get("enabled", True)),
                "knowledge": True,
                "history_read": bool(memory.get("history_read_enabled", True)),
                "memory_injection": True,
                "task_plan_auto_accept": bool(task_plan.get("auto_accept", False)),
                "cron": bool(cron.get("enabled", False)),
                "background_scheduler": bool(
                    runtime_host.get("enable_background_scheduler", True)
                ),
            },
            "limits": {
                "context_rounds": int(agents.get("max_rounds") or 30),
                "context_tokens": int(agents.get("token_limit") or 120000),
                "compression_ratio": float(
                    agents.get("token_compression_ratio") or 0.6
                ),
                "task_plan_steps": int(task_plan.get("max_steps") or 10),
                "tool_iterations": int(tools.get("max_iterations") or 8),
                "tool_timeout": float(tools.get("timeout") or 60),
                "tool_max_per_round": tools.get("max_per_round"),
                "memory_items": sum(
                    int(temporary_memory_limits.get(tier, default))
                    for tier, default in (
                        ("half_year", 300),
                        ("one_month", 200),
                        ("seven_days", 100),
                    )
                ),
                "memory_chars": int(memory.get("important_memory_max_chars", 2000)),
            },
            "users": [item["name"] for item in self.users()],
            "source_policy": source_policy.public_summary(),
            "provenance": self._config_provenance(name),
        }

    def prompt_sections(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        config = load_config(name, self.root)
        bundle = build_prompt_bundle(self.root, name, config)
        selected = bundle.diagnostics.get("sections") or {}
        sections = []
        for section_name in PROMPT_SECTION_ORDER:
            detail = selected.get(section_name)
            sections.append(
                {
                    "name": section_name,
                    "status": "injected" if isinstance(detail, dict) else "omitted",
                    "original_items": int((detail or {}).get("original_items") or 0),
                    "injected_items": int((detail or {}).get("injected_items") or 0),
                    "original_chars": int((detail or {}).get("original_chars") or 0),
                    "injected_chars": int((detail or {}).get("injected_chars") or 0),
                    "truncated": bool((detail or {}).get("truncated", False)),
                    "source_files": list((detail or {}).get("source_files") or []),
                }
            )
        source_selection = bundle.diagnostics.get("source_selection") or {}
        return {
            "user": name,
            "total_chars": int(bundle.diagnostics.get("total_chars") or 0),
            "sections": sections,
            "source_policy": bundle.diagnostics.get("source_policy") or {},
            "source_selection": source_selection,
            "expand": source_selection.get("expand") or {},
        }

    def memory_summary(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        config = load_config(name, self.root)
        items = MemoryStore(self.root, name, config).list_items()
        result = []
        for item in items:
            content = str(item.get("content") or "")
            result.append(
                {
                    "filename": str(item.get("filename") or ""),
                    "tier": str(item.get("tier") or ""),
                    "weight": int(item.get("weight") or 0),
                    "updated_at": str(item.get("updated_at") or ""),
                    "expires_at": item.get("expires_at"),
                    "preview": content[:160],
                    "truncated": len(content) > 160,
                }
            )
        tiers = ("seven_days", "one_month", "half_year", "permanent")
        return {
            "user": name,
            "summary": {
                "total": len(result),
                **{tier: sum(item["tier"] == tier for item in result) for tier in tiers},
            },
            "items": result,
        }

    def _summary_cache_status(self, user: str, session_id: str) -> dict[str, Any]:
        empty = {
            "exists": False,
            "covered_rounds": [],
            "created_at": "",
            "window": "",
        }
        if not session_id:
            return empty
        directory = find_window(self.root, user, "web", session_id)
        if directory is None:
            return empty
        path = runtime_window_path(directory) / "context_summary.json"
        if not path.is_file():
            return {**empty, "window": directory.name}
        try:
            value = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return {**empty, "exists": True, "window": directory.name, "invalid": True}
        return {
            "exists": True,
            "covered_rounds": [
                int(item) for item in value.get("covered_rounds", []) if isinstance(item, int)
            ] if isinstance(value, dict) else [],
            "created_at": str(value.get("created_at") or "") if isinstance(value, dict) else "",
            "window": directory.name,
        }

    def _runtime_status(self) -> dict[str, Any]:
        if self.runtime_status_provider is None:
            return {"state": "unmanaged", "components": {}}
        try:
            value = self.runtime_status_provider()
        except Exception:
            return {"state": "unavailable", "components": {}}
        if not isinstance(value, dict):
            return {"state": "unavailable", "components": {}}
        components = value.get("components")
        return {
            "state": str(value.get("state") or "unknown"),
            "components": dict(components) if isinstance(components, dict) else {},
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
        rounds = 0
        if normalized_session:
            directory = find_window(self.root, name, "web", normalized_session)
            if directory is not None:
                data = load_window(directory).get("data") or {}
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

        agent_registry = discover_agents(self.root, name)
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
                "rounds": rounds,
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
            "agents": agents,
            "summary_cache": self._summary_cache_status(name, normalized_session),
            "runtime_host": self._runtime_status(),
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
        run_id: Any = "",
        content: Any = None,
    ) -> Iterator[RunEvent]:
        name = self.require_user(user)
        normalized_session = self.require_session_id(session_id)
        if not isinstance(prompt, str):
            raise InvalidRequestError("prompt 必须是字符串")
        normalized_prompt = prompt.strip()
        normalized_content = self.require_content(content)
        if not normalized_prompt and not normalized_content:
            raise InvalidRequestError("prompt 和 content 不能同时为空")
        normalized_run_id = (
            self.require_run_id(run_id) if run_id else f"run_{uuid.uuid4().hex}"
        )
        active = ActiveRun(normalized_run_id, name, normalized_session)
        with self._active_runs_lock:
            if normalized_run_id in self._active_runs:
                raise ConflictError(f"run_id 已在使用：{normalized_run_id}")
            self._active_runs[normalized_run_id] = active
        request = {
            "user": name,
            "source": "web",
            "session_id": normalized_session,
            "prompt": normalized_prompt,
            "content": normalized_content,
            "stream": True,
            "run_id": normalized_run_id,
            "_guidance_queue": active.guidance,
        }
                # Run 生成器拥有线程仿射 RLock。  它的 next()/close()
                # 因此，调用必须保留在一个专用工作线程上
                # 在 asyncio.to_thread 工作线程之间跳转。
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
                with self._active_runs_lock:
                    self._active_runs.pop(normalized_run_id, None)

        return events()
