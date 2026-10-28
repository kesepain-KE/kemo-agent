"""1.5.2: one compression policy, exact-usage checkpoints, bounded summaries."""

from __future__ import annotations

import copy
import json
import os
from unittest.mock import patch

import pytest

from provider.schema import ChatResponse, ProviderError, Usage
from run.context import ContextPolicy, build_round_groups, select_context, get_or_create_summary, read_summary_cache
from run.context.summary import summary_source
from run.context.summary_budget import SummaryBudget
from run.context.usage import make_checkpoint, projected_input, usage_checkpoint
from run.engine import handle_request, iter_request_events
from run.history import find_window, load_window, load_runtime_window, commit_window, empty_window, runtime_window_path
from tests.core.test_context_lifecycle import make_window, make_summary_runtime, SummaryRunner
from tests.core.test_compression_recovery import runtime_root, summary_response
from tests.core.test_runtime_features import ScriptedProvider


@pytest.mark.parametrize("entry", ["manual", "token", "api", "round"])
def test_all_entries_stop_after_round_stage_when_it_solves_pressure(entry):
    window = make_window(30, chars=200)
    policy = ContextPolicy(max_rounds=30 if entry == "round" else 80,
                           rounds_after_compression=20, token_limit=10_248, compression_ratio=.3)
    result = select_context(
        window=window, policy=policy, system_message=None, current_user_message=None,
        force_compress=entry == "manual", force_token_compress=entry == "api",
    )
    assert len(result.kept_rounds) == 20
    assert result.estimated_tokens_after < policy.token_limit
    assert result.round_stage_applied and not result.token_stage_applied
    assert result.estimated_tokens_after > policy.input_budget


@pytest.mark.parametrize("manual", [False, True])
def test_token_fallback_when_round_stage_still_exceeds_capacity(manual):
    selected = select_context(
        window=make_window(30, chars=200),
        policy=ContextPolicy(max_rounds=80, rounds_after_compression=20, token_limit=4000, compression_ratio=.3),
        system_message=None, current_user_message=None, force_compress=manual,
    )
    assert selected.round_stage_applied and selected.token_stage_applied
    assert selected.estimated_tokens_after <= selected.input_budget
    assert len(selected.kept_rounds) < 20


def test_exact_usage_checkpoint_uses_last_request_not_cumulative_cost_or_cache_discount():
    provider = {"type": "chat", "model": "test"}
    checkpoint = make_checkpoint(1000, 800, provider, None)
    window = {"data": {"round_metrics": [{"usage": {"total_tokens": 90_000_000}, "context_usage": checkpoint}]}}
    assert usage_checkpoint(window, provider, None) == checkpoint
    assert projected_input(900, checkpoint) == 1100
    assert usage_checkpoint(window, {**provider, "model": "other"}, None) is None
    assert usage_checkpoint(window, provider, {"source_hash": "new"}) is None
    window["data"]["round_metrics"].append({"context_usage": None})
    assert usage_checkpoint(window, provider, None) is None


def test_large_single_round_is_fully_covered_by_bounded_fragments(tmp_path):
    path = make_summary_runtime(tmp_path)
    groups = build_round_groups(make_window(1, chars=20_000), ContextPolicy())
    before = copy.deepcopy(groups)
    runner = SummaryRunner()
    cache, diagnostics = get_or_create_summary(
        runtime_path=path, groups=groups, agent_runner=runner, agent_name="context_manage",
        trigger="token_limit", chunk_token_budget=4000,
    )
    assert cache is not None and not diagnostics["failed"]
    assert diagnostics["fragments"] > 1
    assert cache["covered_rounds"] == [1]
    assert groups == before
    budget = SummaryBudget(4000, 20_000)
    assert all(budget.fits(payload) for payload in runner.inputs)
    pieces = [entry for payload in runner.inputs for entry in payload["rounds"]]
    restored = "".join(piece["source_json_part"] for piece in pieces)
    assert json.loads(restored) == summary_source(groups)[0]
    assert pieces[-1]["fragment"]["final"]


@pytest.mark.parametrize("status,message", [(400, "prompt is too long"), (413, "request body too large")])
def test_summary_size_rejection_retries_smaller_input(tmp_path, status, message):
    class RejectOnce(SummaryRunner):
        attempts = 0

        def run(self, name, input_data, **kwargs):
            self.attempts += 1
            if self.attempts == 1:
                raise ProviderError(message, status_code=status)
            return super().run(name, input_data, **kwargs)

    runner = RejectOnce()
    groups = build_round_groups(make_window(1, chars=3000), ContextPolicy())
    cache, diagnostics = get_or_create_summary(
        runtime_path=make_summary_runtime(tmp_path), groups=groups,
        agent_runner=runner, agent_name="context_manage", trigger="api_context_length",
        chunk_token_budget=10_000,
    )
    assert cache is not None and not diagnostics["failed"]
    assert diagnostics["size_recoveries"] == 1
    assert cache["covered_rounds"] == [1]


def test_failed_fragment_preserves_old_summary_and_reports_safe_reason(tmp_path):
    path = make_summary_runtime(tmp_path)
    original = {"schema_version": 3, "source_hash": "old", "covered_rounds": [],
                "covered_through_round": 0, "summary": {"narrative": "previous"}}
    commit_window(path, empty_window("alice", "web", "summary-window"), summary_cache=original)

    class FailSecond(SummaryRunner):
        attempts = 0

        def run(self, name, input_data, **kwargs):
            self.attempts += 1
            if self.attempts == 2:
                raise ProviderError("private-provider-body-and-secret", status_code=401)
            return super().run(name, input_data, **kwargs)

    runner = FailSecond()
    cache, diagnostics = get_or_create_summary(
        runtime_path=path, groups=build_round_groups(make_window(1, chars=5000), ContextPolicy()),
        agent_runner=runner, agent_name="context_manage", trigger="manual",
        chunk_token_budget=2000, previous_cache=original,
    )
    assert cache is None and diagnostics["failed"]
    assert runner.attempts == 2
    assert diagnostics["failure_kind"] == "provider_error"
    assert "private-provider" not in json.dumps(diagnostics)
    assert read_summary_cache(path)["source_hash"] == "old"


def test_oversized_previous_summary_fails_without_truncating_or_invoking(tmp_path):
    runner = SummaryRunner()
    cache, diagnostics = get_or_create_summary(
        runtime_path=make_summary_runtime(tmp_path),
        groups=build_round_groups(make_window(1), ContextPolicy()), agent_runner=runner,
        agent_name="context_manage", trigger="manual", chunk_token_budget=1000,
        previous_cache={"summary": {"narrative": "旧" * 5000}},
    )
    assert cache is None and diagnostics["failure_kind"] == "fixed_content_over_budget"
    assert runner.calls == 0


def test_final_response_exact_usage_triggers_round_end_compression(runtime_root):
    root = runtime_root
    config_path = root / "config/global_config.json"
    config = json.loads(config_path.read_text("utf-8"))
    config["agents"] = {"enable_mid_context_compression": False, "max_rounds": 80,
                        "rounds_after_compression": 20, "token_limit": 1_000_000,
                        "token_compression_ratio": .3}
    config_path.write_text(json.dumps(config), "utf-8")
    provider = ScriptedProvider(responses=[
        ChatResponse(text="seed", usage=Usage(100, 10, 110, estimated=False)),
        ChatResponse(text="answer", usage=Usage(1_100_000, 10, 1_100_010, estimated=False)),
        summary_response(),
    ])
    request = dict(user="alice", source="web", session_id="exact-usage")
    with patch.dict(os.environ, {"TEST_KEMO_KEY": "test"}):
        handle_request({**request, "prompt": "seed"}, root=root, provider_factory=lambda _: provider)
        events = list(iter_request_events({**request, "prompt": "second"}, root=root, provider_factory=lambda _: provider))
    compression = [event for event in events if event.type == "context_compression"]
    assert any(event.metadata.get("trigger") == "provider_usage_limit" for event in compression)
    assert compression[-1].metadata["status"] == "ready"
    archive_path = find_window(root, "alice", "web", "exact-usage")
    assert archive_path is not None
    archive = load_window(archive_path)
    _, runtime = load_runtime_window(archive_path, archive)
    assert archive["data"]["rounds"] == 2 and runtime["data"]["rounds"] == 1
    assert archive["data"]["round_metrics"][-1]["context_usage"] is None


def test_failed_usage_compression_retries_next_turn_and_survives_cache_drop(runtime_root):
    from run.history import runtime_cache

    root = runtime_root
    request = dict(user="alice", source="web", session_id="usage-retry")
    provider = ScriptedProvider(responses=[
        ChatResponse(text="seed", usage=Usage(100, 1, 101, estimated=False)),
        ChatResponse(text="over", usage=Usage(1_100_000, 1, 1_100_001, estimated=False)),
        summary_response(), ChatResponse(text="recovered"),
    ])
    with patch.dict(os.environ, {"TEST_KEMO_KEY": "test"}):
        handle_request({**request, "prompt": "seed"}, root=root, provider_factory=lambda _: provider)
        with patch("run.conversation.runtime.get_or_create_summary", return_value=(None, {"failed": True, "error": "unavailable"})):
            handle_request({**request, "prompt": "over"}, root=root, provider_factory=lambda _: provider)
        archive_path = find_window(root, "alice", "web", "usage-retry")
        assert archive_path is not None
        archive = load_window(archive_path)
        assert archive["data"]["round_metrics"][-1]["context_usage"]["input_tokens"] == 1_100_000
        assert archive["data"]["round_metrics"][-1]["context_compression"]["summary"]["failed"]
        runtime_cache.drop_all(root)
        events = list(iter_request_events({**request, "prompt": "continue"}, root=root, provider_factory=lambda _: provider))
    assert events[-1].type == "done"
    assert any(event.type == "context_compression" and event.metadata.get("trigger") == "provider_usage_limit" for event in events)
    assert provider.requests[-1].messages[-1]["content"] == "continue"


@pytest.mark.parametrize("grows_summary", [False, True])
def test_main_request_413_only_recovers_when_content_size_reduces(runtime_root, grows_summary):
    from run.conversation.provider_events import is_context_length_exceeded, is_request_size_exceeded

    rejected = ProviderError("gateway request body too large", status_code=413)
    assert is_request_size_exceeded(rejected)
    assert not is_context_length_exceeded(rejected)
    old_chars = 300 if grows_summary else 3000
    provider = ScriptedProvider(responses=[
        ChatResponse(text="old answer"),
        rejected, summary_response("长" * 1500 if grows_summary else "old turn summarized"),
        ChatResponse(text="recovered"),
    ])
    request = dict(user="alice", source="web", session_id="transport-size")
    with patch.dict(os.environ, {"TEST_KEMO_KEY": "test"}):
        handle_request({**request, "prompt": "old " + "甲" * old_chars},
                       root=runtime_root, provider_factory=lambda _: provider)
        events = list(iter_request_events({**request, "prompt": "continue"},
                                         root=runtime_root, provider_factory=lambda _: provider))
    compression = [event for event in events if event.type == "context_compression"]
    assert compression[0].metadata["trigger"] == "api_request_size"
    assert compression[-1].metadata["status"] == ("failed" if grows_summary else "ready")
    if grows_summary:
        assert compression[-1].metadata["diagnostics"]["failure_kind"] == "request_size_not_reduced"
        assert len(provider.requests) == 3  # Do not resend a bigger main request.
        archive_path = find_window(runtime_root, "alice", "web", "transport-size")
        assert archive_path is not None
        assert read_summary_cache(runtime_window_path(archive_path)) is None
        assert load_window(archive_path)["text"]["messages"][0]["content"].endswith("甲" * old_chars)
    else:
        assert events[-1].type == "done"
        assert provider.requests[-1].messages[-1]["content"] == "continue"


def test_summary_budget_uses_actual_summary_model_and_native_request_guard(runtime_root):
    from provider.protocol.models import ModelCapabilities, LimitsCapabilities
    from run.agents import AgentRunner
    from run.agents.runner import AgentProviderError
    from run.context.summary_budget import summary_budget
    from tests.agents.test_subagent_runtime import MockProvider

    class LimitedProvider(MockProvider):
        def capabilities(self, model, **kwargs):
            original = super().capabilities(model, **kwargs)
            return ModelCapabilities.model_validate({
                **original.model_dump(mode="json"),
                "limits": LimitsCapabilities(context_window=24_000, max_output_tokens=3000).model_dump(mode="json"),
            })

    provider = LimitedProvider()
    runner = AgentRunner(runtime_root, "alice", config={
        "provider": {"type": "kemo", "base_url": "http://127.0.0.1:1",
                     "api_key_env": "TEST_KEMO_KEY", "model": "summary-limited-test"},
    }, provider_factory=lambda _: provider)
    payload = {"previous_summary": None, "rounds": [{"round": 1}], "trigger": "manual"}
    with patch.dict(os.environ, {"TEST_KEMO_KEY": "test"}):
        budget = summary_budget(runner, "context_manage", 64_000, 20_000, None)
        assert budget.input_limit <= 18_000
        assert budget.output_limit == 3000
        assert "[output_schema]" in budget.system
        assert budget.fits(payload)
        with pytest.raises(AgentProviderError):
            runner.run("context_manage", payload, input_token_limit=100)
        assert not provider.requests
        runner.run("context_manage", payload, input_token_limit=budget.input_limit,
                   max_tokens=budget.output_limit)
    assert len(provider.requests) == 1
    assert provider.requests[0].generation.max_output_tokens == 3000
    # A JSON repair adds prior output and instructions. It must pass the same
    # native-input gate rather than sending an oversized second request.
    from run.context import estimate_messages_tokens
    first = provider.requests[0]
    native_tokens = estimate_messages_tokens([
        {"role": "system", "content": first.system_prompt},
        *[item.model_dump(mode="json", exclude_none=True) for item in first.input],
    ])
    provider.text = "not valid JSON " * 100
    with patch.dict(os.environ, {"TEST_KEMO_KEY": "test"}), pytest.raises(AgentProviderError):
        runner.run("context_manage", payload, input_token_limit=native_tokens + 10)
    assert len(provider.requests) == 2


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
def test_summary_host_interrupt_is_not_swallowed_or_published(tmp_path, interrupt):
    class InterruptSecond(SummaryRunner):
        def run(self, name, input_data, **kwargs):
            if self.calls:
                raise interrupt()
            return super().run(name, input_data, **kwargs)

    path = make_summary_runtime(tmp_path)
    runner = InterruptSecond()
    with pytest.raises(interrupt):
        get_or_create_summary(
            runtime_path=path, groups=build_round_groups(make_window(1, chars=5000), ContextPolicy()),
            agent_runner=runner, agent_name="context_manage", trigger="manual", chunk_token_budget=2000,
        )
    assert read_summary_cache(path) is None


def test_cancelled_fragment_keeps_previous_cache_and_counts_completed_request(tmp_path):
    import threading

    event = threading.Event()
    class CancelAfterOne(SummaryRunner):
        def run(self, name, input_data, **kwargs):
            result = super().run(name, input_data, **kwargs)
            event.set()
            return result

    path = make_summary_runtime(tmp_path)
    usage = []
    cache, diagnostics = get_or_create_summary(
        runtime_path=path, groups=build_round_groups(make_window(1, chars=5000), ContextPolicy()),
        agent_runner=CancelAfterOne(), agent_name="context_manage", trigger="manual",
        chunk_token_budget=2000, cancel_event=event, response_hook=usage.append,
    )
    assert cache is None and diagnostics["failure_kind"] == "cancelled"
    assert diagnostics["requests"] == 1 and len(usage) == 1
    assert read_summary_cache(path) is None


def test_missing_or_estimated_usage_cannot_replace_exact_checkpoint():
    from run.conversation.request_diagnostics import record_context_checkpoint

    provider = {"type": "kemo", "model": "test"}
    original = make_checkpoint(1000, 800, provider, None)
    stats = {"usage_checkpoint": original}
    for usage in (None, Usage(5000, 1, 5001, estimated=True), Usage(0, 0, 0, estimated=False)):
        assert record_context_checkpoint(stats, usage, 900, provider, None, (1000, 800)) == (1000, 800)
        assert stats["usage_checkpoint"] is original


@pytest.mark.parametrize("status", [400, 413])
def test_size_recovery_budget_is_not_multiplied_by_outer_run_retries(runtime_root, status):
    rejection = ProviderError(
        "prompt is too long" if status == 400 else "request body too large", status_code=status,
    )
    provider = ScriptedProvider(responses=[
        *(ChatResponse(text=f"old {i}") for i in range(3)),
        rejection, summary_response(), rejection, summary_response(), rejection,
        ChatResponse(text="must not reach this response"),
    ])
    request = dict(user="alice", source="web", session_id=f"bounded-{status}")
    with patch.dict(os.environ, {"TEST_KEMO_KEY": "test"}):
        for index in range(3):
            handle_request({**request, "prompt": f"turn {index} " + "甲" * 2000},
                           root=runtime_root, provider_factory=lambda _: provider)
        events = list(iter_request_events({**request, "prompt": "current"},
                                         root=runtime_root, provider_factory=lambda _: provider))
    assert len(provider.requests) == 8
    assert not any(event.type == "retrying" for event in events)
    assert any(event.type == "error" for event in events)
    assert len([event for event in events if event.type == "context_compression"
                and event.metadata.get("status") == "ready"]) == 2


def test_summary_persistence_failure_is_not_misclassified_as_provider_error(tmp_path):
    path = make_summary_runtime(tmp_path)
    with patch("run.context.summary.write_context_summary", side_effect=OSError("private-path")):
        cache, diagnostics = get_or_create_summary(
            runtime_path=path, groups=build_round_groups(make_window(1), ContextPolicy()),
            agent_runner=SummaryRunner(), agent_name="context_manage", trigger="manual",
        )
    assert cache is None and diagnostics["stage"] == "summary_commit"
    assert diagnostics["failure_kind"] == "persistence_error"
    assert diagnostics["attempts"] == 1 and diagnostics["requests"] == 1
    assert "private-path" not in json.dumps(diagnostics)
    assert read_summary_cache(path) is None
