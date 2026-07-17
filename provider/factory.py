"""供应商工厂。"""

from __future__ import annotations

from typing import Any

from provider.kemo_gateway import KemoGatewayProvider
from provider.openai_chat import OpenAIChatProvider
from provider.schema import ChatProvider


def create_provider(config: dict[str, Any]) -> ChatProvider:
    provider_type = str(config.get("type") or "").strip().lower()
    if provider_type == "openai":
        return OpenAIChatProvider(config=config, mode="openai")
    if provider_type == "kemo":
        return KemoGatewayProvider(config=config)
    raise ValueError(f"不支持的 provider.type：{provider_type!r}")
