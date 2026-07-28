"""基于现有运行、历史记录和用户 API 的面向 Web 的服务适配器。"""

from __future__ import annotations

import io
import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from pathlib import PurePosixPath
import queue
import re
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Iterator
import urllib.error
import urllib.request
import uuid
import zipfile
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter, ValidationError

from cron.schedule import compute_next_run
from events import RunEvent
from message.identity import IdentityResolver
from message.plugin import FileMessageTransport, MessagePluginConfig, MessagePluginError
from provider.adapters.gateway import KemoGatewayAdapter
from provider.protocol.models import (
    AudioContent,
    ContentBlock,
    FileContent,
    ImageContent,
    VideoContent,
    normalize_reasoning_effort,
)
from provider.schema import ProviderError
from run.agents import discover_agents
from run.agent_runner import AgentRunner
from run.attachments import AttachmentError, describe_uploaded_asset
from run.config import (
    ConfigError,
    USER_ONLY_SECTIONS,
    load_config,
    provider_runtime_config,
    read_json_object,
)
from run.context import (
    ContextPolicy,
    build_context_snapshot,
    estimate_text_tokens,
    select_context,
)
from run.context_summary import build_summary_message
from run.cron_store import (
    CronConflictError,
    CronError,
    CronNotFoundError,
    CronStore,
    normalize_task,
)
from run.engine import compress_context, iter_request_events
from run.expand_runtime import read_expand_runtime, record_expand_runtime
from run.history import (
    HistoryError,
    delete_all_sessions as delete_all_history_sessions,
    delete_session as delete_history_session,
    empty_window,
    find_window,
    list_sessions,
    load_window,
    queue_memory_extraction,
    rename_session as rename_history_session,
    runtime_window_path,
    session_messages,
    undo_last_round as undo_history_last_round,
)
from run.history_index import (
    close_session as close_index_session,
    queue_summary as queue_history_summary,
    retry_summary as retry_history_summary,
    find_record as find_index_record,
    get_or_reserve_active as get_or_reserve_index_session,
    new_conversation_id,
    reserve_session,
)
from run.log_store import LogStore
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
from run.prompt_sources import iter_files, load_prompt_source_registry, parse_skill_descriptor
from run.source_policy import MainAgentSourcePolicy
from run.task_plan_store import (
    PlanConflictError,
    PlanError,
    PlanNotFoundError,
    PlanStore,
    normalize_plan,
)
from run.module_runtime import (
    module_update_timeout,
    record_module_health,
    run_module_updater,
)
from run.memory_analysis import extract_memory_backlog
from run.session_runtime import session_lock
from run.guidance import GuidanceMailbox
from run.process_utils import hidden_subprocess_kwargs
from run.task_plan_executor import cancel_plan, pause_plan
from run.tools import apply_runtime_tool_policy, discover_tools
from run.users import list_users
from update._utils import UpdateError, compare_versions, parse_version


_SESSION_RE = re.compile(r"^[^\x00-\x1f]{1,128}$")
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_SESSION_TITLE_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,80}$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_AGENT_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_WORKER_DONE = object()
_REDACTED = "***"
_TOOL_TEXT_LIMIT = 5000
AVATAR_MAX_BYTES = 5 * 1024 * 1024
FILE_UPLOAD_MAX_BYTES = 80 * 1024 * 1024
IMAGE_PREVIEW_MAX_BYTES = 10 * 1024 * 1024
AUDIO_PREVIEW_MAX_BYTES = 100 * 1024 * 1024
VIDEO_PREVIEW_MAX_BYTES = 300 * 1024 * 1024
SKILL_ARCHIVE_MAX_BYTES = 32 * 1024 * 1024
SKILL_ARCHIVE_MAX_EXPANDED_BYTES = 128 * 1024 * 1024
SKILL_ARCHIVE_MAX_FILES = 2000
SKILL_ARCHIVE_MAX_SKILLS = 100
SKILL_ARCHIVE_MAX_RATIO = 200
TEXT_DOCUMENT_MAX_CHARS = 1_000_000
IMPORTANT_MEMORY_MAX_HARD_CHARS = 65_536
SESSION_LEASE_TTL_SECONDS = 45.0
VERSION_CHECK_CACHE_SECONDS = 180.0
VERSION_CHECK_TIMEOUT_SECONDS = 8.0
VERSION_MANIFEST_URL = (
    "https://raw.githubusercontent.com/kesepain-KE/kemo-agent/main/version.json"
)
_VERSION_COMPONENT_IDS = ("core", "agents", "plugins", "web")
_CONTENT_LIST_ADAPTER = TypeAdapter(list[ContentBlock])
_FILE_SCOPES = frozenset({"file_upload", "download"})
_KNOWLEDGE_SCOPES = frozenset({"user", "shared", "global"})
_KNOWLEDGE_SUFFIXES = frozenset({".md", ".txt", ".json"})
_SKILL_CATEGORIES = frozenset({"builtin", "shared", "agent_generated", "user_created"})
_EDITABLE_SKILL_CATEGORIES = frozenset({"agent_generated", "user_created"})
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)
_WINDOWS_INVALID_PATH_CHARS = frozenset('<>:"|?*')
_EXPAND_SCOPES = frozenset({"global", "shared", "user"})
_EXPAND_INJECTION_HEADING = re.compile(r"^##\s+注入层\s*$", re.MULTILINE)
_EXPAND_OPERATION_HEADING = re.compile(r"^##\s+操作层\s*$", re.MULTILINE)
_MESSAGE_LOG_HEADING = re.compile(
    r"(?m)^##\s+(?P<timestamp>[^|\r\n]+?)\s*\|\s*(?P<chat_type>[^|\r\n]+?)\s*\|\s*(?P<chat_id>[^\r\n]+?)\s*$"
)
_MESSAGE_LOG_INBOUND = re.compile(
    r"\*\*入站\*\*：(?P<content>.*?)(?=\n\s*-\s*附件：|\n\*\*出站\*\*：|\n---|\Z)",
    re.DOTALL,
)
_MESSAGE_LOG_OUTBOUND = re.compile(
    r"\*\*出站\*\*：(?P<content>.*?)(?=\n\s*-\s*出站附件：|\n---|\Z)",
    re.DOTALL,
)
_MESSAGE_LOG_ATTACHMENT = re.compile(
    r"(?m)^\s*-\s*附件：(?P<name>.+?)\s+\((?P<mime>[^,]+),\s*(?P<size>\d+)\s+bytes\)\s*$"
)
_MESSAGE_LOG_OUTBOUND_ATTACHMENT = re.compile(
    r"(?m)^\s*-\s*出站附件：(?P<name>.+?)\s+\((?P<path>[^\r\n]+)\)\s*$"
)
_BEIJING = ZoneInfo("Asia/Shanghai")
_MESSAGE_LOG_LIMIT = 500
_EDITABLE_TEXT_SUFFIXES = frozenset(
    {".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".tsv", ".log", ".py", ".js", ".ts", ".tsx", ".css", ".html"}
)
_MEDIA_PREVIEW_TYPES: dict[str, tuple[str, str, int, str]] = {
    ".png": ("image", "image/png", IMAGE_PREVIEW_MAX_BYTES, "图片预览上限（10 MB）"),
    ".jpg": ("image", "image/jpeg", IMAGE_PREVIEW_MAX_BYTES, "图片预览上限（10 MB）"),
    ".jpeg": ("image", "image/jpeg", IMAGE_PREVIEW_MAX_BYTES, "图片预览上限（10 MB）"),
    ".gif": ("image", "image/gif", IMAGE_PREVIEW_MAX_BYTES, "图片预览上限（10 MB）"),
    ".webp": ("image", "image/webp", IMAGE_PREVIEW_MAX_BYTES, "图片预览上限（10 MB）"),
    ".bmp": ("image", "image/bmp", IMAGE_PREVIEW_MAX_BYTES, "图片预览上限（10 MB）"),
    ".ico": ("image", "image/x-icon", IMAGE_PREVIEW_MAX_BYTES, "图片预览上限（10 MB）"),
    ".mp3": ("audio", "audio/mpeg", AUDIO_PREVIEW_MAX_BYTES, "音频预览上限（100 MB）"),
    ".wav": ("audio", "audio/wav", AUDIO_PREVIEW_MAX_BYTES, "音频预览上限（100 MB）"),
    ".ogg": ("audio", "audio/ogg", AUDIO_PREVIEW_MAX_BYTES, "音频预览上限（100 MB）"),
    ".m4a": ("audio", "audio/mp4", AUDIO_PREVIEW_MAX_BYTES, "音频预览上限（100 MB）"),
    ".aac": ("audio", "audio/aac", AUDIO_PREVIEW_MAX_BYTES, "音频预览上限（100 MB）"),
    ".flac": ("audio", "audio/flac", AUDIO_PREVIEW_MAX_BYTES, "音频预览上限（100 MB）"),
    ".opus": ("audio", "audio/ogg", AUDIO_PREVIEW_MAX_BYTES, "音频预览上限（100 MB）"),
    ".mp4": ("video", "video/mp4", VIDEO_PREVIEW_MAX_BYTES, "视频预览上限（300 MB）"),
    ".webm": ("video", "video/webm", VIDEO_PREVIEW_MAX_BYTES, "视频预览上限（300 MB）"),
    ".ogv": ("video", "video/ogg", VIDEO_PREVIEW_MAX_BYTES, "视频预览上限（300 MB）"),
    ".mov": ("video", "video/quicktime", VIDEO_PREVIEW_MAX_BYTES, "视频预览上限（300 MB）"),
    ".m4v": ("video", "video/x-m4v", VIDEO_PREVIEW_MAX_BYTES, "视频预览上限（300 MB）"),
}
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


class _VersionCheckFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fetch_remote_version_manifest(url: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "kemo-agent-web-version-check"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise _VersionCheckFailure(
                "remote_manifest_missing",
                "云端 version.json 不存在，请检查发布分支是否完整。",
            ) from exc
        raise _VersionCheckFailure(
            "remote_http_error",
            f"GitHub 返回 HTTP {exc.code}，请稍后重新检查。",
        ) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise _VersionCheckFailure(
            "remote_timeout",
            "连接 GitHub 超时，请检查服务器网络后重试。",
        ) from exc
    except urllib.error.URLError as exc:
        raise _VersionCheckFailure(
            "remote_unreachable",
            "无法连接 GitHub，请检查服务器网络或代理设置。",
        ) from exc
    except (OSError, UnicodeError) as exc:
        raise _VersionCheckFailure(
            "remote_unreachable",
            "读取云端版本信息失败，请检查服务器网络后重试。",
        ) from exc
    except json.JSONDecodeError as exc:
        raise _VersionCheckFailure(
            "invalid_remote_manifest",
            "云端 version.json 格式错误，暂时无法比较版本。",
        ) from exc
    if not isinstance(payload, dict):
        raise _VersionCheckFailure(
            "invalid_remote_manifest",
            "云端 version.json 不是有效对象，暂时无法比较版本。",
        )
    return payload
_CONFIG_SOURCE_PATHS = (
    "provider.type",
    "provider.base_url",
    "provider.model",
    "provider.stream",
    "tools.enabled",
    "tools.max_iterations",
    "tools.consecutive_identical_call_limit",
    "tools.timeout",
    "memory.extraction_mode",
    "memory.history_read_enabled",
    "memory.temporary_injection_limits.half_year",
    "memory.temporary_injection_limits.one_month",
    "memory.temporary_injection_limits.seven_days",
    "memory.important_memory_max_chars",
    "task_plan.auto_accept",
    "task_plan.max_steps",
    "cron.enabled",
    "provider_runtime.max_concurrent_requests",
    "provider_runtime.request_semaphore_timeout",
    "web.max_concurrent_chats",
    "web.max_pending_chats",
    "web.pending_chat_timeout",
    "message.max_queued_messages",
    "agent_runtime.queue_maxsize",
    "cron.avoid_congestion",
    "cron.congestion_threshold_ratio",
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


def _directory_listing(
    directory: Path,
    *,
    path: str = "",
    search: str = "",
    page: int = 1,
    page_size: int = 6,
) -> dict[str, Any]:
    summary = {"total_files": 0, "total_dirs": 0, "total_size": 0}
    entries: list[dict[str, Any]] = []
    normalized_path = path.strip().replace("\\", "/") if isinstance(path, str) else ""
    normalized_search = search.strip().casefold() if isinstance(search, str) else ""

    if normalized_path:
        normalized_path, current_directory = _safe_relative_target(directory, normalized_path)
        _reject_link_path(directory.resolve(), current_directory)
        if not current_directory.is_dir():
            normalized_path = ""

    def visit(current: Path) -> tuple[int, float, int]:
        total_size = 0
        updated_at = 0.0
        children = _visible_children(current)
        for item in children:
            relative = item.relative_to(directory).as_posix()
            parent_path = item.parent.relative_to(directory).as_posix()
            if parent_path == ".":
                parent_path = ""
            try:
                if item.is_dir():
                    summary["total_dirs"] += 1
                    child_size, child_updated_at, child_count = visit(item)
                    entry = {
                        "type": "directory",
                        "name": item.name,
                        "relative_path": relative,
                        "parent_path": parent_path,
                        "size": child_size,
                        "updated_at": child_updated_at,
                        "extension": "",
                        "child_count": child_count,
                    }
                    total_size += child_size
                    updated_at = max(updated_at, child_updated_at)
                elif item.is_file():
                    stat = item.stat()
                    summary["total_files"] += 1
                    summary["total_size"] += stat.st_size
                    entry = {
                        "type": "file",
                        "name": item.name,
                        "relative_path": relative,
                        "parent_path": parent_path,
                        "size": stat.st_size,
                        "updated_at": stat.st_mtime,
                        "extension": item.suffix.lower(),
                        "child_count": 0,
                    }
                    total_size += stat.st_size
                    updated_at = max(updated_at, stat.st_mtime)
                else:
                    continue
            except OSError:
                continue

            if normalized_search:
                haystack = f"{entry['name']} {entry['relative_path']}".casefold()
                if normalized_search in haystack:
                    entries.append(entry)
            elif parent_path == normalized_path:
                entries.append(entry)
        return total_size, updated_at, len(children)

    visit(directory)
    entries.sort(
        key=lambda item: (
            0 if item["type"] == "directory" else 1,
            str(item["name"]).casefold(),
            item["name"],
            item["relative_path"],
        )
    )
    requested_page = max(1, int(page))
    normalized_page_size = min(100, max(1, int(page_size)))
    total_items = len(entries)
    total_pages = max(1, math.ceil(total_items / normalized_page_size))
    current_page = min(requested_page, total_pages)
    start = (current_page - 1) * normalized_page_size
    paged_entries = entries[start : start + normalized_page_size]
    return {
        "summary": summary,
        "entries": paged_entries,
        "path": normalized_path,
        "search": search.strip() if isinstance(search, str) else "",
        "pagination": {
            "page": current_page,
            "page_size": normalized_page_size,
            "total_items": total_items,
            "total_pages": total_pages,
            "has_previous": current_page > 1,
            "has_next": current_page < total_pages,
        },
    }


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


def _agent_registration_value(content: str, label: str) -> str:
    pattern = re.compile(
        rf"^\s*-\s*\*\*{re.escape(label)}\*\*\s*[:：]\s*(.+?)\s*$",
        re.MULTILINE,
    )
    match = pattern.search(content or "")
    return match.group(1).strip() if match else ""


def _reject_tree_links(directory: Path) -> None:
    for current, directories, files in os.walk(directory, topdown=True, followlinks=False):
        for name in [*directories, *files]:
            path = Path(current) / name
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                raise InvalidRequestError("用户子代理目录不能包含符号链接或目录联接")


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


def _validated_skill_archive_path(value: str) -> PurePosixPath:
    """Normalize one ZIP member while keeping extraction portable and contained."""

    normalized = str(value or "").replace("\\", "/")
    if not normalized or "\x00" in normalized or normalized.startswith("/"):
        raise InvalidRequestError("技能压缩包包含无效路径")
    pure = PurePosixPath(normalized.rstrip("/"))
    if not pure.parts or pure.is_absolute() or ".." in pure.parts:
        raise InvalidRequestError("技能压缩包包含路径穿越或绝对路径")
    for part in pure.parts:
        if part in {"", ".", ".."}:
            raise InvalidRequestError("技能压缩包包含无效路径片段")
        if part.endswith((" ", ".")) or any(char in _WINDOWS_INVALID_PATH_CHARS for char in part):
            raise InvalidRequestError(f"技能压缩包路径不兼容 Windows：{part}")
        device_name = part.split(".", 1)[0].upper()
        if device_name in _WINDOWS_RESERVED_NAMES:
            raise InvalidRequestError(f"技能压缩包包含 Windows 保留名称：{part}")
    return pure


def _zip_member_kind(info: zipfile.ZipInfo) -> str:
    """Return file/directory and reject links or other special archive entries."""

    unix_mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(unix_mode)
    if file_type == stat.S_IFLNK or (info.external_attr & 0x400):
        raise InvalidRequestError("技能压缩包不能包含符号链接、目录联接或重解析点")
    is_directory = info.is_dir() or file_type == stat.S_IFDIR
    if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
        raise InvalidRequestError("技能压缩包不能包含设备、套接字等特殊文件")
    return "directory" if is_directory else "file"


def _skill_package_name(value: str) -> str:
    candidate = str(value or "").strip()
    _validated_skill_archive_path(candidate)
    if "/" in candidate or "\\" in candidate:
        raise InvalidRequestError("技能目录名必须是单个文件夹名称")
    if candidate.startswith("."):
        raise InvalidRequestError("技能目录名不能以点开头")
    return candidate


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
    cancel_event: threading.Event = field(default_factory=threading.Event)
    guidance: GuidanceMailbox = field(default_factory=lambda: GuidanceMailbox(maxsize=8))
    started_at: float = field(default_factory=time.monotonic)


class WebServiceError(RuntimeError):
    code = "internal_error"
    status = 500
    headers: dict[str, str] | None = None


class InvalidRequestError(WebServiceError):
    code = "invalid_request"
    status = 400


class ProviderDiscoveryError(WebServiceError):
    code = "provider_discovery_failed"
    status = 502


class NotFoundError(WebServiceError):
    code = "not_found"
    status = 404


class ConflictError(WebServiceError):
    code = "conflict"
    status = 409


class TooManyChatsError(WebServiceError):
    code = "too_many_chats"
    status = 503

    def __init__(self, message: str, *, retry_after: float) -> None:
        super().__init__(message)
        self.headers = {"Retry-After": str(max(1, int(retry_after)))}


class _UserChatGate:
    """单用户聊天并发槽与有界等待区。"""

    def __init__(
        self,
        max_concurrent: int,
        max_pending: int,
        pending_timeout: float,
    ) -> None:
        self.max_concurrent = max(1, int(max_concurrent))
        self.max_pending = max(0, int(max_pending))
        self.pending_timeout = max(1.0, float(pending_timeout))
        self._semaphore = threading.BoundedSemaphore(self.max_concurrent)
        self._pending_count = 0
        self._active_count = 0
        self._lock = threading.Lock()

    def acquire(self, *, cancel_event: threading.Event | None = None) -> bool:
        if cancel_event is not None and cancel_event.is_set():
            return False
        if self._semaphore.acquire(blocking=False):
            if cancel_event is not None and cancel_event.is_set():
                self._semaphore.release()
                return False
            with self._lock:
                self._active_count += 1
            return True
        with self._lock:
            if self._pending_count >= self.max_pending:
                return False
            self._pending_count += 1
        acquired = False
        deadline = time.monotonic() + self.pending_timeout
        try:
            while not acquired:
                if cancel_event is not None and cancel_event.is_set():
                    return False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                acquired = self._semaphore.acquire(timeout=min(0.1, remaining))
            if cancel_event is not None and cancel_event.is_set():
                self._semaphore.release()
                acquired = False
                return False
            return acquired
        finally:
            with self._lock:
                self._pending_count = max(0, self._pending_count - 1)
                if acquired:
                    self._active_count += 1

    def release(self) -> None:
        with self._lock:
            if self._active_count < 1:
                raise RuntimeError("Web 聊天并发槽位重复释放")
            self._active_count -= 1
            self._semaphore.release()

    def status(self) -> dict[str, int]:
        with self._lock:
            return {
                "active_chats": self._active_count,
                "max_chats": self.max_concurrent,
                "pending_chats": self._pending_count,
                "max_pending": self.max_pending,
            }

    def matches(self, max_concurrent: int, max_pending: int, pending_timeout: float) -> bool:
        return (
            self.max_concurrent == max(1, int(max_concurrent))
            and self.max_pending == max(0, int(max_pending))
            and self.pending_timeout == max(1.0, float(pending_timeout))
        )


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
        context_compressor: Callable[..., dict[str, Any]] = compress_context,
        runtime_status_provider: Callable[[], dict[str, Any]] | None = None,
        message_health_checker: Callable[[str, str], dict[str, Any]] | None = None,
        message_transport_remover: Callable[[str, str], None] | None = None,
        plan_waker: Callable[[], None] | None = None,
        summary_waker: Callable[[], None] | None = None,
        router_ref: Any | None = None,
        version_manifest_fetcher: Callable[[str, float], dict[str, Any]] = _fetch_remote_version_manifest,
    ) -> None:
        self.root = root.resolve()
        self.event_source = event_source
        self.context_compressor = context_compressor
        self.runtime_status_provider = runtime_status_provider
        self.message_health_checker = message_health_checker
        self.message_transport_remover = message_transport_remover
        self.plan_waker = plan_waker
        self.summary_waker = summary_waker
        self._router_ref = router_ref
        self.version_manifest_fetcher = version_manifest_fetcher
        self._active_runs: dict[str, ActiveRun] = {}
        self._active_runs_lock = threading.RLock()
        self._session_leases: dict[tuple[str, str, str], dict[str, float]] = {}
        self._chat_gates: dict[str, _UserChatGate] = {}
        self._chat_gates_lock = threading.Lock()
        self._file_upload_lock = threading.RLock()
        self._skill_upload_lock = threading.RLock()
        self._version_check_lock = threading.Lock()
        self._version_check_cache: tuple[float, dict[str, Any]] | None = None

    def _get_chat_gate(self, user: str) -> _UserChatGate:
        try:
            config = load_config(user, self.root)
        except ConfigError:
            # WebRunService 也用于最小化嵌入/测试环境；缺少完整配置时
            # 维持既有行为并使用安全默认值，不能让并发层反向破坏启动。
            config = {}
        web_config = config.get("web") or {}
        if not isinstance(web_config, dict):
            web_config = {}
        try:
            max_concurrent = int(web_config.get("max_concurrent_chats", 3))
        except (TypeError, ValueError):
            max_concurrent = 3
        try:
            max_pending = int(web_config.get("max_pending_chats", 5))
        except (TypeError, ValueError):
            max_pending = 5
        try:
            pending_timeout = float(web_config.get("pending_chat_timeout", 30.0))
        except (TypeError, ValueError):
            pending_timeout = 30.0
        if not math.isfinite(pending_timeout):
            pending_timeout = 30.0
        max_concurrent = max(1, max_concurrent)
        max_pending = max(0, max_pending)
        pending_timeout = max(1.0, pending_timeout)

        with self._chat_gates_lock:
            gate = self._chat_gates.get(user)
            if gate is not None:
                if gate.matches(max_concurrent, max_pending, pending_timeout):
                    return gate
                status = gate.status()
                if status["active_chats"] or status["pending_chats"]:
                    # 在途请求继续服从创建时的闸门，等完全空闲后再原子替换。
                    return gate
            gate = _UserChatGate(
                max_concurrent=max_concurrent,
                max_pending=max_pending,
                pending_timeout=pending_timeout,
            )
            self._chat_gates[user] = gate
            return gate

    def chat_gate_status(self) -> dict[str, dict[str, int]]:
        with self._chat_gates_lock:
            return {user: gate.status() for user, gate in self._chat_gates.items()}

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

    def require_client_id(self, client_id: Any, *, optional: bool = True) -> str:
        if client_id in (None, "") and optional:
            return ""
        if not isinstance(client_id, str) or not _CLIENT_ID_RE.fullmatch(client_id.strip()):
            raise InvalidRequestError("client_id 必须是 8–128 位字母、数字、下划线或连字符")
        return client_id.strip()

    def _prune_session_leases_locked(self, now: float | None = None) -> None:
        cutoff = (time.monotonic() if now is None else now) - SESSION_LEASE_TTL_SECONDS
        for key, clients in list(self._session_leases.items()):
            active = {client: seen for client, seen in clients.items() if seen >= cutoff}
            if active:
                self._session_leases[key] = active
            else:
                self._session_leases.pop(key, None)

    def _touch_session_lease_locked(
        self,
        user: str,
        source: str,
        session_id: str,
        client_id: str,
    ) -> int:
        if not client_id:
            return 0
        self._prune_session_leases_locked()
        clients = self._session_leases.setdefault((user, source, session_id), {})
        clients[client_id] = time.monotonic()
        return len(clients)

    def _release_session_lease_locked(
        self,
        user: str,
        source: str,
        session_id: str,
        client_id: str,
    ) -> int:
        self._prune_session_leases_locked()
        key = (user, source, session_id)
        clients = self._session_leases.get(key, {})
        if client_id:
            clients.pop(client_id, None)
        if clients:
            self._session_leases[key] = clients
            return len(clients)
        self._session_leases.pop(key, None)
        return 0

    def session_lease(
        self,
        user: Any,
        session_id: Any,
        client_id: Any,
        *,
        source: Any = "web",
    ) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        normalized_session = self.require_session_id(session_id)
        normalized_client = self.require_client_id(client_id, optional=False)
        with self._active_runs_lock:
            clients = self._touch_session_lease_locked(
                name, normalized_source, normalized_session, normalized_client
            )
        return {
            "user": name,
            "source": normalized_source,
            "session_id": normalized_session,
            "client_id": normalized_client,
            "active_clients": clients,
            "leased": True,
        }

    def release_session_lease(
        self,
        user: Any,
        session_id: Any,
        client_id: Any,
        *,
        source: Any = "web",
    ) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        normalized_session = self.require_session_id(session_id)
        normalized_client = self.require_client_id(client_id, optional=False)
        with self._active_runs_lock:
            remaining = self._release_session_lease_locked(
                name, normalized_source, normalized_session, normalized_client
            )
        return {
            "user": name,
            "source": normalized_source,
            "session_id": normalized_session,
            "client_id": normalized_client,
            "active_clients": remaining,
            "released": True,
        }

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

    def has_active_runs(self) -> bool:
        with self._active_runs_lock:
            return bool(self._active_runs)

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
                accepted_current_run, queued = active.guidance.offer(text)
            except queue.Full as exc:
                raise ConflictError("运行中引导队列已满，请等待当前引导被处理") from exc
        return {
            "run_id": normalized_run_id,
            "user": name,
            "session_id": active.session_id,
            "status": (
                "accepted_current_run"
                if accepted_current_run
                else "queued_next_turn"
            ),
            "queued": queued,
        }

    def cancel_run(self, user: Any, run_id: Any) -> dict[str, Any]:
        """Request an idempotent emergency stop for one active run owned by the user."""

        name = self.require_user(user)
        normalized_run_id = self.require_run_id(run_id)
        with self._active_runs_lock:
            active = self._active_runs.get(normalized_run_id)
            if active is None or active.user != name:
                raise NotFoundError(f"运行不存在或已结束：{normalized_run_id}")
            active.cancel_event.set()
            active.guidance.close()
        return {
            "run_id": normalized_run_id,
            "user": name,
            "session_id": active.session_id,
            "status": "stopping",
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

    def kemo_provider_models(self, user: Any) -> dict[str, Any]:
        """Discover LLMs through a persisted Kemo configuration without mutating it."""
        name = self.require_user(user)
        config = load_config(name, self.root)
        configured_provider = config.get("provider") or {}
        if str(configured_provider.get("type") or "").strip().lower() != "kemo":
            raise InvalidRequestError("只有已保存的 Kemo 私有协议配置允许拉取模型")
        try:
            runtime_provider = provider_runtime_config(config)
            catalog = KemoGatewayAdapter(runtime_provider).models(task="llm")
        except (ConfigError, ProviderError, ValueError) as exc:
            raise ProviderDiscoveryError(
                "Kemo API 验证失败，未拉取模型"
            ) from exc
        return {
            "user": name,
            "protocol": "kemo",
            "api_valid": True,
            "count": catalog.count,
            "data": [item.model_dump(mode="json") for item in catalog.data],
        }

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

    def files(
        self,
        user: Any,
        scope: Any,
        *,
        path: str = "",
        search: str = "",
        page: int = 1,
        page_size: int = 6,
    ) -> dict[str, Any]:
        name, normalized_scope, directory = self._file_scope_root(user, scope)
        return {
            "user": name,
            "scope": normalized_scope,
            "root": self._project_path(directory),
            **_directory_listing(
                directory,
                path=path,
                search=search,
                page=page,
                page_size=page_size,
            ),
        }

    def _write_area_file(
        self,
        directory: Path,
        path: Any,
        data: bytes,
        *,
        avoid_overwrite: bool = False,
    ) -> dict[str, Any]:
        if len(data) > FILE_UPLOAD_MAX_BYTES:
            raise InvalidRequestError(
                f"文件超过最大限制 {FILE_UPLOAD_MAX_BYTES // (1024 * 1024)} MB"
            )
        relative, target = _safe_relative_target(directory, path)
        _reject_link_path(directory.resolve(), target)
        if target.exists() and target.is_dir():
            raise ConflictError(f"目标是目录：{relative}")
        if avoid_overwrite and target.exists():
            stem = target.stem
            suffix = target.suffix
            index = 2
            while True:
                candidate = target.with_name(f"{stem} ({index}){suffix}")
                _reject_link_path(directory.resolve(), candidate)
                if not candidate.exists():
                    target = candidate
                    relative = target.relative_to(directory.resolve()).as_posix()
                    break
                index += 1
        _atomic_write(target, data)
        return {
            "path": relative,
            "size": len(data),
            "updated": True,
            "renamed": str(relative) != str(path).replace("\\", "/"),
        }

    def save_file(self, user: Any, scope: Any, path: Any, data: bytes) -> dict[str, Any]:
        name, normalized_scope, directory = self._file_scope_root(user, scope)
        with self._file_upload_lock:
            result = self._write_area_file(
                directory,
                path,
                data,
                avoid_overwrite=True,
            )
        return {"user": name, "scope": normalized_scope, **result}

    def require_uploaded_files(self, user: str, uploaded_files: Any) -> list[dict[str, Any]]:
        if uploaded_files in (None, []):
            return []
        if not isinstance(uploaded_files, list) or len(uploaded_files) > 20:
            raise InvalidRequestError("uploaded_files 必须是不超过 20 项的文件路径数组")
        directory = (self.root / "users" / user / "file_upload").resolve()
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for value in uploaded_files:
            if not isinstance(value, str) or not value.strip():
                raise InvalidRequestError("uploaded_files 只能包含非空文件路径")
            relative, target = _safe_relative_target(directory, value)
            _reject_link_path(directory, target)
            relative_path = relative.as_posix() if isinstance(relative, Path) else str(relative).replace("\\", "/")
            if relative_path in seen:
                continue
            if not target.is_file():
                raise NotFoundError(f"上传文件不存在：{relative_path}")
            seen.add(relative_path)
            try:
                normalized.append(describe_uploaded_asset(
                    self.root,
                    user,
                    {
                    "name": target.name,
                    "path": self._project_path(target),
                    "size": target.stat().st_size,
                    },
                ))
            except AttachmentError as exc:
                raise InvalidRequestError(str(exc)) from None
        return normalized

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

    def _media_preview(self, directory: Path, path: Any) -> tuple[Path, str, str]:
        relative, target = _safe_relative_target(directory, path)
        _reject_link_path(directory.resolve(), target)
        if target.is_symlink() or not target.is_file():
            raise NotFoundError(f"文件不存在：{relative}")
        preview_type = _MEDIA_PREVIEW_TYPES.get(target.suffix.lower())
        if preview_type is None:
            raise InvalidRequestError("该文件类型不支持网页预览")
        kind, media_type, max_bytes, limit_label = preview_type
        try:
            size = target.stat().st_size
        except OSError as exc:
            raise WebServiceError(f"无法读取文件信息：{relative}") from exc
        if size > max_bytes:
            raise InvalidRequestError(f"文件超过{limit_label}，请下载后查看")
        return target, media_type, kind

    def file_preview(self, user: Any, scope: Any, path: Any) -> tuple[Path, str, str]:
        _, _, directory = self._file_scope_root(user, scope)
        return self._media_preview(directory, path)

    def tmp_file_preview(self, path: Any) -> tuple[Path, str, str]:
        return self._media_preview(self.root / "tmp", path)

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

    def delete_files(self, user: Any, scope: Any, paths: Any) -> dict[str, Any]:
        name, normalized_scope, directory = self._file_scope_root(user, scope)
        return {
            "user": name,
            "scope": normalized_scope,
            **self._delete_area_files(directory, paths, item_label="文件"),
        }

    def delete_all_files(self, user: Any, scope: Any) -> dict[str, Any]:
        name, normalized_scope, directory = self._file_scope_root(user, scope)
        directory.mkdir(parents=True, exist_ok=True)
        paths = [item["relative_path"] for item in _flat_files(directory)]
        if not paths:
            self._prune_empty_directories(directory)
            result = {"deleted_paths": [], "deleted_count": 0}
        else:
            result = self._delete_area_files(directory, paths, item_label="文件")
        return {"user": name, "scope": normalized_scope, **result}

    def tmp_files(
        self,
        *,
        path: str = "",
        search: str = "",
        page: int = 1,
        page_size: int = 6,
    ) -> dict[str, Any]:
        directory = self.root / "tmp"
        return {
            "root": "tmp",
            **_directory_listing(
                directory,
                path=path,
                search=search,
                page=page,
                page_size=page_size,
            ),
        }

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
        result = self.delete_tmp_files([path])
        return {"path": result["deleted_paths"][0], "deleted": True}

    def delete_tmp_files(self, paths: Any) -> dict[str, Any]:
        return self._delete_area_files(self.root / "tmp", paths, item_label="临时文件")

    def _delete_area_files(
        self,
        directory: Path,
        paths: Any,
        *,
        item_label: str,
    ) -> dict[str, Any]:
        if not isinstance(paths, list) or not paths:
            raise InvalidRequestError("paths 必须是非空数组")
        if len(paths) > 10_000:
            raise InvalidRequestError(f"单次最多删除 10000 个{item_label}")

        root = directory.resolve()
        validated: list[tuple[str, Path]] = []
        seen: set[str] = set()
        for value in paths:
            relative, target = _safe_relative_target(directory, value)
            if relative in seen:
                continue
            _reject_link_path(root, target)
            if target.is_symlink() or not target.is_file():
                raise NotFoundError(f"{item_label}不存在：{relative}")
            seen.add(relative)
            validated.append((relative, target))

        deleted_paths: list[str] = []
        for relative, target in validated:
            try:
                target.unlink()
            except OSError as exc:
                raise WebServiceError(f"{item_label}删除失败：{relative}") from exc
            deleted_paths.append(relative)

        self._prune_empty_directories(directory)
        return {
            "deleted_paths": deleted_paths,
            "deleted_count": len(deleted_paths),
        }

    def delete_all_tmp_files(self) -> dict[str, Any]:
        directory = self.root / "tmp"
        directory.mkdir(parents=True, exist_ok=True)
        paths = [item["relative_path"] for item in _flat_files(directory)]
        if not paths:
            self._prune_empty_directories(directory)
            return {"deleted_paths": [], "deleted_count": 0}
        return self.delete_tmp_files(paths)

    def _prune_empty_directories(self, directory: Path) -> None:
        if not directory.is_dir():
            return
        for current, directories, _ in os.walk(directory, topdown=False, followlinks=False):
            for name in directories:
                candidate = Path(current) / name
                if candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)():
                    continue
                try:
                    candidate.rmdir()
                except OSError:
                    continue

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
                "version": definition.version,
                "description": definition.description,
                "enabled": definition.enabled,
                "source": "global" if definition.source == "builtin" else "user",
                "trigger": (
                    _agent_registration_value(definition.trigger_registration, "触发")
                    or "未声明独立触发条件"
                ),
                "rules": definition.instruction,
                "executor": definition.executor,
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

    def delete_user_agent(self, user: Any, agent: Any) -> dict[str, Any]:
        name = self.require_user(user)
        if not isinstance(agent, str) or not _AGENT_NAME_RE.fullmatch(agent.strip()):
            raise InvalidRequestError("agent 必须是有效的子代理名称")
        agent_name = agent.strip()
        definition = discover_agents(self.root, name).agents.get(agent_name)
        if definition is None or definition.source != "user":
            raise NotFoundError(f"用户子代理不存在：{agent_name}")

        directory = (self.root / "users" / name / "agents").resolve()
        target = definition.directory.resolve()
        try:
            target.relative_to(directory)
        except ValueError:
            raise InvalidRequestError("用户子代理路径越出允许目录") from None
        _reject_link_path(directory, definition.directory)
        _reject_tree_links(definition.directory)

        tombstone = directory / f"_{agent_name}.{uuid.uuid4().hex}.deleting"
        try:
            os.replace(definition.directory, tombstone)
            shutil.rmtree(tombstone)
        except OSError as exc:
            if tombstone.exists() and not definition.directory.exists():
                try:
                    os.replace(tombstone, definition.directory)
                except OSError:
                    pass
            raise WebServiceError(f"用户子代理删除失败：{agent_name}") from exc
        return {
            "user": name,
            "name": agent_name,
            "path": f"users/{name}/agents/{agent_name}",
            "deleted": True,
        }

    def _message_module_directory(
        self, user: Any, module_name: Any
    ) -> tuple[str, str, Path, MessagePluginConfig]:
        name = self.require_user(user)
        if not isinstance(module_name, str) or not module_name.strip():
            raise InvalidRequestError("module_name 必须是非空字符串")
        logical_name = module_name.strip()
        pure = PurePosixPath(logical_name.replace("\\", "/"))
        if (
            len(pure.parts) != 1
            or pure.name in {".", "..", "__pycache__"}
            or pure.name.startswith(".")
            or "\x00" in logical_name
            or ":" in logical_name
        ):
            raise InvalidRequestError("消息模块名称必须是 message/out 下的直接目录名")
        base = (self.root / "message" / "out").resolve()
        target = base / logical_name
        if not target.is_dir():
            raise NotFoundError(f"消息模块不存在：{logical_name}")
        if target.is_symlink() or getattr(target, "is_junction", lambda: False)():
            raise InvalidRequestError("消息模块目录不能是符号链接或目录联接")
        try:
            target.resolve().relative_to(base)
        except ValueError:
            raise InvalidRequestError("消息模块路径越出 message/out") from None
        try:
            config = MessagePluginConfig.load(self.root, target)
        except MessagePluginError as exc:
            raise InvalidRequestError(str(exc)) from exc
        if config.bound_user != name:
            raise NotFoundError(f"当前用户未绑定消息模块：{logical_name}")
        return name, logical_name, target, config

    @staticmethod
    def _message_log_text(value: str) -> str:
        return " ".join(value.strip().split())

    def _legacy_message_logs(
        self, config: MessagePluginConfig
    ) -> tuple[list[dict[str, Any]], bool, int]:
        entries: list[dict[str, Any]] = []
        log_files = [
            item
            for item in _visible_children(config.log_path)
            if item.is_file() and item.suffix.casefold() == ".md"
        ]
        log_files.sort(key=lambda item: (item.name.casefold(), item.name), reverse=True)
        files_root = config.files_path.relative_to(self.root).as_posix()
        for log_file in log_files:
            try:
                content = log_file.read_text("utf-8-sig")
            except (OSError, UnicodeError):
                continue
            headings = list(_MESSAGE_LOG_HEADING.finditer(content))
            for index, heading in enumerate(headings):
                block_end = headings[index + 1].start() if index + 1 < len(headings) else len(content)
                block = content[heading.end():block_end]
                timestamp = heading.group("timestamp").strip()
                common = {
                    "timestamp": timestamp,
                    "chat_type": heading.group("chat_type").strip(),
                    "chat_id": heading.group("chat_id").strip(),
                    "source": log_file.relative_to(self.root).as_posix(),
                }
                inbound = _MESSAGE_LOG_INBOUND.search(block)
                if inbound:
                    inbound_text = self._message_log_text(inbound.group("content"))
                    if inbound_text and inbound_text != "[仅附件]":
                        entries.append({
                            **common,
                            "id": f"{config.directory.name}:{log_file.name}:{index}:receive",
                            "direction": "receive",
                            "kind": "text",
                            "content": inbound_text,
                            "file_path": None,
                            "success": True,
                        })
                for attachment_index, attachment in enumerate(_MESSAGE_LOG_ATTACHMENT.finditer(block)):
                    attachment_name = self._message_log_text(attachment.group("name"))
                    entries.append({
                        **common,
                        "id": f"{config.directory.name}:{log_file.name}:{index}:file:{attachment_index}",
                        "direction": "receive",
                        "kind": "file",
                        "content": attachment_name,
                        "file_path": f"{files_root}/{attachment_name}",
                        "mime": attachment.group("mime").strip(),
                        "size": int(attachment.group("size")),
                        "success": True,
                    })
                outbound = _MESSAGE_LOG_OUTBOUND.search(block)
                if outbound:
                    outbound_text = self._message_log_text(outbound.group("content"))
                    if outbound_text:
                        failed = outbound_text.startswith("处理失败：")
                        entries.append({
                            **common,
                            "id": f"{config.directory.name}:{log_file.name}:{index}:send",
                            "direction": "send",
                            "kind": "system" if failed else "text",
                            "content": outbound_text,
                            "file_path": None,
                            "success": not failed,
                        })
                for attachment_index, attachment in enumerate(
                    _MESSAGE_LOG_OUTBOUND_ATTACHMENT.finditer(block)
                ):
                    entries.append({
                        **common,
                        "id": f"{config.directory.name}:{log_file.name}:{index}:send-file:{attachment_index}",
                        "direction": "send",
                        "kind": "file",
                        "content": self._message_log_text(attachment.group("name")),
                        "file_path": attachment.group("path").strip(),
                        "success": True,
                    })
        entries.sort(key=lambda item: (str(item["timestamp"]), str(item["id"])), reverse=True)
        truncated = len(entries) > _MESSAGE_LOG_LIMIT
        today = datetime.now(_BEIJING).strftime("%Y-%m-%d")
        today_count = sum(
            str(item.get("timestamp") or "").startswith(today) for item in entries
        )
        return entries[:_MESSAGE_LOG_LIMIT], truncated, today_count

    def _message_logs(
        self, config: MessagePluginConfig
    ) -> tuple[list[dict[str, Any]], bool, int]:
        """Read indexed message logs, retaining Markdown as a safe fallback."""

        try:
            store = LogStore(self.root)
            files_root = config.files_path.relative_to(self.root).as_posix()
            store.migrate_message_logs(
                config.log_path,
                machine_id=config.machine_id,
                user=config.bound_user,
                platform=config.platform,
                files_root=files_root,
            )
            entries = store.list_messages(
                config.machine_id,
                limit=_MESSAGE_LOG_LIMIT + 1,
            )
            today = datetime.now(_BEIJING).strftime("%Y-%m-%d")
            today_count = store.count_messages(
                config.machine_id,
                date_prefix=today,
            )
            return (
                entries[:_MESSAGE_LOG_LIMIT],
                len(entries) > _MESSAGE_LOG_LIMIT,
                today_count,
            )
        except Exception:
            return self._legacy_message_logs(config)

    def _message_transport_item(
        self,
        config: MessagePluginConfig,
        directory: Path,
        components: dict[str, Any],
        issues: list[dict[str, str]],
    ) -> dict[str, Any]:
        try:
            state = read_json_object(config.state_path, allow_empty=True)
        except Exception as exc:
            state = {}
            issues.append({"name": directory.name, "error": str(exc)})
        component = components.get(f"transport:{config.platform}")
        component = component if isinstance(component, dict) else {}
        runtime_state = str(component.get("state") or "")
        health = str(state.get("health") or "unknown")
        transport_state = (
            "running"
            if runtime_state == "running"
            else "error"
            if runtime_state == "failed" or health in {"dead", "degraded"}
            else "stopped"
        )
        connection_status = (
            "connected"
            if health == "healthy"
            else "error"
            if health in {"dead", "degraded"} or transport_state == "error"
            else "disconnected"
        )
        logs, logs_truncated, today_logs = self._message_logs(config)
        temporary_files = _flat_files(config.files_path, relative_to=self.root)
        return {
            "id": directory.name,
            "name": config.machine_id,
            "platform": config.platform,
            "display_name": config.display_name,
            "description": f"{config.display_name}，负责 {config.platform} 平台的文本与文件消息传输。",
            "capabilities": sorted(config.capabilities),
            "state": transport_state,
            "connection_status": connection_status,
            "bound_user": config.bound_user,
            "allowed_tools": (
                sorted(config.allowed_tools) if config.allowed_tools is not None else None
            ),
            "last_error": component.get("last_error") or state.get("error"),
            "health": health,
            "last_check": state.get("last_check"),
            "last_message_at": state.get("last_message_at"),
            "latency_ms": state.get("latency_ms"),
            "messages_received_today": int(state.get("messages_received_today") or 0),
            "messages_sent_today": int(state.get("messages_sent_today") or 0),
            "path": directory.relative_to(self.root).as_posix(),
            "files_path": config.files_path.relative_to(self.root).as_posix(),
            "log_path": config.log_path.relative_to(self.root).as_posix(),
            "structured_log_path": "runtime/logs.sqlite3",
            "message_buffer": config.buffer_path.relative_to(self.root).as_posix(),
            "modules": dict(config.modules),
            "api_imported": True,
            "polling_interval": "1s",
            "health_interval": "30s",
            "file_relay_enabled": bool(
                {"receive_file", "send_file"}.intersection(config.capabilities)
            ),
            "log_rotation": "每日轮换",
            "temporary_file_count": len(temporary_files),
            "temporary_file_bytes": sum(int(item["size"]) for item in temporary_files),
            "today_log_count": today_logs,
            "logs": logs,
            "logs_truncated": logs_truncated,
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
        components = self._runtime_status().get("components") or {}
        components = components if isinstance(components, dict) else {}
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
                transports.append(
                    self._message_transport_item(config, directory, components, issues)
                )
            except MessagePluginError as exc:
                try:
                    raw = read_json_object(directory / "message.json", allow_empty=True)
                except Exception:
                    raw = {}
                if raw.get("bound_user") == name:
                    issues.append({"name": directory.name, "error": str(exc)})
        transports.sort(key=lambda item: (item["platform"], item["name"]))
        return {
            "user": name,
            "bindings": bindings,
            "transports": transports,
            "summary": {
                "total_bindings": len(bindings),
                "total_transports": len(transports),
                "running_transports": sum(item["state"] == "running" for item in transports),
                "stopped_transports": sum(item["state"] == "stopped" for item in transports),
                "error_transports": sum(item["state"] == "error" for item in transports),
                "connected_transports": sum(
                    item["connection_status"] == "connected" for item in transports
                ),
                "temporary_files": sum(item["temporary_file_count"] for item in transports),
                "today_logs": sum(item["today_log_count"] for item in transports),
            },
            "issues": issues,
        }

    def check_message_module(self, user: Any, module_name: Any) -> dict[str, Any]:
        name, logical_name, _, config = self._message_module_directory(user, module_name)
        try:
            if self.message_health_checker is not None:
                try:
                    state = self.message_health_checker(config.platform, name)
                except Exception:
                    state = FileMessageTransport(config).check_health()
            else:
                state = FileMessageTransport(config).check_health()
        except Exception as exc:
            raise WebServiceError(f"消息模块连接检测失败：{logical_name}（{exc}）") from exc
        refreshed = self.message_status(name)
        transport = next(
            (item for item in refreshed["transports"] if item["id"] == logical_name),
            None,
        )
        return {
            "user": name,
            "module": logical_name,
            "checked": True,
            "state": state,
            "transport": transport,
        }

    def delete_message_module(self, user: Any, module_name: Any) -> dict[str, Any]:
        name, logical_name, target, config = self._message_module_directory(user, module_name)
        _reject_tree_links(target)
        relative_path = target.relative_to(self.root).as_posix()
        tombstone = target.parent / f".{logical_name}.{uuid.uuid4().hex}.deleting"
        try:
            os.replace(target, tombstone)
            if self.message_transport_remover is not None:
                self.message_transport_remover(config.platform, name)
            shutil.rmtree(tombstone)
        except Exception as exc:
            if tombstone.exists() and not target.exists():
                try:
                    os.replace(tombstone, target)
                except OSError:
                    pass
            raise WebServiceError(f"消息模块删除失败：{logical_name}") from exc
        try:
            LogStore(self.root).delete_message_logs(
                config.machine_id,
                user=name,
            )
        except Exception:
            pass
        return {
            "user": name,
            "module": logical_name,
            "platform": config.platform,
            "path": relative_path,
            "deleted": True,
        }

    def expands(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        config = load_config(name, self.root)
        source_policy = MainAgentSourcePolicy.from_config(config)
        prompt_settings = parse_prompt_settings(config)
        registry = load_prompt_source_registry(self.root, name)
        selection = registry.select_expand(
            max_chars=prompt_settings.char_limits["expand_data"],
            mode=prompt_settings.injection_mode["expand_data"],
            allow={
                "global": source_policy.global_expand.selector(),
                "shared": source_policy.shared_expand.selector(),
                "user": None,
            },
        )
        diagnostics = registry.selection_diagnostics().get("expand") or {}
        scope_roots = {
            "global": self.root / "global_expand",
            "shared": self.root / "shared_expand",
            "user": self.root / "users" / name / "expand",
        }
        expands: list[dict[str, Any]] = []
        scope_counts: dict[str, int] = {}
        injection_cursor = 0
        has_injection_piece = False
        for scope in ("global", "shared", "user"):
            directory = scope_roots[scope]
            scope_diagnostics = diagnostics.get(scope) or {}
            discovered = list(scope_diagnostics.get("discovered") or [])
            discovered_set = set(discovered)
            for module_dir in _visible_children(directory):
                if module_dir.is_dir() and module_dir.name not in discovered_set:
                    discovered.append(module_dir.name)
                    discovered_set.add(module_dir.name)
            selected = set(scope_diagnostics.get("selected") or [])
            health_status = scope_diagnostics.get("health_status") or {}
            items: list[dict[str, Any]] = []
            for module_name in discovered:
                module = directory / module_name
                health = health_status.get(module_name) or {
                    "name": module_name,
                    "valid": False,
                    "input_health": "异常",
                    "error": "模块未进入运行时注册表",
                }
                module_path_safe = (
                    module.is_dir()
                    and not module.is_symlink()
                    and not getattr(module, "is_junction", lambda: False)()
                )
                if module_path_safe:
                    try:
                        module.resolve().relative_to(directory.resolve())
                    except ValueError:
                        module_path_safe = False
                if not module_path_safe:
                    health = {
                        **health,
                        "valid": False,
                        "input_health": "异常",
                        "error": "拓展模块目录不能是符号链接、目录联接或越界路径",
                    }
                files = _flat_files(module, relative_to=directory) if module_path_safe else []
                valid = bool(health.get("valid"))
                whitelisted = scope == "user" or module_name in selected
                collected_markdown = self._read_expand_text(
                    module, health.get("input_data")
                )
                control_document = self._read_expand_text(
                    module, health.get("start_control")
                )
                control_injection, control_operation = self._expand_control_sections(
                    control_document
                )
                module_piece = self._expand_prompt_piece(
                    scope=scope,
                    module_name=module_name,
                    health=health,
                    collected_markdown=collected_markdown,
                    control_injection=control_injection,
                ) if valid and module_name in selected else ""
                injected_markdown = ""
                if module_piece:
                    piece_start = injection_cursor + (2 if has_injection_piece else 0)
                    piece_end = piece_start + len(module_piece)
                    if piece_start < len(selection.text):
                        injected_markdown = selection.text[
                            piece_start:min(piece_end, len(selection.text))
                        ]
                    injection_cursor = piece_end
                    has_injection_piece = True
                updated_at = max(
                    (float(item.get("updated_at") or 0) for item in files),
                    default=0.0,
                )
                runtime_state = (
                    read_expand_runtime(module)
                    if module_path_safe and module.is_dir()
                    else {"schema_version": 1}
                )
                items.append(
                    {
                        "id": f"{scope}:{module_name}",
                        "scope": scope,
                        "name": module_name,
                        "display_name": health.get("name") or module_name,
                        "description": health.get("explain") or "",
                        "type": "directory",
                        "root": self._project_path(directory),
                        "path": self._project_path(module),
                        "relative_path": module_name,
                        "has_register": (module / "expand.json").is_file(),
                        "valid": valid,
                        "error": health.get("error") or "",
                        "whitelisted": whitelisted,
                        "active_for_main_agent": valid and whitelisted,
                        "input_health": health.get("input_health") or "异常",
                        "open_input": bool(health.get("open_input")),
                        "open_control": bool(health.get("open_control")),
                        "input_data": health.get("input_data") or "",
                        "start_update": health.get("start_update") or "",
                        "start_expand": health.get("start_expand") or "",
                        "start_control": health.get("start_control") or "",
                        "control_document": control_document,
                        "control_injection_markdown": control_injection,
                        "control_operation_markdown": control_operation,
                        "collected_markdown": collected_markdown,
                        "injected_markdown": injected_markdown,
                        "injected_tokens": estimate_text_tokens(injected_markdown),
                        "runtime": runtime_state,
                        "files": files,
                        "updated_at": updated_at,
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
            "status_summary": {
                "enabled": sum(
                    item["active_for_main_agent"]
                    for scope in expands
                    for item in scope["items"]
                ),
                "healthy": sum(
                    item["valid"] and item["input_health"] == "正常"
                    for scope in expands
                    for item in scope["items"]
                ),
                "invalid": sum(
                    not item["valid"]
                    for scope in expands
                    for item in scope["items"]
                ),
            },
            "expands": expands,
            "injection": {
                "content": selection.text,
                "source_files": list(selection.source_files),
                "original_chars": selection.original_chars,
                "injected_chars": selection.injected_chars,
                "original_items": selection.original_items,
                "injected_items": selection.injected_items,
                "estimated_tokens": estimate_text_tokens(selection.text),
                "truncated": selection.truncated,
                "prompt_section": "expand_data",
                "prompt_position": "System Prompt / Expand Data",
            },
            "source_policy": source_policy.public_summary(),
        }

    @staticmethod
    def _read_expand_text(module_dir: Path, file_name: Any) -> str:
        if not isinstance(file_name, str) or not file_name.strip():
            return ""
        normalized = file_name.strip()
        if Path(normalized).name != normalized:
            return ""
        path = module_dir / normalized
        if not path.is_file() or path.is_symlink():
            return ""
        try:
            path.resolve().relative_to(module_dir.resolve())
            return path.read_text("utf-8-sig").strip()
        except (OSError, UnicodeError, ValueError):
            return ""

    @staticmethod
    def _expand_control_sections(content: str) -> tuple[str, str]:
        if not content:
            return "", ""
        injection_match = _EXPAND_INJECTION_HEADING.search(content)
        operation_match = _EXPAND_OPERATION_HEADING.search(
            content, injection_match.end() if injection_match else 0
        )
        injection = (
            content[
                injection_match.end():operation_match.start() if operation_match else len(content)
            ].strip()
            if injection_match
            else ""
        )
        operation = content[operation_match.end():].strip() if operation_match else ""
        return injection, operation

    @staticmethod
    def _expand_prompt_piece(
        *,
        scope: str,
        module_name: str,
        health: dict[str, Any],
        collected_markdown: str,
        control_injection: str,
    ) -> str:
        parts: list[str] = []
        if (
            health.get("open_input")
            and health.get("input_health") == "正常"
            and collected_markdown
        ):
            parts.append(f"## 数据采集\n{collected_markdown}")
        if health.get("open_control") and control_injection:
            parts.append(
                "## 操控能力\n"
                f"{control_injection}\n\n"
                f"调用入口：使用 `expand_call`，传入 `scope={scope}`、"
                f"`module={module_name}`，具体命令和参数按需读取操作层。"
            )
        return f"[{scope}:{module_name}]\n" + "\n\n".join(parts) if parts else ""

    def _expand_module_directory(
        self, user: Any, scope: Any, module_name: Any
    ) -> tuple[str, str, str, Path]:
        name = self.require_user(user)
        if not isinstance(scope, str) or scope not in _EXPAND_SCOPES:
            raise InvalidRequestError("scope 只允许 global、shared 或 user")
        if not isinstance(module_name, str) or not module_name.strip():
            raise InvalidRequestError("module_name 必须是非空字符串")
        logical_name = module_name.strip()
        pure = PurePosixPath(logical_name.replace("\\", "/"))
        if (
            len(pure.parts) != 1
            or pure.name in {".", "..", "__pycache__"}
            or pure.name.startswith(".")
            or "\x00" in logical_name
            or ":" in logical_name
        ):
            raise InvalidRequestError("拓展模块名称必须是对应拓展层的直接目录名")
        base = {
            "global": self.root / "global_expand",
            "shared": self.root / "shared_expand",
            "user": self.root / "users" / name / "expand",
        }[scope].resolve()
        target = base / logical_name
        if not target.is_dir():
            raise NotFoundError(f"拓展模块不存在：{scope}:{logical_name}")
        if target.is_symlink() or getattr(target, "is_junction", lambda: False)():
            raise InvalidRequestError("拓展模块目录不能是符号链接或目录联接")
        try:
            target.resolve().relative_to(base)
        except ValueError:
            raise InvalidRequestError("拓展模块路径越出对应拓展目录") from None
        return name, scope, logical_name, target

    def refresh_expand_module(
        self, user: Any, scope: Any, module_name: Any
    ) -> dict[str, Any]:
        name, normalized_scope, logical_name, target = self._expand_module_directory(
            user, scope, module_name
        )
        module = next(
            (
                item
                for group in self.expands(name)["expands"]
                if group["scope"] == normalized_scope
                for item in group["items"]
                if item["name"] == logical_name
            ),
            None,
        )
        if not module or not module["valid"]:
            raise InvalidRequestError(
                f"拓展模块配置无效，无法更新：{module['error'] if module else logical_name}"
            )
        updater = target / str(module["start_update"])
        if not updater.is_file():
            raise NotFoundError(f"拓展模块更新入口不存在：{module['start_update']}")
        if updater.is_symlink() or getattr(updater, "is_junction", lambda: False)():
            raise InvalidRequestError("拓展模块更新入口不能是符号链接或目录联接")
        try:
            updater.resolve().relative_to(target.resolve())
        except ValueError:
            raise InvalidRequestError("拓展模块更新入口越出模块目录") from None
        config = load_config(name, self.root)
        started = time.monotonic()
        result = run_module_updater(
            updater,
            target,
            timeout=module_update_timeout(config),
        )
        duration_ms = round((time.monotonic() - started) * 1000)
        if result.get("ok") is not True:
            reason = str(result.get("reason") or "未知错误")
            try:
                record_module_health(target / "expand.json", "expand", healthy=False)
                record_expand_runtime(
                    target,
                    "update",
                    ok=False,
                    duration_ms=duration_ms,
                    error=reason,
                )
            except Exception as state_exc:
                reason = f"{reason}；写回运行状态失败：{state_exc}"
            raise WebServiceError(
                f"拓展模块更新失败：{normalized_scope}:{logical_name}（{reason[-1000:]}）"
            )
        try:
            record_module_health(target / "expand.json", "expand", healthy=True)
            record_expand_runtime(
                target,
                "update",
                ok=True,
                duration_ms=duration_ms,
                result=result.get("result"),
            )
        except Exception as exc:
            raise WebServiceError(
                f"拓展模块更新成功，但运行状态写回失败：{normalized_scope}:{logical_name}"
            ) from exc
        refreshed = self.expands(name)
        refreshed_module = next(
            (
                item
                for group in refreshed["expands"]
                if group["scope"] == normalized_scope
                for item in group["items"]
                if item["name"] == logical_name
            ),
            None,
        )
        return {
            "user": name,
            "scope": normalized_scope,
            "module": logical_name,
            "updated": True,
            "item": refreshed_module,
            "injection": refreshed["injection"],
        }

    def set_expand_module_enabled(
        self, user: Any, scope: Any, module_name: Any, enabled: Any
    ) -> dict[str, Any]:
        name, normalized_scope, logical_name, _ = self._expand_module_directory(
            user, scope, module_name
        )
        if normalized_scope == "user":
            raise InvalidRequestError("用户拓展始终可用，不支持白名单开关")
        if not isinstance(enabled, bool):
            raise InvalidRequestError("enabled 必须是布尔值")
        inventory = self.expands(name)
        group = next(
            item for item in inventory["expands"] if item["scope"] == normalized_scope
        )
        current = next(
            item for item in group["items"] if item["name"] == logical_name
        )
        if not current["valid"]:
            raise InvalidRequestError("拓展模块配置无效，不能修改白名单")
        candidates = {item["name"] for item in group["items"] if item["valid"]}
        selected = {
            item["name"]
            for item in group["items"]
            if item["valid"] and item["whitelisted"]
        }
        if enabled:
            selected.add(logical_name)
        else:
            selected.discard(logical_name)
        whitelist = [] if selected == candidates else sorted(selected) or ["__kemo_none__"]
        self.patch_user_config(
            name, {"expand": {f"{normalized_scope}_whitelist": whitelist}}
        )
        return {
            "user": name,
            "scope": normalized_scope,
            "module": logical_name,
            "enabled": enabled,
            "whitelist": whitelist,
        }

    def delete_expand_module(
        self, user: Any, scope: Any, module_name: Any
    ) -> dict[str, Any]:
        name, normalized_scope, logical_name, target = self._expand_module_directory(
            user, scope, module_name
        )
        if normalized_scope != "user":
            raise InvalidRequestError("只有用户拓展允许从当前页面删除")
        _reject_tree_links(target)
        relative_path = target.relative_to(self.root).as_posix()
        tombstone = target.parent / f".{logical_name}.{uuid.uuid4().hex}.deleting"
        try:
            os.replace(target, tombstone)
            shutil.rmtree(tombstone)
        except OSError as exc:
            if tombstone.exists() and not target.exists():
                try:
                    os.replace(tombstone, target)
                except OSError:
                    pass
            raise WebServiceError(f"用户拓展删除失败：{logical_name}") from exc
        return {
            "user": name,
            "scope": normalized_scope,
            "module": logical_name,
            "path": relative_path,
            "deleted": True,
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

    @staticmethod
    def _interactive_active_key(user: str, client_id: str = "") -> str:
        return f"interactive:{user}:{client_id}" if client_id else f"interactive:{user}"

    @staticmethod
    def _index_session_payload(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "session_id": str(record.get("session_id") or ""),
            "conversation_id": str(record.get("conversation_id") or ""),
            "window": str(record.get("archive_window") or ""),
            "title": str(record.get("title") or ""),
            "summary": str(record.get("summary") or ""),
            "summary_status": str(record.get("summary_status") or "none"),
            "summary_target_round": int(record.get("summary_target_round") or 0),
            "summary_completed_round": int(record.get("summary_completed_round") or 0),
            "summary_retry_at": str(record.get("summary_retry_at") or ""),
            "summary_retry_count": max(0, int(record.get("summary_retry_count") or 0)),
            "summary_attempt_count": max(0, int(record.get("summary_attempt_count") or 0)),
            "summary_consecutive_failures": max(
                0, int(record.get("summary_consecutive_failures") or 0)
            ),
            "summary_max_attempts": max(1, int(record.get("summary_max_attempts") or 5)),
            "summary_last_attempt_at": str(record.get("summary_last_attempt_at") or ""),
            "summary_recovered_at": str(record.get("summary_recovered_at") or ""),
            "summary_last_error": (
                dict(record["summary_last_error"])
                if isinstance(record.get("summary_last_error"), dict)
                else None
            ),
            "summary_checkpoint_next_chunk": max(
                0, int(record.get("summary_checkpoint_next_chunk") or 0)
            ),
            "summary_checkpoint_total_chunks": max(
                0, int(record.get("summary_checkpoint_total_chunks") or 0)
            ),
            "state": str(record.get("lifecycle") or "open"),
            "run_state": str(record.get("run_state") or "idle"),
            "chain": str(record.get("chain") or "interactive"),
            "rounds": max(0, int(record.get("rounds") or 0)),
            "updated_at": str(record.get("updated_at") or ""),
        }

    def active_session(self, user: Any, client_id: Any = "") -> dict[str, Any]:
        """Return or reserve the user's durable interactive session."""

        name = self.require_user(user)
        normalized_client = self.require_client_id(client_id)
        active_key = self._interactive_active_key(name, normalized_client)
        record, created = get_or_reserve_index_session(
            self.root,
            name,
            "web",
            active_key,
            reuse_latest=True,
        )
        with self._active_runs_lock:
            active_clients = self._touch_session_lease_locked(
                name, "web", str(record.get("session_id") or ""), normalized_client
            )
        return {
            "user": name,
            "active_key": active_key,
            "created": created,
            "client_id": normalized_client,
            "active_clients": active_clients,
            "session": self._index_session_payload(record),
        }

    def create_session(self, user: Any, client_id: Any = "") -> dict[str, Any]:
        name = self.require_user(user)
        normalized_client = self.require_client_id(client_id)
        with self._active_runs_lock:
            session_id = new_conversation_id()
            active_key = self._interactive_active_key(name, normalized_client)
            record = reserve_session(
                self.root,
                name,
                "web",
                session_id,
                active_key=active_key,
            )
            active_clients = self._touch_session_lease_locked(
                name, "web", session_id, normalized_client
            )
        return {
            "user": name,
            "active_key": active_key,
            "created": True,
            "client_id": normalized_client,
            "active_clients": active_clients,
            "session": self._index_session_payload(record),
        }

    def close_session(
        self,
        user: Any,
        session_id: Any,
        *,
        source: Any = "web",
        client_id: Any = "",
    ) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        normalized_session = self.require_session_id(session_id)
        normalized_client = self.require_client_id(client_id)
        with self._active_runs_lock:
            remaining_clients = self._release_session_lease_locked(
                name, normalized_source, normalized_session, normalized_client
            ) if normalized_client else 0
            if normalized_client and remaining_clients:
                record = find_index_record(
                    self.root, name, normalized_source, normalized_session
                )
                if record is None:
                    raise NotFoundError(f"会话不存在：{normalized_session}")
                return {
                    "user": name,
                    "source": normalized_source,
                    "session_id": normalized_session,
                    "closed": False,
                    "deferred": True,
                    "active_clients": remaining_clients,
                    "memory": {
                        "status": "skipped",
                        "reason": "session_in_use_by_other_clients",
                        "rounds": 0,
                        "processed_round": 0,
                    },
                    "summary": {
                        "status": "skipped",
                        "reason": "session_in_use_by_other_clients",
                        "rounds": max(0, int(record.get("rounds") or 0)),
                    },
                    "session": self._index_session_payload(record),
                }
            if any(
                active.user == name and active.session_id == normalized_session
                for active in self._active_runs.values()
            ):
                raise ConflictError("会话正在运行，结束当前响应后再关闭")
            memory = queue_memory_extraction(
                self.root,
                name,
                normalized_source,
                normalized_session,
            )
            record = close_index_session(
                self.root,
                name,
                normalized_source,
                normalized_session,
            )
        if record is None:
            raise NotFoundError(f"会话不存在：{normalized_session}")
        summary = queue_history_summary(
            self.root,
            name,
            normalized_source,
            normalized_session,
        )
        if summary.get("status") == "queued" and self.summary_waker is not None:
            self.summary_waker()
        record = find_index_record(
            self.root, name, normalized_source, normalized_session
        ) or record
        return {
            "user": name,
            "source": normalized_source,
            "session_id": normalized_session,
            "closed": True,
            "deferred": False,
            "active_clients": 0,
            "memory": memory,
            "summary": summary,
            "session": self._index_session_payload(record),
        }

    def retry_session_summary(
        self,
        user: Any,
        session_id: Any,
        *,
        source: Any = "web",
    ) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        normalized_session = self.require_session_id(session_id)
        existing = find_index_record(
            self.root, name, normalized_source, normalized_session
        )
        if existing is None:
            raise NotFoundError(f"会话不存在：{normalized_session}")
        record = retry_history_summary(
            self.root,
            name,
            normalized_source,
            normalized_session,
        )
        if record is None:
            raise ConflictError("当前会话没有可重新生成的历史摘要任务")
        if self.summary_waker is not None:
            self.summary_waker()
        return {
            "user": name,
            "source": normalized_source,
            "session_id": normalized_session,
            "queued": True,
            "session": self._index_session_payload(record),
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
        client_id: Any = "",
    ) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        normalized_session = self.require_session_id(session_id)
        normalized_client = self.require_client_id(client_id)
        with self._active_runs_lock:
            self._prune_session_leases_locked()
            lease_clients = self._session_leases.get(
                (name, normalized_source, normalized_session), {}
            )
            other_clients = [
                value for value in lease_clients if value != normalized_client
            ]
            if other_clients:
                raise ConflictError(
                    f"该对话正在其他 {len(other_clients)} 个页面中使用，暂时不能删除"
                )
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
            self._session_leases.pop(
                (name, normalized_source, normalized_session), None
            )
        if deleted == 0:
            raise NotFoundError(f"会话不存在：{normalized_session}")
        return {
            "user": name,
            "source": normalized_source,
            "session_id": normalized_session,
            "deleted": True,
        }

    def compress_session(
        self,
        user: Any,
        session_id: Any,
        *,
        source: Any = "web",
    ) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        normalized_session = self.require_session_id(session_id)
        directory = find_window(
            self.root,
            name,
            normalized_source,
            normalized_session,
        )
        if directory is None:
            raise NotFoundError(f"会话不存在：{normalized_session}")
        with self._active_runs_lock:
            if any(
                active.user == name and active.session_id == normalized_session
                for active in self._active_runs.values()
            ):
                raise ConflictError("会话正在运行，结束当前响应后再压缩")
        try:
            result = self.context_compressor(
                {
                    "user": name,
                    "source": normalized_source,
                    "session_id": normalized_session,
                    "memory_extraction_policy": "queue",
                },
                root=self.root,
            )
        except WebServiceError:
            raise
        except Exception as exc:
            raise WebServiceError("手动上下文压缩失败") from exc
        context = result.get("context") if isinstance(result.get("context"), dict) else {}
        rounds_removed = max(0, int(context.get("rounds_removed") or 0))
        summary_cache = str(result.get("summary_cache") or "")
        compressed = result.get("compressed") is True
        compression_verified = result.get("compression_verified") is True
        summary = context.get("summary")
        if isinstance(summary, dict) and summary.get("failed") is True:
            detail = str(summary.get("error") or "").strip()
            message = "手动上下文压缩失败"
            if detail:
                message = f"{message}：{detail}"
            raise WebServiceError(message)
        if rounds_removed and not (compressed and compression_verified):
            raise WebServiceError("手动上下文压缩失败：运行窗口落盘校验未通过")
        raw_memory = result.get("memory")
        if isinstance(raw_memory, dict):
            memory = dict(raw_memory)
        else:
            try:
                latest_window = load_window(directory)
                memory = queue_memory_extraction(
                    self.root,
                    name,
                    normalized_source,
                    normalized_session,
                    target_round=int(
                        latest_window.get("data", {}).get("rounds") or 0
                    ),
                    reason="manual_compression",
                )
            except Exception as exc:
                raise WebServiceError(
                    "上下文压缩成功，但后台记忆任务登记失败"
                ) from exc
        memory.setdefault("user", name)
        memory.setdefault("source", normalized_source)
        memory.setdefault("session_id", normalized_session)
        memory.setdefault(
            "round",
            int(memory.get("target_round") or memory.get("rounds") or 0),
        )
        memory.setdefault("candidates", 0)
        memory.setdefault("extraction", None)
        memory["retry_pending"] = memory.get("status") == "failed"
        return {
            "user": name,
            "source": normalized_source,
            "session_id": normalized_session,
            "requested": True,
            "compressed": compressed,
            "compression_verified": compression_verified,
            "rounds_removed": rounds_removed,
            "summary_cache_exists": bool(summary_cache),
            "context": dict(context),
            "memory": memory,
        }

    def extract_session_memory(
        self,
        user: Any,
        session_id: Any,
        *,
        source: Any = "web",
    ) -> dict[str, Any]:
        """Extract every unprocessed archived round through the durable cursor."""

        name = self.require_user(user)
        normalized_source = self.require_source(source)
        normalized_session = self.require_session_id(session_id)
        directory = find_window(
            self.root,
            name,
            normalized_source,
            normalized_session,
        )
        if directory is None:
            raise NotFoundError(f"会话不存在：{normalized_session}")
        with self._active_runs_lock:
            if any(
                active.user == name and active.session_id == normalized_session
                for active in self._active_runs.values()
            ):
                raise ConflictError("会话正在运行，结束当前响应后再提取记忆")

        with session_lock(self.root, name, normalized_source, normalized_session):
            window = load_window(directory)
            if max(0, int((window.get("data") or {}).get("rounds") or 0)) < 1:
                return extract_memory_backlog(
                    root=self.root,
                    user=name,
                    source=normalized_source,
                    session_id=normalized_session,
                    directory=directory,
                    window=window,
                    config={},
                    agent_runner=None,
                    cancel_event=None,
                )
            config = load_config(name, self.root)
            runner = AgentRunner(self.root, name, config=config)
            return extract_memory_backlog(
                root=self.root,
                user=name,
                source=normalized_source,
                session_id=normalized_session,
                directory=directory,
                window=window,
                config=config,
                agent_runner=runner,
                cancel_event=None,
            )

    def undo_last_round(
        self,
        user: Any,
        session_id: Any,
        expected_round: Any,
        prompt: Any,
        *,
        source: Any = "web",
    ) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        normalized_session = self.require_session_id(session_id)
        if isinstance(expected_round, bool) or not isinstance(expected_round, int):
            raise InvalidRequestError("expected_round 必须是正整数")
        if expected_round < 1:
            raise InvalidRequestError("expected_round 必须是正整数")
        if not isinstance(prompt, str) or not prompt.strip():
            raise InvalidRequestError("prompt 不能为空")
        with self._active_runs_lock:
            if any(
                active.user == name and active.session_id == normalized_session
                for active in self._active_runs.values()
            ):
                raise ConflictError("会话仍在运行，确认上一轮结束后再重新发送")
            try:
                result = undo_history_last_round(
                    self.root,
                    name,
                    normalized_source,
                    normalized_session,
                    expected_round=expected_round,
                    expected_prompt=prompt,
                )
            except HistoryError as exc:
                raise ConflictError(str(exc)) from exc
        return {
            "user": name,
            "source": normalized_source,
            "session_id": normalized_session,
            **result,
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
        limit: int | None = None,
        before: int | None = None,
    ) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        normalized_session = self.require_session_id(session_id)
        directory = find_window(self.root, name, normalized_source, normalized_session)
        if directory is None:
            reserved = find_index_record(
                self.root,
                name,
                normalized_source,
                normalized_session,
            )
            if isinstance(reserved, dict) and not reserved.get("archive_window"):
                return {
                    "user": name,
                    "source": normalized_source,
                    "session_id": normalized_session,
                    "messages": [],
                    "round_metrics": [],
                    "round_traces": [],
                    "pagination": {
                        "limit": limit,
                        "total_rounds": 0,
                        "first_round": 0,
                        "last_round": 0,
                        "has_more_before": False,
                        "next_before": None,
                    },
                }
            raise NotFoundError(f"会话不存在：{normalized_session}")
        window = load_window(directory)
        raw_messages = (window.get("text") or {}).get("messages") or []
        message_rounds: list[list[dict[str, Any]]] = []
        current_round: list[dict[str, Any]] = []
        for raw_message in raw_messages if isinstance(raw_messages, list) else []:
            if not isinstance(raw_message, dict):
                continue
            if raw_message.get("role") == "user" and current_round:
                message_rounds.append(current_round)
                current_round = []
            current_round.append(dict(raw_message))
        if current_round:
            message_rounds.append(current_round)

        total_rounds = len(message_rounds)
        end_round = total_rounds
        if before is not None:
            end_round = min(end_round, max(0, int(before) - 1))
        start_round = 1 if end_round > 0 else 0
        if limit is not None and end_round > 0:
            start_round = max(1, end_round - max(1, int(limit)) + 1)
        selected_messages = (
            [
                message
                for group in message_rounds[start_round - 1 : end_round]
                for message in group
            ]
            if start_round > 0
            else []
        )

        def in_selected_page(round_number: int) -> bool:
            return start_round > 0 and start_round <= round_number <= end_round

        def media_artifacts(value: Any) -> list[dict[str, Any]]:
            if not isinstance(value, list):
                return []
            artifacts: list[dict[str, Any]] = []
            for item in value:
                if not isinstance(item, dict):
                    continue
                path = str(item.get("path") or "")
                if not path or str(item.get("scope") or "") != "download":
                    continue
                artifacts.append(
                    {
                        key: item[key]
                        for key in (
                            "asset_id",
                            "type",
                            "name",
                            "scope",
                            "path",
                            "mime_type",
                            "size",
                            "checksum_sha256",
                            "duration_ms",
                        )
                        if key in item
                    }
                )
            return artifacts

        raw_metrics = (window.get("data") or {}).get("round_metrics") or []
        input_attachments_by_round: dict[int, list[dict[str, Any]]] = {}

        def input_attachments(value: Any) -> list[dict[str, Any]]:
            if not isinstance(value, list):
                return []
            attachments: list[dict[str, Any]] = []
            seen: set[str] = set()
            upload_root = (self.root / "users" / name / "file_upload").resolve()
            for item in value:
                if not isinstance(item, dict):
                    continue
                asset_id = str(item.get("asset_id") or "")
                attachment_name = Path(str(item.get("name") or "attachment")).name[:255]
                media_kind = str(item.get("media_kind") or "file").lower()
                if media_kind not in {"image", "audio", "video", "file"}:
                    media_kind = "file"
                scope = str(item.get("scope") or "external")
                relative_path = str(item.get("relative_path") or "").replace("\\", "/").strip("/")
                available = False
                if scope == "file_upload" and relative_path:
                    try:
                        _, target = _safe_relative_target(upload_root, relative_path)
                        _reject_link_path(upload_root, target)
                        expected_size = max(0, int(item.get("size") or 0))
                        available = (
                            not target.is_symlink()
                            and target.is_file()
                            and (not expected_size or target.stat().st_size == expected_size)
                        )
                    except (OSError, WebServiceError):
                        available = False
                else:
                    scope = "external"
                    relative_path = ""
                key = asset_id or f"{scope}\0{relative_path}\0{attachment_name}"
                if key in seen:
                    continue
                seen.add(key)
                attachments.append(
                    {
                        "asset_id": asset_id,
                        "name": attachment_name,
                        "media_kind": media_kind,
                        "mime_type": str(
                            item.get("mime_type") or "application/octet-stream"
                        ),
                        "size": max(0, int(item.get("size") or 0)),
                        "checksum_sha256": str(item.get("checksum_sha256") or ""),
                        "scope": scope,
                        "relative_path": relative_path,
                        "available": available,
                    }
                )
            return attachments

        if isinstance(raw_metrics, list):
            for metric in raw_metrics:
                if not isinstance(metric, dict):
                    continue
                round_number = int(metric.get("round") or 0)
                values = input_attachments(metric.get("input_attachments"))
                if round_number > 0 and values:
                    input_attachments_by_round[round_number] = values
        raw_items = (window.get("items") or {}).get("items") or []
        if isinstance(raw_items, list):
            for raw_item in raw_items:
                if not isinstance(raw_item, dict) or raw_item.get("role") != "user":
                    continue
                metadata = raw_item.get("metadata")
                if not isinstance(metadata, dict):
                    continue
                round_number = int(metadata.get("round") or 0)
                values = input_attachments(metadata.get("input_attachments"))
                if round_number > 0 and values:
                    input_attachments_by_round.setdefault(round_number, values)

        decorated_messages: list[dict[str, Any]] = []
        selected_round = max(0, start_round - 1)
        for raw_message in selected_messages:
            message = dict(raw_message)
            if message.get("role") == "user":
                selected_round += 1
                values = input_attachments(message.get("attachments"))
                if not values:
                    values = input_attachments_by_round.get(selected_round, [])
                if values:
                    message["attachments"] = values
                else:
                    message.pop("attachments", None)
            decorated_messages.append(message)
        selected_messages = decorated_messages

        round_metrics = []
        if isinstance(raw_metrics, list):
            for item in raw_metrics:
                if not isinstance(item, dict):
                    continue
                round_number = int(item.get("round") or 0)
                if not in_selected_page(round_number):
                    continue
                usage = item.get("usage") if isinstance(item.get("usage"), dict) else {}
                artifacts: list[dict[str, Any]] = []
                responses = item.get("provider_responses") or []
                if isinstance(responses, list):
                    for response in responses:
                        metadata = response.get("metadata") if isinstance(response, dict) else None
                        if isinstance(metadata, dict):
                            artifacts.extend(media_artifacts(metadata.get("artifacts")))
                round_metrics.append(
                    {
                        "round": round_number,
                        "usage": dict(usage),
                        "elapsed_ms": max(0, int(item.get("elapsed_ms") or 0)),
                        "tool_calls": max(0, int(item.get("tool_calls") or 0)),
                        "guidance": [
                            str(value)
                            for value in item.get("guidance", [])
                            if isinstance(value, str)
                        ] if isinstance(item.get("guidance"), list) else [],
                        "status": str(item.get("status") or "completed"),
                        "cancelled": bool(item.get("cancelled", False)),
                        "cancel_reason": str(item.get("cancel_reason") or ""),
                        "artifacts": artifacts,
                    }
                )
        reasoning_by_round: dict[int, str] = {}
        raw_reasoning = (window.get("think") or {}).get("rounds") or []
        if isinstance(raw_reasoning, list):
            for item in raw_reasoning:
                if not isinstance(item, dict):
                    continue
                round_number = int(item.get("round") or 0)
                if in_selected_page(round_number):
                    reasoning_by_round[round_number] = str(item.get("content") or "")

        tools_by_round: dict[int, list[dict[str, Any]]] = {}
        raw_tools = (window.get("tool") or {}).get("rounds") or []
        if isinstance(raw_tools, list):
            for item in raw_tools:
                if not isinstance(item, dict):
                    continue
                round_number = int(item.get("round") or 0)
                if not in_selected_page(round_number) or not isinstance(
                    item.get("calls"), list
                ):
                    continue
                calls = []
                for call in item["calls"]:
                    if not isinstance(call, dict):
                        continue
                    arguments_text, arguments_truncated = _tool_text_preview(call.get("arguments") or {})
                    result_text, result_truncated = _tool_text_preview(call.get("result"))
                    raw_result = call.get("result")
                    tool_value = (
                        raw_result.get("result")
                        if isinstance(raw_result, dict)
                        else None
                    )
                    artifacts = media_artifacts(
                        tool_value.get("artifacts")
                        if isinstance(tool_value, dict)
                        else None
                    )
                    raw_status = str(call.get("status") or "completed").casefold()
                    status = (
                        "running"
                        if raw_status in {"running", "started", "pending", "deferred"}
                        else "error"
                        if raw_status
                        in {"failed", "error", "temporarily_unavailable", "cancelled"}
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
                            "artifacts": artifacts,
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
            "messages": selected_messages,
            "round_metrics": round_metrics,
            "round_traces": round_traces,
            "pagination": {
                "limit": limit,
                "total_rounds": total_rounds,
                "first_round": start_round,
                "last_round": end_round,
                "has_more_before": start_round > 1,
                "next_before": start_round if start_round > 1 else None,
            },
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
        if stored.get("status") == "approved" and self.plan_waker is not None:
            self.plan_waker()
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
        if stored.get("status") == "approved" and self.plan_waker is not None:
            self.plan_waker()
        return {"user": name, "plan": self._plan_summary(stored), "updated": True}

    def command_plan(self, user: Any, plan_id: Any, action: Any) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_action = str(action or "").strip().casefold()
        try:
            if normalized_action == "pause":
                stored = pause_plan(self.root, name, str(plan_id))
            elif normalized_action == "cancel":
                stored = cancel_plan(self.root, name, str(plan_id))
            else:
                raise InvalidRequestError("计划状态指令只允许 pause 或 cancel")
        except PlanNotFoundError as exc:
            raise NotFoundError(str(exc)) from None
        except InvalidRequestError:
            raise
        except (PlanError, RuntimeError, ValueError) as exc:
            raise ConflictError(str(exc)) from None
        return {
            "user": name,
            "plan": self._plan_summary(stored),
            "action": normalized_action,
            "updated": True,
        }

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
        registry = discover_tools(self.root, name)
        tools = []
        for tool in sorted(registry.tools.values(), key=lambda item: item.name.casefold()):
            tools.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "version": tool.version,
                    "enabled": bool(tool.enabled and source_policy.plugins.allows(tool.name)),
                    "source": tool.source,
                    "layer": "core",
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
                    "category": (
                        "shared"
                        if descriptor.scope == "shared"
                        else "agent_generated"
                        if logical_name == "agent_create" or logical_name.startswith("agent_create/")
                        else "user_created"
                    ),
                    "path": descriptor.path.parent.relative_to(self.root).as_posix(),
                    "active_for_main_agent": allowed.allows(logical_name),
                }
            )
        items = [
            {
                "id": f"builtin:{tool['name']}",
                "name": tool["name"],
                "title": tool["name"],
                "description": tool["description"],
                "category": "builtin",
                "version": tool["version"],
                "enabled": tool["enabled"],
                "editable": False,
                "toggleable": True,
                "downloadable": True,
                "path": registry.tools[tool["name"]].directory.relative_to(self.root).as_posix(),
            }
            for tool in tools
        ]
        items.extend(
            {
                "id": f"{skill['category']}:{skill['name']}",
                "name": skill["name"],
                "title": skill["title"],
                "description": skill["description"],
                "category": skill["category"],
                "version": "",
                "enabled": skill["active_for_main_agent"],
                "editable": skill["category"] in _EDITABLE_SKILL_CATEGORIES,
                "toggleable": skill["category"] == "shared",
                "downloadable": skill["category"] == "shared",
                "path": skill["path"],
            }
            for skill in prompt_skills
        )
        category_counts = {
            category: sum(item["category"] == category for item in items)
            for category in _SKILL_CATEGORIES
        }
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
            "catalog_summary": {
                "total": len(items),
                "enabled": sum(item["enabled"] for item in items),
                **category_counts,
            },
            "items": items,
            "prompt_summary": {
                "registered": len(prompt_skills),
                "active": sum(item["active_for_main_agent"] for item in prompt_skills),
                "user": sum(item["scope"] == "user" for item in prompt_skills),
                "shared": sum(item["scope"] == "shared" for item in prompt_skills),
            },
            "prompt_skills": prompt_skills,
            "source_policy": source_policy.public_summary(),
        }

    def _skill_directory(self, user: Any, category: Any, skill_name: Any) -> tuple[str, str, str, Path]:
        name = self.require_user(user)
        normalized_category = str(category or "").strip()
        if normalized_category not in _SKILL_CATEGORIES:
            raise InvalidRequestError(f"技能分类无效：{category}")
        logical_name = str(skill_name or "").strip().replace("\\", "/")
        if normalized_category == "builtin":
            registry = discover_tools(self.root, name)
            tool = registry.tools.get(logical_name)
            if tool is None:
                raise NotFoundError(f"基础插件不存在：{logical_name}")
            target = tool.directory
            root = self.root / "plugins"
        else:
            root = (
                self.root / "shared_skills"
                if normalized_category == "shared"
                else self.root / "users" / name / "user_skills"
            )
            relative, target = _safe_relative_target(root, logical_name)
            logical_name = relative
            if normalized_category == "agent_generated" and not (
                logical_name == "agent_create" or logical_name.startswith("agent_create/")
            ):
                raise InvalidRequestError("智能体生成技能必须位于 agent_create 目录")
            if normalized_category == "user_created" and (
                logical_name == "agent_create" or logical_name.startswith("agent_create/")
            ):
                raise InvalidRequestError("智能体生成技能不能按用户自建技能管理")
        _reject_link_path(root.resolve(), target)
        if not target.is_dir() or not (target / "SKILL.md").is_file():
            raise NotFoundError(f"技能不存在：{logical_name}")
        return name, normalized_category, logical_name, target

    def skill_document(self, user: Any, category: Any, skill_name: Any) -> dict[str, Any]:
        name, normalized_category, logical_name, target = self._skill_directory(user, category, skill_name)
        skill_file = target / "SKILL.md"
        content = skill_file.read_text("utf-8")
        return {
            "user": name,
            "category": normalized_category,
            "name": logical_name,
            "path": skill_file.relative_to(self.root).as_posix(),
            "content": content,
            "size": len(content.encode("utf-8")),
            "updated_at": skill_file.stat().st_mtime,
            "editable": normalized_category in _EDITABLE_SKILL_CATEGORIES,
        }

    def put_skill_document(self, user: Any, category: Any, skill_name: Any, content: Any) -> dict[str, Any]:
        name, normalized_category, logical_name, target = self._skill_directory(user, category, skill_name)
        if normalized_category not in _EDITABLE_SKILL_CATEGORIES:
            raise InvalidRequestError("基础插件与共享技能只允许预览和下载")
        text = _validated_text(content, field="content")
        skill_file = target / "SKILL.md"
        previous = skill_file.read_bytes()
        _atomic_write(skill_file, text.encode("utf-8"))
        try:
            load_prompt_source_registry(self.root, name).select_skills()
        except Exception as exc:
            _atomic_write(skill_file, previous)
            raise InvalidRequestError(f"技能文件校验失败：{exc}") from None
        return self.skill_document(name, normalized_category, logical_name)

    def upload_user_skills(self, user: Any, filename: Any, data: Any) -> dict[str, Any]:
        name = self.require_user(user)
        archive_name = PurePosixPath(str(filename or "").strip().replace("\\", "/")).name
        if not archive_name or Path(archive_name).suffix.casefold() != ".zip":
            raise InvalidRequestError("用户技能只支持 ZIP 压缩包")
        if not isinstance(data, bytes) or not data:
            raise InvalidRequestError("技能压缩包不能为空")
        if len(data) > SKILL_ARCHIVE_MAX_BYTES:
            raise InvalidRequestError(
                f"技能压缩包不能超过 {SKILL_ARCHIVE_MAX_BYTES // (1024 * 1024)} MB"
            )
        if not zipfile.is_zipfile(io.BytesIO(data)):
            raise InvalidRequestError("上传内容不是有效的 ZIP 压缩包")

        user_skills_root = self.root / "users" / name / "user_skills"
        destination_root = user_skills_root / "user_create"
        with self._skill_upload_lock:
            user_skills_root.mkdir(parents=True, exist_ok=True)
            _reject_link_path((self.root / "users" / name).resolve(), user_skills_root)
            destination_root.mkdir(parents=True, exist_ok=True)
            _reject_link_path(user_skills_root.resolve(), destination_root)
            staging_root = destination_root / f".upload-{uuid.uuid4().hex}"
            archive_root = staging_root / "archive"
            packages_root = staging_root / "packages"
            moved_targets: list[Path] = []
            try:
                with zipfile.ZipFile(io.BytesIO(data), "r") as archive:
                    infos = archive.infolist()
                    if not infos:
                        raise InvalidRequestError("技能压缩包不能为空")

                    members: list[tuple[zipfile.ZipInfo, PurePosixPath, str]] = []
                    seen_paths: dict[str, tuple[PurePosixPath, str]] = {}
                    expanded_bytes = 0
                    file_count = 0
                    for info in infos:
                        if info.flag_bits & 0x1:
                            raise InvalidRequestError("技能压缩包不能加密")
                        pure = _validated_skill_archive_path(info.filename)
                        kind = _zip_member_kind(info)
                        key = pure.as_posix().casefold()
                        if key in seen_paths:
                            raise InvalidRequestError(
                                f"技能压缩包存在重复或大小写冲突路径：{pure.as_posix()}"
                            )
                        seen_paths[key] = (pure, kind)
                        if kind == "file":
                            file_count += 1
                            expanded_bytes += info.file_size
                            if file_count > SKILL_ARCHIVE_MAX_FILES:
                                raise InvalidRequestError(
                                    f"技能压缩包文件数不能超过 {SKILL_ARCHIVE_MAX_FILES}"
                                )
                            if expanded_bytes > SKILL_ARCHIVE_MAX_EXPANDED_BYTES:
                                raise InvalidRequestError(
                                    "技能压缩包解压后内容超过安全上限"
                                )
                            ratio = info.file_size / max(1, info.compress_size)
                            if info.file_size and ratio > SKILL_ARCHIVE_MAX_RATIO:
                                raise InvalidRequestError("技能压缩包包含异常压缩比文件")
                        members.append((info, pure, kind))

                    file_keys = {
                        pure.as_posix().casefold()
                        for _, pure, kind in members
                        if kind == "file"
                    }
                    for _, pure, _ in members:
                        parent_parts = pure.parts[:-1]
                        for index in range(1, len(parent_parts) + 1):
                            parent_key = PurePosixPath(*parent_parts[:index]).as_posix().casefold()
                            if parent_key in file_keys:
                                raise InvalidRequestError(
                                    "技能压缩包中同一路径不能同时作为文件和目录"
                                )

                    skill_files = [
                        pure
                        for _, pure, kind in members
                        if kind == "file" and pure.name.casefold() == "skill.md"
                    ]
                    if not skill_files:
                        raise InvalidRequestError("技能压缩包中没有找到 SKILL.md")
                    if len(skill_files) > SKILL_ARCHIVE_MAX_SKILLS:
                        raise InvalidRequestError(
                            f"单个压缩包最多包含 {SKILL_ARCHIVE_MAX_SKILLS} 个技能"
                        )
                    if any(
                        part.startswith(".")
                        for skill_file in skill_files
                        for part in skill_file.parent.parts
                    ):
                        raise InvalidRequestError("SKILL.md 不能位于隐藏目录中")

                    candidate_roots = sorted(
                        {skill_file.parent for skill_file in skill_files},
                        key=lambda path: (len(path.parts), path.as_posix().casefold()),
                    )
                    skill_roots: list[PurePosixPath] = []
                    for candidate in candidate_roots:
                        if any(
                            candidate == parent
                            or candidate.is_relative_to(parent)
                            for parent in skill_roots
                        ):
                            continue
                        skill_roots.append(candidate)

                    archive_stem = _skill_package_name(Path(archive_name).stem)
                    packages: list[tuple[str, PurePosixPath]] = []
                    package_names: set[str] = set()
                    for skill_root in skill_roots:
                        package_name = _skill_package_name(
                            archive_stem if str(skill_root) == "." else skill_root.name
                        )
                        key = package_name.casefold()
                        if key in package_names:
                            raise InvalidRequestError(
                                f"压缩包内多个技能会安装到同名目录：{package_name}"
                            )
                        package_names.add(key)
                        packages.append((package_name, skill_root))

                    existing_names = {
                        path.name.casefold(): path.name
                        for path in destination_root.iterdir()
                        if not path.name.startswith(".upload-")
                    }
                    for package_name, _ in packages:
                        existing = existing_names.get(package_name.casefold())
                        if existing is not None:
                            raise ConflictError(f"用户自建技能已存在：user_create/{existing}")

                    archive_root.mkdir(parents=True)
                    for info, pure, kind in members:
                        target = archive_root.joinpath(*pure.parts)
                        if kind == "directory":
                            target.mkdir(parents=True, exist_ok=True)
                            continue
                        target.parent.mkdir(parents=True, exist_ok=True)
                        written = 0
                        with archive.open(info, "r") as source, target.open("wb") as output:
                            while chunk := source.read(1024 * 1024):
                                written += len(chunk)
                                if written > info.file_size:
                                    raise InvalidRequestError("技能压缩包文件大小声明无效")
                                output.write(chunk)
                        if written != info.file_size:
                            raise InvalidRequestError("技能压缩包文件内容不完整")

                    canonical_skill_files: list[Path] = []
                    for skill_file in skill_files:
                        extracted = archive_root.joinpath(*skill_file.parts)
                        canonical = extracted.with_name("SKILL.md")
                        if extracted.name != "SKILL.md":
                            temporary_manifest = extracted.with_name(
                                f".skill-{uuid.uuid4().hex}.tmp"
                            )
                            os.replace(extracted, temporary_manifest)
                            os.replace(temporary_manifest, canonical)
                        canonical_skill_files.append(canonical)
                    try:
                        for skill_file in canonical_skill_files:
                            parse_skill_descriptor(skill_file, scope="user", root=self.root)
                    except Exception as exc:
                        raise InvalidRequestError(f"技能文件校验失败：{exc}") from None

                    packages_root.mkdir()
                    for package_name, skill_root in packages:
                        source = (
                            archive_root
                            if str(skill_root) == "."
                            else archive_root.joinpath(*skill_root.parts)
                        )
                        shutil.copytree(source, packages_root / package_name)

                for package_name, _ in packages:
                    target = destination_root / package_name
                    if target.exists():
                        raise ConflictError(f"用户自建技能已存在：user_create/{package_name}")
                    os.replace(packages_root / package_name, target)
                    moved_targets.append(target)
                try:
                    descriptors = load_prompt_source_registry(self.root, name).select_skills()
                except Exception as exc:
                    raise InvalidRequestError(f"技能注册校验失败：{exc}") from None

                descriptor_by_path = {descriptor.path.parent.resolve(): descriptor for descriptor in descriptors}
                installed = []
                for target in moved_targets:
                    descriptor = descriptor_by_path.get(target.resolve())
                    if descriptor is None:
                        raise InvalidRequestError(f"技能未被注册器发现：{target.name}")
                    installed.append(
                        {
                            "name": f"user_create/{target.name}",
                            "title": descriptor.title,
                            "path": target.relative_to(self.root).as_posix(),
                        }
                    )
                return {
                    "user": name,
                    "category": "user_created",
                    "installed": installed,
                    "count": len(installed),
                }
            except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
                if isinstance(exc, WebServiceError):
                    raise
                raise InvalidRequestError(f"技能压缩包处理失败：{exc}") from None
            finally:
                if sys.exc_info()[0] is not None:
                    for target in reversed(moved_targets):
                        if target.exists():
                            shutil.rmtree(target)
                if staging_root.exists():
                    shutil.rmtree(staging_root)

    def delete_skill(self, user: Any, category: Any, skill_name: Any) -> dict[str, Any]:
        name, normalized_category, logical_name, target = self._skill_directory(user, category, skill_name)
        if normalized_category not in _EDITABLE_SKILL_CATEGORIES:
            raise InvalidRequestError("基础插件与共享技能不允许从用户页面删除")
        _reject_tree_links(target)
        relative_path = target.relative_to(self.root).as_posix()
        shutil.rmtree(target)
        return {
            "user": name,
            "category": normalized_category,
            "name": logical_name,
            "path": relative_path,
            "deleted": True,
        }

    def set_skill_enabled(self, user: Any, category: Any, skill_name: Any, enabled: Any) -> dict[str, Any]:
        name, normalized_category, logical_name, _ = self._skill_directory(user, category, skill_name)
        if normalized_category not in {"builtin", "shared"}:
            raise InvalidRequestError("只有基础插件和共享技能支持白名单启用或禁用")
        if not isinstance(enabled, bool):
            raise InvalidRequestError("enabled 必须是布尔值")
        inventory = self.skills(name)
        candidates = {
            item["name"]
            for item in inventory["items"]
            if item["category"] == normalized_category
        }
        selected = {
            item["name"]
            for item in inventory["items"]
            if item["category"] == normalized_category and item["enabled"]
        }
        if enabled:
            selected.add(logical_name)
        else:
            selected.discard(logical_name)
        whitelist = [] if selected == candidates else sorted(selected) or ["__kemo_none__"]
        changes = (
            {"plugins": {"whitelist": whitelist}}
            if normalized_category == "builtin"
            else {"skills": {"shared_whitelist": whitelist}}
        )
        self.patch_user_config(name, changes)
        return {
            "user": name,
            "category": normalized_category,
            "name": logical_name,
            "enabled": enabled,
            "whitelist": whitelist,
        }

    def skill_archive(self, user: Any, category: Any, skill_name: Any) -> tuple[str, bytes]:
        _, normalized_category, logical_name, target = self._skill_directory(user, category, skill_name)
        if normalized_category not in {"builtin", "shared"}:
            raise InvalidRequestError("用户技能请通过编辑器管理，不提供系统技能下载入口")
        _reject_tree_links(target)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(target.rglob("*")):
                if not path.is_file() or "__pycache__" in path.parts or path.name.startswith("."):
                    continue
                archive.write(path, (Path(target.name) / path.relative_to(target)).as_posix())
        filename = f"{Path(logical_name).name}.zip"
        return filename, buffer.getvalue()

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
        sources: list[dict[str, Any]] = []
        injection_cursor = 0
        has_injection_piece = False
        for item in inventory:
            collected_markdown = self._sense_markdown(item)
            injected_markdown = ""
            if item["active"] and collected_markdown:
                piece = f"[{item['name']}]\n{collected_markdown}"
                piece_start = injection_cursor + (2 if has_injection_piece else 0)
                piece_end = piece_start + len(piece)
                if piece_start < len(selection.text):
                    injected_markdown = selection.text[piece_start:min(piece_end, len(selection.text))]
                injection_cursor = piece_end
                has_injection_piece = True
            sources.append({
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
                "whitelisted": item["selected"],
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
                "collected_markdown": collected_markdown,
                "injected_markdown": injected_markdown,
                "injected_tokens": estimate_text_tokens(injected_markdown),
                # Sense modules currently have no required refresh interval
                # in sense.json. Keep this explicit so clients can render a
                # truthful fallback instead of inventing one.
                "update_interval": "",
                "updated_at": item["updated_at"],
            })
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
                "content": selection.text,
                "source_files": list(selection.source_files),
                "prompt_section": "perception",
                "prompt_position": "System Prompt / Global Sense",
            },
            "decisions": [],
            "source_policy": source_policy.public_summary(),
        }

    def _sense_module_directory(self, user: Any, module_name: Any) -> tuple[str, str, Path]:
        name = self.require_user(user)
        if not isinstance(module_name, str) or not module_name.strip():
            raise InvalidRequestError("module_name 必须是非空字符串")
        logical_name = module_name.strip()
        pure = PurePosixPath(logical_name.replace("\\", "/"))
        if (
            len(pure.parts) != 1
            or pure.name in {".", "..", "__pycache__"}
            or pure.name.startswith(".")
            or "\x00" in logical_name
            or ":" in logical_name
        ):
            raise InvalidRequestError("感知模块名称必须是 global_sense 下的直接目录名")
        base = (self.root / "global_sense").resolve()
        target = base / logical_name
        if not target.is_dir():
            raise NotFoundError(f"感知模块不存在：{logical_name}")
        if target.is_symlink() or getattr(target, "is_junction", lambda: False)():
            raise InvalidRequestError("感知模块目录不能是符号链接或目录联接")
        try:
            target.resolve().relative_to(base)
        except ValueError:
            raise InvalidRequestError("感知模块路径越出 global_sense") from None
        return name, logical_name, target

    def refresh_sense_module(self, user: Any, module_name: Any) -> dict[str, Any]:
        name, logical_name, target = self._sense_module_directory(user, module_name)
        source = next(
            (item for item in self.sense(name)["sources"] if item["id"] == logical_name),
            None,
        )
        if not source or not source["valid"]:
            raise InvalidRequestError(
                f"感知模块配置无效，无法更新：{source['error'] if source else logical_name}"
            )
        updater = target / str(source["start_update"])
        if not updater.is_file():
            raise NotFoundError(f"感知模块更新入口不存在：{source['start_update']}")
        if updater.is_symlink() or getattr(updater, "is_junction", lambda: False)():
            raise InvalidRequestError("感知模块更新入口不能是符号链接或目录联接")
        try:
            completed = subprocess.run(
                [sys.executable, updater.name],
                cwd=str(target),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
                **hidden_subprocess_kwargs(),
            )
        except subprocess.TimeoutExpired as exc:
            raise WebServiceError(f"感知模块更新超时：{logical_name}") from exc
        except OSError as exc:
            raise WebServiceError(f"感知模块更新入口执行失败：{logical_name}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "未知错误").strip()[:1000]
            raise WebServiceError(f"感知模块更新失败：{logical_name}（{detail}）")
        refreshed = self.sense(name)
        refreshed_source = next(
            (item for item in refreshed["sources"] if item["id"] == logical_name),
            None,
        )
        return {
            "user": name,
            "module": logical_name,
            "updated": True,
            "source": refreshed_source,
            "injection": refreshed["injection"],
        }

    def set_sense_module_enabled(self, user: Any, module_name: Any, enabled: Any) -> dict[str, Any]:
        name, logical_name, _ = self._sense_module_directory(user, module_name)
        if not isinstance(enabled, bool):
            raise InvalidRequestError("enabled 必须是布尔值")
        sense = self.sense(name)
        candidates = {item["id"] for item in sense["sources"]}
        selected = {item["id"] for item in sense["sources"] if item["whitelisted"]}
        if enabled:
            selected.add(logical_name)
        else:
            selected.discard(logical_name)
        whitelist = [] if selected == candidates else sorted(selected) or ["__kemo_none__"]
        self.patch_user_config(name, {"perception": {"global_whitelist": whitelist}})
        return {
            "user": name,
            "module": logical_name,
            "enabled": enabled,
            "whitelist": whitelist,
        }

    def delete_sense_module(self, user: Any, module_name: Any) -> dict[str, Any]:
        name, logical_name, target = self._sense_module_directory(user, module_name)
        _reject_tree_links(target)
        relative_path = target.relative_to(self.root).as_posix()
        tombstone = target.parent / f".{logical_name}.{uuid.uuid4().hex}.deleting"
        try:
            os.replace(target, tombstone)
            shutil.rmtree(tombstone)
        except OSError as exc:
            if tombstone.exists() and not target.exists():
                try:
                    os.replace(tombstone, target)
                except OSError:
                    pass
            raise WebServiceError(f"感知模块删除失败：{logical_name}") from exc
        return {
            "user": name,
            "module": logical_name,
            "path": relative_path,
            "deleted": True,
        }

    def _sense_markdown(self, item: dict[str, Any]) -> str:
        if not item.get("valid") or not item.get("data_md"):
            return ""
        path = self.root / str(item.get("root") or "") / str(item.get("name") or "") / str(item["data_md"])
        try:
            return path.read_text("utf-8-sig").strip()
        except (OSError, UnicodeError):
            return ""

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
        provider_runtime = config.get("provider_runtime") or {}
        web_config = config.get("web") or {}
        message_config = config.get("message") or {}
        agent_runtime = config.get("agent_runtime") or {}
        runtime_host = config.get("runtime_host") or {}
        agents = config.get("agents") or {}
        return {
            "user": name,
            "schema_version": int(config.get("schema_version") or 1),
            "provider": {
                "type": str(provider.get("type") or ""),
                "base_url": str(provider.get("base_url") or ""),
                "model": str(provider.get("model") or ""),
                "reasoning_effort": normalize_reasoning_effort(
                    provider.get("reasoning_effort")
                ),
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
                "tool_iterations": int(tools.get("max_iterations") or 80),
                "tool_timeout": float(tools.get("timeout") or 60),
                "memory_items": sum(
                    int(temporary_memory_limits.get(tier, default))
                    for tier, default in (
                        ("half_year", 300),
                        ("one_month", 200),
                        ("seven_days", 100),
                    )
                ),
                "memory_chars": int(memory.get("important_memory_max_chars", 2000)),
                "provider_max_concurrent": int(
                    provider_runtime.get("max_concurrent_requests", 10)
                ),
                "web_max_chats": int(web_config.get("max_concurrent_chats", 3)),
                "message_max_queued": int(
                    message_config.get("max_queued_messages", 20)
                ),
                "agent_queue_maxsize": int(agent_runtime.get("queue_maxsize", 50)),
            },
            "users": [item["name"] for item in self.users()],
            "source_policy": source_policy.public_summary(),
            "provenance": self._config_provenance(name),
        }

    def version_info(self) -> dict[str, Any]:
        """Return a presentation-safe, read-only view of version.json."""

        path = self.root / "version.json"
        try:
            raw = json.loads(path.read_text("utf-8-sig"))
        except FileNotFoundError:
            raw = {}
        except (OSError, UnicodeError, json.JSONDecodeError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        raw_components = raw.get("components")
        if not isinstance(raw_components, dict):
            raw_components = {}
        preferred = ("core", "agents", "plugins", "web")
        component_ids = [
            *[key for key in preferred if key in raw_components],
            *sorted(key for key in raw_components if key not in preferred),
        ]
        components: list[dict[str, str]] = []
        for component_id in component_ids:
            value = raw_components.get(component_id)
            if not isinstance(value, dict):
                continue
            components.append(
                {
                    "id": str(component_id),
                    "version": str(value.get("version") or "").strip(),
                    "description": str(value.get("description") or "").strip(),
                }
            )
        schema_version = raw.get("schema_version")
        if isinstance(schema_version, bool) or not isinstance(schema_version, int):
            schema_version = 0
        return {
            "name": str(raw.get("name") or "kemo-agent").strip() or "kemo-agent",
            "version": str(raw.get("version") or "").strip(),
            "schema_version": schema_version,
            "components": components,
            "read_only": True,
        }

    def version_check(self, *, refresh: bool = False) -> dict[str, Any]:
        """Compare local and published manifests without changing the installation."""

        now = time.monotonic()
        with self._version_check_lock:
            if not refresh and self._version_check_cache is not None:
                cached_at, cached = self._version_check_cache
                if now - cached_at < VERSION_CHECK_CACHE_SECONDS:
                    return cached

            checked_at = datetime.now(_BEIJING).isoformat(timespec="seconds")
            local = self.version_info()
            try:
                local_version = str(local.get("version") or "").strip()
                parse_version(local_version)
                local_components = {
                    str(item.get("id") or ""): item
                    for item in local.get("components") or []
                    if isinstance(item, dict)
                }
                for component_id in _VERSION_COMPONENT_IDS:
                    item = local_components.get(component_id)
                    if not isinstance(item, dict):
                        raise UpdateError(f"version.json 缺少 components.{component_id}")
                    parse_version(str(item.get("version") or "").strip())

                remote = self.version_manifest_fetcher(
                    VERSION_MANIFEST_URL,
                    VERSION_CHECK_TIMEOUT_SECONDS,
                )
                remote_version = str(remote.get("version") or "").strip()
                parse_version(remote_version)
                remote_components = remote.get("components")
                if not isinstance(remote_components, dict):
                    raise UpdateError("version.json 缺少 components")

                components: list[dict[str, str]] = []
                has_component_update = False
                has_component_ahead = False
                for component_id in _VERSION_COMPONENT_IDS:
                    remote_item = remote_components.get(component_id)
                    if not isinstance(remote_item, dict):
                        raise UpdateError(f"version.json 缺少 components.{component_id}")
                    local_item = local_components[component_id]
                    local_component_version = str(local_item.get("version") or "").strip()
                    remote_component_version = str(remote_item.get("version") or "").strip()
                    parse_version(remote_component_version)
                    comparison = compare_versions(
                        local_component_version,
                        remote_component_version,
                    )
                    component_status = (
                        "update_available" if comparison < 0
                        else "local_newer" if comparison > 0
                        else "up_to_date"
                    )
                    has_component_update = has_component_update or comparison < 0
                    has_component_ahead = has_component_ahead or comparison > 0
                    components.append(
                        {
                            "id": component_id,
                            "description": str(local_item.get("description") or component_id),
                            "local_version": local_component_version,
                            "remote_version": remote_component_version,
                            "status": component_status,
                        }
                    )

                overall_comparison = compare_versions(local_version, remote_version)
                if overall_comparison < 0 or has_component_update:
                    status = "update_available"
                elif overall_comparison > 0 or has_component_ahead:
                    status = "local_newer"
                else:
                    status = "up_to_date"

                python_command = "python" if os.name == "nt" else "python3"
                module_commands = {
                    component_id: f"{python_command} update.py --module {component_id}"
                    for component_id in _VERSION_COMPONENT_IDS
                }
                result: dict[str, Any] = {
                    "status": status,
                    "checked_at": checked_at,
                    "local_version": local_version,
                    "remote_version": remote_version,
                    "components": components,
                    "commands": {
                        "check": f"{python_command} update.py --check",
                        "all": f"{python_command} update.py --module all",
                        "recommended": f"{python_command} update.py --module all",
                        "modules": module_commands,
                    },
                    "source": VERSION_MANIFEST_URL,
                    "read_only": True,
                }
            except _VersionCheckFailure as exc:
                result = {
                    "status": "check_failed",
                    "checked_at": checked_at,
                    "error": {"code": exc.code, "message": str(exc)},
                    "read_only": True,
                }
            except UpdateError as exc:
                result = {
                    "status": "check_failed",
                    "checked_at": checked_at,
                    "error": {
                        "code": "invalid_version_manifest",
                        "message": f"版本文件格式不完整：{exc}",
                    },
                    "read_only": True,
                }

            self._version_check_cache = (now, result)
            return result

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
                    "memory_ref": f"{item.get('tier')}:{item.get('filename')}",
                    "filename": str(item.get("filename") or ""),
                    "tier": str(item.get("tier") or ""),
                    "weight": int(item.get("weight") or 0),
                    "created_at": str(item.get("created_at") or ""),
                    "content_updated_at": str(
                        item.get("content_updated_at") or item.get("updated_at") or ""
                    ),
                    "updated_at": str(item.get("updated_at") or ""),
                    "last_used_at": item.get("last_used_at"),
                    "tier_entered_at": item.get("tier_entered_at"),
                    "expires_at": item.get("expires_at"),
                    "timezone": "UTC",
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

    def memory_item(self, user: Any, tier: Any, filename: Any) -> dict[str, Any]:
        name = self.require_user(user)
        try:
            normalized = normalize_memory_filename(filename)
            target_tier = str(tier or "")
            if target_tier not in TIERS:
                raise InvalidRequestError(f"tier 只允许 {', '.join(TIERS)}")
            store = MemoryStore(self.root, name, load_config(name, self.root))
            location = store.locate_in_tier(target_tier, normalized)
            item = (
                store._entry(
                    location,
                    store.load_index(target_tier).get(location.filename)
                    if target_tier != "permanent"
                    else None,
                )
                if location is not None
                else None
            )
        except RuntimeMemoryError as exc:
            raise InvalidRequestError(str(exc)) from None
        if item is None:
            raise NotFoundError(f"记忆不存在：{target_tier}/{normalized}")
        return {
            "user": name,
            "memory_ref": f"{item['tier']}:{item['filename']}",
            **item,
        }

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
            scoped_existing = (
                store.locate_in_tier(target_tier, normalized)
                if target_tier is not None
                else None
            )
            if scoped_existing is not None:
                if not scoped_existing.path.is_file():
                    raise RuntimeMemoryError(
                        f"记忆索引指向不存在的文件：{scoped_existing.path}"
                    )
                previous = scoped_existing.path.read_bytes()
                _atomic_write(scoped_existing.path, text.encode("utf-8"))
                try:
                    if scoped_existing.indexed:
                        store._touch_temporary(
                            scoped_existing,
                            utc_now(),
                            content_changed=True,
                        )
                except Exception:
                    _atomic_write(scoped_existing.path, previous)
                    raise
                existing = scoped_existing
            else:
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
                    existing = store.locate_in_tier(target_tier, normalized)
            if existing is None:
                raise RuntimeMemoryError(f"记忆写入后无法定位：{normalized}")
            item = store._entry(
                existing,
                store.load_index(existing.tier).get(existing.filename)
                if existing.indexed
                else None,
            )
        except RuntimeMemoryError as exc:
            raise InvalidRequestError(str(exc)) from None
        return {
            "user": name,
            "memory_ref": f"{item['tier']}:{item['filename']}",
            **item,
            "updated": True,
        }

    def delete_memory(self, user: Any, tier: Any, filename: Any) -> dict[str, Any]:
        name = self.require_user(user)
        try:
            normalized = normalize_memory_filename(filename)
            target_tier = str(tier or "")
            if target_tier not in TIERS:
                raise InvalidRequestError(f"tier 只允许 {', '.join(TIERS)}")
            store = MemoryStore(self.root, name, load_config(name, self.root))
            location = store.locate_in_tier(target_tier, normalized)
            if location is None:
                raise NotFoundError(f"记忆不存在：{target_tier}/{normalized}")
            file_existed = location.path.is_file()
            store._delete_location(location)
        except RuntimeMemoryError as exc:
            raise InvalidRequestError(str(exc)) from None
        return {
            "user": name,
            "tier": target_tier,
            "memory_ref": f"{target_tier}:{location.filename}",
            "filename": location.filename,
            "deleted": True,
            "index_removed": location.indexed,
            "file_removed": file_existed,
            "repaired_orphan": location.indexed and not file_existed,
        }

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
        if not text.strip():
            raise InvalidRequestError("临时重要记忆内容不能为空")
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
        config = load_config(name, self.root)

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
                self.root, name, "web", normalized_session
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
        current_context = self._current_context_status(
            name,
            normalized_session,
            config=config,
            token_limit=token_limit,
            round_limit=round_limit,
            configured_ratio=compression_ratio,
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

        sense_data = self.sense(name)
        expand_data = self.expands(name)
        message_data = self.message_status(name)
        knowledge_documents = knowledge_data.get("documents") or []
        enabled_knowledge = sum(
            bool(item.get("active_for_main_agent"))
            for item in knowledge_documents
            if isinstance(item, dict)
        )
        knowledge_graph_status = str(
            ((knowledge_data.get("extensions") or {}).get("kemo_graph") or "disabled")
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
                    "graph_enabled": knowledge_graph_status not in {"", "disabled", "unavailable"},
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
        uploaded_files: Any = None,
        task_plan_id: str = "",
        task_plan_mode: str = "",
        client_id: Any = "",
    ) -> Iterator[RunEvent]:
        name = self.require_user(user)
        normalized_session = self.require_session_id(session_id)
        normalized_client = self.require_client_id(client_id)
        if not isinstance(prompt, str):
            raise InvalidRequestError("prompt 必须是字符串")
        normalized_prompt = prompt.strip()
        normalized_content = self.require_content(content)
        normalized_uploaded_files = self.require_uploaded_files(name, uploaded_files)
        if not normalized_prompt and not normalized_content and not normalized_uploaded_files:
            raise InvalidRequestError("prompt、content 和 uploaded_files 不能同时为空")
        normalized_run_id = (
            self.require_run_id(run_id) if run_id else f"run_{uuid.uuid4().hex}"
        )
        gate = self._get_chat_gate(name)
        if not gate.acquire(cancel_event=cancel_event):
            if cancel_event.is_set():
                raise ConflictError("聊天请求已取消")
            raise TooManyChatsError(
                "当前用户并发聊天或等待队列已满，请稍后重试",
                retry_after=gate.pending_timeout,
            )
        active = ActiveRun(
            normalized_run_id,
            name,
            normalized_session,
            cancel_event=cancel_event,
        )
        with self._active_runs_lock:
            if normalized_run_id in self._active_runs:
                gate.release()
                raise ConflictError(f"run_id 已在使用：{normalized_run_id}")
            self._active_runs[normalized_run_id] = active
            self._touch_session_lease_locked(
                name, "web", normalized_session, normalized_client
            )
        request = {
            "user": name,
            "source": "web",
            "session_id": normalized_session,
            "prompt": normalized_prompt,
            "content": normalized_content,
            "uploaded_files": normalized_uploaded_files,
            "stream": True,
            "run_id": normalized_run_id,
            "_guidance_queue": active.guidance,
            "_history_active_key": self._interactive_active_key(
                name, normalized_client
            ),
        }
        if task_plan_id:
            request["_task_plan_id"] = task_plan_id
            request["_task_plan_mode"] = task_plan_mode or "agent_managed"
        if self._router_ref is not None:
            transport_registry = getattr(self._router_ref, "transports", None)
            if transport_registry is not None:
                request["_transport_registry"] = transport_registry
                # Run 生成器拥有线程仿射 RLock。  它的 next()/close()
                # 因此，调用必须保留在一个专用工作线程上
                # 在 asyncio.to_thread 工作线程之间跳转。
        output: queue.Queue[RunEvent | BaseException | object] = queue.Queue(maxsize=32)

        def put(value: RunEvent | BaseException | object) -> bool:
            terminal_value = (
                value is _WORKER_DONE
                or isinstance(value, BaseException)
                or (isinstance(value, RunEvent) and value.type in {"done", "error"})
            )
            while True:
                if cancel_event.is_set() and not terminal_value:
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
                    if event.type in {"done", "error"}:
                        active.guidance.close()
                    if not put(event):
                        break
            except BaseException as exc:
                put(exc)
            finally:
                active.guidance.close()
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
        try:
            worker.start()
        except BaseException:
            with self._active_runs_lock:
                self._active_runs.pop(normalized_run_id, None)
            gate.release()
            raise

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
                gate.release()

        return events()

    def stream_plan(
        self,
        user: Any,
        session_id: Any,
        plan_id: Any,
        *,
        cancel_event: threading.Event,
        run_id: Any = "",
        client_id: Any = "",
    ) -> Iterator[RunEvent]:
        name = self.require_user(user)
        normalized_session = self.require_session_id(session_id)
        normalized_plan_id = str(plan_id or "").strip()
        store = PlanStore(self.root, name)

        def claim(current: dict[str, Any]) -> dict[str, Any]:
            status = str(current.get("status") or "")
            if status not in {"pending", "approved", "paused"}:
                raise ConflictError(
                    f"计划 {normalized_plan_id} 当前状态为 {status!r}，无法开始执行"
                )
            return {**current, "status": "running"}

        try:
            plan = store.update(normalized_plan_id, claim)
        except PlanNotFoundError as exc:
            raise NotFoundError(str(exc)) from None
        except ConflictError:
            raise
        except (PlanError, RuntimeError, ValueError) as exc:
            raise ConflictError(str(exc)) from None

        steps = [step for step in (plan.get("steps") or []) if isinstance(step, dict)]
        by_id = {str(step.get("step_id") or ""): step for step in steps}
        next_step = next(
            (
                step
                for step in steps
                if step.get("status") == "pending"
                and all(
                    by_id.get(str(dependency), {}).get("status") == "completed"
                    for dependency in (step.get("depends_on") or [])
                )
            ),
            None,
        )
        next_description = (
            f"{next_step.get('step_id')} - {next_step.get('title', '')}："
            f"{next_step.get('description', '')}"
            if next_step is not None
            else "请读取计划并确认剩余可执行步骤"
        )
        control_prompt = (
            "【任务计划连续执行】\n"
            f"计划 ID：{normalized_plan_id}\n"
            f"起始步骤：{next_description}\n\n"
            "这是用户批准后的单轮连续执行，不是新的用户提问。完整活跃计划已注入系统提示词。\n"
            "请严格按依赖顺序逐步执行：每次只执行一个步骤；成功后立即调用 "
            "task_plan(action=\"step_done\") 并写入结果摘要，失败时调用 step_fail。\n"
            "step_done 返回 completed_step、progress、next_step 和 plan_status。"
            "只要 plan_status=running 且 next_step 非空，就直接继续下一步，不要等待用户再次发送消息。\n"
            "当返回 completed、paused、failed 或 cancelled，或 next_step 为空时停止执行并给出最终总结。"
            "不得创建或编辑新的任务计划。"
        )
        try:
            source_events = self.stream_chat(
                name,
                normalized_session,
                control_prompt,
                cancel_event=cancel_event,
                run_id=run_id,
                task_plan_id=normalized_plan_id,
                task_plan_mode="agent_managed",
                client_id=client_id,
            )
        except BaseException:
            current = store.read(normalized_plan_id)
            if current.get("status") == "running":
                store.update(normalized_plan_id, lambda value: {**value, "status": "paused"})
            raise

        def events() -> Iterator[RunEvent]:
            try:
                yield from source_events
            finally:
                close = getattr(source_events, "close", None)
                if callable(close):
                    try:
                        close()
                    except BaseException:
                        # 关闭底层生成器失败不能跳过计划状态收口。
                        pass
                try:
                    current = store.read(normalized_plan_id)
                    if current.get("status") == "running":
                        store.update(
                            normalized_plan_id,
                            lambda value: {**value, "status": "paused"},
                        )
                except PlanError:
                    pass

        return events()
