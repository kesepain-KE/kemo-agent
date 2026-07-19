"""Unified protocol and adapter errors."""

from __future__ import annotations

from typing import Any


class ProtocolError(RuntimeError):
    code = "PROTOCOL_ERROR"

    def __init__(
        self,
        message: str,
        *,
        path: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.path = path
        self.details = dict(details or {})


class UnsupportedProtocolVersion(ProtocolError):
    code = "UNSUPPORTED_PROTOCOL_VERSION"


class ProtocolValidationError(ProtocolError):
    code = "VALIDATION_ERROR"


class ToolLinkageError(ProtocolError):
    code = "TOOL_LINKAGE_ERROR"


class CapabilityError(ProtocolError):
    code = "CAPABILITY_ERROR"


class AssetError(ProtocolError):
    code = "ASSET_ERROR"


class AssetNotFoundError(AssetError):
    code = "ASSET_NOT_FOUND"


class StreamProtocolError(ProtocolError):
    code = "STREAM_PROTOCOL_ERROR"
