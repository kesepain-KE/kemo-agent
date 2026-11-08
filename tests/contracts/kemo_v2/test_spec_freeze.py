from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import provider.protocol.spec.generate as spec_generator
from provider.protocol.models import (
    AudioContent,
    EmbeddingRequest,
    ImageContent,
    KemoRequest,
    KemoResponse,
    KemoResponseBatch,
    MessageItem,
    RerankRequest,
    UnifiedError,
)
from provider.protocol.spec.generate import ROOT


MODELS = {
    "AudioContent": AudioContent,
    "EmbeddingRequest": EmbeddingRequest,
    "ImageContent": ImageContent,
    "KemoRequest": KemoRequest,
    "KemoResponse": KemoResponse,
    "KemoResponseBatch": KemoResponseBatch,
    "MessageItem": MessageItem,
    "RerankRequest": RerankRequest,
    "UnifiedError": UnifiedError,
}


def _fixture(name: str) -> dict:
    return json.loads((ROOT / "fixtures" / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "name",
    [
        "minimal-request.json",
        "text-response.json",
        "refusal-response.json",
        "tool-loop.json",
        "two-choice-batch.json",
        "inflight-response.json",
        "embedding.json",
        "rerank.json",
        "asset.json",
        "error.json",
    ],
)
def test_positive_fixtures_round_trip(name: str) -> None:
    fixture = _fixture(name)
    model = MODELS[fixture["model"]]
    value = model.model_validate(fixture["payload"])
    round_trip = model.model_validate_json(value.model_dump_json(by_alias=True))
    assert round_trip.model_dump(mode="json", by_alias=True) == value.model_dump(
        mode="json", by_alias=True
    )


@pytest.mark.parametrize(
    "name",
    [
        "invalid-version.json",
        "invalid-tool-choice.json",
        "custom-tool.json",
        "invalid-batch.json",
        "empty-final-message.json",
        "invalid-inline-audio.json",
        "rerank-invalid-top-n.json",
    ],
)
def test_negative_fixtures_fail_the_named_model(name: str) -> None:
    fixture = _fixture(name)
    with pytest.raises(ValidationError):
        MODELS[fixture["model"]].model_validate(fixture["payload"])


def test_schema_generation_matches_frozen_contract_without_rewriting_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(spec_generator, "ROOT", tmp_path)
    filenames = ("schema.json", "model-index.json", "fixture-manifest.json", "freeze.json")
    first = spec_generator.generate()
    first_files = {name: (tmp_path / name).read_bytes() for name in filenames}
    second = spec_generator.generate()
    assert first == second
    for name, data in first_files.items():
        assert data == (tmp_path / name).read_bytes()
        assert data == (ROOT / name).read_bytes(), f"Frozen protocol drift: {name}"
    assert first["schema_sha256"] == hashlib.sha256(first_files["schema.json"]).hexdigest()


def test_model_index_covers_every_root_contract_and_nested_symbol() -> None:
    index = json.loads((ROOT / "model-index.json").read_text(encoding="utf-8"))
    symbols = {entry["symbol"] for entry in index}
    required_roots = {
        "AssetDescriptor",
        "ErrorEnvelope",
        "EmbeddingRequest",
        "EmbeddingResponse",
        "RerankRequest",
        "RerankResponse",
        "ModelCapabilities",
        "ModelCatalogResponse",
        "KemoRequest",
        "KemoResponse",
        "KemoResponseBatch",
        "ProviderStreamEvent",
        "ContentBlock",
        "Item",
        "ItemStart",
        "ResponseEnvelope",
    }
    assert required_roots <= symbols
    assert {"AssetDescriptor", "ErrorEnvelope", "ProviderStreamEvent"} <= symbols
    assert any(
        entry["symbol"] == "AssetDescriptor" and entry["wire_name"] == "checksum_sha256"
        for entry in index
    )
    assert any(
        entry["symbol"] == "ProviderStreamEvent" and entry["wire_name"] == "previous_sequence"
        for entry in index
    )
