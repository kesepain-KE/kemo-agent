"""平台中立消息子系统的公共表面。"""

from message.identity import IdentityBinding, IdentityError, IdentityResolver
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
    "MessageContractError",
    "MessageEnvelope",
    "OutboundMessage",
    "MockTransport",
    "Transport",
    "TransportError",
    "TransportPolicy",
    "TransportRegistry",
]
