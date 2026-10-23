"""Regression coverage for whole-round compression and provider rejections."""

from __future__ import annotations

from tests.type_assertions import require_present

import json
import os
from contextlib import nullcontext
from unittest.mock import patch

import pytest

from run.context import ContextPolicy, select_context
from run.conversation.provider_events import is_context_length_exceeded
from provider.schema import ChatResponse, ProviderError, Usage
from tests.core.test_context_lifecycle import make_window, SummaryRunner
from tests.core import test_runtime_features as runtime_features


def test_manual_compression_falls_back_to_tokens_below_round_threshold():
    selection = select_context(
        window=make_window(3, chars=300),
        policy=ContextPolicy(
            max_rounds=80, rounds_after_compression=20,
            token_limit=3000, compression_ratio=0.3,
        ),
        system_message=None, current_user_message=None, force_compress=True,
    )
    assert selection.token_limit_triggered
    assert selection.removed_rounds
    assert selection.estimated_tokens_after <= selection.input_budget
    assert len(selection.kept_rounds) < 3


def test_token_pressure_can_retain_one_round_despite_recent_full_rounds():
    selection = select_context(
        window=make_window(4, chars=300),
        policy=ContextPolicy(token_limit=1000, compression_ratio=0.3),
        system_message=None, current_user_message=None,
    )
    assert len(selection.kept_rounds) == 1
    assert selection.recent_content_over_budget
    assert len(selection.removed_rounds) == 3


def test_provider_context_error_requires_input_limit_evidence():
    for message in (
        "maximum context length is 32768 tokens; requested 40000 tokens",
        "prompt is too long: 40000 tokens > 32768 maximum",
        "input token limit exceeded",
        "input_length_exceeded",
    ):
        assert is_context_length_exceeded(ProviderError(message, status_code=400))
    for payload in (
        {"code": "PROVIDER_BAD_RESPONSE", "provider_status": 400},
        {"code": "invalid_request", "message": "unsupported tool schema"},
        {"reason": "max_output_tokens"},
        {"finish_reason": "length"},
    ):
        assert not is_context_length_exceeded(payload)


def test_restore_does_not_resurrect_summarized_rounds(tmp_path):
    from run.context import build_round_groups, get_or_create_summary
    from run.history import commit_window, empty_window, load_runtime_window, runtime_window_path

    archive_path = tmp_path / "users" / "alice" / "history" / "restored"
    archive_path.parent.mkdir(parents=True)
    archive = empty_window("alice", "web", "restored")
    seeded = make_window(5)
    for section in ("text", "think", "tool"):
        archive[section] = seeded[section]
    archive["data"]["rounds"] = 5
    commit_window(archive_path, archive)
    policy = ContextPolicy()
    cache, diagnostics = get_or_create_summary(
        runtime_path=runtime_window_path(archive_path),
        groups=build_round_groups(archive, policy)[:3],
        agent_runner=SummaryRunner(), agent_name="context_manage", trigger="manual",
    )
    assert not diagnostics["failed"]
    assert cache["covered_through_round"] == 3
    _, restored = load_runtime_window(archive_path)
    assert restored["data"]["rounds"] == 2
    assert restored["data"]["context"]["round_offset"] == 3
    assert [message["content"][:3] for message in restored["text"]["messages"]] == [
        "u4-", "a4-", "u5-", "a5-",
    ]


@pytest.fixture
def runtime_root():
    case = runtime_features.RuntimeFeatureTests(methodName="runTest")
    _, root = case.make_root()
    try:
        yield root
    finally:
        case.doCleanups()


@pytest.mark.parametrize("summary_fails", [False, True])
def test_completed_output_triggers_compaction_and_accounts_for_summary_usage(runtime_root, summary_fails):
    from run.context import estimate_messages_tokens
    from run.engine import handle_request
    from run.history import find_window, load_runtime_window, load_window

    root = runtime_root
    provider = runtime_features.ScriptedProvider(responses=[
        ChatResponse(text="first reply", usage=Usage(10, 2, 12)),
        ChatResponse(text="乙" * 50_000, usage=Usage(20, 3, 23)),
        ChatResponse(
            text=json.dumps({
                "facts": [], "requirements": [], "decisions": [], "unfinished": [],
                "tool_results": [], "entities": [], "narrative": "first turn summarized",
            }),
            usage=Usage(7, 4, 11),
        ),
    ])
    request = dict(user="alice", source="web", session_id="end-token")
    summary_patch = (
        patch(
            "run.conversation.runtime.get_or_create_summary",
            return_value=(None, {"failed": True, "error": "summary unavailable"}),
        )
        if summary_fails else nullcontext()
    )
    with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}), summary_patch:
        handle_request({**request, "prompt": "first"}, root=root, provider_factory=lambda _: provider)
        config_path = root / "config" / "global_config.json"
        config = json.loads(config_path.read_text("utf-8"))
        config["agents"] = {
            "enable_mid_context_compression": False,
            "max_rounds": 80, "rounds_after_compression": 20,
            "token_limit": estimate_messages_tokens(provider.requests[0].messages) + 10_000,
            "token_compression_ratio": 0.3,
        }
        config_path.write_text(json.dumps(config), "utf-8")
        result = handle_request(
            {**request, "prompt": "second"}, root=root, provider_factory=lambda _: provider,
        )
    assert result["text"] == "乙" * 50_000
    archive_path = find_window(root, "alice", "web", "end-token")
    archive = load_window(require_present(archive_path))
    _, runtime = load_runtime_window(require_present(archive_path), archive)
    assert archive["data"]["rounds"] == 2
    if summary_fails:
        from run.context.service import context_status

        assert result["context"]["end_compression"]["summary"]["failed"]
        assert result["context"]["end_compression"]["rounds_removed"] == 0
        assert runtime["data"]["rounds"] == 2
        assert runtime["data"]["context"]["round_offset"] == 0
        assert runtime["data"]["context"]["summary_cache"] is None
        assert archive["data"]["token_usage"]["total_tokens"] == 35
        status = context_status(request, root=root)
        assert status["context"]["rounds_removed"] == 0
        assert status["context"]["rounds_kept"] == 2
        assert status["context"]["estimated_tokens_after"] > status["policy"]["token_limit"]
        return
    assert not result["context"]["end_compression"]["summary"]["failed"], result["context"]["end_compression"]["summary"]
    assert result["context"]["end_compression"]["rounds_removed"] == 1
    assert result["context"]["end_compression"]["summary"]["generated"]
    assert result["usage"]["total_tokens"] == 34
    assert runtime["data"]["rounds"] == 1
    assert runtime["data"]["context"]["round_offset"] == 1
    assert runtime["data"]["context"]["summary_usage"]["total_tokens"] == 11
    assert archive["data"]["token_usage"]["total_tokens"] == 46
    assert runtime["data"]["token_usage"]["total_tokens"] == 46


@pytest.mark.parametrize("fail_second_pass", [False, True])
def test_compaction_converges_with_expanding_summary_or_rolls_back(tmp_path, fail_second_pass):
    from run.conversation.context_compaction import compact_context
    from tests.core.test_context_lifecycle import make_summary_runtime

    class ExpandingRunner(SummaryRunner):
        def run(self, name, input_data, **kwargs):
            if fail_second_pass and input_data["rounds"][0]["round"] == 4:
                raise RuntimeError("second pass failed")
            return super().run(name, input_data, **kwargs)

    runner = ExpandingRunner(narrative="摘要" * 300, include_fact=False)
    original_cache = {
        "source_hash": "original", "covered_rounds": [], "covered_through_round": 0,
        "summary": {"narrative": "previous summary"},
    }
    result = compact_context(
        window=make_window(5, chars=200),
        policy=ContextPolicy(
            max_rounds=80, rounds_after_compression=20,
            token_limit=4000, compression_ratio=0.3,
        ),
        runtime_path=make_summary_runtime(tmp_path),
        system_message=None, current_user_message=None, tools=None,
        previous_cache=original_cache, agent_runner=runner,
        trigger="manual", force_compress=True,
    )
    if fail_second_pass:
        assert result.diagnostics["failed"]
        assert result.summary_cache is original_cache
        assert len(result.selection.kept_rounds) == 5
        assert not result.selection.removed_rounds
    else:
        assert runner.calls >= 2
        assert runner.inputs[-1]["rounds"][0]["round"] == 4
        assert runner.inputs[-1]["previous_summary"]["narrative"] == "摘要" * 300
        assert len(result.selection.kept_rounds) == 1
        assert result.selection.estimated_tokens_after <= result.selection.input_budget
        assert require_present(result.summary_cache)["covered_rounds"] == [1, 2, 3, 4]


def summary_response(narrative="summarized history"):
    return ChatResponse(
        text=json.dumps({
            "facts": [], "requirements": [], "decisions": [], "unfinished": [],
            "tool_results": [], "entities": [], "narrative": narrative,
        }),
        usage=Usage(7, 4, 11),
    )


def test_repeated_provider_limits_shrink_history_on_each_retry(runtime_root):
    from run.engine import handle_request
    from run.history import find_window, load_runtime_window, load_window

    root = runtime_root
    config_path = root / "config" / "global_config.json"
    config = json.loads(config_path.read_text("utf-8"))
    config["agents"] = {
        "enable_mid_context_compression": False,
        "max_rounds": 80, "rounds_after_compression": 6,
        "token_limit": 120_000, "token_compression_ratio": 0.3,
    }
    config_path.write_text(json.dumps(config), "utf-8")
    error = ProviderError("prompt is too long", status_code=400)
    provider = runtime_features.ScriptedProvider(responses=[
        *(ChatResponse(text=f"old-reply-{i}") for i in range(5)),
        error, summary_response(), error, summary_response(), ChatResponse(text="recovered"),
    ])
    request = dict(user="alice", source="web", session_id="two-retries")
    with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}):
        for i in range(5):
            handle_request(
                {**request, "prompt": f"old-user-{i}"},
                root=root, provider_factory=lambda _: provider,
            )
        result = handle_request(
            {**request, "prompt": "current user"},
            root=root, provider_factory=lambda _: provider,
        )
    assert result["text"] == "recovered"
    assert result["context"]["api_context_retries"] == 2
    attempted = [provider.requests[index].messages for index in (5, 7, 9)]
    assert [
        sum(message.get("role") == "user" for message in messages)
        for messages in attempted
    ] == [6, 3, 1]
    archive_path = find_window(root, "alice", "web", "two-retries")
    archive = load_window(require_present(archive_path))
    _, runtime = load_runtime_window(require_present(archive_path), archive)
    assert archive["data"]["rounds"] == 6
    assert runtime["data"]["rounds"] == 1
    assert runtime["data"]["context"]["round_offset"] == 5


def test_manual_low_round_count_uses_token_target_and_keeps_archive(runtime_root):
    from run.engine import compress_context, handle_request
    from run.history import find_window, load_runtime_window, load_window

    root = runtime_root
    config_path = root / "config" / "global_config.json"
    config = json.loads(config_path.read_text("utf-8"))
    config["agents"] = {
        "enable_mid_context_compression": False,
        "max_rounds": 80, "rounds_after_compression": 20,
        "token_limit": 24_000, "token_compression_ratio": 0.3,
    }
    config_path.write_text(json.dumps(config), "utf-8")
    provider = runtime_features.ScriptedProvider(responses=[
        *(ChatResponse(text="答" * 1000) for _ in range(3)),
        summary_response(), summary_response(), summary_response(),
    ])
    request = dict(user="alice", source="web", session_id="manual-token")
    with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}):
        for i in range(3):
            handle_request(
                {**request, "prompt": f"{i}问" + "问" * 1000},
                root=root, provider_factory=lambda _: provider,
            )
        result = compress_context(
            {**request, "memory_extraction_policy": "queue"},
            root=root, provider_factory=lambda _: provider,
        )
    assert not result["context"]["summary"]["failed"], result["context"]["summary"]
    assert result["compressed"] and result["compression_verified"], result["context"]
    assert result["context"]["token_limit_triggered"]
    assert result["context"]["rounds_kept"] == 1
    assert result["context"]["target_reached"]
    assert result["memory"]["status"] == "queued"
    archive_path = find_window(root, "alice", "web", "manual-token")
    archive = load_window(require_present(archive_path))
    _, runtime = load_runtime_window(require_present(archive_path), archive)
    assert archive["data"]["rounds"] == 3
    assert runtime["data"]["rounds"] == 1


def test_manual_last_turn_above_hard_limit_reports_floor_without_provider_call(runtime_root):
    from run.engine import compress_context, handle_request
    from run.history import find_window, load_window

    root = runtime_root
    provider = runtime_features.ScriptedProvider(responses=[ChatResponse(text="答" * 5000)])
    request = dict(user="alice", source="web", session_id="single-turn")
    with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}):
        handle_request(
            {**request, "prompt": "question"}, root=root, provider_factory=lambda _: provider,
        )
        config_path = root / "config" / "global_config.json"
        config = json.loads(config_path.read_text("utf-8"))
        config["agents"] = {
            "enable_mid_context_compression": False,
            "max_rounds": 80, "rounds_after_compression": 20,
            "token_limit": 3000, "token_compression_ratio": 0.3,
        }
        config_path.write_text(json.dumps(config), "utf-8")
        result = compress_context(
            {**request, "memory_extraction_policy": "queue"},
            root=root, provider_factory=lambda _: provider,
        )
    assert not result["compressed"]
    assert result["context"]["minimum_rounds_reached"]
    assert not result["context"]["target_reached"]
    assert len(provider.requests) == 1
    archive_path = find_window(root, "alice", "web", "single-turn")
    assert load_window(require_present(archive_path))["data"]["rounds"] == 1
