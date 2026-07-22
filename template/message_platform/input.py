"""{{PLATFORM}} 消息接收适配器。

start() 在传输启动时调用，负责持续接收消息并写入 buffer 文件。
"""

from __future__ import annotations

from typing import Any


def start(
    raw_config: dict[str, Any],
    buffer_path: str,
    files_dir: str,
    state_path: str,
) -> None:
    """启动消息接收循环。

    Args:
        raw_config: message.json 的完整内容
        buffer_path: message.md 缓冲文件的绝对路径
        files_dir: 附件目录的绝对路径
        state_path: state.json 状态文件的绝对路径
    """
    # TODO: 对接实际平台 SDK，接收消息后写入 buffer_path。
    # 消息格式参考 message/plugin.py 的 parse_message_buffer()。
    raise NotImplementedError("input.start() 需要实现")


def stop() -> None:
    """停止消息接收。"""

