"""传输协议、注册表和确定性模拟传输。"""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any, Callable, Literal, Protocol, runtime_checkable

from message.schema import MessageEnvelope, OutboundMessage


class TransportError(RuntimeError):
    pass


class TransportRegistrationError(TransportError):
    pass


InboundCallback = Callable[[MessageEnvelope], Any]
ErrorCallback = Callable[[str, BaseException], None]
MessageTrigger = Literal["direct", "mention", "prefix"]
# file/memory_manage mix reads with writes under one tool name. Until an
# explicit entry grants them, expose only the intrinsically read-only tool.
DEFAULT_MESSAGE_TOOLS = frozenset({"history_search"})


@runtime_checkable
class Transport(Protocol):
    name: str
    capabilities: frozenset[str]

    def start(self, on_message: InboundCallback, on_error: ErrorCallback) -> None: ...
    def send(self, message: OutboundMessage) -> bool | dict[str, Any]: ...
    def stop(self) -> None: ...

    @property
    def running(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class TransportPolicy:
    allowed_tools: frozenset[str] | None = DEFAULT_MESSAGE_TOOLS
    capabilities: frozenset[str] = frozenset({"receive_text", "send_text"})
    bound_user: str | None = None
    allowed_chat_ids: frozenset[str] = frozenset()
    allowed_sender_ids: frozenset[str] = frozenset()
    trigger: MessageTrigger = "direct"  # mention/prefix are reserved, not implemented.

    @classmethod
    def from_dict(cls, value: dict | None) -> "TransportPolicy":
        raw = value or {}
        allowed = raw.get("allowed_tools", list(DEFAULT_MESSAGE_TOOLS))
        if allowed is None:
            normalized = None
        elif isinstance(allowed, list) and all(isinstance(item, str) for item in allowed):
            normalized = frozenset(item.strip() for item in allowed if item.strip())
        else:
            raise TransportRegistrationError("allowed_tools 必须是字符串数组或 null")
        capabilities = raw.get("capabilities", ["receive_text", "send_text"])
        if not isinstance(capabilities, list) or not all(
            isinstance(item, str) for item in capabilities
        ):
            raise TransportRegistrationError("capabilities 必须是字符串数组")
        allowlists: dict[str, frozenset[str]] = {}
        for field_name in ("allowed_chat_ids", "allowed_sender_ids"):
            values = raw.get(field_name, [])
            if values is None:
                values = []
            if not isinstance(values, list) or not all(
                isinstance(item, str) and item.strip() for item in values
            ):
                raise TransportRegistrationError(f"{field_name} 必须是非空字符串组成的数组")
            allowlists[field_name] = frozenset(item.strip() for item in values)
        return cls(
            allowed_tools=normalized,
            capabilities=frozenset(item.strip() for item in capabilities if item.strip()),
            bound_user=(str(raw.get("bound_user") or "").strip() or None),
            allowed_chat_ids=allowlists["allowed_chat_ids"],
            allowed_sender_ids=allowlists["allowed_sender_ids"],
        )


@dataclass(slots=True)
class RegisteredTransport:
    transport: Transport
    policy: TransportPolicy
    state: str = "registered"
    last_error: dict[str, str] | None = None


class TransportRegistry:
    def __init__(self) -> None:
        self._items: dict[str, RegisteredTransport] = {}
        self._lock = threading.RLock()

    def register(
        self, transport: Transport, policy: TransportPolicy | None = None
    ) -> RegisteredTransport:
        name = str(getattr(transport, "name", "")).strip().lower()
        if not name:
            raise TransportRegistrationError("Transport name 不能为空")
        if not isinstance(transport, Transport):
            raise TransportRegistrationError(f"Transport {name!r} 未实现完整协议")
        effective_policy = policy or TransportPolicy(
            capabilities=frozenset(transport.capabilities)
        )
        unsupported = effective_policy.capabilities - frozenset(transport.capabilities)
        if unsupported:
            raise TransportRegistrationError(
                f"Transport {name!r} 不支持声明能力：{', '.join(sorted(unsupported))}"
            )
        with self._lock:
            if name in self._items:
                raise TransportRegistrationError(f"Transport 已注册：{name}")
            item = RegisteredTransport(
                transport=transport,
                policy=effective_policy,
            )
            self._items[name] = item
            return item

    def get(self, name: str) -> RegisteredTransport:
        key = name.strip().lower()
        with self._lock:
            item = self._items.get(key)
            if item is None:
                raise TransportRegistrationError(f"未知 Transport：{key}")
            return item

    def items(self) -> list[RegisteredTransport]:
        with self._lock:
            return list(self._items.values())

    def names(self) -> list[str]:
        with self._lock:
            return list(self._items)

    def unregister(self, name: str) -> RegisteredTransport:
        key = name.strip().lower()
        with self._lock:
            item = self._items.pop(key, None)
            if item is None:
                raise TransportRegistrationError(f"未知 Transport：{key}")
            return item


class MockTransport:
    """In-memory transport used by tests and local routing diagnostics."""

    name = "mock"
    capabilities = frozenset({"receive_text", "send_text"})

    def __init__(self, *, fail_start: bool = False, fail_send: bool = False) -> None:
        self.fail_start = fail_start
        self.fail_send = fail_send
        self.sent: list[OutboundMessage] = []
        self._on_message: InboundCallback | None = None
        self._on_error: ErrorCallback | None = None
        self._running = False
        self._lock = threading.RLock()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def start(self, on_message: InboundCallback, on_error: ErrorCallback) -> None:
        with self._lock:
            if self.fail_start:
                raise TransportError("MockTransport start failure")
            self._on_message = on_message
            self._on_error = on_error
            self._running = True

    def emit(self, envelope: MessageEnvelope) -> None:
        with self._lock:
            callback = self._on_message
            running = self._running
        if not running or callback is None:
            raise TransportError("MockTransport 未运行")
        callback(envelope)

    def send(self, message: OutboundMessage) -> bool:
        with self._lock:
            if not self._running:
                raise TransportError("MockTransport 未运行")
            if self.fail_send:
                raise TransportError("MockTransport send failure")
            self.sent.append(message)
            return True

    def stop(self) -> None:
        with self._lock:
            self._running = False
            self._on_message = None
            self._on_error = None
