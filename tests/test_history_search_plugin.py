from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from plugins.history_search.tool import run
from plugins.manifest import discover_plugin_manifests


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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
    ) -> None:
        window = self.history / name
        window.mkdir()
        data = {
            "complete": complete,
            "source": "web",
            "session_id": f"session-{name}",
        }
        if created_at:
            data["created_at"] = created_at
        if updated_at:
            data["updated_at"] = updated_at
        (window / "data.json").write_text(
            json.dumps(data),
            "utf-8",
        )
        (window / "text.json").write_text(
            json.dumps({"messages": messages}, ensure_ascii=False), "utf-8"
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
        self.assertEqual(
            result["matches"][0]["session_id"], "session-2026-07-20-15-30"
        )
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

    def test_validation_empty_result_and_manifest_contract(self) -> None:
        empty = run("  ", since="2026-07-20", context=self.context)
        self.assertEqual(empty["matches"], [])
        self.assertEqual(
            empty["time_range"], {"since": "2026-07-20", "until": None}
        )
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
        self.assertEqual(manifest.tool["version"], "1.1.1")
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
            },
        )


if __name__ == "__main__":
    unittest.main()
