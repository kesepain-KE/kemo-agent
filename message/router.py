"""与平台无关的外部消息路由到运行事件引擎。"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import threading
import secrets
import time
from typing import Any, Callable

from events import RunEvent
from message.identity import IdentityError, IdentityResolver, filter_tool_registry
from message.schema import MessageEnvelope, OutboundMessage
from message.state import ProcessedMessageStore
from message.transport import TransportRegistry
from provider.factory import create_provider
from run.engine import iter_request_events
from run.history import clear_session, queue_memory_extraction
from run.history import (
    set_active as set_history_active,
    close_session as close_history_session,
    get_or_reserve_active as get_or_reserve_history_session,
)
from run.tools import ToolRegistry, discover_tools
from run.config import user_dir
from run.infra import LogStore, record_runtime_event
from run.retry import HOST_CONTROL_EXCEPTIONS


class MessageRouteError(RuntimeError):
    pass


class MessageQueueFullError(MessageRouteError):
    """消息路由工作线程与等待队列均已满。"""


@dataclass(slots=True)
class RouteResult:
    envelope: MessageEnvelope
    user: str
    source: str
    session_id: str
    status: str
    text: str = ""
    events: list[RunEvent] = field(default_factory=list)
    outbound: OutboundMessage | None = None
    error: dict[str, Any] | None = None
    duplicate: bool = False
    sender_verified: bool | None = None
    warnings: list[str] = field(default_factory=list)


_SESSION_LOCKS: dict[tuple[str, str, str, str], threading.RLock] = {}
_SESSION_LOCKS_GUARD = threading.Lock()


def _session_lock(root: Path, user: str, source: str, session_id: str) -> threading.RLock:
    key = (str(root.resolve()).casefold(), user, source, session_id)
    with _SESSION_LOCKS_GUARD:
        return _SESSION_LOCKS.setdefault(key, threading.RLock())


def _slash_command(text: str) -> tuple[str, str] | None:
    parts = text.strip().split(maxsplit=1)
    if not parts or not parts[0].startswith("/"):
        return None
    return (
        parts[0].split("@", 1)[0].casefold(),
        parts[1].strip() if len(parts) > 1 else "",
    )


def _new_session_command_title(text: str, default: str) -> str | None:
    command = _slash_command(text)
    if command is None or command[0] != "/new":
        return None
    return command[1][:80] or default


class MessageRouter:
    """Route inbound envelopes to Run with idempotency and session isolation."""

    def __init__(
        self,
        root: Path,
        resolver: IdentityResolver,
        transports: TransportRegistry,
        *,
        max_workers: int = 4,
        max_queued_messages: int = 20,
        processed_message_limit: int = 2000,
        provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
        tool_registry_factory: Callable[[Path, str], ToolRegistry] = discover_tools,
        event_source: Callable[..., Any] = iter_request_events,
        on_result: Callable[[RouteResult], None] | None = None,
        on_error: Callable[[str, BaseException], None] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.resolver = resolver
        self.transports = transports
        self.max_workers = max(1, int(max_workers))
        self.max_queued_messages = max(0, int(max_queued_messages))
        self.processed_message_limit = max(1, int(processed_message_limit))
        self.provider_factory = provider_factory
        self.tool_registry_factory = tool_registry_factory
        self.event_source = event_source
        self.on_result = on_result
        self.on_error = on_error
        self._executor: ThreadPoolExecutor | None = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._clear_confirmations: dict[tuple[str, ...], tuple[str, float]] = {}
        self._pending_lock = threading.Lock()
        self._outstanding_count = 0
        self._active_count = 0
        self._capacity = (
            threading.BoundedSemaphore(self.max_workers + self.max_queued_messages)
            if self.max_queued_messages > 0
            else None
        )

    @property
    def running(self) -> bool:
        with self._lock:
            return self._executor is not None and not self._stop_event.is_set()

    def start(self) -> None:
        with self._lock:
            if self._executor is not None:
                return
            self._stop_event.clear()
            self._executor = ThreadPoolExecutor(
                max_workers=self.max_workers,
                thread_name_prefix="message-router",
            )

    def stop(self, *, wait: bool = True) -> None:
        self._stop_event.set()
        with self._lock:
            executor = self._executor
            self._executor = None
        if executor is not None:
            executor.shutdown(wait=wait, cancel_futures=True)

    def submit(self, envelope: MessageEnvelope) -> Future[RouteResult]:
        with self._lock:
            executor = self._executor
        if executor is None or self._stop_event.is_set():
            raise MessageRouteError("MessageRouter 未运行")
        if self._capacity is not None and not self._capacity.acquire(blocking=False):
            raise MessageQueueFullError(
                f"消息路由队列已满（工作线程 {self.max_workers}，"
                f"等待上限 {self.max_queued_messages}），请稍后重试"
            )
        ticket = {"started": False, "released": False}
        with self._pending_lock:
            self._outstanding_count += 1
        try:
            future = executor.submit(self._route_tracked, envelope, ticket)
        except BaseException:
            self._release_ticket(ticket, active=False)
            raise
        future.add_done_callback(lambda item: self._release_cancelled(item, ticket))
        return future

    def _route_tracked(
        self,
        envelope: MessageEnvelope,
        ticket: dict[str, bool],
    ) -> RouteResult:
        with self._pending_lock:
            ticket["started"] = True
            self._active_count += 1
        try:
            return self.route(envelope)
        finally:
            self._release_ticket(ticket, active=True)

    def _release_cancelled(
        self,
        future: Future[RouteResult],
        ticket: dict[str, bool],
    ) -> None:
        if future.cancelled():
            self._release_ticket(ticket, active=False)

    def _release_ticket(self, ticket: dict[str, bool], *, active: bool) -> None:
        release_capacity = False
        with self._pending_lock:
            if ticket["released"]:
                return
            ticket["released"] = True
            if active:
                self._active_count = max(0, self._active_count - 1)
            self._outstanding_count = max(0, self._outstanding_count - 1)
            release_capacity = self._capacity is not None
        if release_capacity and self._capacity is not None:
            self._capacity.release()

    def queue_status(self) -> dict[str, int]:
        with self._pending_lock:
            active = self._active_count
            queued = max(0, self._outstanding_count - active)
        return {
            "active_workers": active,
            "max_workers": self.max_workers,
            "queued_messages": queued,
            "max_queued": self.max_queued_messages,
        }

    def route(self, envelope: MessageEnvelope) -> RouteResult:
        """Synchronously route one message; normally called by the worker pool."""
        registered = self.transports.get(envelope.platform)
        policy = registered.policy
        if envelope.chat_type != "private":
            # Only direct messages trigger a Run. mention/prefix are reserved;
            # do not reserve a session or claim a message for ignored traffic.
            user = policy.bound_user
            if not user:
                try:
                    user = self.resolver.resolve(envelope)
                except IdentityError:
                    user = "__system__"
            return self._untriggered_result(
                envelope, user, "ignored", "direct_only", "当前只处理私聊消息",
            )
        if policy.bound_user:
            user = policy.bound_user
            user_dir(user, self.root)
        else:
            user = self.resolver.resolve(envelope)
        if policy.allowed_chat_ids and envelope.external_chat_id not in policy.allowed_chat_ids:
            return self._untriggered_result(
                envelope, user, "ignored", "chat_not_allowed", "私聊 chat_id 不在允许列表",
            )
        sender_verified: bool | None = None
        warnings: list[str] = []
        if policy.bound_user:
            sender = str(envelope.external_user_id or "").strip()
            if not sender:
                # Valid envelopes require an ID; retain observable compatibility
                # for legacy transports that cannot provide one.
                sender_verified = False
                warnings.append("该平台无法验证发送者")
                record_runtime_event(
                    self.root, user, category="backend",
                    name="message_sender_unverifiable", status="failed",
                    error_type="MissingSenderIdentity",
                    error_message=warnings[0],
                )
            else:
                if policy.allowed_sender_ids:
                    sender_verified = sender in policy.allowed_sender_ids
                elif sender == user:
                    sender_verified = True
                else:
                    try:
                        sender_verified = self.resolver.resolve(envelope) == user
                    except IdentityError:
                        sender_verified = False
                if not sender_verified:
                    return self._untriggered_result(
                        envelope, user, "rejected", "sender_not_allowed",
                        "发送者未获绑定用户授权；请配置 allowed_sender_ids 或现有身份绑定",
                        sender_verified=False,
                    )
        source = f"message:{envelope.platform}"
        active_key = (
            f"message:{envelope.platform}:{envelope.chat_type}:"
            f"{envelope.external_chat_id}"
        )
        active_record, _ = get_or_reserve_history_session(
            self.root,
            user,
            source,
            active_key,
            title=f"{envelope.platform} · {envelope.chat_type}",
        )
        session_id = str(active_record["session_id"])
        store = ProcessedMessageStore(
            self.root, user, max_entries=self.processed_message_limit
        )
        raw_dedupe_keys = envelope.metadata.get("dedupe_keys")
        if isinstance(raw_dedupe_keys, list) and raw_dedupe_keys and all(
            isinstance(item, str) and item.strip() for item in raw_dedupe_keys
        ):
            dedupe_keys = tuple(dict.fromkeys(item.strip() for item in raw_dedupe_keys))
        else:
            dedupe_keys = (envelope.dedupe_key,)
        if not store.claim_many(dedupe_keys):
            result = RouteResult(
                envelope=envelope,
                user=user,
                source=source,
                session_id=session_id,
                status="duplicate",
                duplicate=True,
                sender_verified=sender_verified,
                warnings=warnings,
            )
            self._notify_result(result)
            return result

        result = RouteResult(
            envelope=envelope,
            user=user,
            source=source,
            session_id=session_id,
            status="processing",
            sender_verified=sender_verified,
            warnings=warnings,
        )
        send_unconfirmed = False
        try:
            if self._stop_event.is_set():
                raise MessageRouteError("宿主正在停止，不再处理新消息")
            policy = registered.policy

            def filtered_registry(root: Path, target_user: str) -> ToolRegistry:
                discovered = self.tool_registry_factory(root, target_user)
                return filter_tool_registry(discovered, policy.allowed_tools)

            request = {
                "user": user,
                "prompt": envelope.text,
                "source": source,
                "session_id": session_id,
                "stream": True,
                "_history_active_key": active_key,
                "_transport_registry": self.transports,
            }
            request_payload = getattr(registered.transport, "request_payload", None)
            if callable(request_payload):
                prepared = request_payload(envelope)
                if not isinstance(prepared, dict):
                    raise MessageRouteError("Transport request_payload() 必须返回对象")
                request["prompt"] = str(prepared.get("prompt") or "")
                content = prepared.get("content") or []
                if not isinstance(content, list):
                    raise MessageRouteError("Transport content 必须是数组")
                request["content"] = content
                assets = prepared.get("assets")
                if assets is None:
                    assets = prepared.get("uploaded_files") or []
                if not isinstance(assets, list) or not all(
                    isinstance(item, dict) for item in assets
                ):
                    raise MessageRouteError("Transport assets 必须是对象数组")
                request["uploaded_files"] = assets
            chunks: list[str] = []
            terminal_seen = False
            route_error: dict[str, Any] | None = None
            lock = _session_lock(self.root, user, source, active_key)
            with lock:
                active_record, _ = get_or_reserve_history_session(
                    self.root,
                    user,
                    source,
                    active_key,
                    title=f"{envelope.platform} · {envelope.chat_type}",
                )
                session_id = str(active_record["session_id"])
                result.session_id = session_id
                request["session_id"] = session_id
                slash_command = _slash_command(envelope.text)
                command_title = _new_session_command_title(
                    envelope.text,
                    f"{envelope.platform} · {envelope.chat_type}",
                )
                if command_title is not None:
                    memory = queue_memory_extraction(
                        self.root,
                        user,
                        source,
                        session_id,
                    )
                    closed = close_history_session(
                        self.root,
                        user,
                        source,
                        session_id,
                    )
                    if closed is None:
                        raise MessageRouteError(f"会话不存在：{session_id}")
                    next_record, _ = get_or_reserve_history_session(
                        self.root,
                        user,
                        source,
                        active_key,
                        title=command_title,
                    )
                    next_session_id = str(next_record.get("session_id") or "")
                    text = "当前对话已保存，已创建并切换到新对话。"
                    if memory.get("status") == "queued":
                        text += "旧对话记忆将在后台继续提取。"
                    outbound = OutboundMessage.reply(
                        envelope,
                        text,
                        metadata={
                            "user": user,
                            "source": source,
                            "session_id": next_session_id,
                            "previous_session_id": session_id,
                            "memory": memory,
                            **(
                                {
                                    "message_queue_token": envelope.metadata[
                                        "message_queue_token"
                                    ]
                                }
                                if envelope.metadata.get("message_queue_token")
                                else {}
                            ),
                        },
                    )
                    send_unconfirmed = True
                    result.outbound = outbound
                    receipt = registered.transport.send(outbound)
                    if receipt is not True and not (isinstance(receipt, dict) and receipt.get("ok") is True):
                        raise MessageRouteError("发送未得到显式成功确认")
                    send_unconfirmed = False
                    result.session_id = next_session_id
                    result.status = "completed"
                    result.text = text
                    result.outbound = outbound
                    store.complete_many(dedupe_keys, status="completed")
                    self._notify_result(result)
                    return result
                if slash_command is not None:
                    command_name, command_argument = slash_command
                    if command_name == "/clear":
                        confirmation_key = (user, source, active_key, session_id, envelope.external_user_id)
                        now = time.monotonic()
                        with self._lock:
                            for key, value in list(self._clear_confirmations.items()):
                                if value[1] <= now:
                                    self._clear_confirmations.pop(key, None)
                            pending = self._clear_confirmations.get(confirmation_key)
                            token = ""
                            confirmed = bool(
                                pending and len(command_argument) == 16 and command_argument.isascii()
                                and secrets.compare_digest(command_argument, pending[0])
                            )
                            if confirmed:
                                self._clear_confirmations.pop(confirmation_key, None)
                            else:
                                if len(self._clear_confirmations) >= 128:
                                    self._clear_confirmations.pop(next(iter(self._clear_confirmations)))
                                token = secrets.token_hex(8)
                                self._clear_confirmations[confirmation_key] = (token, now + 60)
                        if confirmed:
                            session_id = clear_session(self.root, user, source, session_id)
                            set_history_active(self.root, user, active_key, session_id, source=source)
                            result.session_id = session_id
                            text = "已切换到空白对话；原始历史完整保留。"
                        else:
                            text = f"尚未清空对话。请在60秒内由同一发送者在当前聊天发送 /clear {token} 确认；过期或重启后需重新申请。"
                    else:
                        text = (
                            f"未知指令：{command_name}。"
                            "目前支持：/new、/clear。"
                        )
                    outbound = OutboundMessage.reply(
                        envelope,
                        text,
                        metadata={
                            "user": user,
                            "source": source,
                            "session_id": session_id,
                            **(
                                {
                                    "message_queue_token": envelope.metadata[
                                        "message_queue_token"
                                    ]
                                }
                                if envelope.metadata.get("message_queue_token")
                                else {}
                            ),
                        },
                    )
                    send_unconfirmed = True
                    result.outbound = outbound
                    receipt = registered.transport.send(outbound)
                    if receipt is not True and not (isinstance(receipt, dict) and receipt.get("ok") is True):
                        raise MessageRouteError("发送未得到显式成功确认")
                    send_unconfirmed = False
                    result.status = "completed"
                    result.text = text
                    result.outbound = outbound
                    store.complete_many(dedupe_keys, status="completed")
                    self._notify_result(result)
                    return result
                for event in self.event_source(
                    request,
                    root=self.root,
                    provider_factory=self.provider_factory,
                    tool_registry_factory=filtered_registry,
                    cancel_event=self._stop_event,
                ):
                    result.events.append(event)
                    if event.type == "text_delta":
                        chunks.append(event.content)
                    elif event.type == "error":
                        terminal_seen = True
                        route_error = dict(event.error or {})
                        break
                    elif event.type == "done":
                        terminal_seen = True

            if route_error is not None:
                raise MessageRouteError(
                    str(route_error.get("message") or "Run 返回错误事件")
                )
            if not terminal_seen:
                raise MessageRouteError("Run 事件流缺少终态")

            text = "".join(chunks).strip()
            if not text:
                done = next(
                    (event for event in reversed(result.events) if event.type == "done"),
                    None,
                )
                if done is not None:
                    text = str(done.metadata.get("text") or "").strip()
            if not text:
                text = "任务已完成。"
            outbound = OutboundMessage.reply(
                envelope,
                text,
                metadata={
                    "user": user,
                    "source": source,
                    "session_id": session_id,
                    **(
                        {
                            "message_queue_token": envelope.metadata[
                                "message_queue_token"
                            ]
                        }
                        if envelope.metadata.get("message_queue_token")
                        else {}
                    ),
                },
            )
            send_unconfirmed = True
            result.outbound = outbound
            receipt = registered.transport.send(outbound)
            if receipt is not True and not (isinstance(receipt, dict) and receipt.get("ok") is True):
                raise MessageRouteError("发送未得到显式成功确认")
            send_unconfirmed = False
            result.status = "completed"
            result.text = text
            result.outbound = outbound
            store.complete_many(dedupe_keys, status="completed")
        except BaseException as exc:
            if isinstance(exc, HOST_CONTROL_EXCEPTIONS):
                raise
            error = {
                "message": (
                    "发送未得到显式成功确认；发送可能已经发生，请先核对目标会话，勿自动重复发送。"
                    if send_unconfirmed else str(exc)
                ),
                "exception_type": type(exc).__name__,
                "phase": "message_route",
                **({"code": "send_unconfirmed", "retryable": False} if send_unconfirmed else {}),
            }
            result.status = "failed"
            result.error = error
            try:
                store.complete_many(dedupe_keys, status="failed", error=error)
            except Exception:
                pass
            self._notify_error(envelope.platform, MessageRouteError(error["message"]) if send_unconfirmed else exc)

        self._notify_result(result)
        return result

    def _untriggered_result(
        self, envelope: MessageEnvelope, user: str, status: str,
        code: str, message: str, *, sender_verified: bool | None = None,
    ) -> RouteResult:
        result = RouteResult(
            envelope=envelope, user=user, source=f"message:{envelope.platform}",
            session_id="", status=status,
            error={"code": code, "message": message},
            sender_verified=sender_verified,
        )
        try:
            LogStore(self.root).append_message_entries([{
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "user": user,
                "machine_id": str(envelope.metadata.get("machine_id") or envelope.platform),
                "platform": envelope.platform,
                "chat_type": envelope.chat_type,
                "chat_id": envelope.external_chat_id,
                "exchange_id": envelope.message_id,
                "direction": "receive",
                "kind": "system",
                "content": f"{status}:{code} {message}",
                "success": status == "ignored",
            }])
        except Exception as exc:
            self._notify_error(envelope.platform, exc)
        self._notify_result(result)
        return result

    def _notify_result(self, result: RouteResult) -> None:
        if self.on_result is not None:
            try:
                self.on_result(result)
            except Exception as exc:
                self._notify_error(result.envelope.platform, exc)

    def _notify_error(self, component: str, exc: BaseException) -> None:
        if self.on_error is not None:
            try:
                self.on_error(component, exc)
            except Exception:
                pass
