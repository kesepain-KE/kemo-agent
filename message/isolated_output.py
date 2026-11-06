"""Export only a validated folder transport's outbound interface to a worker."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from message.plugin_contracts import MessagePluginConfig, _load_module
from message.transport import TransportPolicy, TransportRegistry


@dataclass(frozen=True, slots=True)
class OutboundSnapshot:
    config: MessagePluginConfig
    policy: TransportPolicy


def prepare_outbound(context: dict[str, Any], platform: str):
    """Check the live host registration before crossing the process boundary."""
    from message.plugin import FileMessageTransport

    registry = context.get("transport_registry")
    if not isinstance(registry, TransportRegistry):
        raise RuntimeError("当前运行上下文未提供外部消息 TransportRegistry")
    registered = registry.get(platform)
    transport = registered.transport
    if registered.state != "running" or not transport.running:
        raise RuntimeError("外部消息平台未运行")
    if registered.policy.bound_user and registered.policy.bound_user != context.get("user"):
        raise PermissionError("外部消息平台未绑定当前用户")
    if not isinstance(transport, FileMessageTransport):
        raise RuntimeError("该外部 Transport 尚无进程隔离出口，不能降级为线程发送")
    return OutboundSnapshot(transport.config, registered.policy), transport


class IsolatedOutboundTransport:
    """Output only: never start inbound polling, WebSocket, or host services."""

    def __init__(self, snapshot: OutboundSnapshot):
        self.snapshot = snapshot

    def send(self, message):
        config = self.snapshot.config
        output = _load_module(config.module_path("output"), config.machine_id, "output")
        return output.send({
            "chat_type": message.chat_type, "external_chat_id": message.external_chat_id,
            "text": message.text, "file_path": message.file_path, "reply_to": message.reply_to,
        }) is True
