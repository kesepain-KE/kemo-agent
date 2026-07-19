from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from plugins.memory_manage.memory_ops import (
    add_fragment,
    delete_fragment,
    edit_fragment,
    search_by_content,
    search_by_title,
)
from plugins.memory_manage.tool import run as run_memory_manage
from run.memory import MemoryStore


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
        important = search_by_content(
            self.root, "alice", self.config, "important", ""
        )["matches"]
        self.assertEqual(len(important), 1)
        self.assertIn("Hot profile", important[0]["content"])
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
                query="",
                context=context,
            )["matches"],
            [],
        )
        with self.assertRaises(PermissionError):
            run_memory_manage(
                "add",
                "seven_days",
                filename="blocked",
                content="must be persisted by runtime",
                context=context,
            )


if __name__ == "__main__":
    unittest.main()
