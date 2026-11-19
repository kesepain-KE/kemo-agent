"""Status budgets and execution share the same bounded capability evidence."""
from types import SimpleNamespace

import pytest

from provider.protocol.models import ModelCapabilities
from provider.schema import ProviderError
from run.extensions import model_capabilities as cache


@pytest.mark.parametrize("capabilities_url", [None, "/model/models/shared-model/capabilities"])
def test_status_preserves_runtime_budget_during_bounded_stale_recovery(monkeypatch, capabilities_url):
    clock = [0.0]
    monkeypatch.setattr(cache, "_capability_cache", {})
    monkeypatch.setattr(cache, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    runtime = {
        "type": "kemo", "base_url": "https://gateway.example.test",
        "api_key": "test-only", "model": "shared-model",
    }
    declared = ModelCapabilities.model_validate({
        "protocol_version": "2.0", "model": "shared-model",
        "provider_id": "test", "provider_model": "upstream",
        "limits": {
            "context_window": 1000000, "max_input_tokens": 500000,
            "max_output_tokens": 384000,
        },
    })
    calls = []

    class Provider:
        def capabilities(self, model, **kwargs):
            calls.append((model, kwargs))
            if clock[0] > 0:
                raise ProviderError("temporarily unavailable", category="transport_error")
            return declared

    provider = Provider()
    lookup = cache.lookup_model_capabilities({}, runtime, provider, capabilities_url=capabilities_url)
    assert lookup.capabilities == declared
    clock[0] = 301
    recovered = cache.lookup_model_capabilities({}, runtime, provider, capabilities_url=capabilities_url)
    assert recovered.stale and recovered.error is not None
    status = cache.cached_model_capabilities(runtime)
    assert cache.context_input_budget(1000000, status, output_reserve=10000) == (
        cache.context_input_budget(1000000, recovered.capabilities, output_reserve=10000)
    )
    assert cache.model_output_token_limit(status) == 384000
    assert cache.cached_model_capabilities({**runtime, "base_url": "https://other.example.test"}) is None
    assert len(calls) == 2  # Status reads do not make network requests.

    clock[0] = 1801
    assert cache.cached_model_capabilities(runtime) is None
    expired = cache.lookup_model_capabilities({}, runtime, provider, capabilities_url=capabilities_url)
    assert expired.capabilities is None and not expired.stale
