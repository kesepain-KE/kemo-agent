"""主动向运行中的外部消息 Transport 发送文本或文件。"""

from __future__ import annotations

from message.schema import ChatType

from pathlib import Path
import os
from typing import Any
import uuid

from message.schema import OutboundMessage
from message.transport import TransportRegistry
from run.infra import record_runtime_event
from run.extensions import _is_link_or_junction
from run.retry import HOST_CONTROL_EXCEPTIONS


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
        from message.isolated_output import OutboundSnapshot, IsolatedOutboundTransport
        snapshot = context.get("_outbound_snapshot")
        if not isinstance(snapshot, OutboundSnapshot) or snapshot.config.platform != platform:
            raise RuntimeError("当前运行上下文未提供外部消息 TransportRegistry")
        if snapshot.policy.bound_user and snapshot.policy.bound_user != context.get("user"):
            raise PermissionError("外部消息平台未绑定当前用户")
        if capability not in snapshot.policy.capabilities:
            raise PermissionError(f"外部消息平台未声明 {capability} 能力")
        return IsolatedOutboundTransport(snapshot)

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


def _checked_outbound_file(raw: str, context: dict[str, Any]) -> Path:
    root = Path(str(context["root"])).resolve()
    user = _required_text(context.get("user"), "context.user")
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise ValueError("file_path 必须是绝对路径")
    candidate = Path(os.path.abspath(candidate))
    allowed = (root / "users" / user / "download", root / "users" / user / "file_upload", root / "tmp")
    try:
        if not any(candidate.is_relative_to(directory) for directory in allowed):
            raise PermissionError("文件外发仅限当前用户 download/file_upload 和项目 tmp")
        for ancestor in (candidate, *candidate.parents):
            if _is_link_or_junction(ancestor):
                raise PermissionError("文件外发路径不得包含符号链接或目录联接")
        resolved = candidate.resolve(strict=True)
        if not any(resolved.is_relative_to(directory) for directory in allowed):
            raise PermissionError("文件外发路径超出授权目录")
        if not resolved.is_file():
            raise ValueError("待发送路径不是普通文件")
        return resolved
    except PermissionError:
        record_runtime_event(root, user, category="backend", name="external_message_file_denied", status="failed")
        raise


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
        normalized_file = str(_checked_outbound_file(_required_text(file_path, "file_path"), context))

    if normalized_chat_type not in ("private", "group", "channel"):
        raise ValueError("Invalid chat type")
    validated_chat_type: ChatType = normalized_chat_type
    outbound = OutboundMessage(
        message_id=f"out_{uuid.uuid4().hex[:12]}",
        platform=normalized_platform,
        chat_type=validated_chat_type,
        external_chat_id=normalized_target,
        text=normalized_message,
        file_path=normalized_file,
        reply_to="",
        metadata={"initiated_by": "external_message", "user": context["user"]},
    )
    try:
        receipt = transport.send(outbound)
    except HOST_CONTROL_EXCEPTIONS:
        raise
    except Exception:
        # The remote side may already have accepted the message.
        receipt = None
    if receipt is not True and not (isinstance(receipt, dict) and receipt.get("ok") is True):
        return {
            "ok": False, "action": normalized_action, "message_id": outbound.message_id,
            "error": {
                "code": "send_unconfirmed", "retryable": False,
                "message": "发送未得到显式成功确认；发送可能已经发生，请先核对目标会话，勿自动重复发送。",
            },
        }
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
