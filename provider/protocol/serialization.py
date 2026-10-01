"""Versioned JSON serialization helpers for the unified protocol."""

from __future__ import annotations

import hashlib
import json
from typing import Any, TypeVar

from pydantic import BaseModel, TypeAdapter, ValidationError

from provider.protocol.errors import ProtocolValidationError
from provider.protocol.models import (
    KemoRequest,
    KemoResponse,
    PROTOCOL_VERSION,
    ResponseEnvelope,
)


T = TypeVar("T", bound=BaseModel)


def to_json_bytes(value: BaseModel) -> bytes:
    return value.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8")


def to_json_dict(value: BaseModel) -> dict[str, Any]:
    return value.model_dump(mode="json", by_alias=True, exclude_none=True)


def _parse(model: type[T], value: bytes | str | dict[str, Any]) -> T:
    try:
        if isinstance(value, dict):
            return model.model_validate(value)
        # Pydantic's JSON parser intentionally follows the permissive JSON
        # object semantics and keeps the last value for duplicate keys.  Kemo
        # wire JSON is an idempotency boundary, so silently accepting two
        # spellings of the same field would make the request fingerprint
        # ambiguous.  Do a small preflight only for duplicate-key detection;
        # actual validation still uses Pydantic's JSON path, with strict=True
        # so JSON strings/numbers/bools cannot silently change wire types.
        _validate_json_syntax(value)
        return model.model_validate_json(value, strict=True)
    except ValidationError as exc:
        first = exc.errors(include_url=False)[0] if exc.errors() else {}
        path = ".".join(str(item) for item in first.get("loc", ()))
        raise ProtocolValidationError(
            str(first.get("msg") or "协议 JSON 校验失败"),
            path=path,
            details={"errors": exc.errors(include_url=False)},
        ) from exc
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ProtocolValidationError(
            str(exc) or "协议 JSON 解析失败",
            details={"kind": "invalid_json"},
        ) from exc


def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"重复 JSON key：{key}")
        result[key] = value
    return result


def _reject_non_finite_json_constant(value: str) -> Any:
    raise ValueError(f"JSON 不允许非有限数值：{value}")


def _validate_json_syntax(value: bytes | str) -> None:
    json.loads(
        value,
        object_pairs_hook=_reject_duplicate_object_keys,
        parse_constant=_reject_non_finite_json_constant,
    )


def request_fingerprint(value: KemoRequest | BaseModel | bytes | str | dict[str, Any]) -> str:
    """Return the canonical Kemo idempotency fingerprint for a request.

    The request is parsed through the protocol model first, which fills model
    defaults and normalizes enum/date values.  Wire aliases are emitted,
    model fields with ``None`` are omitted, while ``null`` values nested in
    user-owned dictionaries remain significant.  Sorting object keys and
    compact UTF-8 JSON makes the digest stable across the Agent and Gateway
    repositories and across operating systems.
    """

    if isinstance(value, BaseModel) and not isinstance(value, KemoRequest):
        canonical_value = value.model_dump(mode="json", by_alias=True, exclude_none=True)
    else:
        request = value if isinstance(value, KemoRequest) else _parse(KemoRequest, value)
        canonical_value = request.model_dump(mode="json", by_alias=True, exclude_none=True)
    canonical = json.dumps(
        canonical_value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def parse_request(value: bytes | str | dict[str, Any]) -> KemoRequest:
    return _parse(KemoRequest, value)


def parse_response(value: bytes | str | dict[str, Any]) -> KemoResponse:
    return _parse(KemoResponse, value)


def parse_response_envelope(
    value: bytes | str | dict[str, Any],
) -> ResponseEnvelope:
    try:
        adapter = TypeAdapter(ResponseEnvelope)
        if isinstance(value, dict):
            return adapter.validate_python(value)
        _validate_json_syntax(value)
        return adapter.validate_json(value, strict=True)
    except ValidationError as exc:
        first = exc.errors(include_url=False)[0] if exc.errors() else {}
        path = ".".join(str(item) for item in first.get("loc", ()))
        raise ProtocolValidationError(
            str(first.get("msg") or "协议 JSON 校验失败"),
            path=path,
            details={"errors": exc.errors(include_url=False)},
        ) from exc
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ProtocolValidationError(
            str(exc) or "协议 JSON 解析失败",
            details={"kind": "invalid_json"},
        ) from exc


def current_protocol_version() -> str:
    return PROTOCOL_VERSION
