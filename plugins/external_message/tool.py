"""主动向运行中的外部消息 Transport 发送文本或文件。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import uuid

from message.schema import OutboundMessage
from message.transport import TransportRegistry


_ACTIONS = frozenset({"send_message", "send_file"})
_CHAT_TYPES = frozenset({"private", "group"})


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串")
    return value.strip()


def _registered_transport(
    platform: str,
    *,
    context: dict[str, Any],
    capability: str,
) -> Any:
    registry = context.get("transport_registry")
    if not isinstance(registry, TransportRegistry):
        raise RuntimeError("当前运行上下文未提供外部消息 TransportRegistry")

    registered = registry.get(platform)
    current_user = _required_text(context.get("user"), "context.user")
    bound_user = registered.policy.bound_user
    if bound_user and bound_user != current_user:
        raise PermissionError(
            f"外部消息平台 {platform!r} 未绑定当前用户 {current_user!r}"
        )
    if capability not in registered.policy.capabilities:
        raise PermissionError(
            f"外部消息平台 {platform!r} 未声明 {capability} 能力"
        )

    transport = registered.transport
    if registered.state != "running" or not transport.running:
        raise RuntimeError(f"外部消息平台 {platform!r} 未运行")
    return transport


def run(
    action: str,
    platform: str,
    target: str,
    chat_type: str,
    message: str = "",
    file_path: str = "",
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    """执行主动外部消息发送。"""

    normalized_action = _required_text(action, "action")
    if normalized_action not in _ACTIONS:
        raise ValueError(f"不支持的 action：{normalized_action}")
    normalized_platform = _required_text(platform, "platform").lower()
    normalized_target = _required_text(target, "target")
    normalized_chat_type = _required_text(chat_type, "chat_type").lower()
    if normalized_chat_type not in _CHAT_TYPES:
        raise ValueError("chat_type 只能是 private 或 group")

    capability = "send_text" if normalized_action == "send_message" else "send_file"
    transport = _registered_transport(
        normalized_platform,
        context=context,
        capability=capability,
    )
    normalized_message = ""
    normalized_file = ""
    if normalized_action == "send_message":
        normalized_message = _required_text(message, "message")
    else:
        requested_path = Path(_required_text(file_path, "file_path"))
        if not requested_path.is_absolute():
            raise ValueError("file_path 必须是绝对路径")
        try:
            resolved_path = requested_path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise FileNotFoundError(f"待发送文件不存在：{requested_path}") from exc
        if not resolved_path.is_file():
            raise ValueError(f"待发送路径不是文件：{resolved_path}")
        normalized_file = str(resolved_path)

    outbound = OutboundMessage(
        message_id=f"out_{uuid.uuid4().hex[:12]}",
        platform=normalized_platform,
        chat_type=normalized_chat_type,
        external_chat_id=normalized_target,
        text=normalized_message,
        file_path=normalized_file,
        reply_to="",
        metadata={"initiated_by": "external_message", "user": context["user"]},
    )
    transport.send(outbound)
    result: dict[str, Any] = {
        "ok": True,
        "action": normalized_action,
        "message_id": outbound.message_id,
        "platform": normalized_platform,
        "target": normalized_target,
        "chat_type": normalized_chat_type,
    }
    if normalized_file:
        result["file"] = normalized_file
    return result
