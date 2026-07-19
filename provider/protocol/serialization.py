"""Versioned JSON serialization helpers for the unified protocol."""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from provider.protocol.errors import ProtocolValidationError
from provider.protocol.models import KemoRequest, KemoResponse, PROTOCOL_VERSION


T = TypeVar("T", bound=BaseModel)


def to_json_bytes(value: BaseModel) -> bytes:
    return value.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8")


def to_json_dict(value: BaseModel) -> dict[str, Any]:
    return value.model_dump(mode="json", by_alias=True, exclude_none=True)


def _parse(model: type[T], value: bytes | str | dict[str, Any]) -> T:
    try:
        if isinstance(value, dict):
            return model.model_validate(value)
        return model.model_validate_json(value)
    except ValidationError as exc:
        first = exc.errors(include_url=False)[0] if exc.errors() else {}
        path = ".".join(str(item) for item in first.get("loc", ()))
        raise ProtocolValidationError(
            str(first.get("msg") or "协议 JSON 校验失败"),
            path=path,
            details={"errors": exc.errors(include_url=False)},
        ) from exc


def parse_request(value: bytes | str | dict[str, Any]) -> KemoRequest:
    return _parse(KemoRequest, value)


def parse_response(value: bytes | str | dict[str, Any]) -> KemoResponse:
    return _parse(KemoResponse, value)


def current_protocol_version() -> str:
    return PROTOCOL_VERSION
