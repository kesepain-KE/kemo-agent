from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from provider.adapters.gateway import KemoGatewayAdapter
from provider.protocol.assets import AssetDescriptor
from provider.protocol.errors import ProtocolValidationError
from provider.protocol.models import (
    EmbeddingRequest,
    EmbeddingResponse,
    KemoRequest,
    KemoResponse,
    ModelCapabilities,
    ModelCatalogResponse,
    RerankRequest,
    RerankResponse,
)
from provider.protocol.serialization import parse_request, parse_response, to_json_bytes
from provider.protocol.streaming import (
    ProviderStreamEvent,
    StreamSequenceGuard,
    encode_sse,
    parse_sse_events,
)
from tests.contracts.kemo_v1.fixture_loader import (
    load_bundle,
    materialize,
    stream_events,
    validate_stream_envelope,
)


BUNDLE = load_bundle()
MODELS = {
    "AssetDescriptor": AssetDescriptor,
    "EmbeddingRequest": EmbeddingRequest,
    "EmbeddingResponse": EmbeddingResponse,
    "KemoRequest": KemoRequest,
    "KemoResponse": KemoResponse,
    "ModelCapabilities": ModelCapabilities,
    "ModelCatalogResponse": ModelCatalogResponse,
    "RerankRequest": RerankRequest,
    "RerankResponse": RerankResponse,
    "SSEEvent": ProviderStreamEvent,
}


def _id(case: dict[str, object]) -> str:
    return str(case["id"])


@pytest.mark.parametrize("case", BUNDLE["valid_cases"], ids=_id)
def test_agent_models_accept_shared_valid_fixture(case) -> None:
    payload = materialize(BUNDLE, case)
    model = MODELS[case["model"]]
    parsed = model.model_validate(payload)
    wire = parsed.model_dump(mode="json", by_alias=True, exclude_none=True)
    encoded = json.dumps(wire, ensure_ascii=False, allow_nan=False).encode("utf-8")
    reparsed = model.model_validate_json(encoded)
    assert reparsed.model_dump(mode="json", by_alias=True, exclude_none=True) == wire


@pytest.mark.parametrize("case", BUNDLE["invalid_cases"], ids=_id)
def test_agent_models_reject_shared_invalid_fixture(case) -> None:
    payload = materialize(BUNDLE, case)
    with pytest.raises(ValidationError):
        MODELS[case["model"]].model_validate(payload)


@pytest.mark.parametrize("stream", BUNDLE["valid_streams"], ids=_id)
def test_agent_sse_parser_and_guard_accept_shared_stream(stream) -> None:
    payloads = stream_events(BUNDLE, stream)
    assert validate_stream_envelope(payloads) == stream["accepted_sequences"]
    frames = b"".join(encode_sse(ProviderStreamEvent.model_validate(item)) for item in payloads)
    parsed = list(parse_sse_events(frames.splitlines(keepends=True)))
    guard = StreamSequenceGuard()
    accepted = [event.sequence for event in parsed if guard.accept(event)]
    assert accepted == stream["accepted_sequences"]


@pytest.mark.parametrize("stream", BUNDLE["invalid_streams"], ids=_id)
def test_shared_stream_envelope_rejects_invalid_sequences(stream) -> None:
    with pytest.raises(ValueError):
        validate_stream_envelope(stream_events(BUNDLE, stream))


@pytest.mark.parametrize(
    "case",
    [case for case in BUNDLE["valid_cases"] if case["model"] == "KemoRequest"],
    ids=_id,
)
def test_gateway_adapter_accepts_every_shared_request_without_network(case) -> None:
    request = parse_request(materialize(BUNDLE, case))
    adapter = KemoGatewayAdapter({
        "base_url": "https://gateway.fixture.invalid",
        "api_key": "fixture-only",
        "model": request.model,
    })
    adapter.validate(request)
    assert parse_request(to_json_bytes(request)).request_id == request.request_id


@pytest.mark.parametrize(
    "case",
    [case for case in BUNDLE["invalid_cases"] if case["model"] == "KemoRequest"],
    ids=_id,
)
def test_agent_request_parser_reports_invalid_shared_request(case) -> None:
    with pytest.raises(ProtocolValidationError):
        parse_request(materialize(BUNDLE, case))


@pytest.mark.parametrize(
    "case",
    [case for case in BUNDLE["invalid_cases"] if case["model"] == "KemoResponse"],
    ids=_id,
)
def test_agent_response_parser_reports_invalid_shared_response(case) -> None:
    with pytest.raises(ProtocolValidationError):
        parse_response(materialize(BUNDLE, case))


def test_dynamic_reasoning_and_nullable_item_time_are_locked_by_fixture() -> None:
    request = parse_request(BUNDLE["documents"]["request_text"])
    response = parse_response(BUNDLE["documents"]["response_completed"])
    capabilities = ModelCapabilities.model_validate(BUNDLE["documents"]["capability_llm"])
    assert request.reasoning is not None and request.reasoning.effort == "adaptive-high"
    assert "adaptive-high" in capabilities.reasoning.efforts
    assert response.output[0].created_at is None


def test_agent_consumers_accept_same_major_future_minor_payloads() -> None:
    response_payload = materialize(BUNDLE, "response_completed")
    response_payload["protocol_version"] = "1.1"
    asset_payload = materialize(BUNDLE, "asset_descriptor")
    asset_payload["protocol_version"] = "1.1"
    assert parse_response(response_payload).protocol_version == "1.1"
    assert AssetDescriptor.model_validate(asset_payload).protocol_version == "1.1"
