"""基于现有运行、历史记录和用户 API 的面向 Web 的服务适配器。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from pathlib import PurePosixPath
import queue
import re
import threading
import time
from typing import Any, Callable, Iterator
import uuid
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter, ValidationError

from cron.schedule import compute_next_run
from events import RunEvent
from message.identity import IdentityResolver
from message.plugin import MessagePluginConfig, MessagePluginError
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
from run.cron_store import (
    CronConflictError,
    CronError,
    CronNotFoundError,
    CronStore,
    normalize_task,
)
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
from run.memory import (
    TIERS,
    MemoryError as RuntimeMemoryError,
    MemoryStore,
    contains_sensitive_credential,
    normalize_memory_filename,
    utc_now,
)
from run.prompt import (
    PROMPT_SECTION_ORDER,
    build_prompt_bundle,
    parse_prompt_settings,
)
from run.prompt_sources import iter_files, load_prompt_source_registry
from run.source_policy import MainAgentSourcePolicy
from run.task_plan_store import (
    PlanConflictError,
    PlanError,
    PlanNotFoundError,
    PlanStore,
    normalize_plan,
)
from run.tools import apply_runtime_tool_policy, discover_tools
from run.users import list_users


_SESSION_RE = re.compile(r"^[^\x00-\x1f]{1,128}$")
_SESSION_TITLE_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,80}$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_WORKER_DONE = object()
_REDACTED = "***"
_TOOL_TEXT_LIMIT = 5000
AVATAR_MAX_BYTES = 5 * 1024 * 1024
FILE_UPLOAD_MAX_BYTES = 25 * 1024 * 1024
TEXT_DOCUMENT_MAX_CHARS = 1_000_000
IMPORTANT_MEMORY_MAX_HARD_CHARS = 65_536
_CONTENT_LIST_ADAPTER = TypeAdapter(list[ContentBlock])
_FILE_SCOPES = frozenset({"file_upload", "download"})
_KNOWLEDGE_SCOPES = frozenset({"user", "shared", "global"})
_KNOWLEDGE_SUFFIXES = frozenset({".md", ".txt", ".json"})
_EDITABLE_TEXT_SUFFIXES = frozenset(
    {".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".tsv", ".log", ".py", ".js", ".ts", ".tsx", ".css", ".html"}
)
_AVATAR_FORMATS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
_AVATAR_SEARCH_ORDER = (".jpg", ".jpeg", ".png", ".gif", ".webp")
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


def _visible_children(directory: Path) -> list[Path]:
    try:
        children = [
            item
            for item in directory.iterdir()
            if not item.name.startswith(".")
            and item.name != "__pycache__"
            and not item.is_symlink()
            and not getattr(item, "is_junction", lambda: False)()
        ]
    except (FileNotFoundError, NotADirectoryError):
        return []
    except OSError:
        return []
    children.sort(
        key=lambda item: (
            0 if item.is_dir() else 1,
            item.name.casefold(),
            item.name,
        )
    )
    return children


def _directory_tree(directory: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    summary = {"total_files": 0, "total_dirs": 0, "total_size": 0}

    def visit(current: Path) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in _visible_children(current):
            relative = item.relative_to(directory).as_posix()
            try:
                if item.is_dir():
                    summary["total_dirs"] += 1
                    result.append(
                        {
                            "type": "directory",
                            "name": item.name,
                            "relative_path": relative,
                            "children": visit(item),
                        }
                    )
                elif item.is_file():
                    stat = item.stat()
                    summary["total_files"] += 1
                    summary["total_size"] += stat.st_size
                    result.append(
                        {
                            "type": "file",
                            "name": item.name,
                            "relative_path": relative,
                            "size": stat.st_size,
                            "updated_at": stat.st_mtime,
                            "extension": item.suffix.lower(),
                        }
                    )
            except OSError:
                continue
        return result

    return visit(directory), summary


def _flat_files(directory: Path, *, relative_to: Path | None = None) -> list[dict[str, Any]]:
    base = relative_to or directory
    files: list[dict[str, Any]] = []

    def visit(current: Path) -> None:
        for item in _visible_children(current):
            try:
                if item.is_dir():
                    visit(item)
                elif item.is_file():
                    stat = item.stat()
                    files.append(
                        {
                            "name": item.name,
                            "relative_path": item.relative_to(base).as_posix(),
                            "size": stat.st_size,
                            "updated_at": stat.st_mtime,
                        }
                    )
            except OSError:
                continue

    visit(directory)
    files.sort(key=lambda item: (str(item["relative_path"]).casefold(), item["relative_path"]))
    return files


def _safe_relative_target(directory: Path, value: Any) -> tuple[str, Path]:
    if not isinstance(value, str) or not value.strip():
        raise InvalidRequestError("path 必须是非空相对路径")
    normalized = value.strip().replace("\\", "/")
    pure = PurePosixPath(normalized)
    if (
        pure.is_absolute()
        or Path(normalized).is_absolute()
        or ".." in pure.parts
        or "\x00" in normalized
        or (pure.parts and ":" in pure.parts[0])
    ):
        raise InvalidRequestError("path 必须位于允许的目录内且不能包含 ..")
    root = directory.resolve()
    candidate = root.joinpath(*pure.parts)
    target = candidate.resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise InvalidRequestError("path 越出允许的目录") from None
    return pure.as_posix(), candidate


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _reject_link_path(root: Path, target: Path) -> None:
    """Reject existing symlink/junction components for every Web mutation."""

    current = root.resolve()
    try:
        relative = target.relative_to(root)
    except ValueError:
        raise InvalidRequestError("path 越出允许的目录") from None
    for part in relative.parts:
        current = current / part
        if current.exists() and (
            current.is_symlink() or getattr(current, "is_junction", lambda: False)()
        ):
            raise InvalidRequestError("Web 文件操作不允许符号链接或目录联接")


def _validated_text(value: Any, *, field: str = "content", max_chars: int = TEXT_DOCUMENT_MAX_CHARS) -> str:
    if not isinstance(value, str):
        raise InvalidRequestError(f"{field} 必须是字符串")
    if len(value) > max_chars:
        raise InvalidRequestError(f"{field} 超过最大长度 {max_chars}")
    return value


def _contains_redacted_placeholder(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_redacted_placeholder(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_redacted_placeholder(item) for item in value)
    return value == _REDACTED


def _merge_patch(target: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    result = dict(target)
    for key, value in changes.items():
        if not isinstance(key, str) or not key:
            raise InvalidRequestError("配置字段名必须是非空字符串")
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_patch(result[key], value)
        else:
            result[key] = value
    return result


def _image_media_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


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

    def global_config(self) -> dict[str, Any]:
        path = self.root / "config" / "global_config.json"
        config = read_json_object(path)
        redacted, redacted_paths = _redact_config(config)
        return {
            "scope": "global",
            "config": redacted,
            "redacted_paths": redacted_paths,
        }

    def _patch_config_document(
        self,
        path: Path,
        changes: Any,
        *,
        user: str | None,
    ) -> dict[str, Any]:
        if not isinstance(changes, dict) or not changes:
            raise InvalidRequestError("changes 必须是非空对象")
        if _contains_redacted_placeholder(changes):
            raise InvalidRequestError("不能把脱敏占位符 *** 写回配置")
        current = read_json_object(path, allow_empty=user is not None)
        updated = _merge_patch(current, changes)
        encoded = (json.dumps(updated, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        previous = path.read_bytes() if path.is_file() else None
        _atomic_write(path, encoded)
        try:
            if user is not None:
                load_config(user, self.root)
            else:
                # Validate the global document through one concrete user when possible.
                available = self.users()
                if available:
                    load_config(available[0]["name"], self.root)
        except Exception as exc:
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_write(path, previous)
            raise InvalidRequestError(f"配置校验失败：{exc}") from None
        redacted, redacted_paths = _redact_config(updated)
        response: dict[str, Any] = {
            "config": redacted,
            "redacted_paths": redacted_paths,
            "updated": True,
        }
        if user is None:
            response["scope"] = "global"
        else:
            response["user"] = user
        return response

    def patch_user_config(self, user: Any, changes: Any) -> dict[str, Any]:
        name = self.require_user(user)
        return self._patch_config_document(self._config_path(name), changes, user=name)

    def patch_global_config(self, changes: Any) -> dict[str, Any]:
        return self._patch_config_document(
            self.root / "config" / "global_config.json",
            changes,
            user=None,
        )

    def preferences(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        path = self.root / "users" / name / "web_preferences.json"
        value = read_json_object(path, allow_empty=True)
        appearance = value.get("appearance") if isinstance(value.get("appearance"), dict) else {}
        return {
            "user": name,
            "appearance": {
                "theme": appearance.get("theme", "light"),
                "font_size": appearance.get("font_size", "medium"),
            },
        }

    def patch_preferences(self, user: Any, changes: Any) -> dict[str, Any]:
        name = self.require_user(user)
        if not isinstance(changes, dict):
            raise InvalidRequestError("appearance 必须是对象")
        theme = changes.get("theme", self.preferences(name)["appearance"]["theme"])
        font_size = changes.get(
            "font_size", self.preferences(name)["appearance"]["font_size"]
        )
        if theme not in {"light", "dark"}:
            raise InvalidRequestError("theme 只允许 light 或 dark")
        if font_size not in {"small", "medium", "large"}:
            raise InvalidRequestError("font_size 只允许 small、medium 或 large")
        value = {"schema_version": 1, "appearance": {"theme": theme, "font_size": font_size}}
        path = self.root / "users" / name / "web_preferences.json"
        _atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())
        return {"user": name, "appearance": value["appearance"], "updated": True}

    def _project_path(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def _file_scope_root(self, user: Any, scope: Any) -> tuple[str, str, Path]:
        name = self.require_user(user)
        if not isinstance(scope, str) or scope not in _FILE_SCOPES:
            raise InvalidRequestError(
                "scope 只允许 file_upload 或 download"
            )
        return name, scope, self.root / "users" / name / scope

    def files(self, user: Any, scope: Any) -> dict[str, Any]:
        name, normalized_scope, directory = self._file_scope_root(user, scope)
        tree, summary = _directory_tree(directory)
        return {
            "user": name,
            "scope": normalized_scope,
            "root": self._project_path(directory),
            "summary": summary,
            "tree": tree,
        }

    def _write_area_file(self, directory: Path, path: Any, data: bytes) -> dict[str, Any]:
        if len(data) > FILE_UPLOAD_MAX_BYTES:
            raise InvalidRequestError(
                f"文件超过最大限制 {FILE_UPLOAD_MAX_BYTES // (1024 * 1024)} MB"
            )
        relative, target = _safe_relative_target(directory, path)
        _reject_link_path(directory.resolve(), target)
        if target.exists() and target.is_dir():
            raise ConflictError(f"目标是目录：{relative}")
        _atomic_write(target, data)
        return {"path": relative, "size": len(data), "updated": True}

    def save_file(self, user: Any, scope: Any, path: Any, data: bytes) -> dict[str, Any]:
        name, normalized_scope, directory = self._file_scope_root(user, scope)
        result = self._write_area_file(directory, path, data)
        return {"user": name, "scope": normalized_scope, **result}

    def write_file_text(
        self,
        user: Any,
        scope: Any,
        path: Any,
        content: Any,
    ) -> dict[str, Any]:
        text = _validated_text(content)
        name, normalized_scope, directory = self._file_scope_root(user, scope)
        relative, target = _safe_relative_target(directory, path)
        if target.suffix.lower() not in _EDITABLE_TEXT_SUFFIXES:
            raise InvalidRequestError("该文件类型不允许通过文本编辑接口修改")
        result = self._write_area_file(directory, relative, text.encode("utf-8"))
        return {"user": name, "scope": normalized_scope, **result}

    def read_file_text(self, user: Any, scope: Any, path: Any) -> dict[str, Any]:
        name, normalized_scope, directory = self._file_scope_root(user, scope)
        relative, target = _safe_relative_target(directory, path)
        _reject_link_path(directory.resolve(), target)
        if target.suffix.lower() not in _EDITABLE_TEXT_SUFFIXES:
            raise InvalidRequestError("该文件类型不允许通过文本读取接口打开")
        if not target.is_file():
            raise NotFoundError(f"文件不存在：{relative}")
        try:
            data = target.read_bytes()
            if len(data) > TEXT_DOCUMENT_MAX_CHARS * 4:
                raise InvalidRequestError("文本文件超过在线编辑大小限制")
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            raise InvalidRequestError("文件不是有效 UTF-8 文本") from None
        return {"user": name, "scope": normalized_scope, "path": relative, "content": content, "size": len(data)}

    def _make_area_directory(self, directory: Path, path: Any) -> dict[str, Any]:
        relative, target = _safe_relative_target(directory, path)
        _reject_link_path(directory.resolve(), target)
        if target.exists():
            raise ConflictError(f"目标已存在：{relative}")
        target.mkdir(parents=True)
        return {"path": relative, "created": True}

    def make_directory(self, user: Any, scope: Any, path: Any) -> dict[str, Any]:
        name, normalized_scope, directory = self._file_scope_root(user, scope)
        return {
            "user": name,
            "scope": normalized_scope,
            **self._make_area_directory(directory, path),
        }

    def _move_area_path(self, directory: Path, path: Any, new_path: Any) -> dict[str, Any]:
        relative, source = _safe_relative_target(directory, path)
        target_relative, target = _safe_relative_target(directory, new_path)
        _reject_link_path(directory.resolve(), source)
        _reject_link_path(directory.resolve(), target)
        if not source.exists():
            raise NotFoundError(f"文件或目录不存在：{relative}")
        if target.exists():
            raise ConflictError(f"目标已存在：{target_relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)
        return {"path": relative, "new_path": target_relative, "moved": True}

    def move_file(self, user: Any, scope: Any, path: Any, new_path: Any) -> dict[str, Any]:
        name, normalized_scope, directory = self._file_scope_root(user, scope)
        return {
            "user": name,
            "scope": normalized_scope,
            **self._move_area_path(directory, path, new_path),
        }

    def file_download(self, user: Any, scope: Any, path: Any) -> Path:
        _, _, directory = self._file_scope_root(user, scope)
        _, target = _safe_relative_target(directory, path)
        if target.is_symlink() or not target.is_file():
            raise NotFoundError(f"文件不存在：{path}")
        return target

    def delete_file(self, user: Any, scope: Any, path: Any) -> dict[str, Any]:
        name, normalized_scope, directory = self._file_scope_root(user, scope)
        relative, target = _safe_relative_target(directory, path)
        if target.is_symlink() or not target.is_file():
            raise NotFoundError(f"文件不存在：{relative}")
        try:
            target.unlink()
        except OSError as exc:
            raise WebServiceError(f"文件删除失败：{relative}") from exc
        return {
            "user": name,
            "scope": normalized_scope,
            "path": relative,
            "deleted": True,
        }

    def tmp_files(self) -> dict[str, Any]:
        directory = self.root / "tmp"
        tree, summary = _directory_tree(directory)
        return {"root": "tmp", "summary": summary, "tree": tree}

    def save_tmp_file(self, path: Any, data: bytes) -> dict[str, Any]:
        return {"root": "tmp", **self._write_area_file(self.root / "tmp", path, data)}

    def write_tmp_text(self, path: Any, content: Any) -> dict[str, Any]:
        text = _validated_text(content)
        directory = self.root / "tmp"
        relative, target = _safe_relative_target(directory, path)
        if target.suffix.lower() not in _EDITABLE_TEXT_SUFFIXES:
            raise InvalidRequestError("该文件类型不允许通过文本编辑接口修改")
        return {"root": "tmp", **self._write_area_file(directory, relative, text.encode())}

    def read_tmp_text(self, path: Any) -> dict[str, Any]:
        directory = self.root / "tmp"
        relative, target = _safe_relative_target(directory, path)
        _reject_link_path(directory.resolve(), target)
        if target.suffix.lower() not in _EDITABLE_TEXT_SUFFIXES:
            raise InvalidRequestError("该文件类型不允许通过文本读取接口打开")
        if not target.is_file():
            raise NotFoundError(f"文件不存在：{relative}")
        data = target.read_bytes()
        if len(data) > TEXT_DOCUMENT_MAX_CHARS * 4:
            raise InvalidRequestError("文本文件超过在线编辑大小限制")
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            raise InvalidRequestError("文件不是有效 UTF-8 文本") from None
        return {"root": "tmp", "path": relative, "content": content, "size": len(data)}

    def make_tmp_directory(self, path: Any) -> dict[str, Any]:
        return {"root": "tmp", **self._make_area_directory(self.root / "tmp", path)}

    def move_tmp_file(self, path: Any, new_path: Any) -> dict[str, Any]:
        return {
            "root": "tmp",
            **self._move_area_path(self.root / "tmp", path, new_path),
        }

    def delete_tmp_file(self, path: Any) -> dict[str, Any]:
        relative, target = _safe_relative_target(self.root / "tmp", path)
        if target.is_symlink() or not target.is_file():
            raise NotFoundError(f"临时文件不存在：{relative}")
        try:
            target.unlink()
        except OSError as exc:
            raise WebServiceError(f"临时文件删除失败：{relative}") from exc
        return {"path": relative, "deleted": True}

    def _avatar_path(self, user: str) -> Path | None:
        directory = self.root / "users" / user / "avatar"
        if not directory.is_dir():
            return None
        candidates = {
            item.suffix.casefold(): item
            for item in _visible_children(directory)
            if item.is_file() and item.stem.casefold() == "avatar"
        }
        for suffix in _AVATAR_SEARCH_ORDER:
            path = candidates.get(suffix)
            if path is not None:
                return path
        return None

    def avatar(self, user: Any) -> Path | None:
        name = self.require_user(user)
        return self._avatar_path(name)

    def save_avatar(
        self,
        user: Any,
        data: Any,
        content_type: Any,
    ) -> dict[str, Any]:
        name = self.require_user(user)
        if not isinstance(data, bytes) or not data:
            raise InvalidRequestError("头像文件不能为空")
        if len(data) > AVATAR_MAX_BYTES:
            raise InvalidRequestError("头像文件不能超过 5 MB")
        declared = str(content_type or "").strip().casefold()
        if declared == "image/jpg":
            declared = "image/jpeg"
        if declared not in _AVATAR_FORMATS:
            raise InvalidRequestError("头像只支持 PNG、JPEG、GIF 或 WebP")
        detected = _image_media_type(data)
        if detected is None or detected != declared:
            raise InvalidRequestError("头像文件内容与声明的图片格式不一致")
        directory = self.root / "users" / name / "avatar"
        target = directory / f"avatar{_AVATAR_FORMATS[detected]}"
        _atomic_write(target, data)
        for candidate in _visible_children(directory):
            if (
                candidate != target
                and candidate.is_file()
                and candidate.stem.casefold() == "avatar"
                and candidate.suffix.casefold() in _AVATAR_SEARCH_ORDER
            ):
                try:
                    candidate.unlink()
                except OSError as exc:
                    try:
                        target.unlink()
                    except OSError:
                        pass
                    raise WebServiceError("旧头像清理失败") from exc
        return {
            "user": name,
            "avatar_path": self._project_path(target),
            "size": len(data),
            "format": detected,
        }

    @staticmethod
    def _validated_soul_content(content: Any) -> str:
        if not isinstance(content, str) or not content.strip():
            raise InvalidRequestError("content 必须是非空字符串")
        if len(content) > 65_536:
            raise InvalidRequestError("content 不能超过 65536 字符")
        return content

    def _soul_document(self, path: Path, *, user: str | None = None) -> dict[str, Any]:
        if not path.is_file() or path.is_symlink():
            label = "用户人格文件尚未创建" if user is not None else "全局人格文件不存在"
            raise NotFoundError(label)
        try:
            content = path.read_text("utf-8")
            stat = path.stat()
        except (OSError, UnicodeError) as exc:
            raise WebServiceError("人格文件不可读") from exc
        result: dict[str, Any] = {
            "path": self._project_path(path),
            "content": content,
            "size": stat.st_size,
            "updated_at": stat.st_mtime,
        }
        if user is not None:
            result = {"user": user, **result}
        return result

    def user_soul(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        return self._soul_document(
            self.root / "users" / name / "user_soul.md",
            user=name,
        )

    def update_user_soul(self, user: Any, content: Any) -> dict[str, Any]:
        name = self.require_user(user)
        path = self.root / "users" / name / "user_soul.md"
        _atomic_write(path, self._validated_soul_content(content).encode("utf-8"))
        return self._soul_document(path, user=name)

    def global_soul(self) -> dict[str, Any]:
        return self._soul_document(self.root / "config" / "global_soul.md")

    def update_global_soul(self, content: Any) -> dict[str, Any]:
        path = self.root / "config" / "global_soul.md"
        _atomic_write(path, self._validated_soul_content(content).encode("utf-8"))
        return self._soul_document(path)

    def logo(self) -> Path | None:
        path = self.root / "kemo-agent.jpg"
        return path if path.is_file() and not path.is_symlink() else None

    def agents(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        definitions = sorted(
            discover_agents(self.root, name).agents.values(),
            key=lambda item: (item.source, item.name.casefold(), item.name),
        )
        agents = [
            {
                "name": definition.name,
                "description": definition.description,
                "enabled": definition.enabled,
                "source": "global" if definition.source == "builtin" else "user",
                "execution": definition.execution,
                "model_profile": definition.model_profile,
                "exposure": definition.capabilities.exposure,
                "root": self._project_path(definition.directory),
                "files": _flat_files(definition.directory),
            }
            for definition in definitions
        ]
        return {
            "user": name,
            "summary": {
                "total": len(agents),
                "enabled": sum(item["enabled"] for item in agents),
                "global": sum(item["source"] == "global" for item in agents),
                "user": sum(item["source"] == "user" for item in agents),
            },
            "agents": agents,
        }

    def message_status(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        message_config = read_json_object(
            self.root / "config" / "message_config.json",
            allow_empty=True,
        )
        resolver = IdentityResolver.from_config(self.root, message_config)
        bindings = [
            {
                "platform": binding.platform,
                "external_user_id": binding.external_user_id,
                "internal_user": binding.internal_user,
                "chat_type": binding.chat_type,
                "external_chat_id": binding.external_chat_id,
                "match_priority": (
                    2
                    + (1 if binding.chat_type is not None else 0)
                    + (2 if binding.external_chat_id is not None else 0)
                ),
            }
            for binding in resolver.bindings
            if binding.internal_user == name
        ]
        runtime = self._runtime_status()
        components = runtime.get("components") or {}
        transports: list[dict[str, Any]] = []
        issues: list[dict[str, str]] = []
        base = self.root / "message" / "out"
        for directory in _visible_children(base):
            if not directory.is_dir():
                continue
            try:
                config = MessagePluginConfig.load(self.root, directory)
                if config.bound_user != name:
                    continue
                try:
                    state = read_json_object(config.state_path, allow_empty=True)
                except Exception as exc:
                    state = {}
                    issues.append({"name": directory.name, "error": str(exc)})
                component = components.get(f"transport:{config.platform}")
                component = component if isinstance(component, dict) else {}
                runtime_state = str(component.get("state") or "")
                transport_state = (
                    "running"
                    if runtime_state == "running"
                    else "error"
                    if runtime_state == "failed" or state.get("health") == "dead"
                    else "stopped"
                )
                last_error = component.get("last_error") or state.get("error")
                transports.append(
                    {
                        "name": config.machine_id,
                        "platform": config.platform,
                        "display_name": config.display_name,
                        "capabilities": sorted(config.capabilities),
                        "state": transport_state,
                        "bound_user": config.bound_user,
                        "allowed_tools": (
                            sorted(config.allowed_tools)
                            if config.allowed_tools is not None
                            else None
                        ),
                        "last_error": last_error,
                        "health": str(state.get("health") or "unknown"),
                        "last_check": state.get("last_check"),
                        "last_message_at": state.get("last_message_at"),
                        "latency_ms": state.get("latency_ms"),
                        "messages_received_today": int(
                            state.get("messages_received_today") or 0
                        ),
                        "messages_sent_today": int(
                            state.get("messages_sent_today") or 0
                        ),
                    }
                )
            except MessagePluginError as exc:
                issues.append({"name": directory.name, "error": str(exc)})
        transports.sort(key=lambda item: (item["platform"], item["name"]))
        return {
            "user": name,
            "bindings": bindings,
            "transports": transports,
            "summary": {
                "total_bindings": len(bindings),
                "total_transports": len(transports),
                "running_transports": sum(
                    item["state"] == "running" for item in transports
                ),
                "stopped_transports": sum(
                    item["state"] == "stopped" for item in transports
                ),
                "error_transports": sum(
                    item["state"] == "error" for item in transports
                ),
            },
            "issues": issues,
        }

    def expands(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        scopes = (
            ("global", self.root / "global_expand"),
            ("shared", self.root / "shared_expand"),
            ("user", self.root / "users" / name / "expand"),
        )
        expands = []
        scope_counts: dict[str, int] = {}
        for scope, directory in scopes:
            items = []
            for module in _visible_children(directory):
                if not module.is_dir():
                    continue
                items.append(
                    {
                        "name": module.name,
                        "type": "directory",
                        "relative_path": module.name,
                        "has_register": (module / "expand.json").is_file(),
                        "files": _flat_files(module, relative_to=directory),
                    }
                )
            scope_counts[scope] = len(items)
            expands.append(
                {
                    "scope": scope,
                    "root": self._project_path(directory),
                    "items": items,
                }
            )
        return {
            "user": name,
            "summary": {"total": sum(scope_counts.values()), **scope_counts},
            "expands": expands,
        }

    def sessions(
        self,
        user: Any,
        *,
        source: Any = "web",
        query: Any = "",
    ) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        if not isinstance(query, str):
            raise InvalidRequestError("query 必须是字符串")
        normalized_query = query.strip().casefold()
        sessions = list_sessions(self.root, name, normalized_source)
        if normalized_query:
            matched = []
            for item in sessions:
                searchable = " ".join(
                    str(item.get(key) or "") for key in ("session_id", "title", "window")
                ).casefold()
                if normalized_query in searchable:
                    matched.append(item)
                    continue
                directory = find_window(
                    self.root,
                    name,
                    normalized_source,
                    str(item.get("session_id") or ""),
                )
                if directory is None:
                    continue
                try:
                    messages = session_messages(load_window(directory))
                except (OSError, ValueError, TypeError):
                    continue
                if any(
                    normalized_query in str(message.get("content") or "").casefold()
                    for message in messages
                    if isinstance(message, dict)
                ):
                    matched.append(item)
            sessions = matched
        return {
            "user": name,
            "source": normalized_source,
            "query": query.strip(),
            "sessions": sessions,
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
                    "depends_on": [str(value) for value in (item.get("depends_on") or [])],
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
            "auto_accept": bool(plan.get("auto_accept", False)),
            "reminder": str(plan.get("reminder") or ""),
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
            "user_defined": task.get("exec_mode") != "system",
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
        executions: list[dict[str, Any]] = []
        for plan in plans:
            for step in plan.get("steps", []):
                if not isinstance(step, dict) or not step.get("finished_at"):
                    continue
                executions.append(
                    {
                        "kind": "plan_step",
                        "task_id": plan["plan_id"],
                        "title": step.get("title", ""),
                        "status": step.get("status", ""),
                        "updated_at": step.get("finished_at", ""),
                        "result": step.get("result"),
                        "error": step.get("error"),
                    }
                )
        for task in crons:
            if task.get("latest_run_at"):
                executions.append(
                    {
                        "kind": "cron",
                        "task_id": task["task_id"],
                        "title": task.get("title", ""),
                        "status": task.get("last_state", task.get("status", "")),
                        "updated_at": task.get("latest_run_at", ""),
                        "result": None,
                        "error": task.get("last_error"),
                    }
                )
        executions.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
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
            "executions": executions[:100],
        }

    def create_plan(self, user: Any, payload: Any) -> dict[str, Any]:
        name = self.require_user(user)
        if not isinstance(payload, dict):
            raise InvalidRequestError("计划必须是对象")
        try:
            plan = normalize_plan(
                plan_id=payload.get("plan_id"),
                title=payload.get("title", ""),
                description=payload.get("description", ""),
                user=name,
                source="web",
                session_id=str(payload.get("session_id") or "web"),
                steps=payload.get("steps") or [],
                auto_accept=payload.get("auto_accept", False),
                reminder=payload.get("reminder", ""),
                status=payload.get("status", "pending"),
                current_step=payload.get("current_step"),
            )
            stored = PlanStore(self.root, name).create(plan)
        except (PlanError, KeyError, TypeError, ValueError) as exc:
            raise InvalidRequestError(f"计划校验失败：{exc}") from None
        return {"user": name, "plan": self._plan_summary(stored), "updated": True}

    def update_plan(self, user: Any, plan_id: Any, payload: Any) -> dict[str, Any]:
        name = self.require_user(user)
        if not isinstance(payload, dict):
            raise InvalidRequestError("计划更新必须是对象")
        expected = payload.get("revision")

        def mutate(current: dict[str, Any]) -> dict[str, Any]:
            if expected is not None and expected != current.get("revision"):
                raise PlanConflictError("计划版本已变化，请重新读取后再保存")
            updated = dict(current)
            for key in (
                "title",
                "description",
                "status",
                "auto_accept",
                "reminder",
                "current_step",
                "steps",
            ):
                if key in payload:
                    updated[key] = payload[key]
            updated["user"] = name
            return updated

        try:
            stored = PlanStore(self.root, name).update(str(plan_id), mutate)
        except PlanNotFoundError as exc:
            raise NotFoundError(str(exc)) from None
        except PlanConflictError as exc:
            raise ConflictError(str(exc)) from None
        except (PlanError, KeyError, TypeError, ValueError) as exc:
            raise InvalidRequestError(f"计划校验失败：{exc}") from None
        return {"user": name, "plan": self._plan_summary(stored), "updated": True}

    def delete_plan(self, user: Any, plan_id: Any) -> dict[str, Any]:
        name = self.require_user(user)
        if not PlanStore(self.root, name).delete(str(plan_id)):
            raise NotFoundError(f"计划不存在：{plan_id}")
        return {"user": name, "plan_id": str(plan_id), "deleted": True}

    def _cron_payload(
        self,
        user: str,
        payload: dict[str, Any],
        current: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        source = dict(current or {})
        allowed = {
            "title",
            "prompt",
            "type",
            "interval_seconds",
            "time",
            "next_run_at",
            "status",
        }
        source.update({key: value for key, value in payload.items() if key in allowed})
        task_type = source.get("type")
        interval = source.get("interval_seconds")
        if task_type == "recurring" and (
            isinstance(interval, bool) or not isinstance(interval, int) or interval < 60
        ):
            raise InvalidRequestError("recurring interval_seconds 必须是 ≥ 60 的整数")
        try:
            if task_type in {"daily", "recurring"}:
                source["next_run_at"] = compute_next_run(source)
            return normalize_task(
                task_id=source.get("task_id"),
                title=source.get("title", ""),
                prompt=source.get("prompt", ""),
                user=user,
                type=task_type,
                interval_seconds=interval,
                time=source.get("time"),
                next_run_at=source.get("next_run_at", ""),
                latest_run_at=source.get("latest_run_at", ""),
                status=source.get("status", "enabled"),
                created_at=source.get("created_at", ""),
                exec_mode=source.get("exec_mode", "agent"),
            )
        except (CronError, KeyError, TypeError, ValueError) as exc:
            raise InvalidRequestError(f"定时任务校验失败：{exc}") from None

    def create_cron(self, user: Any, payload: Any) -> dict[str, Any]:
        name = self.require_user(user)
        if not isinstance(payload, dict):
            raise InvalidRequestError("定时任务必须是对象")
        task = self._cron_payload(name, payload)
        try:
            stored = CronStore(self.root, name).create(task)
        except CronConflictError as exc:
            raise ConflictError(str(exc)) from None
        except CronError as exc:
            raise InvalidRequestError(str(exc)) from None
        return {"user": name, "cron_task": self._cron_summary(stored), "updated": True}

    def update_cron(self, user: Any, task_id: Any, payload: Any) -> dict[str, Any]:
        name = self.require_user(user)
        if not isinstance(payload, dict):
            raise InvalidRequestError("定时任务更新必须是对象")
        store = CronStore(self.root, name)
        try:
            stored = store.update(
                str(task_id),
                lambda current: self._cron_payload(name, payload, current),
            )
        except CronNotFoundError as exc:
            raise NotFoundError(str(exc)) from None
        except CronError as exc:
            raise InvalidRequestError(str(exc)) from None
        return {"user": name, "cron_task": self._cron_summary(stored), "updated": True}

    def delete_cron(self, user: Any, task_id: Any) -> dict[str, Any]:
        name = self.require_user(user)
        if not CronStore(self.root, name).delete(str(task_id)):
            raise NotFoundError(f"定时任务不存在：{task_id}")
        return {"user": name, "task_id": str(task_id), "deleted": True}

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

    def _knowledge_root(self, user: Any, scope: Any) -> tuple[str, Path]:
        name = self.require_user(user)
        if scope not in _KNOWLEDGE_SCOPES:
            raise InvalidRequestError("scope 只允许 user、shared 或 global")
        roots = {
            "user": self.root / "users" / name / "knowledge",
            "shared": self.root / "shared_knowledge",
            "global": self.root / "global_knowledge",
        }
        return name, roots[scope]

    def _knowledge_target(self, user: Any, scope: Any, path: Any) -> tuple[str, str, Path]:
        name, root = self._knowledge_root(user, scope)
        relative, target = _safe_relative_target(root, path)
        _reject_link_path(root.resolve(), target)
        if Path(relative).suffix.lower() not in _KNOWLEDGE_SUFFIXES:
            raise InvalidRequestError("知识文件只允许 .md、.txt 或 .json")
        return name, str(scope), target

    def knowledge_document(self, user: Any, scope: Any, path: Any) -> dict[str, Any]:
        name, normalized_scope, target = self._knowledge_target(user, scope, path)
        if not target.is_file():
            raise NotFoundError(f"知识文件不存在：{path}")
        try:
            content = target.read_text("utf-8")
        except UnicodeDecodeError:
            raise InvalidRequestError("知识文件不是有效 UTF-8 文本") from None
        return {
            "user": name,
            "scope": normalized_scope,
            "relative_path": target.relative_to(
                self._knowledge_root(name, normalized_scope)[1]
            ).as_posix(),
            "content": content,
            "size": len(content.encode("utf-8")),
            "updated_at": target.stat().st_mtime,
        }

    def put_knowledge_document(
        self,
        user: Any,
        scope: Any,
        path: Any,
        content: Any,
    ) -> dict[str, Any]:
        name, normalized_scope, target = self._knowledge_target(user, scope, path)
        text = _validated_text(content)
        if target.suffix.lower() == ".json":
            try:
                json.loads(text)
            except json.JSONDecodeError as exc:
                raise InvalidRequestError(f"JSON 知识文件格式无效：{exc.msg}") from None
        _atomic_write(target, text.encode("utf-8"))
        return {
            "user": name,
            "scope": normalized_scope,
            "relative_path": target.relative_to(
                self._knowledge_root(name, normalized_scope)[1]
            ).as_posix(),
            "size": len(text.encode("utf-8")),
            "updated": True,
            "index_refresh": "next_request",
        }

    def delete_knowledge_document(self, user: Any, scope: Any, path: Any) -> dict[str, Any]:
        name, normalized_scope, target = self._knowledge_target(user, scope, path)
        if not target.is_file():
            raise NotFoundError(f"知识文件不存在：{path}")
        target.unlink()
        return {
            "user": name,
            "scope": normalized_scope,
            "relative_path": target.relative_to(
                self._knowledge_root(name, normalized_scope)[1]
            ).as_posix(),
            "deleted": True,
        }

    def move_knowledge_document(
        self,
        user: Any,
        scope: Any,
        path: Any,
        new_path: Any,
    ) -> dict[str, Any]:
        name, normalized_scope, source = self._knowledge_target(user, scope, path)
        _, _, target = self._knowledge_target(user, scope, new_path)
        if not source.is_file():
            raise NotFoundError(f"知识文件不存在：{path}")
        if target.exists():
            raise ConflictError(f"知识文件已存在：{new_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)
        root = self._knowledge_root(name, normalized_scope)[1]
        return {
            "user": name,
            "scope": normalized_scope,
            "relative_path": source.relative_to(root).as_posix(),
            "new_relative_path": target.relative_to(root).as_posix(),
            "moved": True,
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
                "value_preview": self._sense_value_preview(item),
                # Sense modules currently have no required refresh interval
                # in sense.json. Keep this explicit so clients can render a
                # truthful fallback instead of inventing one.
                "update_interval": "",
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

    def _sense_value_preview(self, item: dict[str, Any]) -> str:
        """Return a bounded, presentation-safe preview of a sense data file."""

        if not item.get("valid") or not item.get("data_md"):
            return ""
        path = self.root / str(item.get("root") or "") / str(item.get("name") or "") / str(item["data_md"])
        try:
            content = path.read_text("utf-8-sig")
        except (OSError, UnicodeError):
            return ""
        lines: list[str] = []
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith(">"):
                continue
            line = re.sub(r"^[-*]\s*", "", line)
            lines.append(line)
            if len(" · ".join(lines)) >= 160:
                break
        return " · ".join(lines)[:160]

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

    def memory_item(self, user: Any, filename: Any) -> dict[str, Any]:
        name = self.require_user(user)
        try:
            normalized = normalize_memory_filename(filename)
            store = MemoryStore(self.root, name, load_config(name, self.root))
            item = next(
                (entry for entry in store.list_items() if entry["filename"] == normalized),
                None,
            )
        except RuntimeMemoryError as exc:
            raise InvalidRequestError(str(exc)) from None
        if item is None:
            raise NotFoundError(f"记忆不存在：{filename}")
        return {"user": name, **item}

    def put_memory(
        self,
        user: Any,
        filename: Any,
        content: Any,
        tier: Any = None,
    ) -> dict[str, Any]:
        name = self.require_user(user)
        text = _validated_text(content, max_chars=TEXT_DOCUMENT_MAX_CHARS)
        if not text.strip():
            raise InvalidRequestError("记忆内容不能为空")
        if contains_sensitive_credential(text):
            raise InvalidRequestError("记忆内容包含疑似敏感凭据，已拒绝写入")
        try:
            normalized = normalize_memory_filename(filename)
            target_tier = tier if tier is not None else None
            if target_tier is not None and target_tier not in TIERS:
                raise InvalidRequestError(f"tier 只允许 {', '.join(TIERS)}")
            store = MemoryStore(self.root, name, load_config(name, self.root))
            existing = store.locate(normalized)
            if existing is not None and target_tier == "permanent":
                result = store.upsert_candidates(
                    [{"filename": normalized, "content": text, "explicit": True}]
                )
            else:
                result = store.upsert_candidates(
                    [{"filename": normalized, "content": text}]
                )
            if result.get("rejected"):
                raise RuntimeMemoryError("记忆内容未通过运行时校验")
            existing = store.locate(normalized)
            if existing is not None and target_tier and target_tier != existing.tier:
                if existing.tier == "permanent":
                    raise RuntimeMemoryError("永久记忆不能降级到临时层")
                rank = {tier_name: index for index, tier_name in enumerate(TIERS)}
                if rank[target_tier] < rank[existing.tier]:
                    raise RuntimeMemoryError("临时记忆只能向更长期层级晋升")
                store._promote_location(existing, target_tier, utc_now())
            item = next(
                entry for entry in store.list_items() if entry["filename"] == normalized
            )
        except RuntimeMemoryError as exc:
            raise InvalidRequestError(str(exc)) from None
        return {"user": name, **item, "updated": True}

    def delete_memory(self, user: Any, filename: Any) -> dict[str, Any]:
        name = self.require_user(user)
        try:
            normalized = normalize_memory_filename(filename)
            store = MemoryStore(self.root, name, load_config(name, self.root))
            removed = store.forget(normalized)
        except RuntimeMemoryError as exc:
            raise InvalidRequestError(str(exc)) from None
        if not removed:
            raise NotFoundError(f"记忆不存在：{filename}")
        return {"user": name, "filename": normalized, "deleted": True}

    def important_memory(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        path = self.root / "users" / name / "memory_temporary_important.md"
        if not path.is_file():
            raise NotFoundError("临时重要记忆不存在")
        content = path.read_text("utf-8")
        return {
            "user": name,
            "path": f"users/{name}/memory_temporary_important.md",
            "content": content,
            "size": len(content.encode()),
            "updated_at": datetime.fromtimestamp(
                path.stat().st_mtime, timezone.utc
            ).astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
        }

    def update_important_memory(self, user: Any, content: Any) -> dict[str, Any]:
        name = self.require_user(user)
        config = load_config(name, self.root)
        configured_limit = int(
            (config.get("memory") or {}).get(
                "important_memory_max_chars", IMPORTANT_MEMORY_MAX_HARD_CHARS
            )
        )
        text = _validated_text(
            content,
            max_chars=min(IMPORTANT_MEMORY_MAX_HARD_CHARS, max(1, configured_limit)),
        )
        if contains_sensitive_credential(text):
            raise InvalidRequestError("临时重要记忆包含疑似敏感凭据，已拒绝写入")
        path = self.root / "users" / name / "memory_temporary_important.md"
        _atomic_write(path, text.encode("utf-8"))
        return {
            "user": name,
            "path": f"users/{name}/memory_temporary_important.md",
            "content": text,
            "size": len(text.encode()),
            "updated_at": datetime.fromtimestamp(
                path.stat().st_mtime, timezone.utc
            ).astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
            "updated": True,
        }

    def delete_important_memory(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        path = self.root / "users" / name / "memory_temporary_important.md"
        if not path.is_file():
            raise NotFoundError("临时重要记忆不存在")
        path.unlink()
        return {
            "user": name,
            "path": f"users/{name}/memory_temporary_important.md",
            "deleted": True,
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
