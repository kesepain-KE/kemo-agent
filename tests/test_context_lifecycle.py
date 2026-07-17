from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from provider.schema import ChatResponse, Usage
from run.context import ContextPolicy, build_round_groups, select_context
from run.context_summary import build_summary_message, get_or_create_summary


def make_window(rounds: int, *, chars: int = 8, with_tools: bool = False) -> dict:
    text_messages = []
    think_rounds = []
    tool_rounds = []
    for number in range(1, rounds + 1):
        text_messages.extend(
            [
                {"role": "user", "content": f"u{number}-" + "甲" * chars},
                {"role": "assistant", "content": f"a{number}-" + "乙" * chars},
            ]
        )
        think_rounds.append({"round": number, "content": f"think-{number}"})
        calls = []
        if with_tools:
            calls.append(
                {
                    "id": f"call-{number}",
                    "name": "lookup",
                    "arguments": {"query": f"q{number}"},
                    "result": {"ok": True, "result": "R" * 500},
                    "iteration": 1,
                }
            )
        tool_rounds.append({"round": number, "calls": calls})
    return {
        "text": {"messages": text_messages},
        "think": {"rounds": think_rounds},
        "tool": {"rounds": tool_rounds},
        "data": {"rounds": rounds},
    }


class SummaryProvider:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def chat(self, request):
        self.calls += 1
        if self.fail:
            raise RuntimeError("summary unavailable")
        payload = {
            "facts": [f"chunk-{self.calls}"],
            "requirements": [],
            "decisions": [],
            "unfinished": [],
            "tool_results": [],
            "entities": [],
            "narrative": "compressed",
        }
        return ChatResponse(
            text=json.dumps(payload),
            usage=Usage(10, 2, 12, source="summary-mock"),
        )


class ContextLifecycleTests(unittest.TestCase):
    def test_policy_budget_and_invalid_values(self) -> None:
        policy = ContextPolicy.from_config(
            {
                "agents": {
                    "n1_recent_rounds_before_tool_compression": 2,
                    "n2_max_rounds": 8,
                    "n3_rounds_after_compression": 3,
                    "n4_token_limit": 1000,
                    "n5_token_compression_ratio": 0.6,
                },
                "history": {"older_tool_log_max_chars": 80},
            }
        )
        self.assertEqual(policy.input_budget, 600)
        self.assertEqual(policy.output_reserve, 400)
        self.assertEqual(policy.recent_tool_rounds, 2)
        with self.assertRaises(ValueError):
            ContextPolicy.from_config(
                {"agents": {"n2_max_rounds": 2, "n3_rounds_after_compression": 3}}
            )

    def test_round_threshold_keeps_whole_latest_rounds_without_mutating_history(self) -> None:
        window = make_window(6)
        snapshot = json.dumps(window, ensure_ascii=False, sort_keys=True)
        policy = ContextPolicy(max_rounds=6, rounds_after_compression=2, token_limit=10000)
        selected = select_context(
            window=window,
            policy=policy,
            system_message={"role": "system", "content": "system"},
            current_user_message={"role": "user", "content": "current"},
        )
        self.assertEqual([item.number for item in selected.kept_rounds], [5, 6])
        self.assertEqual([item.number for item in selected.removed_rounds], [1, 2, 3, 4])
        self.assertEqual(selected.messages[1]["content"].split("-")[0], "u5")
        self.assertEqual(selected.messages[-1]["content"], "current")
        self.assertEqual(json.dumps(window, ensure_ascii=False, sort_keys=True), snapshot)

    def test_token_trim_never_splits_round_and_marks_oversized_fixed_content(self) -> None:
        window = make_window(4, chars=120)
        policy = ContextPolicy(
            max_rounds=100,
            rounds_after_compression=10,
            token_limit=500,
            compression_ratio=0.5,
        )
        selected = select_context(
            window=window,
            policy=policy,
            system_message={"role": "system", "content": "S" * 20},
            current_user_message={"role": "user", "content": "当" * 600},
        )
        self.assertEqual(selected.kept_rounds, [])
        self.assertEqual(len(selected.removed_rounds), 4)
        self.assertTrue(selected.token_limit_triggered)
        self.assertTrue(selected.fixed_content_over_budget)
        self.assertEqual(selected.messages[-1]["content"], "当" * 600)

    def test_tool_messages_stay_as_assistant_call_plus_result_and_old_results_compact(self) -> None:
        window = make_window(4, with_tools=True)
        policy = ContextPolicy(
            recent_tool_rounds=1,
            max_rounds=100,
            token_limit=100000,
            older_tool_log_max_chars=100,
        )
        groups = build_round_groups(window, policy)
        for group in groups:
            roles = [message["role"] for message in group.messages]
            self.assertEqual(roles, ["user", "assistant", "tool", "assistant"])
            self.assertEqual(
                group.messages[1]["tool_calls"][0]["id"],
                group.messages[2]["tool_call_id"],
            )
        self.assertIn('"compressed": true', groups[0].messages[2]["content"])
        self.assertNotIn('"compressed": true', groups[-1].messages[2]["content"])

    def test_summary_cache_generation_and_exact_reuse(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        cache_path = Path(temporary.name) / "summary.json"
        groups = build_round_groups(make_window(3), ContextPolicy())
        provider = SummaryProvider()
        usage = []
        first, diagnostics = get_or_create_summary(
            cache_path=cache_path,
            groups=groups,
            provider=provider,
            model="mock",
            response_hook=usage.append,
        )
        self.assertTrue(diagnostics["generated"])
        self.assertEqual(provider.calls, 1)
        self.assertEqual(usage[0]["total_tokens"], 12)
        self.assertIsNotNone(build_summary_message(first))

        second, diagnostics = get_or_create_summary(
            cache_path=cache_path,
            groups=groups,
            provider=provider,
            model="mock",
        )
        self.assertTrue(diagnostics["cache_hit"])
        self.assertEqual(provider.calls, 1)
        self.assertEqual(second["source_hash"], first["source_hash"])

    def test_summary_failure_and_cancel_leave_existing_cache_untouched(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        cache_path = Path(temporary.name) / "summary.json"
        original = '{"schema_version":1,"source_hash":"old","summary":{"narrative":"old"}}'
        cache_path.write_text(original, "utf-8")
        groups = build_round_groups(make_window(2), ContextPolicy())

        value, diagnostics = get_or_create_summary(
            cache_path=cache_path,
            groups=groups,
            provider=SummaryProvider(fail=True),
            model="mock",
        )
        self.assertIsNone(value)
        self.assertTrue(diagnostics["failed"])
        self.assertEqual(cache_path.read_text("utf-8"), original)

        cancelled = threading.Event()
        cancelled.set()
        value, diagnostics = get_or_create_summary(
            cache_path=cache_path,
            groups=groups,
            provider=SummaryProvider(),
            model="mock",
            cancel_event=cancelled,
        )
        self.assertIsNone(value)
        self.assertEqual(diagnostics["error"], "cancelled")
        self.assertEqual(cache_path.read_text("utf-8"), original)

    def test_summary_chunking_keeps_rounds_indivisible(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        groups = build_round_groups(make_window(5, chars=300), ContextPolicy())
        provider = SummaryProvider()
        value, diagnostics = get_or_create_summary(
            cache_path=Path(temporary.name) / "summary.json",
            groups=groups,
            provider=provider,
            model="mock",
            chunk_token_budget=256,
        )
        self.assertIsNotNone(value)
        self.assertEqual(diagnostics["chunks"], 5)
        self.assertEqual(provider.calls, 5)
        self.assertEqual(value["covered_rounds"], [1, 2, 3, 4, 5])


if __name__ == "__main__":
    unittest.main()
