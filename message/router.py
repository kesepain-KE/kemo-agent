"""与平台无关的外部消息路由到运行事件引擎。"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
import threading
from typing import Any, Callable

from events import RunEvent
from message.identity import IdentityResolver, filter_tool_registry
from message.schema import MessageEnvelope, OutboundMessage
from message.state import ProcessedMessageStore
from message.transport import TransportRegistry
from provider.factory import create_provider
from run.engine import iter_request_events
from run.tools import ToolRegistry, discover_tools
from run.users import user_dir


class MessageRouteError(RuntimeError):
    pass


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


_SESSION_LOCKS: dict[tuple[str, str, str, str], threading.RLock] = {}
_SESSION_LOCKS_GUARD = threading.Lock()


def _session_lock(root: Path, user: str, source: str, session_id: str) -> threading.RLock:
    key = (str(root.resolve()).casefold(), user, source, session_id)
    with _SESSION_LOCKS_GUARD:
        return _SESSION_LOCKS.setdefault(key, threading.RLock())


class MessageRouter:
    """Route inbound envelopes to Run with idempotency and session isolation."""

    def __init__(
        self,
        root: Path,
        resolver: IdentityResolver,
        transports: TransportRegistry,
        *,
        max_workers: int = 4,
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
        self.processed_message_limit = max(1, int(processed_message_limit))
        self.provider_factory = provider_factory
        self.tool_registry_factory = tool_registry_factory
        self.event_source = event_source
        self.on_result = on_result
        self.on_error = on_error
        self._executor: ThreadPoolExecutor | None = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()

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
        return executor.submit(self.route, envelope)

    def route(self, envelope: MessageEnvelope) -> RouteResult:
        """Synchronously route one message; normally called by the worker pool."""
        registered = self.transports.get(envelope.platform)
        if registered.policy.bound_user:
            user = registered.policy.bound_user
            user_dir(user, self.root)
        else:
            user = self.resolver.resolve(envelope)
        source = f"message:{envelope.platform}"
        session_id = f"{envelope.chat_type}:{envelope.external_chat_id}"
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
            )
            self._notify_result(result)
            return result

        result = RouteResult(
            envelope=envelope,
            user=user,
            source=source,
            session_id=session_id,
            status="processing",
        )
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
            chunks: list[str] = []
            terminal_seen = False
            route_error: dict[str, Any] | None = None
            lock = _session_lock(self.root, user, source, session_id)
            with lock:
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
                    if done.metadata.get("awaiting_tool_confirmation"):
                        pause = done.metadata.get("tool_pause") or {}
                        text = (
                            "已达到本轮工具调用上限"
                            f"（{pause.get('executed', 0)}/{pause.get('limit', 0)}），"
                            "中间结果已保存。请回复“继续”后恢复执行。"
                        )
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
            registered.transport.send(outbound)
            done = next(
                (event for event in reversed(result.events) if event.type == "done"),
                None,
            )
            result.status = (
                "waiting_confirmation"
                if done is not None
                and done.metadata.get("awaiting_tool_confirmation")
                else "completed"
            )
            result.text = text
            result.outbound = outbound
            store.complete_many(dedupe_keys, status="completed")
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            error = {
                "message": str(exc),
                "exception_type": type(exc).__name__,
                "phase": "message_route",
            }
            result.status = "failed"
            result.error = error
            try:
                store.complete_many(dedupe_keys, status="failed", error=error)
            except Exception:
                pass
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
