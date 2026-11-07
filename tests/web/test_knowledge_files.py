from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import unittest

import httpx

from web.app import create_app
from web.service import WebRunService


class KnowledgeFilesystemTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "config").mkdir()
        (self.root / "config/global_config.yaml").write_text('{"schema_version":1}', "utf-8")
        self.user = self.root / "users/alice/knowledge"
        self.user.mkdir(parents=True)
        (self.user.parent / "user_config.yaml").write_text('{"schema_version":1}', "utf-8")
        (self.root / "knowledge/share").mkdir(parents=True)
        (self.root / "knowledge/global").mkdir()
        self.app = create_app(service=WebRunService(self.root))

    def request(self, method: str, url: str, **kwargs):
        async def invoke():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, raise_app_exceptions=False),
                                         base_url="http://test") as client:
                return await client.request(method, url, **kwargs)
        return asyncio.run(invoke())

    def entry(self, method: str, path: str, *, scope="user", **kwargs):
        return self.request(method, f"/api/users/alice/knowledge/{scope}/entry", params={"path": path}, **kwargs)

    def test_tree_lists_real_nested_and_empty_directories_without_creating_metadata(self):
        (self.user / "a/b/c").mkdir(parents=True)
        (self.user / "empty").mkdir()
        (self.user / "a/b/c/note.md").write_text("# Actual file", "utf-8")
        response = self.request("GET", "/api/users/alice/knowledge")
        self.assertEqual(response.status_code, 200, response.text)
        view = response.json()
        self.assertEqual([root["scope"] for root in view["roots"]], ["user", "shared", "global"])
        self.assertEqual(view["roots"][0]["absolute_path"], str(self.user.resolve()))
        self.assertEqual(view["roots"][0]["relative_path"], "users/alice/knowledge")
        self.assertEqual({item["relative_path"] for item in view["directories"]}, {"a", "a/b", "a/b/c", "empty"})
        self.assertEqual([item["relative_path"] for item in view["documents"]], ["a/b/c/note.md"])
        self.assertNotIn("categories", view)
        self.assertNotIn("category", view["documents"][0])
        for root in (self.user, self.root / "knowledge/share", self.root / "knowledge/global"):
            self.assertFalse((root / "classify.sqlite3").exists())

    def test_retired_metadata_is_preserved_and_does_not_block_read_write_move_delete(self):
        sidecar = self.user / "classify.sqlite3"
        sidecar.write_bytes(b"damaged legacy metadata")
        document = self.user / "note.md"
        document.write_text("# Original", "utf-8")
        for method, query, body in (
            ("GET", "note.md", {}),
            ("PUT", "note.md", {"json": {"content": "# Changed"}}),
        ):
            response = self.request(method, "/api/users/alice/knowledge/user/document", params={"path": query}, **body)
            self.assertEqual(response.status_code, 200, response.text)
        moved = self.entry("PATCH", "note.md", json={"action": "move", "target_scope": "user", "target_path": "notes/renamed.md"})
        self.assertEqual(moved.status_code, 200, moved.text)
        self.assertEqual((self.user / "notes/renamed.md").read_text("utf-8"), "# Changed")
        self.assertEqual(self.entry("DELETE", "notes/renamed.md").status_code, 200)
        self.assertEqual(sidecar.read_bytes(), b"damaged legacy metadata")
        self.assertIn(self.request("PATCH", "/api/users/alice/knowledge/user/classification", params={"path": "note.md"}, json={"category": "old"}).status_code, (404, 405))

    def test_create_file_folder_and_valid_empty_json_without_overwriting_existing_content(self):
        self.assertEqual(self.entry("POST", "projects/new/empty", json={"kind": "directory"}).status_code, 200)
        self.assertTrue((self.user / "projects/new/empty").is_dir())
        response = self.entry("POST", "projects/new/item.json", json={"kind": "file"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(json.loads((self.user / "projects/new/item.json").read_text("utf-8")), {})
        (self.user / "projects/new/item.json").write_text('{"keep": true}', "utf-8")
        self.assertEqual(self.entry("POST", "projects/new/item.json", json={"kind": "file"}).status_code, 409)
        self.assertEqual(json.loads((self.user / "projects/new/item.json").read_text("utf-8")), {"keep": True})

    def test_copy_move_and_recursive_delete_operate_on_disk_across_scopes(self):
        (self.user / "project/nested/empty").mkdir(parents=True)
        (self.user / "project/nested/note.txt").write_text("Preserve me", "utf-8")
        copied = self.entry("PATCH", "project", json={"action": "copy", "target_scope": "shared", "target_path": "team/project"})
        self.assertEqual(copied.status_code, 200, copied.text)
        copied_path = self.root / "knowledge/share/team/project"
        self.assertTrue((copied_path / "nested/empty").is_dir())
        self.assertEqual((copied_path / "nested/note.txt").read_text("utf-8"), "Preserve me")
        self.assertTrue((self.user / "project/nested/note.txt").is_file())
        moved = self.entry("PATCH", "team/project", scope="shared", json={"action": "move", "target_scope": "global", "target_path": "archive/project"})
        self.assertEqual(moved.status_code, 200, moved.text)
        self.assertFalse(copied_path.exists())
        self.assertEqual((self.root / "knowledge/global/archive/project/nested/note.txt").read_text("utf-8"), "Preserve me")
        self.assertEqual(self.entry("DELETE", "archive/project", scope="global").status_code, 200)
        self.assertFalse((self.root / "knowledge/global/archive/project").exists())
        self.assertTrue((self.user / "project/nested/note.txt").is_file())

    def test_conflicting_or_descendant_target_does_not_change_source(self):
        (self.user / "source").mkdir()
        (self.user / "source/a.md").write_text("source", "utf-8")
        (self.user / "target").mkdir()
        for action in ("copy", "move"):
            for target, status in (("target", 409), ("source/nested", 400)):
                response = self.entry("PATCH", "source", json={"action": action, "target_scope": "user", "target_path": target})
                self.assertEqual(response.status_code, status, response.text)
        self.assertEqual((self.user / "source/a.md").read_text("utf-8"), "source")
        self.assertFalse((self.user / "source/nested").exists())

    def test_paste_generates_unique_copy_names_without_overwriting_files_or_folders(self):
        (self.user / "note.md").write_text("source", "utf-8")
        for expected in ("note - 副本.md", "note - 副本 (2).md"):
            response = self.entry("PATCH", "note.md", json={
                "action": "copy", "target_scope": "user", "target_path": "note.md", "auto_rename": True,
            })
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["new_relative_path"], expected)
            self.assertEqual((self.user / expected).read_text("utf-8"), "source")
        (self.user / "folder.v1/nested").mkdir(parents=True)
        (self.user / "folder.v1/nested/a.txt").write_text("nested", "utf-8")
        response = self.entry("PATCH", "folder.v1", json={
            "action": "copy", "target_scope": "user", "target_path": "folder.v1", "auto_rename": True,
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["new_relative_path"], "folder.v1 - 副本")
        self.assertEqual((self.user / "folder.v1 - 副本/nested/a.txt").read_text("utf-8"), "nested")
        invalid = self.entry("PATCH", "folder.v1", json={
            "action": "copy", "target_scope": "user", "target_path": "folder.v1/nested/child", "auto_rename": True,
        })
        self.assertEqual(invalid.status_code, 400)
        self.assertFalse((self.user / "folder.v1/nested/child").exists())

    def test_concurrent_pastes_choose_distinct_destinations_and_preserve_existing_data(self):
        (self.user / "note.md").write_text("source", "utf-8")
        target = self.root / "knowledge/share"
        (target / "note.md").write_text("keep existing", "utf-8")
        backend = WebRunService(self.root)
        def paste(_):
            return backend.transfer_knowledge_entry("alice", "user", "note.md", action="copy",
                target_scope="shared", target_path="note.md", auto_rename=True)["new_relative_path"]
        with ThreadPoolExecutor(max_workers=4) as pool:
            paths = list(pool.map(paste, range(4)))
        self.assertEqual(len(set(paths)), 4)
        self.assertEqual((target / "note.md").read_text("utf-8"), "keep existing")
        self.assertTrue(all((target / path).read_text("utf-8") == "source" for path in paths))
        conflict = self.entry("PATCH", "note.md", json={
            "action": "move", "target_scope": "shared", "target_path": "note.md",
        })
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual((self.user / "note.md").read_text("utf-8"), "source")

    def test_root_traversal_private_runtime_and_hidden_entries_are_protected(self):
        for path in (".", "../escape.md", "kemo-graph-storage/db.md", ".secret/note.md", "classify.sqlite3"):
            for method, kwargs in (("DELETE", {}), ("POST", {"json": {"kind": "directory"}})):
                with self.subTest(path=path, method=method):
                    self.assertEqual(self.entry(method, path, json=kwargs.get("json")).status_code, 400)
        self.assertTrue(self.user.is_dir())
        self.assertFalse((self.root / "users/alice/escape.md").exists())
        self.assertEqual(self.entry("POST", "new.md", scope="other", json={"kind": "file"}).status_code, 400)

    def test_recursive_operations_reject_internal_data_without_partial_changes(self):
        (self.user / "mixed/.internal").mkdir(parents=True)
        (self.user / "mixed/note.md").write_text("keep", "utf-8")
        self.assertEqual(self.entry("DELETE", "mixed").status_code, 400)
        self.assertEqual(self.entry("PATCH", "mixed", json={"action": "copy", "target_scope": "shared", "target_path": "mixed"}).status_code, 400)
        self.assertTrue((self.user / "mixed/note.md").exists())
        self.assertFalse((self.root / "knowledge/share/mixed").exists())

    def test_links_cannot_expose_or_mutate_files_outside_knowledge(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "private.md").write_text("private", "utf-8")
        try:
            os.symlink(outside, self.user / "redirect", target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"Symlink unavailable: {exc}")
        view = self.request("GET", "/api/users/alice/knowledge").json()
        self.assertNotIn("redirect", {item["relative_path"] for item in view["directories"]})
        self.assertEqual(self.entry("DELETE", "redirect/private.md").status_code, 400)
        self.assertEqual((outside / "private.md").read_text("utf-8"), "private")
        (self.user / "note.md").write_text("source", "utf-8")
        os.symlink(outside / "missing.md", self.user / "note - 副本.md")
        copied = self.entry("PATCH", "note.md", json={
            "action": "copy", "target_scope": "user", "target_path": "note.md", "auto_rename": True,
        })
        self.assertEqual(copied.status_code, 200, copied.text)
        self.assertEqual(copied.json()["new_relative_path"], "note - 副本 (2).md")
        self.assertFalse((outside / "missing.md").exists())

    def test_document_validation_is_kept_at_real_file_boundary(self):
        self.assertEqual(self.entry("POST", "bad.exe", json={"kind": "file"}).status_code, 400)
        response = self.request("PUT", "/api/users/alice/knowledge/user/document", params={"path": "bad.json"}, json={"content": "not JSON"})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertFalse((self.user / "bad.json").exists())
