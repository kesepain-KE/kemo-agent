from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from run.config import build_prompt_bundle
from run.memory import MemoryStore, connection


class ImportantMemoryLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "users/alice").mkdir(parents=True)
        (self.root / "users/bob").mkdir(parents=True)
        self.now = datetime.now(timezone.utc)
        self.store = MemoryStore(self.root, "alice", {})
        self.store.create_fragment("seven_days", "source.md", "original fact", now=self.now)
        self.store.set_important_view_sources(["source.md"], now=self.now)
        self.path = self.root / "users/alice/memory_temporary_important.md"
        self.path.write_text("DERIVED_PROFILE", "utf-8")

    def test_valid_and_legacy_status_are_distinct_and_reads_do_not_weight(self) -> None:
        before = self.store.get_entry("seven_days", "source.md")
        self.assertEqual(self.store.important_view_status()["status"], "valid")
        self.assertTrue(self.store.important_view_is_current())
        self.assertEqual(self.store.load_important_view_sources(), {"source.md"})
        self.assertEqual(self.store.get_entry("seven_days", "source.md"), before)
        other = MemoryStore(self.root, "bob", {})
        self.assertEqual(other.important_view_status()["status"], "untracked")
        self.assertTrue(other.important_view_is_current())

    def test_changed_source_invalidates_prompt_until_provenance_is_rebuilt(self) -> None:
        self.store.edit_fragment("seven_days", "source.md", "updated fact", now=self.now)
        status = self.store.important_view_status()
        self.assertEqual(status["reason_codes"], ["source_changed"])
        self.assertFalse(self.store.important_view_is_current())
        self.assertEqual(self.store.load_important_view_sources(), set())
        bundle = build_prompt_bundle(self.root, "alice", {}, plugin_manifests=())
        self.assertNotIn("DERIVED_PROFILE", bundle.text)
        self.assertIn("updated fact", bundle.text)
        self.assertEqual(self.path.read_text("utf-8"), "DERIVED_PROFILE")
        self.store.set_important_view_sources(["source.md"])
        self.assertEqual(self.store.important_view_status()["status"], "valid")

    def test_deleted_and_promoted_sources_have_honest_short_reasons(self) -> None:
        self.store.delete_fragment("seven_days", "source.md")
        status = self.store.important_view_status()
        self.assertEqual(status["reason_codes"], ["source_missing"])
        self.assertIn("已删除或清理", status["reason"])
        self.store.create_fragment("seven_days", "new.md", "fact", now=self.now)
        self.store.set_important_view_sources(["new.md"])
        self.store._promote_location(self.store.locate("new.md"), "permanent", self.now)
        self.assertEqual(self.store.important_view_status()["reason_codes"], ["source_promoted"])

    def test_expiry_is_checked_before_cleanup_and_never_deletes_old_content(self) -> None:
        expired_now = self.now + timedelta(days=8)
        with patch("run.memory.utc_now", return_value=expired_now):
            self.assertEqual(self.store.important_view_status()["reason_codes"], ["source_expired"])
            self.assertFalse(self.store.important_view_is_current())
            self.assertEqual(self.store.load_important_view_sources(), set())
            self.assertNotIn("DERIVED_PROFILE", build_prompt_bundle(self.root, "alice", {}, plugin_manifests=()).text)
        self.assertIsNotNone(self.store.get_entry("seven_days", "source.md"))
        self.assertEqual(self.path.read_text("utf-8"), "DERIVED_PROFILE")

    def test_corrupt_provenance_count_fails_closed_without_exposing_sources(self) -> None:
        with connection(self.root, "alice", write=True) as database:
            database.execute("UPDATE memory_meta SET value='bad' WHERE key='important_view_count'")
        status = self.store.important_view_status()
        self.assertEqual(status["reason_codes"], ["invalid_metadata"])
        self.assertFalse(status["is_current"])
        self.assertNotIn("source.md", str(status))
        self.assertNotIn("original fact", str(status))
