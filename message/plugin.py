"""Folder-based external message plugins backed by Markdown file queues."""

from __future__ import annotations

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
from run.extensions import describe_message_asset
from run.infra import LogStore
from run.config import user_dir

if TYPE_CHECKING:
    from message.router import RouteResult


from message.plugin_contracts import (
    BufferedAttachment,
    BufferedMessage,
    MessagePluginConfig,
    MessagePluginError,
    MessagePluginIssue,
    _MAX_ATTACHMENT_BYTES,
    _MAX_TEXT_ATTACHMENT_CHARS,
    _PendingEnvelope,
    _initial_state,
    _load_module,
    _normalize_state,
    _resolve_within,
    _state_counter_date,
    parse_message_buffer,
)

class FileMessageTransport:
    """Transport implementation for one message/out/<platform> folder."""

    def __init__(
        self,
        config: MessagePluginConfig,
        *,
        poll_interval: float = 1.0,
        health_interval: float = 30.0,
        settle_interval: float = 0.2,
        input_supervision_interval: float = 1.0,
        input_start_grace: float = 2.0,
        input_restart_initial_backoff: float = 1.0,
        input_restart_max_backoff: float = 60.0,
        input_restart_stable_seconds: float = 30.0,
    ) -> None:
        self.config = config
        self.name = config.platform
        self.capabilities = config.capabilities
        self.policy = config.policy()
        self.poll_interval = max(0.05, float(poll_interval))
        self.health_interval = max(0.05, float(health_interval))
        self.settle_interval = max(0.0, float(settle_interval))
        self.input_supervision_interval = max(
            0.05, float(input_supervision_interval)
        )
        self.input_start_grace = max(0.0, float(input_start_grace))
        self.input_restart_initial_backoff = max(
            0.05, float(input_restart_initial_backoff)
        )
        self.input_restart_max_backoff = max(
            self.input_restart_initial_backoff,
            float(input_restart_max_backoff),
        )
        self.input_restart_stable_seconds = max(
            0.0, float(input_restart_stable_seconds)
        )
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
        self._input_supervisor_thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._input_lifecycle_lock = threading.RLock()
        self._poll_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._pending: dict[str, _PendingEnvelope] = {}
        self._claim_counts: dict[Path, int] = {}
        self._active_claims: set[Path] = set()
        self._input_started_at = 0.0
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
            self.config.buffer_path.parent.mkdir(parents=True, exist_ok=True)
            self.config.buffer_path.touch(exist_ok=True)
            self._ensure_state()
            self._set_input_state("starting")
            self._running = True
            self._input_thread = self._new_input_thread()
            self._poll_thread = threading.Thread(
                target=self._run_poll,
                name=f"message-poll-{self.name}",
                daemon=True,
            )
            self._input_supervisor_thread = threading.Thread(
                target=self._run_input_supervisor,
                name=f"message-input-supervisor-{self.name}",
                daemon=True,
            )
            self._input_started_at = time.monotonic()
            self._input_thread.start()
            self._poll_thread.start()
            self._input_supervisor_thread.start()

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._stop_event.set()
            supervisor = self._input_supervisor_thread
        if supervisor is not None and supervisor is not threading.current_thread():
            supervisor.join(timeout=5.0)
        try:
            with self._input_lifecycle_lock:
                self._input.stop()
        finally:
            for thread in (self._input_thread, self._poll_thread):
                if thread is not None and thread is not threading.current_thread():
                    thread.join(timeout=5.0)
            self._set_input_state("stopped")
            with self._lock:
                self._running = False
                self._on_message = None
                self._on_error = None
                self._input_thread = None
                self._poll_thread = None
                self._input_supervisor_thread = None

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
        """Build Engine prompt and Run assets from validated local attachments."""
        prompt_parts = [envelope.text] if envelope.text.strip() else []
        content: list[dict[str, Any]] = []
        assets: list[dict[str, Any]] = []
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
            assets.append(
                describe_message_asset(
                    self.config.root,
                    self.config.bound_user,
                    {
                        "path": str(raw.get("path") or ""),
                        "name": name,
                    },
                    source=self.config.directory.name,
                )
            )
        return {
            "prompt": "\n\n".join(prompt_parts),
            "content": content,
            "assets": assets,
        }

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
                self._write_state(state)
                return state
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                current["health"] = "dead"
                current["last_check"] = datetime.now().astimezone().isoformat()
                current["error"] = str(exc)
                current["latency_ms"] = int((time.monotonic() - started) * 1000)
                self._write_state(current)
                self._report_error(exc)
                return current

    def _new_input_thread(self) -> threading.Thread:
        return threading.Thread(
            target=self._run_input,
            name=f"message-input-{self.name}",
            daemon=True,
        )

    def _run_input(self) -> None:
        try:
            self._input.start(
                dict(self.config.raw),
                str(self.config.buffer_path),
                str(self.config.files_path),
            )
        except BaseException as exc:
            if not isinstance(exc, (KeyboardInterrupt, SystemExit)):
                self._report_error(exc)

    def _input_is_alive(self) -> bool:
        probe = getattr(self._input, "is_alive", None)
        if callable(probe):
            value = probe()
            if not isinstance(value, bool):
                raise MessagePluginError("input.is_alive() 必须返回布尔值")
            return value
        thread = self._input_thread
        return bool(thread is not None and thread.is_alive())

    def _input_last_error(self) -> str | None:
        reader = getattr(self._input, "last_error", None)
        if not callable(reader):
            return None
        value = reader()
        if value is None:
            return None
        return str(value).strip() or None

    def _restart_input(self) -> bool:
        with self._input_lifecycle_lock:
            if self._stop_event.is_set():
                return False
            restart = getattr(self._input, "restart", None)
            if callable(restart):
                restart()
            else:
                self._input.stop()
                previous = self._input_thread
                if (
                    previous is not None
                    and previous is not threading.current_thread()
                    and previous.is_alive()
                ):
                    previous.join(timeout=5.0)
                if previous is not None and previous.is_alive():
                    raise MessagePluginError(
                        "input.stop() 后输入线程仍未退出，已拒绝重复启动"
                    )
                thread = self._new_input_thread()
                self._input_thread = thread
                thread.start()
            self._input_started_at = time.monotonic()
            return True

    def _run_input_supervisor(self) -> None:
        restart_attempts = 0
        next_restart_at = 0.0
        healthy_since: float | None = None
        while not self._stop_event.is_set():
            now = time.monotonic()
            probe_error: BaseException | None = None
            try:
                alive = self._input_is_alive()
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    return
                alive = False
                probe_error = exc

            if alive:
                if healthy_since is None:
                    healthy_since = now
                    self._set_input_state("running")
                elif now - healthy_since >= self.input_restart_stable_seconds:
                    restart_attempts = 0
                    next_restart_at = 0.0
            else:
                healthy_since = None
                within_start_grace = (
                    now - self._input_started_at < self.input_start_grace
                )
                if not within_start_grace and now >= next_restart_at:
                    module_error = None
                    try:
                        module_error = self._input_last_error()
                    except BaseException as exc:
                        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                            return
                        probe_error = probe_error or exc
                    reason = module_error or (
                        str(probe_error) if probe_error is not None else "输入模块已停止"
                    )
                    self._set_input_state("restarting", error=reason)
                    restart_attempts += 1
                    try:
                        self._set_input_state("starting", restarted=True)
                        restarted = self._restart_input()
                        if not restarted:
                            return
                    except BaseException as exc:
                        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                            return
                        self._set_input_state("restarting", error=str(exc))
                        self._report_error(exc)
                    exponent = min(restart_attempts - 1, 16)
                    delay = min(
                        self.input_restart_max_backoff,
                        self.input_restart_initial_backoff * (2 ** exponent),
                    )
                    next_restart_at = time.monotonic() + delay

            self._stop_event.wait(self.input_supervision_interval)

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
            store = LogStore(self.config.root)
            state = store.read_message_route_state(self.config.machine_id)
            if state is None:
                state = _initial_state()
            self._write_state(state)

    def _read_state(self) -> dict[str, Any]:
        stored = LogStore(self.config.root).read_message_route_state(
            self.config.machine_id
        )
        if stored is not None:
            return _normalize_state(stored)
        return _initial_state()

    def _write_state(self, state: dict[str, Any]) -> None:
        normalized = _normalize_state(state)
        LogStore(self.config.root).write_message_route_state(
            self.config.machine_id,
            user=self.config.bound_user,
            platform=self.config.platform,
            state=normalized,
        )

    def _update_state(self, update: Callable[[dict[str, Any]], Any]) -> None:
        with self._state_lock:
            state = self._read_state()
            update(state)
            self._write_state(state)

    def _set_input_state(
        self,
        status: str,
        *,
        error: str | None = None,
        restarted: bool = False,
    ) -> None:
        def update(state: dict[str, Any]) -> None:
            state["input_status"] = status
            state["input_error"] = error
            if status == "restarting" and state.get("health") != "dead":
                state["health"] = "degraded"
            if restarted:
                state["input_restart_count"] = (
                    int(state.get("input_restart_count") or 0) + 1
                )
                state["input_last_restart_at"] = (
                    datetime.now().astimezone().isoformat()
                )

        self._update_state(update)

    def _write_log(
        self, messages: tuple[BufferedMessage, ...], result: "RouteResult"
    ) -> None:
        if result.status in {"completed", "waiting_confirmation"}:
            outbound = result.text
        elif result.status == "duplicate":
            outbound = "重复消息已按幂等记录跳过。"
        else:
            outbound = f"处理失败：{(result.error or {}).get('message', '未知错误')}"
        store = LogStore(self.config.root)
        files_root = self.config.files_path.relative_to(self.config.root).as_posix()
        entries: list[dict[str, Any]] = []
        for message in messages:
            timestamp = datetime.fromisoformat(
                message.timestamp[:-1] + "+00:00"
                if message.timestamp.endswith("Z")
                else message.timestamp
            )
            display_timestamp = timestamp.strftime("%Y-%m-%d %H:%M:%S")
            outbound_file_display = ""
            if result.outbound is not None and result.outbound.file_path:
                outbound_path = Path(result.outbound.file_path)
                try:
                    display_path = outbound_path.resolve().relative_to(
                        self.config.root
                    ).as_posix()
                except (OSError, ValueError):
                    display_path = outbound_path.name
                outbound_file_display = display_path
            common = {
                "occurred_at": display_timestamp,
                "user": self.config.bound_user,
                "machine_id": self.config.machine_id,
                "platform": self.config.platform,
                "chat_type": message.chat_type,
                "chat_id": message.external_chat_id,
                "source": "runtime/logs.sqlite3",
            }
            if message.text:
                entries.append(
                    {
                        **common,
                        "direction": "receive",
                        "kind": "text",
                        "content": message.text,
                        "success": True,
                    }
                )
            for attachment in message.attachments:
                entries.append(
                    {
                        **common,
                        "direction": "receive",
                        "kind": "file",
                        "content": attachment.name,
                        "file_path": f"{files_root}/{attachment.name}",
                        "mime": attachment.mime,
                        "size": attachment.size,
                        "success": True,
                    }
                )
            failed = outbound.startswith("处理失败：")
            if outbound:
                entries.append(
                    {
                        **common,
                        "direction": "send",
                        "kind": "system" if failed else "text",
                        "content": outbound,
                        "success": not failed,
                    }
                )
            if result.outbound is not None and result.outbound.file_path:
                entries.append(
                    {
                        **common,
                        "direction": "send",
                        "kind": "file",
                        "content": Path(result.outbound.file_path).name,
                        "file_path": outbound_file_display,
                        "success": True,
                    }
                )
        try:
            store.append_message_entries(entries)
        except Exception:
            # Observability persistence must not replay an already-routed message.
            pass

    def _report_error(self, exc: BaseException) -> None:
        try:
            with self._state_lock:
                state = self._read_state()
                if state.get("health") != "dead":
                    state["health"] = "degraded"
                state["error"] = str(exc)
                self._write_state(state)
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
