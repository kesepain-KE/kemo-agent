from __future__ import annotations

import errno
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from provider.schema import Usage
from run.agent_runner import AgentRunResult
from run.context import ContextPolicy, build_round_groups, select_context
from run.context_summary import (
    _atomic_write as write_summary_cache,
    build_summary_message,
    get_or_create_summary,
)
from run.context_service import compress_per_round_tool_think
from run.session_runtime import copy_committed_round_to_archive
from run.history import (
    _trim_to_max_rounds,
    commit_window,
    empty_window,
    load_runtime_window,
    load_window,
    runtime_window_path,
)


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
    def __init__(
        self,
        *,
        fail: bool = False,
        narrative: str = "compressed",
        include_fact: bool = True,
    ) -> None:
        self.fail = fail
        self.narrative = narrative
        self.include_fact = include_fact
        self.calls = 0
        self.inputs: list[dict] = []
        self.kwargs: list[dict] = []

    def run(self, name, input_data, **kwargs):
        self.calls += 1
        self.inputs.append(json.loads(json.dumps(input_data, ensure_ascii=False)))
        self.kwargs.append(dict(kwargs))
        if self.fail:
            raise RuntimeError("summary unavailable")
        payload = {
            "facts": [f"chunk-{self.calls}"] if self.include_fact else [],
            "requirements": [],
            "decisions": [],
            "unfinished": [],
            "tool_results": [],
            "entities": [],
            "narrative": self.narrative,
        }
        return AgentRunResult(
            agent=name,
            data=payload,
            raw_text=json.dumps(payload),
            usage=Usage(10, 2, 12, source="summary-mock").to_dict(),
            model="mock",
        )


class ContextLifecycleTests(unittest.TestCase):
    def test_trim_to_max_rounds_keeps_latest_data_and_renumbers_workspace(self) -> None:
        window = empty_window("alice", "cli", "session")
        for number in range(1, 6):
            window["text"]["messages"].extend(
                [
                    {"role": "user", "content": f"user-{number}"},
                    {"role": "assistant", "content": f"assistant-{number}"},
                ]
            )
            window["think"]["rounds"].append(
                {"round": number, "content": f"think-{number}"}
            )
            window["tool"]["rounds"].append(
                {"round": number, "calls": [{"name": f"tool-{number}"}]}
            )
            window["items"]["items"].append(
                {
                    "id": f"item-{number}",
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "text", "text": f"item-{number}"}],
                    "metadata": {"round": number},
                }
            )
            window["data"]["round_metrics"].append(
                {"round": number, "usage": {"total_tokens": number}}
            )
        window["data"]["rounds"] = 5
        window["data"]["context"] = {"summary": {"generated": True}}

        trimmed = _trim_to_max_rounds(window, 2)

        self.assertEqual(window["data"]["rounds"], 5)
        self.assertEqual(
            [item["content"] for item in trimmed["text"]["messages"]],
            ["user-4", "assistant-4", "user-5", "assistant-5"],
        )
        self.assertEqual(
            [(item["round"], item["content"]) for item in trimmed["think"]["rounds"]],
            [(1, "think-4"), (2, "think-5")],
        )
        self.assertEqual(
            [item["round"] for item in trimmed["tool"]["rounds"]],
            [1, 2],
        )
        self.assertEqual(
            [item["metadata"]["round"] for item in trimmed["items"]["items"]],
            [1, 2],
        )
        self.assertEqual(
            [item["round"] for item in trimmed["data"]["round_metrics"]],
            [1, 2],
        )
        self.assertEqual(trimmed["data"]["rounds"], 2)
        self.assertEqual(trimmed["data"]["context"]["round_offset"], 3)
        self.assertTrue(trimmed["data"]["context"]["summary"]["generated"])

    def test_archive_data_is_clean_and_runtime_restore_is_bounded(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        archive_path = Path(temporary.name) / "history" / "archive-window"
        archive = empty_window("alice", "cli", "session")
        for number in range(1, 6):
            archive["text"]["messages"].extend(
                [
                    {"role": "user", "content": f"u{number}"},
                    {"role": "assistant", "content": f"a{number}"},
                ]
            )
            archive["think"]["rounds"].append(
                {"round": number, "content": f"t{number}"}
            )
            archive["tool"]["rounds"].append({"round": number, "calls": []})
            archive["items"]["items"].append(
                {"id": f"i{number}", "metadata": {"round": number}}
            )
            archive["data"]["round_metrics"].append({"round": number})
        archive["data"]["rounds"] = 5
        archive["data"]["context"] = {"rounds_removed": 3}
        archive["data"]["summary_cache"] = "must-not-archive"
        commit_window(archive_path, archive)

        raw_archive = json.loads((archive_path / "data.json").read_text("utf-8"))
        self.assertEqual(
            set(raw_archive),
            {
                "schema_version",
                "user",
                "source",
                "session_id",
                "title",
                "created_at",
                "updated_at",
                "rounds",
                "round_metrics",
                "token_usage",
                "complete",
            },
        )
        self.assertNotIn("context", raw_archive)
        self.assertNotIn("summary_cache", raw_archive)
        runtime_path, runtime = load_runtime_window(
            archive_path,
            load_window(archive_path),
            max_rounds=2,
        )
        self.assertEqual(runtime_path, runtime_window_path(archive_path))
        self.assertEqual(runtime["data"]["rounds"], 2)
        self.assertEqual(runtime["data"]["context"]["round_offset"], 3)
        self.assertEqual(
            [item["content"] for item in runtime["text"]["messages"]],
            ["u4", "a4", "u5", "a5"],
        )
        commit_window(runtime_path, runtime)
        raw_runtime = json.loads((runtime_path / "data.json").read_text("utf-8"))
        self.assertIn("context", raw_runtime)

    def test_archive_append_maps_local_temp_round_to_absolute_round(self) -> None:
        archive = empty_window("alice", "cli", "session")
        archive["data"]["rounds"] = 5
        archive["data"]["round_metrics"] = [
            {"round": number, "usage": {"total_tokens": number}}
            for number in range(1, 6)
        ]
        runtime = empty_window("alice", "cli", "session")
        runtime["text"]["messages"] = [
            {"role": "user", "content": "new"},
            {"role": "assistant", "content": "reply"},
        ]
        runtime["think"]["rounds"] = [{"round": 3, "content": "reason"}]
        runtime["tool"]["rounds"] = [{"round": 3, "calls": []}]
        runtime["items"]["items"] = [
            {"id": "new-item", "metadata": {"round": 3}}
        ]
        runtime["data"]["rounds"] = 3
        runtime["data"]["round_metrics"] = [
            {"round": 3, "usage": {"total_tokens": 6}}
        ]
        runtime["data"]["context"] = {"round_offset": 3}

        copy_committed_round_to_archive(archive, runtime, 3, 6)

        self.assertEqual(archive["data"]["rounds"], 6)
        self.assertEqual(archive["data"]["round_metrics"][-1]["round"], 6)
        self.assertEqual(archive["think"]["rounds"][-1]["round"], 6)
        self.assertEqual(archive["tool"]["rounds"][-1]["round"], 6)
        self.assertEqual(archive["items"]["items"][-1]["metadata"]["round"], 6)
        self.assertNotIn("context", archive["data"])

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

    def test_context_summary_cache_retries_transient_replace_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "context_summary.json"
            original_replace = os.replace
            attempts = 0

            def briefly_locked(source: Path, destination: Path) -> None:
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise PermissionError(errno.EACCES, "temporarily locked")
                original_replace(source, destination)

            with (
                patch("run.atomic_io.os.replace", side_effect=briefly_locked),
                patch("run.atomic_io.time.sleep") as sleep,
            ):
                write_summary_cache(target, {"schema_version": 3, "summary": "ok"})

            self.assertEqual(attempts, 3)
            self.assertEqual(sleep.call_count, 2)
            self.assertEqual(json.loads(target.read_text("utf-8"))["summary"], "ok")
            self.assertEqual(list(target.parent.glob(".*.tmp")), [])

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
        self.assertEqual([item.number for item in selected.kept_rounds], [3, 4])
        self.assertEqual(len(selected.removed_rounds), 2)
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

    def test_legacy_empty_message_keeps_round_boundary_without_invalid_native_item(self) -> None:
        window = empty_window("alice", "web", "legacy-empty")
        window["data"]["rounds"] = 1
        window["items"]["items"] = [
            {
                "id": "msg_empty_user",
                "type": "message",
                "status": "completed",
                "role": "user",
                "content": [],
                "metadata": {"round": 1},
                "extensions": {},
            },
            {
                "id": "msg_answer",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "phase": "final_answer",
                "content": [{"type": "text", "text": "旧回复"}],
                "metadata": {"round": 1},
                "extensions": {},
            },
        ]

        groups = build_round_groups(window, ContextPolicy())

        self.assertEqual(len(groups), 1)
        self.assertEqual([item["role"] for item in groups[0].messages], ["user", "assistant"])
        legacy_user = groups[0].messages[0]
        self.assertIn("历史兼容", legacy_user["content"])
        self.assertNotIn("_kemo_message", legacy_user)

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

    def test_summary_source_includes_reasoning_and_schema_upgrade_invalidates_cache(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        cache_path = Path(temporary.name) / "summary.json"
        window = make_window(1)
        window["think"]["rounds"][0]["content"] = "关键判断：使用已验证的路径"
        groups = build_round_groups(window, ContextPolicy())
        provider = SummaryRunner()
        cache_path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "source_hash": "old",
                    "summary": {"narrative": "旧缓存"},
                },
                ensure_ascii=False,
            ),
            "utf-8",
        )

        value, diagnostics = get_or_create_summary(
            cache_path=cache_path,
            groups=groups,
            agent_runner=provider,
            agent_name="context_manage",
            trigger="manual",
        )

        self.assertTrue(diagnostics["generated"])
        self.assertIsNotNone(value)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(
            provider.inputs[0]["rounds"][0]["reasoning"]["content"],
            "关键判断：使用已验证的路径",
        )

    def test_context_summary_requests_twenty_thousand_output_tokens(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        provider = SummaryRunner()
        groups = build_round_groups(make_window(1), ContextPolicy())
        get_or_create_summary(
            cache_path=Path(temporary.name) / "summary.json",
            groups=groups,
            agent_runner=provider,
            agent_name="context_manage",
            trigger="manual",
        )
        self.assertEqual(provider.kwargs[0]["max_tokens"], 20_000)

    def test_summary_cache_rolls_forward_using_absolute_round_numbers(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        cache_path = Path(temporary.name) / "summary.json"
        provider = SummaryRunner()

        first_groups = build_round_groups(make_window(2), ContextPolicy())
        first, first_diagnostics = get_or_create_summary(
            cache_path=cache_path,
            groups=first_groups,
            agent_runner=provider,
            agent_name="context_manage",
            trigger="round_limit",
        )
        self.assertTrue(first_diagnostics["generated"])
        self.assertEqual(first["covered_through_round"], 2)

        second_groups = build_round_groups(make_window(2), ContextPolicy())
        second, second_diagnostics = get_or_create_summary(
            cache_path=cache_path,
            groups=second_groups,
            agent_runner=provider,
            agent_name="context_manage",
            trigger="round_limit",
            previous_cache=first,
            round_offset=2,
        )

        self.assertTrue(second_diagnostics["generated"])
        self.assertEqual(second_diagnostics["new_rounds"], [3, 4])
        self.assertEqual(second["covered_rounds"], [1, 2, 3, 4])
        self.assertEqual(second["covered_through_round"], 4)
        self.assertEqual(provider.calls, 2)
        self.assertEqual(provider.inputs[1]["previous_summary"], first["summary"])
        self.assertEqual(
            [item["round"] for item in provider.inputs[1]["rounds"]],
            [3, 4],
        )

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
        diagnostics = compress_per_round_tool_think(
            window=window,
            conserved_rounds=2,
            agent_runner=runner,
            cancel_event=None,
        )
        self.assertTrue(diagnostics["compressed"])
        self.assertEqual(diagnostics["round"], 1)
        self.assertEqual(runner.calls, 1)
        self.assertEqual(runner.kwargs[0]["max_tokens"], 20_000)
        self.assertTrue(window["think"]["rounds"][0]["compressed"])
        self.assertIn("compressed", window["think"]["rounds"][0]["content"])
        self.assertEqual(window["tool"]["rounds"][0]["calls"], [])
        self.assertFalse(window["think"]["rounds"][1].get("compressed", False))

    def test_tool_think_compression_rejects_empty_summary_without_mutating_data(self) -> None:
        window = make_window(5, with_tools=True)
        original_reasoning = {
            "id": "rs_original",
            "type": "reasoning",
            "status": "completed",
            "content": "original reasoning",
            "metadata": {"round": 1},
        }
        window["items"] = {
            "items": [
                original_reasoning,
                {
                    "id": "msg_answer",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "answer"}],
                    "metadata": {"round": 1},
                },
            ]
        }

        with self.assertRaisesRegex(RuntimeError, "摘要为空"):
            compress_per_round_tool_think(
                window=window,
                conserved_rounds=2,
                agent_runner=SummaryRunner(narrative="", include_fact=False),
                cancel_event=None,
            )

        self.assertEqual(window["think"]["rounds"][0]["content"], "think-1")
        self.assertEqual(window["tool"]["rounds"][0]["calls"][0]["name"], "lookup")
        self.assertEqual(window["items"]["items"][0], original_reasoning)

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
