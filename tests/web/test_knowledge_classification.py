from __future__ import annotations

import asyncio
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import httpx

from run.config.knowledge_classification import (
    KnowledgeClassificationError,
    read_classifications,
    set_classification,
)
from web.app import create_app
from web.service import WebRunService


class KnowledgeClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "global_config.json").write_text(
            json.dumps({"schema_version": 1}),
            "utf-8",
        )
        user_dir = self.root / "users" / "alice"
        (user_dir / "knowledge").mkdir(parents=True)
        (user_dir / "user_config.json").write_text(
            json.dumps({"schema_version": 1, "knowledge": {}}),
            "utf-8",
        )
        (self.root / "shared_knowledge").mkdir()
        (self.root / "global_knowledge").mkdir()
        self.app = create_app(service=WebRunService(self.root))

    def request(self, method: str, url: str, **kwargs):
        async def invoke():
            transport = httpx.ASGITransport(app=self.app, raise_app_exceptions=False)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                return await client.request(method, url, **kwargs)

        return asyncio.run(invoke())

    def test_classify_sidecar_is_portable_and_uncategorized_is_implicit(self) -> None:
        knowledge = self.root / "users" / "alice" / "knowledge"
        set_classification(knowledge, "notes.md", "教程")
        self.assertEqual(read_classifications(knowledge), {"notes.md": "教程"})
        database_path = knowledge / "classify.sqlite3"
        self.assertTrue(database_path.is_file())
        with closing(sqlite3.connect(database_path)) as database:
            columns = {
                row[1] for row in database.execute("PRAGMA table_info(classify)")
            }
            self.assertEqual(
                columns,
                {"relative_path", "category"},
            )
            self.assertEqual(
                database.execute(
                    "SELECT relative_path, category FROM classify"
                ).fetchall(),
                [("notes.md", "教程")],
            )
        self.assertEqual(set_classification(knowledge, "notes.md", "未分类"), "未分类")
        self.assertEqual(read_classifications(knowledge), {})

    def test_legacy_sql_sidecar_migrates_to_real_sqlite_table(self) -> None:
        knowledge = self.root / "users" / "alice" / "knowledge"
        legacy = knowledge / "classify.sql"
        legacy.write_text(
            "\n".join(
                [
                    "CREATE TABLE classify (relative_path TEXT PRIMARY KEY, category TEXT NOT NULL);",
                    "INSERT INTO classify(relative_path, category) VALUES ('a.md', 'A');",
                ]
            ),
            "utf-8",
        )
        self.assertEqual(read_classifications(knowledge), {"a.md": "A"})
        from run.config.knowledge_classification import ensure_classification_file

        ensure_classification_file(knowledge)
        self.assertFalse(legacy.exists())
        self.assertTrue((knowledge / "classify.sqlite3").is_file())
        self.assertEqual(read_classifications(knowledge), {"a.md": "A"})

    def test_duplicate_bindings_are_rejected_and_paths_cannot_escape(self) -> None:
        knowledge = self.root / "users" / "alice" / "knowledge"
        (knowledge / "classify.sql").write_text(
            "\n".join([
                "CREATE TABLE classify (relative_path TEXT PRIMARY KEY, category TEXT NOT NULL);",
                "INSERT INTO classify(relative_path, category) VALUES ('a.md', 'A');",
                "INSERT INTO classify(relative_path, category) VALUES ('a.md', 'B');",
            ]),
            "utf-8",
        )
        with self.assertRaises(KnowledgeClassificationError):
            read_classifications(knowledge)
        with self.assertRaises(KnowledgeClassificationError):
            set_classification(knowledge, "../escape.md", "A")

    def test_web_can_assign_filter_and_remove_category_without_moving_document(self) -> None:
        document = self.root / "users" / "alice" / "knowledge" / "notes.md"
        document.write_text("# Notes", "utf-8")

        listed = self.request("GET", "/api/users/alice/knowledge")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["documents"][0]["category"], "未分类")
        self.assertEqual(listed.json()["categories"]["user"][0]["name"], "未分类")
        self.assertNotIn(
            "classify.sqlite3",
            [item["relative_path"] for item in listed.json()["documents"]],
        )

        assigned = self.request(
            "PATCH",
            "/api/users/alice/knowledge/user/classification?path=notes.md",
            json={"category": "项目资料"},
        )
        self.assertEqual(assigned.status_code, 200, assigned.text)
        self.assertEqual(assigned.json()["category"], "项目资料")
        self.assertTrue(document.is_file())

        detail = self.request(
            "GET",
            "/api/users/alice/knowledge/user/document?path=notes.md",
        )
        self.assertEqual(detail.json()["category"], "项目资料")

        filtered = self.request("GET", "/api/users/alice/knowledge")
        payload = filtered.json()
        self.assertEqual(payload["documents"][0]["category"], "项目资料")
        self.assertEqual(
            {item["name"] for item in payload["categories"]["user"]},
            {"未分类", "项目资料"},
        )

        removed = self.request(
            "PATCH",
            "/api/users/alice/knowledge/user/classification?path=notes.md",
            json={"category": ""},
        )
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(removed.json()["category"], "未分类")
        self.assertEqual(read_classifications(document.parent), {})

    def test_delete_and_move_keep_classification_sidecar_in_sync(self) -> None:
        knowledge = self.root / "users" / "alice" / "knowledge"
        removable = knowledge / "removable.md"
        removable.write_text("# Remove", "utf-8")
        set_classification(knowledge, "removable.md", "归档")

        deleted = self.request(
            "DELETE",
            "/api/users/alice/knowledge/user/document?path=removable.md",
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertFalse(removable.exists())
        self.assertEqual(read_classifications(knowledge), {})

        movable = knowledge / "movable.md"
        movable.write_text("# Move", "utf-8")
        set_classification(knowledge, "movable.md", "项目资料")
        moved = self.request(
            "PATCH",
            "/api/users/alice/knowledge/user/document"
            "?path=movable.md&new_path=archive/movable.md",
        )
        self.assertEqual(moved.status_code, 200, moved.text)
        self.assertFalse(movable.exists())
        self.assertTrue((knowledge / "archive" / "movable.md").is_file())
        self.assertEqual(
            read_classifications(knowledge),
            {"archive/movable.md": "项目资料"},
        )
