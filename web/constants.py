"""Web 后端跨路由与领域服务共享的稳定常量。"""

from __future__ import annotations

import re

from pydantic import TypeAdapter
from zoneinfo import ZoneInfo

from provider.protocol.models import ContentBlock


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



