"""Kemo 网关聊天提供商。

Kemo 公开了 OpenAI 聊天完成有线格式。  这个子类保留了
运行时类型显式，是未来仅 Kemo 令牌的扩展点，
能力和路由端点。"""

from __future__ import annotations

from typing import Any

from provider.openai_chat import OpenAIChatProvider


class KemoGatewayProvider(OpenAIChatProvider):
    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config=config, mode="kemo")
