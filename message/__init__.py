"""平台中立消息子系统的公共表面。"""

from message.identity import IdentityBinding, IdentityError, IdentityResolver
from message.plugin import (
    FileMessageTransport,
    MessagePluginConfig,
    MessagePluginError,
    discover_message_plugins,
    parse_message_buffer,
)
from message.schema import MessageContractError, MessageEnvelope, OutboundMessage
from message.transport import (
    MockTransport,
    Transport,
    TransportError,
    TransportPolicy,
    TransportRegistry,
)

__all__ = [
    "IdentityBinding",
    "IdentityError",
    "IdentityResolver",
    "FileMessageTransport",
    "MessagePluginConfig",
    "MessagePluginError",
    "discover_message_plugins",
    "parse_message_buffer",
    "MessageContractError",
    "MessageEnvelope",
    "OutboundMessage",
    "MockTransport",
    "Transport",
    "TransportError",
    "TransportPolicy",
    "TransportRegistry",
]
