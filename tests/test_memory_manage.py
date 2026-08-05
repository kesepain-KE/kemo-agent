from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from plugins.memory_manage.memory_ops import (
    add_fragment,
    delete_fragment,
    edit_fragment,
    get_fragment,
    list_entries,
    search_by_content,
    search_many,
    search_by_title,
)
from plugins.memory_manage.tool import run as run_memory_manage
from run.memory import MemoryError as RuntimeMemoryError, MemoryStore
from run.tools import discover_tools, validate_arguments


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class MemoryManageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = {}
        (self.root / "config").mkdir()
        (self.root / "users" / "alice").mkdir(parents=True)
        (self.root / "users" / "bob").mkdir(parents=True)
        (self.root / "config" / "global_config.json").write_text("{}", "utf-8")
        for user in ("alice", "bob"):
            (self.root / "users" / user / "user_config.json").write_text(
                "{}",
                "utf-8",
            )

    def test_temporary_crud_keeps_index_in_sync(self) -> None:
        added = add_fragment(
            self.root,
            "alice",
            self.config,
            "seven_days",
            "editor preference",
            "User prefers Vim.",
        )
        filename = added["filename"]
        store = MemoryStore(self.root, "alice", self.config)
        self.assertIsNotNone(store.get_entry("seven_days", filename))
        self.assertEqual(added["memory_ref"], f"seven_days:{filename}")
        self.assertEqual(
            search_by_title(self.root, "alice", self.config, "seven_days", "editor")[
                "matches"
            ][0]["filename"],
            filename,
        )
        self.assertIn(
            "Vim",
            search_by_content(self.root, "alice", self.config, "seven_days", "prefers")[
                "matches"
            ][0]["snippet"],
        )

        edited = edit_fragment(
            self.root,
            "alice",
            self.config,
            "seven_days",
            filename,
            "User prefers Neovim.",
            new_filename="terminal editor",
        )
        renamed = edited["new_filename"]
        self.assertIsNone(store.get_entry("seven_days", filename))
        self.assertIsNotNone(store.get_entry("seven_days", renamed))
        self.assertEqual(
            store.get_entry("seven_days", renamed)["content"],
            "User prefers Neovim.",
        )

        deleted = delete_fragment(
            self.root, "alice", self.config, "seven_days", renamed
        )
        self.assertTrue(deleted["deleted"])
        self.assertIsNone(store.get_entry("seven_days", renamed))

    def test_cross_tier_duplicate_names_are_rejected(self) -> None:
        store = MemoryStore(self.root, "alice", self.config)
        filename = "shared-device.md"
        store.create_fragment("seven_days", filename, "content from seven days")
        with self.assertRaises(FileExistsError):
            store.create_fragment("one_month", filename, "content from one month")
        renamed = edit_fragment(
            self.root,
            "alice",
            self.config,
            "seven_days",
            filename,
            "renamed content",
            new_filename="renamed-device.md",
        )["new_filename"]
        self.assertEqual(
            get_fragment(self.root, "alice", self.config, "seven_days", renamed)[
                "content"
            ],
            "renamed content",
        )
        deleted = delete_fragment(
            self.root, "alice", self.config, "seven_days", renamed
        )
        self.assertTrue(deleted["deleted"])
        self.assertEqual(deleted["memory_ref"], f"seven_days:{renamed}")
        self.assertTrue(deleted["row_removed"])
        self.assertFalse(deleted["index_removed"])
        self.assertFalse(deleted["file_removed"])
        self.assertFalse(deleted["repaired_orphan"])
        self.assertEqual(store.integrity_issues(), [])

    def test_permanent_and_important_documents_are_managed(self) -> None:
        permanent = add_fragment(
            self.root,
            "alice",
            self.config,
            "permanent",
            "stable identity",
            "The user is Alice.",
        )
        self.assertTrue(
            delete_fragment(
                self.root,
                "alice",
                self.config,
                "permanent",
                permanent["filename"],
            )["deleted"]
        )

        add_fragment(
            self.root,
            "alice",
            self.config,
            "important",
            "ignored",
            "# Hot profile\n\n- Alice",
        )
        important = get_fragment(
            self.root,
            "alice",
            self.config,
            "important",
            "memory_temporary_important.md",
        )
        self.assertIn("Hot profile", important["content"])
        self.assertIsNone(important["weight"])
        self.assertIsNone(important["expires_at"])
        edit_fragment(
            self.root,
            "alice",
            self.config,
            "important",
            "memory_temporary_important.md",
            "# Updated profile",
        )
        with self.assertRaisesRegex(RuntimeMemoryError, "不可删除"):
            delete_fragment(
                self.root,
                "alice",
                self.config,
                "important",
                "memory_temporary_important.md",
            )
        self.assertTrue(
            (self.root / "users" / "alice" / "memory_temporary_important.md").is_file()
        )

    def test_user_isolation_and_sensitive_content_rejection(self) -> None:
        add_fragment(
            self.root,
            "alice",
            self.config,
            "one_month",
            "private preference",
            "Alice likes concise replies.",
        )
        self.assertEqual(
            search_by_content(self.root, "bob", self.config, "one_month", "Alice")[
                "matches"
            ],
            [],
        )
        with self.assertRaisesRegex(ValueError, "敏感凭据"):
            add_fragment(
                self.root,
                "alice",
                self.config,
                "permanent",
                "secret",
                "api_key=abcd1234",
            )

    def test_self_improve_can_search_but_cannot_mutate_directly(self) -> None:
        context = {
            "root": str(self.root),
            "user": "alice",
            "agent": "self_improve",
            "agent_trigger": "context_compression",
        }
        self.assertEqual(
            run_memory_manage(
                "search_by_title",
                "seven_days",
                query="missing",
                context=context,
            )["matches"],
            [],
        )
        with self.assertRaises(PermissionError):
            run_memory_manage("list", "seven_days", context=context)
        with self.assertRaises(PermissionError):
            run_memory_manage(
                "get",
                "seven_days",
                filename="blocked.md",
                context=context,
            )
        with self.assertRaises(PermissionError):
            run_memory_manage(
                "add",
                "seven_days",
                filename="blocked",
                content="must be persisted by runtime",
                context=context,
            )

    def test_background_memory_work_cannot_read_important_but_user_review_can(
        self,
    ) -> None:
        important = self.root / "users" / "alice" / "memory_temporary_important.md"
        important.write_text("# 临时重要记忆\n\n- 用户偏好简洁回复。", "utf-8")

        for trigger in ("context_compression", "memory_promotion"):
            with self.subTest(trigger=trigger):
                with self.assertRaisesRegex(PermissionError, "禁止读取 important"):
                    run_memory_manage(
                        "search_by_content",
                        "important",
                        query="简洁",
                        context={
                            "root": str(self.root),
                            "user": "alice",
                            "agent": "self_improve",
                            "agent_trigger": trigger,
                        },
                    )

        manual = run_memory_manage(
            "search_by_content",
            "important",
            query="简洁",
            context={
                "root": str(self.root),
                "user": "alice",
                "agent": "self_improve",
                "agent_trigger": "manual_review",
            },
        )
        self.assertEqual(manual["total_matches"], 1)

        direct = run_memory_manage(
            "get",
            "important",
            filename="memory_temporary_important.md",
            context={"root": str(self.root), "user": "alice", "caller": "main_agent"},
        )
        self.assertIn("简洁回复", direct["content"])

    def test_direct_memory_reads_do_not_change_temporary_weight(self) -> None:
        added = add_fragment(
            self.root,
            "alice",
            self.config,
            "seven_days",
            "只读记忆",
            "用户明确偏好只读查看。",
        )
        filename = added["filename"]
        context = {"root": str(self.root), "user": "alice", "caller": "main_agent"}

        get_fragment(self.root, "alice", self.config, "seven_days", filename)
        run_memory_manage(
            "search_by_content",
            "seven_days",
            query="只读查看",
            context=context,
        )

        metadata = MemoryStore(self.root, "alice", self.config).get_entry(
            "seven_days", filename
        )
        self.assertEqual(metadata["weight"], 0)
        self.assertIsNone(metadata["last_weight_date"])

    def test_important_memory_agent_can_read_but_cannot_mutate_directly(self) -> None:
        add_fragment(
            self.root,
            "alice",
            self.config,
            "seven_days",
            "source",
            "stable source",
        )
        context = {
            "root": str(self.root),
            "user": "alice",
            "agent": "memory_temporary_important",
            "agent_trigger": "periodic_scan",
        }
        listed = run_memory_manage("list", "seven_days", context=context)
        self.assertEqual(listed["total"], 1)
        with self.assertRaises(PermissionError):
            run_memory_manage(
                "delete",
                "seven_days",
                filename="source.md",
                context=context,
            )
        with self.assertRaises(PermissionError):
            run_memory_manage(
                "add",
                "seven_days",
                filename="blocked",
                content="must be persisted by runtime",
                context=context,
            )

    def test_list_and_get_return_bounded_metadata_and_exact_content(self) -> None:
        filenames = []
        for index in range(4):
            filenames.append(
                add_fragment(
                    self.root,
                    "alice",
                    self.config,
                    "half_year",
                    f"device {index}",
                    f"Device {index} details",
                )["filename"]
            )
        listed = list_entries(self.root, "alice", self.config, "half_year", limit=3)
        self.assertEqual(listed["total"], 4)
        self.assertEqual(len(listed["entries"]), 3)
        self.assertTrue(listed["truncated"])
        self.assertNotIn("content", listed["entries"][0])
        self.assertEqual(
            listed["entries"][0]["memory_ref"],
            f"half_year:{listed['entries'][0]['filename']}",
        )
        self.assertIsInstance(listed["entries"][0]["weight"], int)
        self.assertIsNotNone(listed["entries"][0]["expires_at"])

        fetched = get_fragment(
            self.root, "alice", self.config, "half_year", filenames[0]
        )
        self.assertEqual(fetched["content"], "Device 0 details")
        self.assertEqual(fetched["filename"], filenames[0])
        self.assertIsInstance(fetched["weight"], int)
        routed = run_memory_manage(
            "get",
            "half_year",
            filename=filenames[0],
            context={"root": str(self.root), "user": "alice"},
        )
        self.assertEqual(routed["content"], "Device 0 details")

        permanent = add_fragment(
            self.root,
            "alice",
            self.config,
            "permanent",
            "stable profile",
            "Permanent body",
        )
        permanent_list = list_entries(self.root, "alice", self.config, "permanent")
        self.assertIsNone(permanent_list["entries"][0]["weight"])
        self.assertIsNone(permanent_list["entries"][0]["expires_at"])
        compact_permanent = list_entries(
            self.root,
            "alice",
            self.config,
            "permanent",
            compact=True,
        )
        self.assertEqual(
            set(compact_permanent["entries"][0]),
            {"memory_ref", "filename", "weight"},
        )
        permanent_get = get_fragment(
            self.root,
            "alice",
            self.config,
            "permanent",
            permanent["filename"],
        )
        self.assertIsNone(permanent_get["weight"])
        self.assertEqual(permanent_get["content"], "Permanent body")

    def test_list_supports_compact_pagination_without_losing_entries(self) -> None:
        filenames = [
            add_fragment(
                self.root,
                "alice",
                self.config,
                "seven_days",
                f"paged memory {index}",
                f"Paged memory body {index}",
            )["filename"]
            for index in range(5)
        ]

        first = list_entries(
            self.root,
            "alice",
            self.config,
            "seven_days",
            limit=2,
            offset=0,
            compact=True,
        )
        self.assertEqual(first["total"], 5)
        self.assertEqual(first["offset"], 0)
        self.assertEqual(first["next_offset"], 2)
        self.assertTrue(first["has_more"])
        self.assertTrue(first["truncated"])
        self.assertTrue(first["compact"])
        self.assertEqual(
            set(first["entries"][0]),
            {"memory_ref", "filename", "weight"},
        )

        second = list_entries(
            self.root,
            "alice",
            self.config,
            "seven_days",
            limit=2,
            offset=first["next_offset"],
            compact=True,
        )
        self.assertEqual(second["offset"], 2)
        self.assertEqual(second["next_offset"], 4)
        self.assertTrue(second["has_more"])

        final = list_entries(
            self.root,
            "alice",
            self.config,
            "seven_days",
            limit=2,
            offset=second["next_offset"],
            compact=True,
        )
        self.assertEqual(final["offset"], 4)
        self.assertIsNone(final["next_offset"])
        self.assertFalse(final["has_more"])
        self.assertFalse(final["truncated"])
        self.assertEqual(
            [
                entry["filename"]
                for page in (first, second, final)
                for entry in page["entries"]
            ],
            filenames,
        )

        detailed = list_entries(
            self.root,
            "alice",
            self.config,
            "seven_days",
            limit=1,
        )
        self.assertFalse(detailed["compact"])
        for field in (
            "created_at",
            "content_updated_at",
            "last_used_at",
            "expires_at",
        ):
            self.assertIn(field, detailed["entries"][0])

    def test_search_is_bounded_case_aware_and_never_returns_full_content(self) -> None:
        long_body = "prefix " * 80 + "RaspberryPi" + " suffix" * 80
        for index in range(5):
            add_fragment(
                self.root,
                "alice",
                self.config,
                "one_month",
                f"Device Profile {index}",
                long_body,
            )

        content_result = search_by_content(
            self.root,
            "alice",
            self.config,
            "one_month",
            "raspberrypi",
            limit=3,
            context_chars=80,
        )
        self.assertEqual(content_result["total_matches"], 5)
        self.assertEqual(len(content_result["matches"]), 3)
        self.assertTrue(content_result["truncated"])
        for match in content_result["matches"]:
            self.assertNotIn("content", match)
            self.assertEqual(match["memory_ref"], f"one_month:{match['filename']}")
            self.assertLessEqual(len(match["snippet"]), 80)
            self.assertIn("RaspberryPi", match["snippet"])
            self.assertTrue(match["snippet"].startswith("…"))
            self.assertTrue(match["snippet"].endswith("…"))

        self.assertEqual(
            search_by_content(
                self.root,
                "alice",
                self.config,
                "one_month",
                "raspberrypi",
                case_sensitive=True,
            )["total_matches"],
            0,
        )
        title_result = search_by_title(
            self.root,
            "alice",
            self.config,
            "one_month",
            "device profile",
            limit=2,
        )
        self.assertEqual(title_result["total_matches"], 5)
        self.assertEqual(len(title_result["matches"]), 2)
        self.assertTrue(title_result["truncated"])
        self.assertEqual(
            search_by_title(
                self.root,
                "alice",
                self.config,
                "one_month",
                "device profile",
                case_sensitive=True,
            )["total_matches"],
            0,
        )
        with self.assertRaisesRegex(ValueError, "list action"):
            search_by_content(self.root, "alice", self.config, "one_month", "")
        with self.assertRaisesRegex(ValueError, "list action"):
            search_by_title(self.root, "alice", self.config, "one_month", "")

    def test_search_many_matches_a_batch_across_memory_tiers(self) -> None:
        add_fragment(
            self.root,
            "alice",
            self.config,
            "seven_days",
            "回答偏好",
            "用户偏好简洁回答。",
        )
        add_fragment(
            self.root,
            "alice",
            self.config,
            "permanent",
            "设备信息",
            "用户的主力设备是工作站。",
        )

        result = search_many(
            self.root,
            "alice",
            self.config,
            "all",
            [
                {"title": "回答偏好", "content": "简洁回答"},
                {"title": "设备", "content": "主力设备"},
            ],
        )

        self.assertEqual(len(result["results"]), 2)
        self.assertEqual(
            result["results"][0]["matches"][0]["memory_ref"],
            "seven_days:回答偏好.md",
        )
        self.assertEqual(
            result["results"][0]["matches"][0]["matched_by"],
            ["title", "content"],
        )
        self.assertEqual(
            result["results"][1]["matches"][0]["memory_ref"],
            "permanent:设备信息.md",
        )

    def test_single_search_actions_accept_all_memory_fragment_tiers(self) -> None:
        for tier, title in (
            ("seven_days", "STM32 临时记录"),
            ("one_month", "STM32 月度记录"),
            ("half_year", "STM32 半年记录"),
            ("permanent", "STM32 永久记录"),
        ):
            add_fragment(
                self.root,
                "alice",
                self.config,
                tier,
                title,
                f"{title}：使用 STM32 控制器。",
            )

        content_result = run_memory_manage(
            "search_by_content",
            "all",
            query="stm32",
            limit=20,
            context={"root": str(self.root), "user": "alice"},
        )
        self.assertEqual(content_result["tier"], "all")
        self.assertEqual(content_result["total_matches"], 4)
        self.assertFalse(content_result["truncated"])
        self.assertEqual(
            {match["tier"] for match in content_result["matches"]},
            {"seven_days", "one_month", "half_year", "permanent"},
        )
        self.assertTrue(
            all(
                match["memory_ref"].startswith(f"{match['tier']}:")
                for match in content_result["matches"]
            )
        )

        title_result = search_by_title(
            self.root,
            "alice",
            self.config,
            "all",
            "STM32",
            limit=2,
        )
        self.assertEqual(title_result["total_matches"], 4)
        self.assertEqual(len(title_result["matches"]), 2)
        self.assertTrue(title_result["truncated"])
        with self.assertRaisesRegex(ValueError, "不支持的记忆层级：all"):
            list_entries(self.root, "alice", self.config, "all")

    def test_manifest_exposes_batch_search_and_bounded_parameters(self) -> None:
        tool = discover_tools(PROJECT_ROOT, "kesepain").get("memory_manage")
        self.assertEqual(tool.version, "1.6.0")
        schema = tool.input_schema
        self.assertEqual(
            set(schema["properties"]["action"]["enum"]),
            {
                "list",
                "get",
                "search_by_title",
                "search_by_content",
                "search_many",
                "add",
                "edit",
                "delete",
            },
        )
        for field in (
            "queries",
            "limit",
            "offset",
            "compact",
            "context_chars",
            "case_sensitive",
        ):
            self.assertIn(field, schema["properties"])
        validate_arguments(
            schema,
            {
                "action": "list",
                "tier": "seven_days",
                "limit": 100,
                "offset": 200,
                "compact": True,
            },
        )
        validate_arguments(
            schema,
            {
                "action": "search_by_content",
                "tier": "half_year",
                "query": "树莓派",
                "limit": 3,
                "context_chars": 500,
                "case_sensitive": False,
            },
        )
        validate_arguments(
            schema,
            {
                "action": "search_many",
                "tier": "all",
                "queries": [{"title": "树莓派", "content": "主机配置"}],
            },
        )


if __name__ == "__main__":
    unittest.main()
