"""平台中立的入站和出站消息合同。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
import uuid


ChatType = Literal["private", "group", "channel"]
_VALID_CHAT_TYPES = frozenset({"private", "group", "channel"})


class MessageContractError(ValueError):
    """A message payload does not satisfy the transport-neutral contract."""


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MessageContractError(f"{name} 必须是非空字符串")
    return value.strip()


def _iso_timestamp(value: str) -> str:
    raw = _required_text(value, "timestamp")
    candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise MessageContractError(f"timestamp 不是有效 ISO 时间：{value!r}") from exc
    if parsed.tzinfo is None:
        raise MessageContractError("timestamp 必须包含时区")
    return parsed.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class MessageEnvelope:
    message_id: str
    platform: str
    chat_type: ChatType
    external_user_id: str
    external_chat_id: str
    text: str
    timestamp: str
    attachments: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "message_id", _required_text(self.message_id, "message_id"))
        object.__setattr__(self, "platform", _required_text(self.platform, "platform").lower())
        if self.chat_type not in _VALID_CHAT_TYPES:
            raise MessageContractError(f"chat_type 无效：{self.chat_type!r}")
        object.__setattr__(
            self, "external_user_id", _required_text(self.external_user_id, "external_user_id")
        )
        object.__setattr__(
            self, "external_chat_id", _required_text(self.external_chat_id, "external_chat_id")
        )
        object.__setattr__(self, "text", _required_text(self.text, "text"))
        object.__setattr__(self, "timestamp", _iso_timestamp(self.timestamp))
        if not isinstance(self.attachments, tuple) or not all(
            isinstance(item, dict) for item in self.attachments
        ):
            raise MessageContractError("attachments 必须是对象元组")
        if not isinstance(self.metadata, dict):
            raise MessageContractError("metadata 必须是对象")

    @property
    def dedupe_key(self) -> str:
        return f"{self.platform}:{self.message_id}"

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MessageEnvelope":
        if not isinstance(value, dict):
            raise MessageContractError("入站消息必须是对象")
        return cls(
            message_id=value.get("message_id", ""),
            platform=value.get("platform", ""),
            chat_type=value.get("chat_type", ""),
            external_user_id=value.get("external_user_id", ""),
            external_chat_id=value.get("external_chat_id", ""),
            text=value.get("text", ""),
            timestamp=value.get("timestamp", ""),
            attachments=tuple(value.get("attachments") or ()),
            metadata=dict(value.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "platform": self.platform,
            "chat_type": self.chat_type,
            "external_user_id": self.external_user_id,
            "external_chat_id": self.external_chat_id,
            "text": self.text,
            "timestamp": self.timestamp,
            "attachments": [dict(item) for item in self.attachments],
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    message_id: str
    platform: str
    chat_type: ChatType
    external_chat_id: str
    text: str
    reply_to: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "message_id", _required_text(self.message_id, "message_id"))
        object.__setattr__(self, "platform", _required_text(self.platform, "platform").lower())
        if self.chat_type not in _VALID_CHAT_TYPES:
            raise MessageContractError(f"chat_type 无效：{self.chat_type!r}")
        object.__setattr__(
            self, "external_chat_id", _required_text(self.external_chat_id, "external_chat_id")
        )
        object.__setattr__(self, "text", _required_text(self.text, "text"))
        if not isinstance(self.metadata, dict):
            raise MessageContractError("metadata 必须是对象")

    @classmethod
    def reply(
        cls,
        envelope: MessageEnvelope,
        text: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> "OutboundMessage":
        return cls(
            message_id=f"out_{uuid.uuid4().hex[:12]}",
            platform=envelope.platform,
            chat_type=envelope.chat_type,
            external_chat_id=envelope.external_chat_id,
            text=text,
            reply_to=envelope.message_id,
            metadata=dict(metadata or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "platform": self.platform,
            "chat_type": self.chat_type,
            "external_chat_id": self.external_chat_id,
            "text": self.text,
            "reply_to": self.reply_to,
            "metadata": dict(self.metadata),
        }
