"""Sub-agent requests obey the same complete-input/output capacity contract."""
import json

import pytest

from provider.protocol.models import KemoResponse, MessageItem, ModelCapabilities, ToolCallItem
from run.agents import AgentProviderError, AgentRunner
from run.cards import estimate_request_tokens
from tests.agents.test_general_worker import OUTPUT, plugin, workspace


class Provider:
    def __init__(self, *, invalid_first=False):
        self.requests = []
        self.invalid_first = invalid_first

    def capabilities(self, model, **kwargs):
        return ModelCapabilities.model_validate({
            "protocol_version": "2.0", "model": model, "provider_id": "capacity-test",
            "provider_model": model, "input_modalities": ["text"],
            "limits": {"max_input_tokens": 20000, "context_window": 20512,
                       "max_output_tokens": 512},
        })

    def create(self, request):
        self.requests.append(request)
        if self.invalid_first and len(self.requests) == 1:
            output = [ToolCallItem(
                id="call_invalid", call_id="callid_invalid", name="check",
                arguments={"unknown": "schema rejects this"},
            )]
        else:
            output = [MessageItem.text("assistant", json.dumps(OUTPUT))]
        return KemoResponse(
            request_id=request.request_id, model=request.model, status="completed", output=output,
        )


@pytest.fixture(autouse=True)
def isolated_capability_evidence(monkeypatch):
    monkeypatch.setattr("run.extensions.model_capabilities._capability_cache", {})


def test_subagent_uses_its_models_actual_output_cap(workspace):
    root, config = workspace
    config["agents"] = {"token_limit": 30000, "output_reserve": 8192}
    provider = Provider()
    runner = AgentRunner(root, "alice", config=config, provider_factory=lambda _: provider)
    assert runner.run("general_worker", {"task": "check"}).data == OUTPUT
    request = provider.requests[0]
    assert request.generation.max_output_tokens == 512
    assert estimate_request_tokens(request.system_prompt, request.input, request.tools) <= 20000


def test_subagent_rejects_complete_input_before_contacting_provider(workspace):
    root, config = workspace
    provider = Provider()
    runner = AgentRunner(root, "alice", config=config, provider_factory=lambda _: provider)
    with pytest.raises(AgentProviderError, match="完整请求超过输入预算"):
        runner.run("general_worker", {"task": "check " * 20000}, input_token_limit=4096)
    assert not provider.requests


def test_argument_repair_is_rechecked_against_the_full_input_limit(workspace):
    root, config = workspace
    plugin(root, "check")
    baseline = Provider(invalid_first=True)
    runner = AgentRunner(root, "alice", config=config, provider_factory=lambda _: baseline)
    assert runner.run("general_worker", {"task": "check"}).data == OUTPUT
    assert len(baseline.requests) == 2
    initial = baseline.requests[0]
    measured = estimate_request_tokens(initial.system_prompt, initial.input, initial.tools)
    repaired = baseline.requests[1]
    assert estimate_request_tokens(repaired.system_prompt, repaired.input, repaired.tools) > measured + 16
    provider = Provider(invalid_first=True)
    runner = AgentRunner(root, "alice", config=config, provider_factory=lambda _: provider)
    with pytest.raises(AgentProviderError, match="完整请求超过输入预算"):
        runner.run("general_worker", {"task": "check"}, input_token_limit=measured + 16)
    assert len(provider.requests) == 1
