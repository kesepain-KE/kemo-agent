"""Asset resolution boundary used by provider adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from provider.protocol.errors import AssetNotFoundError
from provider.protocol.models import MediaSource


@dataclass(frozen=True, slots=True)
class ResolvedAsset:
    asset_id: str
    mime_type: str
    size: int
    source: MediaSource
    filename: str = ""


class AssetResolver(Protocol):
    def resolve(self, asset_id: str, *, provider: str) -> ResolvedAsset: ...


class MissingAssetResolver:
    """Default resolver that refuses to fabricate unresolved media."""

    def resolve(self, asset_id: str, *, provider: str) -> ResolvedAsset:
        raise AssetNotFoundError(
            f"Asset 尚未接入解析器：{asset_id}",
            details={"asset_id": asset_id, "provider": provider},
        )
