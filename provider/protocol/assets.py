"""Asset resolution boundary used by provider adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from provider.protocol.errors import AssetNotFoundError
from provider.protocol.models import MediaSource


class AssetDescriptor(BaseModel):
    """Authenticated Kemo gateway Asset metadata."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: str = "1.0"
    id: str
    object: Literal["kemo.asset"] = "kemo.asset"
    status: Literal["uploading", "processing", "ready", "failed", "deleted"]
    purpose: Literal["input", "output"]
    filename: str
    mime_type: str
    size: int = Field(ge=0)
    checksum_sha256: str
    created_at: datetime | None = None
    expires_at: datetime | None = None
    error: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not value.startswith("asset_") or len(value) > 128:
            raise ValueError("Asset id 必须使用 asset_ 前缀且不超过 128 字符")
        return value

    @field_validator("checksum_sha256")
    @classmethod
    def validate_checksum(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
            raise ValueError("checksum_sha256 必须是 64 位十六进制 SHA-256")
        return normalized


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
