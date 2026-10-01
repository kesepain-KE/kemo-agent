"""Generate deterministic Kemo 2.0 S0 schema, model index and fixture digests."""

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from provider.protocol import models as protocol_models
from provider.protocol.models import ContentBlock, Item, PROTOCOL_VERSION, ProtocolModel, ResponseEnvelope
from provider.protocol.streaming import ItemStart, ProviderStreamEvent


ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
def _root_models() -> dict[str, Any]:
    """Return every authoritative public model and discriminated root alias.

    Keeping this derived from the protocol module prevents schema generation from
    silently omitting models that are not reachable from ``KemoRequest`` (assets,
    catalog, errors, retrieval responses, stream events, and batch envelopes).
    """

    roots: dict[str, Any] = {}
    for name, value in inspect.getmembers(protocol_models, inspect.isclass):
        if (
            value.__module__ == protocol_models.__name__
            and issubclass(value, ProtocolModel)
            and value not in {protocol_models.ProtocolModel, protocol_models.ExtensionModel}
        ):
            roots[name] = value
    roots.update(
        {
            "ContentBlock": TypeAdapter(ContentBlock),
            "Item": TypeAdapter(Item),
            "ItemStart": TypeAdapter(ItemStart),
            "ProviderStreamEvent": ProviderStreamEvent,
            "ResponseEnvelope": TypeAdapter(ResponseEnvelope),
        }
    )
    return dict(sorted(roots.items()))


MODELS = _root_models()


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _schema(model: Any) -> dict[str, Any]:
    return model.json_schema() if isinstance(model, TypeAdapter) else model.model_json_schema()


def _definition_symbols() -> dict[str, dict[str, Any]]:
    symbols: dict[str, dict[str, Any]] = {}

    def add(symbol: str, definition: dict[str, Any]) -> None:
        previous = symbols.get(symbol)
        if previous is not None and _canonical(previous) != _canonical(definition):
            raise RuntimeError(f"协议 schema 中符号定义冲突：{symbol}")
        symbols[symbol] = definition

    for root_name, model in MODELS.items():
        schema = _schema(model)
        definitions = schema.pop("$defs", {})
        schema.setdefault("title", root_name)
        add(root_name, schema)
        for symbol, definition in definitions.items():
            add(symbol, definition)
    return dict(sorted(symbols.items()))


def _nullable(field: dict[str, Any]) -> bool:
    field_type = field.get("type")
    if field_type == "null" or (
        isinstance(field_type, list) and "null" in field_type
    ):
        return True
    return any(
        isinstance(branch, dict) and branch.get("type") == "null"
        for keyword in ("anyOf", "oneOf")
        for branch in field.get(keyword, [])
    )


def _model_index(symbols: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for symbol, definition in sorted(symbols.items()):
        required = set(definition.get("required", []))
        properties = definition.get("properties", {})
        if not properties:
            # Root discriminated aliases (ContentBlock/Item/ItemStart and
            # ResponseEnvelope) have no flat properties but are still public
            # contracts that must appear in the authoritative index.
            result.append(
                {
                    "symbol": symbol,
                    "wire_name": "",
                    "internal_name": "",
                    "required": False,
                    "nullable": False,
                    "default": "__model__",
                    "discriminator": definition.get("discriminator"),
                    "enum_source": None,
                    "constraints": {},
                    "authority": "kemo协议2.0-设计方案.md r2 / P.1",
                }
            )
            continue
        for wire_name, field in sorted(properties.items()):
            result.append(
                {
                    "symbol": symbol,
                    "wire_name": wire_name,
                    "internal_name": "schema_" if symbol == "StructuredOutputConfig" and wire_name == "schema" else wire_name,
                    "required": wire_name in required,
                    "nullable": _nullable(field),
                    "default": field.get("default", "__required__"),
                    "discriminator": definition.get("discriminator"),
                    "enum_source": field.get("$ref"),
                    "constraints": {key: field[key] for key in ("minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems", "pattern") if key in field},
                    "authority": "kemo协议2.0-设计方案.md r2 / P.1",
                }
            )
    return result


def _fixture_digest() -> tuple[str, list[dict[str, str]]]:
    entries: list[dict[str, str]] = []
    for path in sorted(FIXTURES.rglob("*.json"), key=lambda item: item.relative_to(FIXTURES).as_posix()):
        raw = path.read_bytes()
        entries.append({"path": path.relative_to(FIXTURES).as_posix(), "sha256": hashlib.sha256(raw).hexdigest()})
    manifest = _canonical(entries)
    return hashlib.sha256(manifest).hexdigest(), entries


def generate() -> dict[str, str]:
    ROOT.mkdir(parents=True, exist_ok=True)
    combined = {"protocol_version": PROTOCOL_VERSION, "models": {name: _schema(model) for name, model in sorted(MODELS.items())}}
    schema_bytes = _canonical(combined)
    (ROOT / "schema.json").write_bytes(schema_bytes)
    symbols = _definition_symbols()
    (ROOT / "model-index.json").write_bytes(_canonical(_model_index(symbols)))
    fixture_sha, entries = _fixture_digest()
    (ROOT / "fixture-manifest.json").write_bytes(_canonical(entries))
    freeze = {
        "protocol_version": PROTOCOL_VERSION,
        "freeze_revision": "2026-10-01-r3-s0",
        "schema_sha256": hashlib.sha256(schema_bytes).hexdigest(),
        "fixture_sha256": fixture_sha,
    }
    (ROOT / "freeze.json").write_bytes(_canonical(freeze))
    return freeze


if __name__ == "__main__":
    print(json.dumps(generate(), ensure_ascii=False, indent=2))
