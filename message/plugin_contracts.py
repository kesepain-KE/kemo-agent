"""Configuration, buffer parsing, and persisted-state contracts for message plugins."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import re
from types import ModuleType
from typing import Any

import yaml

from message.transport import TransportPolicy
from run.config import user_dir

_CONFIG_FIELDS = frozenset(
    {
        "schema_version",
        "machine_id",
        "platform",
        "display_name",
        "bound_user",
        "modules",
        "capabilities",
        "allowed_tools",
        "message_buffer",
        "files_dir",
    }
)
_MODULE_FIELDS = frozenset({"input", "output", "detect"})
_CAPABILITIES = frozenset(
    {"receive_text", "send_text", "receive_file", "send_file"}
)
_HEALTH_VALUES = frozenset({"unknown", "healthy", "degraded", "dead"})
_REQUIRED_MESSAGE_FIELDS = (
    "machine_id",
    "message_id",
    "chat_type",
    "external_user_id",
    "external_chat_id",
    "timestamp",
)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_MARKER_RE = re.compile(r"(?m)^---[\t ]*(?:\r?\n|$)")
_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
_MAX_TEXT_ATTACHMENT_CHARS = 100_000


class MessagePluginError(RuntimeError):
    """A folder plugin or its file queue violates the plugin contract."""


@dataclass(frozen=True, slots=True)
class MessagePluginIssue:
    name: str
    path: Path
    error: str


@dataclass(frozen=True, slots=True)
class MessagePluginConfig:
    root: Path
    directory: Path
    machine_id: str
    platform: str
    display_name: str
    bound_user: str
    modules: dict[str, str]
    capabilities: frozenset[str]
    allowed_tools: frozenset[str] | None
    message_buffer: str
    files_dir: str
    raw: dict[str, Any]

    @classmethod
    def load(cls, root: Path, directory: Path) -> "MessagePluginConfig":
        config_path = directory / "message.json"
        try:
            value = json.loads(config_path.read_text("utf-8"))
        except FileNotFoundError:
            raise MessagePluginError(f"缺少静态配置：{config_path}") from None
        except (OSError, json.JSONDecodeError) as exc:
            raise MessagePluginError(f"静态配置不可读：{config_path}（{exc}）") from exc
        if not isinstance(value, dict):
            raise MessagePluginError("message.json 根节点必须是对象")
        unknown = sorted(set(value) - _CONFIG_FIELDS)
        if unknown:
            raise MessagePluginError(
                "message.json 包含未知字段：" + ", ".join(unknown)
            )
        if value.get("schema_version") != 1:
            raise MessagePluginError("message.json schema_version 必须为 1")

        machine_id = _required_id(value.get("machine_id"), "machine_id")
        platform = _required_id(value.get("platform"), "platform").lower()
        display_name = _required_text(value.get("display_name"), "display_name")
        bound_user = _required_text(value.get("bound_user"), "bound_user")
        user_dir(bound_user, root)

        modules = value.get("modules")
        if not isinstance(modules, dict) or set(modules) != _MODULE_FIELDS:
            raise MessagePluginError(
                "modules 必须且只能包含 input、output、detect"
            )
        normalized_modules = {
            name: _relative_path(modules.get(name), f"modules.{name}")
            for name in sorted(_MODULE_FIELDS)
        }

        capabilities = value.get("capabilities")
        if not isinstance(capabilities, list) or not capabilities or not all(
            isinstance(item, str) and item.strip() for item in capabilities
        ):
            raise MessagePluginError("capabilities 必须是非空字符串数组")
        normalized_capabilities = frozenset(item.strip() for item in capabilities)
        unsupported = normalized_capabilities - _CAPABILITIES
        if unsupported:
            raise MessagePluginError(
                "capabilities 包含未知能力：" + ", ".join(sorted(unsupported))
            )
        required_capabilities = {"receive_text", "send_text"}
        if not required_capabilities.issubset(normalized_capabilities):
            raise MessagePluginError("capabilities 必须包含 receive_text 和 send_text")

        raw_allowed = value.get("allowed_tools")
        if raw_allowed is None:
            allowed_tools = None
        elif isinstance(raw_allowed, list) and all(
            isinstance(item, str) and item.strip() for item in raw_allowed
        ):
            allowed_tools = frozenset(item.strip() for item in raw_allowed)
        else:
            raise MessagePluginError("allowed_tools 必须是非空字符串数组或 null")

        message_buffer = _relative_path(
            value.get("message_buffer"), "message_buffer"
        )
        files_dir = _relative_path(value.get("files_dir"), "files_dir")
        resolved_directory = directory.resolve()
        for relative in (*normalized_modules.values(), message_buffer, files_dir):
            _resolve_within(resolved_directory, relative)
        for name, relative in normalized_modules.items():
            module_path = _resolve_within(resolved_directory, relative)
            if module_path.suffix.casefold() != ".py" or not module_path.is_file():
                raise MessagePluginError(f"modules.{name} 文件不存在或不是 Python：{relative}")

        return cls(
            root=root.resolve(),
            directory=resolved_directory,
            machine_id=machine_id,
            platform=platform,
            display_name=display_name,
            bound_user=bound_user,
            modules=normalized_modules,
            capabilities=normalized_capabilities,
            allowed_tools=allowed_tools,
            message_buffer=message_buffer,
            files_dir=files_dir,
            raw=dict(value),
        )

    @property
    def buffer_path(self) -> Path:
        return _resolve_within(self.directory, self.message_buffer)

    @property
    def files_path(self) -> Path:
        return _resolve_within(self.directory, self.files_dir)

    def module_path(self, name: str) -> Path:
        return _resolve_within(self.directory, self.modules[name])

    def policy(self) -> TransportPolicy:
        return TransportPolicy(
            allowed_tools=self.allowed_tools,
            capabilities=self.capabilities,
            bound_user=self.bound_user,
        )


@dataclass(frozen=True, slots=True)
class BufferedAttachment:
    path: str
    name: str
    mime: str
    size: int


@dataclass(frozen=True, slots=True)
class BufferedMessage:
    machine_id: str
    message_id: str
    chat_type: str
    external_user_id: str
    external_chat_id: str
    timestamp: str
    text: str
    attachments: tuple[BufferedAttachment, ...] = ()


@dataclass(slots=True)
class _PendingEnvelope:
    messages: tuple[BufferedMessage, ...]
    claim_path: Path


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MessagePluginError(f"{field} 必须是非空字符串")
    return value.strip()


def _required_id(value: Any, field: str) -> str:
    text = _required_text(value, field)
    if not _ID_RE.fullmatch(text):
        raise MessagePluginError(f"{field} 格式无效：{text!r}")
    return text


def _relative_path(value: Any, field: str) -> str:
    text = _required_text(value, field).replace("\\", "/")
    candidate = Path(text)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise MessagePluginError(f"{field} 必须是插件目录内的相对路径")
    return candidate.as_posix().rstrip("/")


def _resolve_within(directory: Path, relative: str) -> Path:
    target = (directory / relative).resolve()
    try:
        target.relative_to(directory.resolve())
    except ValueError:
        raise MessagePluginError(f"路径越出插件目录：{relative}") from None
    return target


def _yaml_object(text: str) -> dict[str, Any] | None:
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return value if isinstance(value, dict) else None


def _looks_like_message_header(text: str) -> bool:
    value = _yaml_object(text)
    return value is not None and all(field in value for field in _REQUIRED_MESSAGE_FIELDS)


def _metadata_message(value: dict[str, Any]) -> BufferedMessage:
    missing = [field for field in _REQUIRED_MESSAGE_FIELDS if field not in value]
    if missing:
        raise MessagePluginError("消息 front matter 缺少字段：" + ", ".join(missing))
    attachments_value = value.get("attachments") or []
    if not isinstance(attachments_value, list):
        raise MessagePluginError("attachments 必须是数组")
    attachments: list[BufferedAttachment] = []
    for index, item in enumerate(attachments_value):
        if not isinstance(item, dict):
            raise MessagePluginError(f"attachments[{index}] 必须是对象")
        size = item.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise MessagePluginError(f"attachments[{index}].size 必须是非负整数")
        attachments.append(
            BufferedAttachment(
                path=_relative_path(item.get("path"), f"attachments[{index}].path"),
                name=_required_text(item.get("name"), f"attachments[{index}].name"),
                mime=_required_text(item.get("mime"), f"attachments[{index}].mime").lower(),
                size=size,
            )
        )
    timestamp_value = value.get("timestamp")
    timestamp = (
        timestamp_value.isoformat()
        if isinstance(timestamp_value, datetime)
        else _required_text(timestamp_value, "timestamp")
    )
    return BufferedMessage(
        machine_id=_required_id(value.get("machine_id"), "machine_id"),
        message_id=_required_id(value.get("message_id"), "message_id"),
        chat_type=_required_text(value.get("chat_type"), "chat_type"),
        external_user_id=_required_text(
            value.get("external_user_id"), "external_user_id"
        ),
        external_chat_id=_required_text(
            value.get("external_chat_id"), "external_chat_id"
        ),
        timestamp=timestamp,
        text="",
        attachments=tuple(attachments),
    )


def parse_message_buffer(text: str) -> tuple[BufferedMessage, ...]:
    """Parse repeated YAML-front-matter messages without treating body rules as headers."""
    if not isinstance(text, str) or not text.strip():
        return ()
    markers = list(_MARKER_RE.finditer(text))
    if len(markers) < 2 or text[: markers[0].start()].strip():
        raise MessagePluginError("message.md 必须以 YAML front matter 的 --- 开始")

    messages: list[BufferedMessage] = []
    opening_index = 0
    while opening_index < len(markers):
        if opening_index + 1 >= len(markers):
            raise MessagePluginError("消息 front matter 缺少结束分隔符 ---")
        opening = markers[opening_index]
        closing = markers[opening_index + 1]
        metadata_text = text[opening.end() : closing.start()]
        metadata = _yaml_object(metadata_text)
        if metadata is None:
            raise MessagePluginError("消息 front matter 不是有效 YAML 对象")
        message = _metadata_message(metadata)

        next_opening_index: int | None = None
        for candidate in range(opening_index + 2, len(markers) - 1):
            possible = text[markers[candidate].end() : markers[candidate + 1].start()]
            if _looks_like_message_header(possible):
                next_opening_index = candidate
                break
        body_end = (
            markers[next_opening_index].start()
            if next_opening_index is not None
            else len(text)
        )
        body = text[closing.end() : body_end].strip()
        if not body and not message.attachments:
            raise MessagePluginError(f"消息 {message.message_id} 的正文和附件不能同时为空")
        messages.append(
            BufferedMessage(
                machine_id=message.machine_id,
                message_id=message.message_id,
                chat_type=message.chat_type,
                external_user_id=message.external_user_id,
                external_chat_id=message.external_chat_id,
                timestamp=message.timestamp,
                text=body,
                attachments=message.attachments,
            )
        )
        if next_opening_index is None:
            break
        opening_index = next_opening_index
    return tuple(messages)


def _load_module(path: Path, machine_id: str, role: str) -> ModuleType:
    digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:12]
    module_name = f"_kemo_message_{machine_id}_{role}_{digest}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise MessagePluginError(f"无法加载 {role} 模块：{path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise MessagePluginError(f"加载 {role} 模块失败：{path}（{exc}）") from exc
    return module


def _initial_state() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "health": "unknown",
        "last_check": None,
        "last_message_at": None,
        "error": None,
        "latency_ms": None,
        "messages_received_today": 0,
        "messages_sent_today": 0,
        "input_status": "unknown",
        "input_restart_count": 0,
        "input_last_restart_at": None,
        "input_error": None,
    }


def _normalize_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MessagePluginError("消息路由状态必须是对象")
    state = {**_initial_state(), **value}
    if state.get("schema_version") != 1:
        raise MessagePluginError("消息路由状态 schema_version 必须为 1")
    if state.get("health") not in _HEALTH_VALUES:
        raise MessagePluginError("state.health 必须是 unknown/healthy/degraded/dead")
    for field in ("messages_received_today", "messages_sent_today"):
        current = state.get(field)
        if isinstance(current, bool) or not isinstance(current, int) or current < 0:
            raise MessagePluginError(f"state.{field} 必须是非负整数")
    latency = state.get("latency_ms")
    if latency is not None and (
        isinstance(latency, bool) or not isinstance(latency, int) or latency < 0
    ):
        raise MessagePluginError("state.latency_ms 必须是非负整数或 null")
    if state.get("input_status") not in {
        "unknown",
        "starting",
        "running",
        "restarting",
        "stopped",
    }:
        raise MessagePluginError(
            "state.input_status 必须是 unknown/starting/running/restarting/stopped"
        )
    restart_count = state.get("input_restart_count")
    if (
        isinstance(restart_count, bool)
        or not isinstance(restart_count, int)
        or restart_count < 0
    ):
        raise MessagePluginError("state.input_restart_count 必须是非负整数")
    return state


def _state_counter_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(candidate).astimezone().date()
    except ValueError:
        return None
