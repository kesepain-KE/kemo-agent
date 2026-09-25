from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from plugins.history_search.tool import run
from plugins.manifest import discover_plugin_manifests
from run.history import delete_session_windows, save_window


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class HistorySearchPluginTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.history = self.root / "users" / "alice" / "history"
        self.history.mkdir(parents=True)
        self.context = {"root": str(self.root), "user": "alice"}

    def write_window(
        self,
        name: str,
        messages: list[dict[str, object]],
        *,
        complete: bool = True,
        created_at: str = "",
        updated_at: str = "",
        source: str = "web",
        session_id: str = "",
    ) -> None:
        window = self.history / name
        data = {
            "complete": complete,
            "source": source,
            "session_id": session_id or f"session-{name}",
        }
        if created_at:
            data["created_at"] = created_at
        if updated_at:
            data["updated_at"] = updated_at
        if complete:
            save_window(
                window,
                {
                    "data": data,
                    "text": {"schema_version": 1, "messages": messages},
                    "think": {"schema_version": 1, "rounds": []},
                    "tool": {"schema_version": 1, "rounds": []},
                    "items": {"schema_version": 2, "items": []},
                },
            )

    def test_time_and_role_filters_return_stable_metadata(self) -> None:
        self.write_window(
            "2026-07-19-09-00",
            [{"role": "user", "content": "needle old"}],
        )
        self.write_window(
            "2026-07-20-15-30",
            [
                {"role": "user", "content": "needle from user"},
                {"role": "assistant", "content": "needle from assistant"},
            ],
        )
        self.write_window(
            "2026-07-21-09-00",
            [{"role": "user", "content": "needle new"}],
        )
        (self.history / "temp").mkdir()

        result = run(
            "needle",
            since="2026-07-20",
            until="2026-07-20",
            role="user",
            context=self.context,
        )

        self.assertEqual(result["total_matches"], 1)
        self.assertFalse(result["truncated"])
        self.assertEqual(
            result["time_range"],
            {"since": "2026-07-20", "until": "2026-07-20"},
        )
        self.assertEqual(result["matches"][0]["window"], "2026-07-20-15-30")
        self.assertEqual(result["matches"][0]["source"], "web")
        self.assertEqual(result["matches"][0]["session_id"], "session-2026-07-20-15-30")
        self.assertEqual(result["matches"][0]["match_index"], 0)
        self.assertNotIn("context", result["matches"][0])

    def test_opaque_windows_use_metadata_dates_and_sort_by_updated_time(self) -> None:
        self.write_window(
            "conv_z_older",
            [{"role": "user", "content": "opaque needle older"}],
            created_at="2026-07-19T16:30:00+00:00",
            updated_at="2026-07-20T01:00:00+00:00",
        )
        self.write_window(
            "conv_a_newer",
            [{"role": "assistant", "content": "opaque needle newer"}],
            created_at="2026-07-21T01:00:00Z",
            updated_at="2026-07-21T02:00:00Z",
        )
        self.write_window(
            "conv_no_timestamp",
            [{"role": "user", "content": "opaque needle without date"}],
        )

        all_matches = run("opaque needle", context=self.context)
        self.assertEqual(all_matches["total_matches"], 3)
        self.assertEqual(
            [item["window"] for item in all_matches["matches"]],
            ["conv_a_newer", "conv_z_older", "conv_no_timestamp"],
        )

        beijing_day = run(
            "opaque needle",
            since="2026-07-20",
            until="2026-07-20",
            context=self.context,
        )
        self.assertEqual(beijing_day["total_matches"], 1)
        self.assertEqual(beijing_day["matches"][0]["window"], "conv_z_older")

    def test_word_exact_substring_and_regex_modes(self) -> None:
        self.write_window(
            "2026-07-20-15-30",
            [
                {"role": "user", "content": "main"},
                {"role": "user", "content": "email"},
                {"role": "assistant", "content": "AI is great"},
                {"role": "user", "content": " AI "},
                {
                    "role": "assistant",
                    "content": "设备 IP 当前为 192.168.10.110，连接正常",
                },
            ],
        )

        substring = run("AI", match_mode="substring", context=self.context)
        self.assertEqual(substring["total_matches"], 4)
        word = run("AI", match_mode="word", context=self.context)
        self.assertEqual([item["match_index"] for item in word["matches"]], [2, 3])
        exact = run("AI", match_mode="exact", context=self.context)
        self.assertEqual([item["match_index"] for item in exact["matches"]], [3])
        regex = run(
            r"IP.*\d+\.\d+\.\d+\.\d+",
            regex=True,
            match_mode="exact",
            context=self.context,
        )
        self.assertEqual(regex["total_matches"], 1)
        self.assertIn("192.168.10.110", regex["matches"][0]["snippet"])

    def test_snippet_limit_and_context_index_ignore_non_message_roles(self) -> None:
        long_content = "前缀" * 100 + "树莓派" + "后缀" * 100
        self.write_window(
            "2026-07-20-15-30",
            [
                {"role": "user", "content": "前一条用户消息"},
                {"role": "system", "content": "不应出现在上下文"},
                {"role": "assistant", "content": long_content},
                {"role": "tool", "content": "不应出现在上下文"},
                {"role": "user", "content": "后一条用户消息"},
            ],
        )

        result = run(
            "树莓派",
            max_snippet=40,
            context_messages=2,
            context=self.context,
        )
        match = result["matches"][0]
        self.assertLessEqual(len(match["snippet"]), 40)
        self.assertTrue(match["snippet"].startswith("…"))
        self.assertTrue(match["snippet"].endswith("…"))
        self.assertIn("树莓派", match["snippet"])
        self.assertEqual(
            [item["role"] for item in match["context"]],
            ["user", "assistant", "user"],
        )
        self.assertEqual(match["context_index"], 1)

    def test_limit_counts_all_matches_and_reports_truncation(self) -> None:
        self.write_window(
            "2026-07-20-15-30",
            [{"role": "user", "content": f"hit {index}"} for index in range(10)],
        )
        result = run("hit", limit=3, context=self.context)
        self.assertEqual(len(result["matches"]), 3)
        self.assertEqual(result["total_matches"], 10)
        self.assertTrue(result["truncated"])

    def test_source_session_pagination_and_character_budget(self) -> None:
        self.write_window(
            "2026-09-26-web",
            [{"role": "user", "content": f"paged hit {index} " + "x" * 300} for index in range(5)],
            created_at="2026-09-26T01:00:00+08:00",
            updated_at="2026-09-26T01:05:00+08:00",
            source="web",
            session_id="web-session",
        )
        self.write_window(
            "2026-09-26-cli",
            [{"role": "user", "content": "paged hit from cli"}],
            created_at="2026-09-26T02:00:00+08:00",
            updated_at="2026-09-26T02:05:00+08:00",
            source="cli",
            session_id="cli-session",
        )

        first = run(
            "paged hit",
            source="web",
            session_id="web-session",
            limit=5,
            max_snippet=320,
            page_char_limit=1000,
            context=self.context,
        )
        self.assertEqual(first["total_matches"], 5)
        self.assertEqual(first["filters"], {"source": "web", "session_id": "web-session"})
        self.assertTrue(first["has_more"])
        self.assertTrue(first["page_limited_by_chars"])
        self.assertGreater(first["next_offset"], 0)
        self.assertNotIn("cli", " ".join(item["snippet"] for item in first["matches"]))

        second = run(
            "paged hit",
            source="web",
            session_id="web-session",
            offset=first["next_offset"],
            limit=5,
            max_snippet=320,
            page_char_limit=1000,
            context=self.context,
        )
        self.assertEqual(second["offset"], first["next_offset"])
        self.assertGreater(len(second["matches"]), 0)
        self.assertEqual(
            first["next_offset"] + len(second["matches"]),
            second["next_offset"] or second["total_matches"],
        )

    def test_multimodal_text_uses_structured_text_column_and_preserves_index(self) -> None:
        self.write_window(
            "2026-09-26-multimodal",
            [
                {"role": "system", "content": "internal"},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "multimodal needle"},
                        {"type": "image_url", "image_url": {"url": "asset://1"}},
                    ],
                },
                {"role": "tool", "content": "private tool output"},
                {"role": "assistant", "content": "visible reply"},
            ],
            created_at="2026-09-26T03:00:00+08:00",
        )

        result = run(
            "multimodal needle",
            context_messages=1,
            max_context_chars=50,
            context=self.context,
        )
        self.assertEqual(result["total_matches"], 1)
        self.assertEqual(result["matches"][0]["match_index"], 1)
        self.assertEqual(
            result["matches"][0]["context"],
            [
                {"role": "user", "content": "multimodal needle"},
                {"role": "assistant", "content": "visible reply"},
            ],
        )

    def test_deleted_session_is_never_returned_even_if_search_was_previously_visible(self) -> None:
        name = "2026-09-26-deleted"
        session_id = "deleted-session"
        self.write_window(
            name,
            [{"role": "user", "content": "deleted needle"}],
            source="app",
            session_id=session_id,
        )
        self.assertEqual(run("deleted needle", context=self.context)["total_matches"], 1)
        delete_session_windows(self.root, "alice", "app", session_id)
        self.assertEqual(run("deleted needle", context=self.context)["total_matches"], 0)

    def test_validation_empty_result_and_manifest_contract(self) -> None:
        empty = run("  ", since="2026-07-20", context=self.context)
        self.assertEqual(empty["matches"], [])
        self.assertEqual(empty["time_range"], {"since": "2026-07-20", "until": None})
        with self.assertRaisesRegex(ValueError, "有效日期"):
            run("x", since="2026-02-30", context=self.context)
        with self.assertRaisesRegex(ValueError, "不能晚于"):
            run(
                "x",
                since="2026-07-21",
                until="2026-07-20",
                context=self.context,
            )
        with self.assertRaisesRegex(ValueError, "正则表达式"):
            run("[", regex=True, context=self.context)
        with self.assertRaisesRegex(ValueError, "role 必须"):
            run("x", role="tool", context=self.context)
        with self.assertRaisesRegex(ValueError, "match_mode 必须"):
            run("x", match_mode="fuzzy", context=self.context)

        manifest = next(
            item
            for item in discover_plugin_manifests(PROJECT_ROOT)
            if item.tool["name"] == "history_search"
        )
        self.assertEqual(manifest.tool["version"], "1.2.0")
        self.assertEqual(
            set(manifest.tool["input_schema"]["properties"]),
            {
                "query",
                "limit",
                "since",
                "until",
                "role",
                "match_mode",
                "regex",
                "max_snippet",
                "context_messages",
                "max_context_chars",
                "offset",
                "page_char_limit",
                "source",
                "session_id",
            },
        )


if __name__ == "__main__":
    unittest.main()
