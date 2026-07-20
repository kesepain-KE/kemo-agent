"""Folder-based external message plugins backed by Markdown file queues."""

from __future__ import annotations

import base64
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import threading
import time
from types import ModuleType
from typing import Any, Callable, TYPE_CHECKING
import uuid

import yaml

from message.schema import MessageEnvelope, OutboundMessage
from message.state import ProcessedMessageStore
from message.transport import (
    ErrorCallback,
    InboundCallback,
    TransportError,
    TransportPolicy,
)
from run.users import user_dir

if TYPE_CHECKING:
    from message.router import RouteResult


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
        "log_dir",
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
    log_dir: str
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
        log_dir = _relative_path(value.get("log_dir"), "log_dir")
        resolved_directory = directory.resolve()
        for relative in (*normalized_modules.values(), message_buffer, files_dir, log_dir):
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
            log_dir=log_dir,
            raw=dict(value),
        )

    @property
    def buffer_path(self) -> Path:
        return _resolve_within(self.directory, self.message_buffer)

    @property
    def files_path(self) -> Path:
        return _resolve_within(self.directory, self.files_dir)

    @property
    def log_path(self) -> Path:
        return _resolve_within(self.directory, self.log_dir)

    @property
    def state_path(self) -> Path:
        return self.directory / "state.json"

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


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", "utf-8")
    os.replace(temporary, path)


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
    }


def _normalize_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MessagePluginError("state.json 必须是对象")
    state = {**_initial_state(), **value}
    if state.get("schema_version") != 1:
        raise MessagePluginError("state.json schema_version 必须为 1")
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
    return state


def _state_counter_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(candidate).astimezone().date()
    except ValueError:
        return None


class FileMessageTransport:
    """Transport implementation for one message/out/<platform> folder."""

    def __init__(
        self,
        config: MessagePluginConfig,
        *,
        poll_interval: float = 1.0,
        health_interval: float = 30.0,
        settle_interval: float = 0.2,
    ) -> None:
        self.config = config
        self.name = config.platform
        self.capabilities = config.capabilities
        self.policy = config.policy()
        self.poll_interval = max(0.05, float(poll_interval))
        self.health_interval = max(0.05, float(health_interval))
        self.settle_interval = max(0.0, float(settle_interval))
        self._input = _load_module(config.module_path("input"), config.machine_id, "input")
        self._output = _load_module(config.module_path("output"), config.machine_id, "output")
        self._detect = _load_module(config.module_path("detect"), config.machine_id, "detect")
        for module, role, function in (
            (self._input, "input", "start"),
            (self._input, "input", "stop"),
            (self._output, "output", "send"),
            (self._detect, "detect", "check"),
        ):
            if not callable(getattr(module, function, None)):
                raise MessagePluginError(f"{role}.py 缺少可调用的 {function}()")
        self._on_message: InboundCallback | None = None
        self._on_error: ErrorCallback | None = None
        self._stop_event = threading.Event()
        self._input_thread: threading.Thread | None = None
        self._poll_thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._poll_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._pending: dict[str, _PendingEnvelope] = {}
        self._claim_counts: dict[Path, int] = {}
        self._active_claims: set[Path] = set()
        self._running = False

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def start(self, on_message: InboundCallback, on_error: ErrorCallback) -> None:
        with self._lock:
            if self._running:
                return
            self._on_message = on_message
            self._on_error = on_error
            self._stop_event.clear()
            self.config.files_path.mkdir(parents=True, exist_ok=True)
            self.config.log_path.mkdir(parents=True, exist_ok=True)
            self.config.buffer_path.parent.mkdir(parents=True, exist_ok=True)
            self.config.buffer_path.touch(exist_ok=True)
            self._ensure_state()
            self._running = True
            self._input_thread = threading.Thread(
                target=self._run_input,
                name=f"message-input-{self.name}",
                daemon=True,
            )
            self._poll_thread = threading.Thread(
                target=self._run_poll,
                name=f"message-poll-{self.name}",
                daemon=True,
            )
            self._input_thread.start()
            self._poll_thread.start()

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._stop_event.set()
        try:
            self._input.stop()
        finally:
            for thread in (self._input_thread, self._poll_thread):
                if thread is not None and thread is not threading.current_thread():
                    thread.join(timeout=5.0)
            with self._lock:
                self._running = False
                self._on_message = None
                self._on_error = None

    def send(self, message: OutboundMessage) -> None:
        token = str(message.metadata.get("message_queue_token") or "")
        pending = self._pending.get(token)
        reply_to = (
            pending.messages[-1].message_id
            if pending is not None and pending.messages
            else message.reply_to
        )
        payload = {
            "chat_type": message.chat_type,
            "external_chat_id": message.external_chat_id,
            "text": message.text,
            "file_path": message.file_path,
            "reply_to": reply_to,
        }
        try:
            sent = self._output.send(payload)
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise TransportError(f"{self.name} output.send() 失败：{exc}") from exc
        if sent is not True:
            raise TransportError(f"{self.name} output.send() 返回 False")
        self._update_state(
            lambda state: state.__setitem__(
                "messages_sent_today", state["messages_sent_today"] + 1
            )
        )

    def request_payload(self, envelope: MessageEnvelope) -> dict[str, Any]:
        """Build Engine prompt/content fields from validated local attachments."""
        prompt_parts = [envelope.text] if envelope.text.strip() else []
        content: list[dict[str, Any]] = []
        for raw in envelope.attachments:
            path = self._attachment_path(str(raw.get("path") or ""))
            mime = str(raw.get("mime") or "application/octet-stream").lower()
            name = str(raw.get("name") or path.name)
            size = path.stat().st_size
            declared_size = raw.get("size")
            if isinstance(declared_size, int) and not isinstance(declared_size, bool):
                if declared_size != size:
                    raise MessagePluginError(
                        f"附件大小与 message.md 声明不一致：{name}"
                    )
            if size > _MAX_ATTACHMENT_BYTES:
                raise MessagePluginError(
                    f"附件超过 {_MAX_ATTACHMENT_BYTES} 字节限制：{name}"
                )
            if mime.startswith("text/"):
                try:
                    text = path.read_text("utf-8")
                except UnicodeError:
                    text = path.read_text("utf-8", errors="replace")
                if len(text) > _MAX_TEXT_ATTACHMENT_CHARS:
                    text = text[:_MAX_TEXT_ATTACHMENT_CHARS] + "\n…（附件内容已截断）"
                prompt_parts.append(f"[文本附件：{name} | {mime}]\n{text}")
                continue
            if mime.startswith("video/"):
                prompt_parts.append(
                    f"[视频附件暂未自动解析：{name} | {mime} | {size} bytes]"
                )
                continue
            if mime.startswith("image/"):
                kind = "image"
            elif mime.startswith("audio/"):
                kind = "audio"
            elif mime == "application/pdf":
                kind = "file"
            else:
                prompt_parts.append(f"[文件附件：{name} | {mime} | {size} bytes]")
                continue
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            block: dict[str, Any] = {
                "type": kind,
                "source": {"kind": "inline_base64", "data": encoded},
                "mime_type": mime,
            }
            if kind == "file":
                block["filename"] = name
            content.append(block)
        return {"prompt": "\n\n".join(prompt_parts), "content": content}

    def finalize(self, result: "RouteResult") -> None:
        token = str(result.envelope.metadata.get("message_queue_token") or "")
        pending = self._pending.pop(token, None)
        if pending is None:
            return
        try:
            self._write_log(pending.messages, result)
            for message in pending.messages:
                for attachment in message.attachments:
                    try:
                        self._attachment_path(attachment.path).unlink(missing_ok=True)
                    except OSError as exc:
                        self._report_error(exc)
        finally:
            remaining = self._claim_counts.get(pending.claim_path, 1) - 1
            if remaining <= 0:
                self._claim_counts.pop(pending.claim_path, None)
                self._active_claims.discard(pending.claim_path)
                pending.claim_path.unlink(missing_ok=True)
            else:
                self._claim_counts[pending.claim_path] = remaining

    def poll_once(self) -> int:
        """Claim and submit available queue files; public for diagnostics/tests."""
        with self._poll_lock:
            return self._poll_once()

    def _poll_once(self) -> int:
        submitted = 0
        for claim in self._claim_paths():
            if claim in self._active_claims:
                continue
            self._active_claims.add(claim)
            try:
                messages = parse_message_buffer(claim.read_text("utf-8"))
                envelopes = self._envelopes(messages)
                if not envelopes:
                    claim.unlink(missing_ok=True)
                    self._active_claims.discard(claim)
                    continue
                self._claim_counts[claim] = len(envelopes)
                callback = self._on_message
                if callback is None:
                    raise MessagePluginError("消息回调尚未注册")
                for envelope, originals in envelopes:
                    token = str(envelope.metadata["message_queue_token"])
                    self._pending[token] = _PendingEnvelope(
                        originals, claim
                    )
                    callback(envelope)
                    submitted += 1
                self._update_state(
                    lambda state: (
                        state.__setitem__(
                            "messages_received_today",
                            state["messages_received_today"] + len(messages),
                        ),
                        state.__setitem__("last_message_at", messages[-1].timestamp),
                    )
                )
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                failed = claim.with_suffix(claim.suffix + ".failed")
                try:
                    os.replace(claim, failed)
                except OSError:
                    pass
                self._active_claims.discard(claim)
                self._report_error(exc)
        return submitted

    def check_health(self) -> dict[str, Any]:
        with self._state_lock:
            current = self._read_state()
            last_check_date = _state_counter_date(current.get("last_check"))
            today = datetime.now().astimezone().date()
            if last_check_date is not None and last_check_date != today:
                current["messages_received_today"] = 0
                current["messages_sent_today"] = 0
            started = time.monotonic()
            try:
                updated = self._detect.check(dict(self.config.raw), dict(current))
                state = _normalize_state(updated)
                state["last_check"] = datetime.now().astimezone().isoformat()
                if state.get("latency_ms") is None:
                    state["latency_ms"] = int((time.monotonic() - started) * 1000)
                _atomic_json(self.config.state_path, state)
                return state
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                current["health"] = "dead"
                current["last_check"] = datetime.now().astimezone().isoformat()
                current["error"] = str(exc)
                current["latency_ms"] = int((time.monotonic() - started) * 1000)
                _atomic_json(self.config.state_path, current)
                self._report_error(exc)
                return current

    def _run_input(self) -> None:
        try:
            self._input.start(
                dict(self.config.raw),
                str(self.config.buffer_path),
                str(self.config.files_path),
                str(self.config.state_path),
            )
        except BaseException as exc:
            if not isinstance(exc, (KeyboardInterrupt, SystemExit)):
                self._report_error(exc)

    def _run_poll(self) -> None:
        next_health = 0.0
        while not self._stop_event.is_set():
            try:
                now = time.monotonic()
                if now >= next_health:
                    self.check_health()
                    next_health = now + self.health_interval
                self.poll_once()
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    return
                self._report_error(exc)
            self._stop_event.wait(self.poll_interval)

    def _claim_paths(self) -> list[Path]:
        buffer_path = self.config.buffer_path
        claims = sorted(
            buffer_path.parent.glob(
                f".{buffer_path.stem}.*.processing{buffer_path.suffix}"
            ),
            key=lambda path: path.name,
        )
        try:
            stat = buffer_path.stat()
            has_data = (
                buffer_path.is_file()
                and stat.st_size > 0
                and time.time() - stat.st_mtime >= self.settle_interval
            )
        except OSError:
            has_data = False
        if has_data:
            claim = buffer_path.with_name(
                f".{buffer_path.stem}.{uuid.uuid4().hex}.processing{buffer_path.suffix}"
            )
            os.replace(buffer_path, claim)
            buffer_path.touch()
            claims.append(claim)
        return claims

    def _envelopes(
        self, messages: tuple[BufferedMessage, ...]
    ) -> list[tuple[MessageEnvelope, tuple[BufferedMessage, ...]]]:
        batches: "OrderedDict[tuple[str, str], list[BufferedMessage]]" = OrderedDict()
        processed = ProcessedMessageStore(self.config.root, self.config.bound_user)
        for message in messages:
            if message.machine_id != self.config.machine_id:
                raise MessagePluginError(
                    f"消息 {message.message_id} 的 machine_id 与插件不匹配"
                )
            if message.chat_type not in {"private", "group"}:
                raise MessagePluginError(
                    f"消息 {message.message_id} 的 chat_type 必须是 private/group"
                )
            if message.attachments and "receive_file" not in self.capabilities:
                raise MessagePluginError(
                    f"插件 {self.name} 未声明 receive_file，不能接收附件"
                )
            dedupe_key = f"{self.config.platform}:{message.message_id}"
            if processed.get(dedupe_key) is not None:
                key = ("duplicate", message.message_id)
            elif message.chat_type == "group":
                key = ("group", message.external_chat_id)
            else:
                key = ("private", message.message_id)
            batches.setdefault(key, []).append(message)
        result: list[tuple[MessageEnvelope, tuple[BufferedMessage, ...]]] = []
        for batch in batches.values():
            original = tuple(batch)
            result.append((self._envelope_for(original), original))
        return result

    def _envelope_for(self, messages: tuple[BufferedMessage, ...]) -> MessageEnvelope:
        last = messages[-1]
        if len(messages) == 1:
            message_id = last.message_id
            text = last.text
        else:
            digest = hashlib.sha256(
                (self.config.machine_id + "\0" + "\0".join(
                    item.message_id for item in messages
                )).encode("utf-8")
            ).hexdigest()[:24]
            message_id = f"batch_{digest}"
            text = "\n\n".join(
                f"[{item.timestamp} | {item.external_user_id}]\n"
                f"{item.text or '[仅附件]'}"
                for item in messages
            )
        attachments = tuple(
            {
                "path": attachment.path,
                "name": attachment.name,
                "mime": attachment.mime,
                "size": attachment.size,
                "message_id": message.message_id,
            }
            for message in messages
            for attachment in message.attachments
        )
        return MessageEnvelope(
            message_id=message_id,
            platform=self.config.platform,
            chat_type=last.chat_type,
            external_user_id=last.external_user_id,
            external_chat_id=last.external_chat_id,
            text=text,
            timestamp=last.timestamp,
            attachments=attachments,
            metadata={
                "machine_id": self.config.machine_id,
                "source_message_ids": [item.message_id for item in messages],
                "dedupe_keys": [
                    f"{self.config.platform}:{item.message_id}" for item in messages
                ],
                "reply_to": last.message_id,
                "message_queue_token": uuid.uuid4().hex,
            },
        )

    def _attachment_path(self, relative: str) -> Path:
        path = _resolve_within(self.config.directory, relative)
        try:
            path.relative_to(self.config.files_path)
        except ValueError:
            raise MessagePluginError(f"附件路径不在 files_dir 内：{relative}") from None
        if not path.is_file():
            raise MessagePluginError(f"附件不存在：{relative}")
        return path

    def _ensure_state(self) -> None:
        with self._state_lock:
            if not self.config.state_path.is_file():
                _atomic_json(self.config.state_path, _initial_state())
            else:
                _atomic_json(self.config.state_path, self._read_state())

    def _read_state(self) -> dict[str, Any]:
        try:
            value = json.loads(self.config.state_path.read_text("utf-8"))
        except FileNotFoundError:
            value = _initial_state()
        except (OSError, json.JSONDecodeError) as exc:
            raise MessagePluginError(f"state.json 不可读：{exc}") from exc
        return _normalize_state(value)

    def _update_state(self, update: Callable[[dict[str, Any]], Any]) -> None:
        with self._state_lock:
            state = self._read_state()
            update(state)
            _atomic_json(self.config.state_path, _normalize_state(state))

    def _write_log(
        self, messages: tuple[BufferedMessage, ...], result: "RouteResult"
    ) -> None:
        if result.status in {"completed", "waiting_confirmation"}:
            outbound = result.text
        elif result.status == "duplicate":
            outbound = "重复消息已按幂等记录跳过。"
        else:
            outbound = f"处理失败：{(result.error or {}).get('message', '未知错误')}"
        pieces: list[str] = []
        for message in messages:
            timestamp = datetime.fromisoformat(
                message.timestamp[:-1] + "+00:00"
                if message.timestamp.endswith("Z")
                else message.timestamp
            )
            pieces.append(
                f"## {timestamp.strftime('%Y-%m-%d %H:%M:%S')} | "
                f"{message.chat_type} | {message.external_chat_id}\n\n"
                f"**入站**：{message.text or '[仅附件]'}\n"
            )
            for attachment in message.attachments:
                pieces.append(
                    f"  - 附件：{attachment.name} "
                    f"({attachment.mime}, {attachment.size} bytes)\n"
                )
            pieces.append(f"\n**出站**：{outbound}\n\n---\n\n")
            log_file = self.config.log_path / f"{timestamp.date().isoformat()}.md"
            log_file.parent.mkdir(parents=True, exist_ok=True)
            with log_file.open("a", encoding="utf-8") as handle:
                handle.write("".join(pieces))
            pieces.clear()

    def _report_error(self, exc: BaseException) -> None:
        try:
            with self._state_lock:
                state = self._read_state()
                if state.get("health") != "dead":
                    state["health"] = "degraded"
                state["error"] = str(exc)
                _atomic_json(self.config.state_path, state)
        except Exception:
            pass
        callback = self._on_error
        if callback is not None:
            try:
                callback(self.name, exc)
            except Exception:
                pass


def discover_message_plugins(
    root: Path,
) -> tuple[list[FileMessageTransport], list[MessagePluginIssue]]:
    """Discover valid direct child plugins without executing unrelated files."""
    base = root.resolve() / "message" / "out"
    if not base.is_dir():
        return [], []
    transports: list[FileMessageTransport] = []
    issues: list[MessagePluginIssue] = []
    machine_ids: set[str] = set()
    platforms: set[str] = set()
    for directory in sorted(
        (item for item in base.iterdir() if item.is_dir()),
        key=lambda item: item.name.casefold(),
    ):
        try:
            config = MessagePluginConfig.load(root.resolve(), directory)
            if config.machine_id in machine_ids:
                raise MessagePluginError(f"machine_id 重复：{config.machine_id}")
            if config.platform in platforms:
                raise MessagePluginError(f"platform 重复：{config.platform}")
            transport = FileMessageTransport(config)
            transports.append(transport)
            machine_ids.add(config.machine_id)
            platforms.add(config.platform)
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            issues.append(MessagePluginIssue(directory.name, directory, str(exc)))
    return transports, issues
