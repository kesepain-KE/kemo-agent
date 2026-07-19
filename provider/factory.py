"""供应商工厂。"""

from __future__ import annotations

from typing import Any

from provider.adapters.base import ProviderAdapter
from provider.adapters.chat_bridge import ChatBridgeProvider
from provider.kemo_gateway import KemoGatewayProvider


def create_provider(config: dict[str, Any]) -> ProviderAdapter:
    provider_type = str(config.get("type") or "").strip().lower()
    if provider_type == "chat":
        return ChatBridgeProvider(config=config)
    if provider_type == "kemo":
        return KemoGatewayProvider(config=config)
    raise ValueError(f"不支持的 provider.type：{provider_type!r}")
