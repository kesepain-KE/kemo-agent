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
    search_by_title,
)
from plugins.memory_manage.tool import run as run_memory_manage
from run.memory import MemoryStore, utc_now
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
        self.assertIn(filename, store.load_index("seven_days"))
        self.assertEqual(added["memory_ref"], f"seven_days:{filename}")
        self.assertEqual(
            search_by_title(
                self.root, "alice", self.config, "seven_days", "editor"
            )["matches"][0]["filename"],
            filename,
        )
        self.assertIn(
            "Vim",
            search_by_content(
                self.root, "alice", self.config, "seven_days", "prefers"
            )["matches"][0]["snippet"],
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
        self.assertNotIn(filename, store.load_index("seven_days"))
        self.assertIn(renamed, store.load_index("seven_days"))
        self.assertEqual(
            store.fragment_path("seven_days", renamed).read_text("utf-8").strip(),
            "User prefers Neovim.",
        )

        deleted = delete_fragment(
            self.root, "alice", self.config, "seven_days", renamed
        )
        self.assertTrue(deleted["deleted"])
        self.assertNotIn(renamed, store.load_index("seven_days"))
        self.assertFalse(store.fragment_path("seven_days", renamed).exists())

    def test_cross_tier_duplicates_can_be_read_renamed_deleted_and_repaired(self) -> None:
        store = MemoryStore(self.root, "alice", self.config)
        filename = "shared-device.md"
        now = utc_now()
        for tier in ("seven_days", "one_month", "half_year"):
            path = store.fragment_path(tier, filename)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"content from {tier}", "utf-8")
            store.write_index(tier, {filename: store._new_meta(tier, now)})
        permanent_path = store.fragment_path("permanent", filename)
        permanent_path.parent.mkdir(parents=True, exist_ok=True)
        permanent_path.write_text("content from permanent", "utf-8")

        self.assertEqual(
            get_fragment(
                self.root, "alice", self.config, "half_year", filename
            )["content"],
            "content from half_year",
        )
        self.assertEqual(
            get_fragment(
                self.root, "alice", self.config, "half_year", filename
            )["memory_ref"],
            f"half_year:{filename}",
        )
        self.assertEqual(
            get_fragment(
                self.root, "alice", self.config, "permanent", filename
            )["content"],
            "content from permanent",
        )

        renamed = edit_fragment(
            self.root,
            "alice",
            self.config,
            "half_year",
            filename,
            "renamed half-year content",
            new_filename="shared-device-half-year.md",
        )["new_filename"]
        self.assertNotIn(filename, store.load_index("half_year"))
        self.assertIn(renamed, store.load_index("half_year"))
        self.assertIn(filename, store.load_index("seven_days"))
        self.assertIn(filename, store.load_index("one_month"))
        self.assertTrue(permanent_path.is_file())

        deleted = delete_fragment(
            self.root, "alice", self.config, "one_month", filename
        )
        self.assertTrue(deleted["deleted"])
        self.assertEqual(deleted["memory_ref"], f"one_month:{filename}")
        self.assertTrue(deleted["index_removed"])
        self.assertTrue(deleted["file_removed"])
        self.assertFalse(deleted["repaired_orphan"])
        self.assertNotIn(filename, store.load_index("one_month"))
        self.assertIn(filename, store.load_index("seven_days"))
        self.assertTrue(permanent_path.is_file())

        orphan = "orphan.md"
        index = store.load_index("seven_days")
        index[orphan] = store._new_meta("seven_days", now)
        store.write_index("seven_days", index)
        repaired = delete_fragment(
            self.root, "alice", self.config, "seven_days", orphan
        )
        self.assertTrue(repaired["deleted"])
        self.assertTrue(repaired["index_removed"])
        self.assertFalse(repaired["file_removed"])
        self.assertTrue(repaired["repaired_orphan"])
        self.assertNotIn(orphan, store.load_index("seven_days"))

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
        self.assertTrue(
            delete_fragment(
                self.root,
                "alice",
                self.config,
                "important",
                "memory_temporary_important.md",
            )["deleted"]
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
            search_by_content(
                self.root, "bob", self.config, "one_month", "Alice"
            )["matches"],
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
        listed = list_entries(
            self.root, "alice", self.config, "half_year", limit=3
        )
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
        permanent_list = list_entries(
            self.root, "alice", self.config, "permanent"
        )
        self.assertIsNone(permanent_list["entries"][0]["weight"])
        self.assertIsNone(permanent_list["entries"][0]["expires_at"])
        permanent_get = get_fragment(
            self.root,
            "alice",
            self.config,
            "permanent",
            permanent["filename"],
        )
        self.assertIsNone(permanent_get["weight"])
        self.assertEqual(permanent_get["content"], "Permanent body")

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
            self.assertEqual(
                match["memory_ref"], f"one_month:{match['filename']}"
            )
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
            search_by_content(
                self.root, "alice", self.config, "one_month", ""
            )
        with self.assertRaisesRegex(ValueError, "list action"):
            search_by_title(
                self.root, "alice", self.config, "one_month", ""
            )

    def test_manifest_exposes_seven_actions_and_bounded_search_parameters(self) -> None:
        tool = discover_tools(PROJECT_ROOT, "kesepain").get("memory_manage")
        self.assertEqual(tool.version, "1.2.0")
        schema = tool.input_schema
        self.assertEqual(
            set(schema["properties"]["action"]["enum"]),
            {
                "list",
                "get",
                "search_by_title",
                "search_by_content",
                "add",
                "edit",
                "delete",
            },
        )
        for field in ("limit", "context_chars", "case_sensitive"):
            self.assertIn(field, schema["properties"])
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


if __name__ == "__main__":
    unittest.main()
