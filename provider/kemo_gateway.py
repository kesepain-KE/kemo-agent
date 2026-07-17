"""Kemo gateway chat provider.

Kemo exposes the OpenAI Chat Completions wire format.  This subclass keeps the
runtime type explicit and is the extension point for future Kemo-only token,
capability and routing endpoints.
"""

from __future__ import annotations

from typing import Any

from provider.openai_chat import OpenAIChatProvider


class KemoGatewayProvider(OpenAIChatProvider):
    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config=config, mode="kemo")
