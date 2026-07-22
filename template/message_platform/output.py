"""{{PLATFORM}} 消息发送适配器。"""

from __future__ import annotations

from typing import Any


def send(payload: dict[str, Any]) -> bool:
    """发送消息到外部平台。

    Args:
        payload: 包含 chat_type、external_chat_id、text、file_path、reply_to

    Returns:
        True 表示发送成功，False 表示失败
    """
    # TODO: 对接实际平台 SDK，发送消息。
    raise NotImplementedError("output.send() 需要实现")
