from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from provider.schema import ChatResponse, Usage
from run.agent_runner import AgentRunResult
from run.context import ContextPolicy, build_round_groups, select_context
from run.context_summary import build_summary_message, get_or_create_summary
from run.engine import _compress_per_round_tool_think
from run.history import commit_window, empty_window, load_runtime_window, load_window, runtime_window_path


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


class SummaryRunner:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def run(self, name, input_data, **kwargs):
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
        return AgentRunResult(
            agent=name,
            data=payload,
            raw_text=json.dumps(payload),
            usage=Usage(10, 2, 12, source="summary-mock").to_dict(),
            model="mock",
        )


class ContextLifecycleTests(unittest.TestCase):
    def test_policy_budget_and_invalid_values(self) -> None:
        policy = ContextPolicy.from_config(
            {
                "agents": {
                    "conserved_rounds": 2,
                    "max_rounds": 8,
                    "rounds_after_compression": 3,
                    "token_limit": 1000,
                    "token_compression_ratio": 0.6,
                },
                "history": {"recent_full_rounds": 2},
            }
        )
        self.assertEqual(policy.input_budget, 600)
        self.assertEqual(policy.output_reserve, 400)
        self.assertEqual(policy.recent_tool_rounds, 2)
        with self.assertRaises(ValueError):
            ContextPolicy.from_config(
                {"agents": {"max_rounds": 2, "rounds_after_compression": 3}}
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
        self.assertEqual([item.number for item in selected.kept_rounds], [4, 5, 6])
        self.assertEqual([item.number for item in selected.removed_rounds], [1, 2, 3])
        self.assertEqual(selected.messages[1]["content"].split("-")[0], "u4")
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
        self.assertEqual([item.number for item in selected.kept_rounds], [2, 3, 4])
        self.assertEqual(len(selected.removed_rounds), 1)
        self.assertTrue(selected.token_limit_triggered)
        self.assertTrue(selected.fixed_content_over_budget)
        self.assertTrue(selected.recent_content_over_budget)
        self.assertEqual(selected.messages[-1]["content"], "当" * 600)

    def test_tool_messages_stay_as_assistant_call_plus_result_and_old_results_compact(self) -> None:
        window = make_window(4, with_tools=True)
        policy = ContextPolicy(
            recent_tool_rounds=1,
            max_rounds=100,
            token_limit=100000,
            older_tool_result_chars=100,
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
        provider = SummaryRunner()
        usage = []
        first, diagnostics = get_or_create_summary(
            cache_path=cache_path,
            groups=groups,
            agent_runner=provider,
            agent_name="context_manage",
            trigger="round_limit",
            response_hook=usage.append,
        )
        self.assertTrue(diagnostics["generated"])
        self.assertEqual(provider.calls, 1)
        self.assertEqual(usage[0]["total_tokens"], 12)
        self.assertIsNotNone(build_summary_message(first))

        second, diagnostics = get_or_create_summary(
            cache_path=cache_path,
            groups=groups,
            agent_runner=provider,
            agent_name="context_manage",
            trigger="round_limit",
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
            agent_runner=SummaryRunner(fail=True),
            agent_name="context_manage",
            trigger="round_limit",
        )
        self.assertIsNone(value)
        self.assertTrue(diagnostics["failed"])
        self.assertEqual(cache_path.read_text("utf-8"), original)

        cancelled = threading.Event()
        cancelled.set()
        value, diagnostics = get_or_create_summary(
            cache_path=cache_path,
            groups=groups,
            agent_runner=SummaryRunner(),
            agent_name="context_manage",
            trigger="round_limit",
            cancel_event=cancelled,
        )
        self.assertIsNone(value)
        self.assertEqual(diagnostics["error"], "cancelled")
        self.assertEqual(cache_path.read_text("utf-8"), original)

    def test_summary_chunking_keeps_rounds_indivisible(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        groups = build_round_groups(make_window(5, chars=300), ContextPolicy())
        provider = SummaryRunner()
        value, diagnostics = get_or_create_summary(
            cache_path=Path(temporary.name) / "summary.json",
            groups=groups,
            agent_runner=provider,
            agent_name="context_manage",
            trigger="round_limit",
            chunk_token_budget=256,
        )
        self.assertIsNotNone(value)
        self.assertEqual(diagnostics["chunks"], 5)
        self.assertEqual(provider.calls, 5)
        self.assertEqual(value["covered_rounds"], [1, 2, 3, 4, 5])

    def test_tool_think_compression_processes_one_unprotected_round(self) -> None:
        window = make_window(5, with_tools=True)
        window["items"] = {"items": []}
        runner = SummaryRunner()
        diagnostics = _compress_per_round_tool_think(
            window=window,
            conserved_rounds=2,
            agent_runner=runner,
            cancel_event=None,
        )
        self.assertTrue(diagnostics["compressed"])
        self.assertEqual(diagnostics["round"], 1)
        self.assertEqual(runner.calls, 1)
        self.assertTrue(window["think"]["rounds"][0]["compressed"])
        self.assertEqual(window["think"]["rounds"][0]["content"], "compressed")
        self.assertEqual(window["tool"]["rounds"][0]["calls"], [])
        self.assertFalse(window["think"]["rounds"][1].get("compressed", False))

    def test_runtime_mirror_can_compress_without_mutating_archive(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        archive_path = Path(temporary.name) / "history" / "archive-window"
        archive = empty_window("alice", "cli", "session")
        archive["think"]["rounds"].append({"round": 1, "content": "raw reasoning"})
        archive["tool"]["rounds"].append(
            {"round": 1, "calls": [{"name": "lookup", "result": "raw result"}]}
        )
        archive["data"]["rounds"] = 1
        commit_window(archive_path, archive)
        runtime = json.loads(json.dumps(archive, ensure_ascii=False))
        runtime["think"]["rounds"][0].update(
            {"content": "compressed", "compressed": True}
        )
        runtime["tool"]["rounds"][0].update({"calls": [], "compressed": True})
        commit_window(runtime_window_path(archive_path), runtime)

        reloaded_archive = load_window(archive_path)
        runtime_path, reloaded_runtime = load_runtime_window(
            archive_path, reloaded_archive
        )
        self.assertEqual(reloaded_archive["think"]["rounds"][0]["content"], "raw reasoning")
        self.assertEqual(reloaded_archive["tool"]["rounds"][0]["calls"][0]["result"], "raw result")
        self.assertEqual(runtime_path, runtime_window_path(archive_path))
        self.assertEqual(reloaded_runtime["think"]["rounds"][0]["content"], "compressed")
        self.assertEqual(reloaded_runtime["tool"]["rounds"][0]["calls"], [])


if __name__ == "__main__":
    unittest.main()
