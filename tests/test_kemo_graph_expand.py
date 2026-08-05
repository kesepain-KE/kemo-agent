from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_ROOT = ROOT / "global_expand" / "kemo_graph"
sys.path.insert(0, str(MODULE_ROOT))

import graph_core as graph  # noqa: E402
import library_sync as sync  # noqa: E402
import operations  # noqa: E402
import registry  # noqa: E402
import render  # noqa: E402
import start_expand  # noqa: E402

from plugins.kemo_graph.tool import run as graph_guide  # noqa: E402
from run.prompt_sources import read_expand_meta  # noqa: E402


class KemoGraphExpandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.module = self.root / "global_expand" / "kemo_graph"
        self.module.mkdir(parents=True)
        self.store = self.root / "stores" / "project"
        self.source = self.root / "documents" / "project"
        self.store.mkdir(parents=True)
        self.source.mkdir(parents=True)
        self.manifest = self.module / "expand.json"
        self.manifest.write_text(
            json.dumps({
                "name": "Kemo Graph 外挂文档站",
                "explain": "test",
                "open_input": False,
                "input_data": "input_data.md",
                "input_health": "正常",
                "start_update": "data_update.py",
                "open_control": True,
                "start_expand": "start_expand.py",
                "start_control": "expand_control.md",
                "recent_update": "2026-08-05 12:00:00",
            }, ensure_ascii=False),
            "utf-8",
        )
        (self.module / "input_data.md").write_text("inactive", "utf-8")
        data = self.module / "data"
        query_artifacts = self.module / "artifacts" / "queries"
        self.paths = {
            "BASE_DIR": self.module,
            "CONFIG_PATH": self.module / "graph_config.json",
            "MANIFEST_PATH": self.manifest,
            "INPUT_PATH": self.module / "input_data.md",
            "LAST_RUN_PATH": self.module / "_last_run.json",
            "DATA_DIR": data,
            "STATUS_PATH": data / "library_status.json",
            "SYNC_STATE_PATH": data / "library_sync_state.json",
            "QUERY_ARTIFACT_DIR": query_artifacts,
        }
        patchers = [
            patch.multiple(registry, **self.paths),
            patch.multiple(
                render,
                INPUT_PATH=self.paths["INPUT_PATH"],
                MANIFEST_PATH=self.paths["MANIFEST_PATH"],
            ),
            patch.multiple(
                operations,
                STATUS_PATH=self.paths["STATUS_PATH"],
                QUERY_ARTIFACT_DIR=self.paths["QUERY_ARTIFACT_DIR"],
            ),
            patch.object(sync, "SYNC_STATE_PATH", self.paths["SYNC_STATE_PATH"]),
            patch.multiple(
                graph,
                CONFIG_PATH=self.paths["CONFIG_PATH"],
                STATUS_PATH=self.paths["STATUS_PATH"],
                SYNC_STATE_PATH=self.paths["SYNC_STATE_PATH"],
            ),
            patch.multiple(
                start_expand,
                CONFIG_PATH=self.paths["CONFIG_PATH"],
                LAST_RUN_PATH=self.paths["LAST_RUN_PATH"],
            ),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def portable_mapping(
        self,
        *,
        library_id: str = "project_docs",
        store_root: Path | None = None,
        source_roots: list[Path] | None = None,
    ) -> dict:
        return {
            "schema_version": 2,
            "base_url": "http://127.0.0.1:8000/api/v1",
            "admin_users": ["alice"],
            "libraries": [{
                "id": library_id,
                "kind": "portable",
                "display_name": "项目文档",
                "store_root": str(store_root or self.store),
                "source_roots": [
                    str(path) for path in (
                        source_roots if source_roots is not None else [self.source]
                    )
                ],
                "scope": "knowledge.user",
                "owner_id": "alice",
                "allowed_users": ["alice"],
            }],
        }

    def test_real_manifest_is_a_valid_expand_module(self) -> None:
        meta = read_expand_meta(MODULE_ROOT)
        self.assertTrue(meta.valid, meta.error)
        self.assertTrue(meta.open_control)
        self.assertEqual(meta.start_update, "data_update.py")
        self.assertEqual(meta.start_expand, "start_expand.py")

    def test_registry_requires_stable_ids_and_separate_absolute_paths(self) -> None:
        with self.assertRaisesRegex(graph.GraphExpandError, "allow_remote"):
            graph.config_from_mapping({
                "schema_version": 2,
                "base_url": "http://graph.example.test:8000",
                "admin_users": ["alice"],
                "libraries": [],
            })
        with self.assertRaisesRegex(graph.GraphExpandError, "https"):
            graph.config_from_mapping({
                "schema_version": 2,
                "base_url": "http://graph.example.test:8000",
                "admin_users": ["alice"],
                "allow_remote": True,
                "libraries": [],
            })
        invalid = self.portable_mapping()
        invalid["libraries"][0]["id"] = "Project Docs"
        with self.assertRaisesRegex(graph.GraphExpandError, "必须匹配"):
            graph.config_from_mapping(invalid)
        nested = self.portable_mapping(source_roots=[self.store])
        with self.assertRaisesRegex(graph.GraphExpandError, "不能相同或互相嵌套"):
            graph.config_from_mapping(nested)

        config = graph.config_from_mapping(self.portable_mapping())
        self.assertEqual(config.base_url, "http://127.0.0.1:8000/api/v1")
        self.assertEqual(config.libraries[0].id, "project_docs")
        with self.assertRaisesRegex(graph.GraphExpandError, "未知、禁用或未注册"):
            graph.resolve_libraries(config, ["user-supplied-path"])

    def test_catalog_refresh_is_local_and_has_no_replacement_language(self) -> None:
        config = graph.config_from_mapping(self.portable_mapping())
        graph.save_config(config)
        with patch.object(operations, "api_request") as request:
            result = render.refresh_catalog()
        request.assert_not_called()
        self.assertTrue(result["active"])
        text = self.paths["INPUT_PATH"].read_text("utf-8")
        self.assertNotIn("project_docs", text)
        self.assertNotIn(str(self.store), text)
        self.assertIn("私有库不会写入全局 Prompt", text)
        self.assertIn("不替换、不增强、也不缩减", text)
        self.assertIn("用户明确要求", text)

    def test_status_keeps_uninitialized_portable_store_distinct_from_error(self) -> None:
        config = graph.config_from_mapping(self.portable_mapping())
        store_status = {
            "result": {
                "initialized": False,
                "sources": {"total": 0, "pending_graph": 0, "pending_rag": 0},
                "rag": {"faiss_healthy": False},
            }
        }
        with (
            patch.object(operations, "verify_service", return_value={"initialized": True}),
            patch.object(operations, "api_request", return_value=store_status) as request,
        ):
            result = operations.status_libraries(config, {})
        self.assertEqual(result["libraries"][0]["status"], "not_initialized")
        self.assertEqual(request.call_count, 1)
        self.assertEqual(result["summary"]["not_initialized"], 1)

    def test_service_default_status_and_query_follow_default_api_schema(self) -> None:
        config = graph.config_from_mapping({
            "schema_version": 2,
            "base_url": "http://127.0.0.1:8000/api/v1",
            "admin_users": ["alice"],
            "libraries": [{
                "id": "kemo_graph_builtin",
                "kind": "service_default",
                "display_name": "内置文档库",
                "allowed_users": ["alice"],
            }],
        })
        with patch.object(
            operations,
            "verify_service",
            return_value={
                "initialized": False,
                "sources": {"total": 0, "pending_graph": 0, "pending_rag": 0},
                "rag": {"faiss_healthy": False},
            },
        ):
            status = operations.status_libraries(config, {})
        self.assertEqual(status["libraries"][0]["status"], "not_initialized")

        captured: dict = {}

        def request(_config, path, payload=None, **kwargs):
            captured.update({"path": path, "payload": payload, **kwargs})
            return {"result": {"graph": {}, "rag": {}}}

        with patch.object(operations, "api_request", side_effect=request):
            result = operations.query_libraries(config, {
                "library_ids": ["kemo_graph_builtin"],
                "query": "项目约束是什么？",
                "mode": "hybrid",
                "force": True,
            })
        self.assertTrue(result["ok"])
        self.assertEqual(captured["path"], "/query/hybrid")
        self.assertNotIn("force", captured["payload"])
        self.assertNotIn("direction", captured["payload"])
        self.assertEqual(captured["query"], {"force": True})

    def test_sync_retries_failed_hash_and_binds_cursor_to_registry(self) -> None:
        document = self.source / "guide.md"
        document.write_text("version one", "utf-8")
        config = graph.config_from_mapping(self.portable_mapping())

        def first_request(_config, path, payload, **_kwargs):
            if path == "/stores/initialize":
                return {"manifest": {"store_id": "store-1"}}
            self.assertEqual(path, "/stores/import-path")
            return {"result": {"source_id": "source-1", "markdown_relative_path": "guide.md"}}

        with patch.object(sync, "api_request", side_effect=first_request):
            first = sync.sync_libraries(config, {})
        self.assertTrue(first["ok"])
        state = json.loads(self.paths["SYNC_STATE_PATH"].read_text("utf-8"))
        old_hash = next(iter(state["libraries"]["project_docs"]["files"].values()))["sha256"]
        self.assertEqual(state["libraries"]["project_docs"]["store_id"], "store-1")

        document.write_text("version two", "utf-8")

        def failed_request(_config, path, payload, **_kwargs):
            if path == "/stores/initialize":
                return {"manifest": {"store_id": "store-1"}}
            raise graph.GraphExpandError("conversion failed")

        with patch.object(sync, "api_request", side_effect=failed_request):
            second = sync.sync_libraries(config, {})
        self.assertFalse(second["ok"])
        state = json.loads(self.paths["SYNC_STATE_PATH"].read_text("utf-8"))
        current_hash = next(iter(state["libraries"]["project_docs"]["files"].values()))["sha256"]
        self.assertEqual(current_hash, old_hash)

        rebound_store = self.root / "stores" / "rebound"
        rebound_store.mkdir()
        rebound = graph.config_from_mapping(
            self.portable_mapping(store_root=rebound_store)
        )
        scanned = sync.scan_libraries(rebound, {})
        self.assertTrue(scanned["libraries"][0]["registry_changed"])
        self.assertEqual(scanned["libraries"][0]["summary"]["added"], 1)

    def test_partial_batch_delete_is_not_reported_as_success(self) -> None:
        first = self.source / "first.md"
        second = self.source / "second.md"
        first.write_text("first", "utf-8")
        second.write_text("second", "utf-8")
        config = graph.config_from_mapping(self.portable_mapping())
        source_ids = {str(first.resolve()): "source-first", str(second.resolve()): "source-second"}

        def import_request(_config, path, payload, **_kwargs):
            if path == "/stores/initialize":
                return {"manifest": {"store_id": "store-1"}}
            return {
                "result": {
                    "source_id": source_ids[payload["path"]],
                    "markdown_relative_path": Path(payload["path"]).name,
                }
            }

        with patch.object(sync, "api_request", side_effect=import_request):
            self.assertTrue(sync.sync_libraries(config, {})["ok"])
        first.unlink()
        second.unlink()

        def delete_request(_config, path, payload, **_kwargs):
            if path == "/stores/initialize":
                return {"manifest": {"store_id": "store-1"}}
            self.assertEqual(path, "/stores/documents/delete-batch")
            return {
                "result": {
                    "deleted": 1,
                    "failed": 1,
                    "documents": [{"source_id": "source-first"}],
                    "failures": [{
                        "source_id": "source-second",
                        "message": "busy",
                    }],
                }
            }

        with patch.object(sync, "api_request", side_effect=delete_request):
            result = sync.sync_libraries(config, {"confirm_deletions": True})
        self.assertFalse(result["ok"])
        row = result["libraries"][0]
        self.assertEqual(row["deleted"], 1)
        self.assertEqual(row["deletions_pending_confirmation"], 1)
        state = json.loads(self.paths["SYNC_STATE_PATH"].read_text("utf-8"))
        files = state["libraries"]["project_docs"]["files"]
        self.assertNotIn(str(first.resolve()), files)
        self.assertTrue(files[str(second.resolve())]["missing"])

    def test_ingest_rejects_nested_http_200_failures(self) -> None:
        config = graph.config_from_mapping(self.portable_mapping(source_roots=[]))
        with patch.object(
            operations,
            "api_request",
            return_value={
                "result": {
                    "processed": 2,
                    "failed": 1,
                    "details": [{"path": "broken.md", "graph": "failed"}],
                }
            },
        ):
            result = operations.ingest_library(config, {
                "library_ids": ["project_docs"],
                "mode": "both",
            })
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["details"][0]["path"], "broken.md")

    def test_large_query_uses_module_relative_artifact(self) -> None:
        config = graph.config_from_mapping(self.portable_mapping(source_roots=[]))
        with patch.object(
            operations,
            "api_request",
            return_value={"result": {"evidence": "x" * 15_000}},
        ):
            result = operations.query_libraries(config, {
                "library_ids": ["project_docs"],
                "query": "large result",
            })
        self.assertTrue(result["result_omitted"])
        self.assertIsNone(result["inline"])
        artifact = result["artifacts"][0]
        self.assertTrue(artifact["path"].startswith("artifacts/queries/query-"))
        self.assertTrue((self.module / artifact["path"]).is_file())

    def test_deactivate_preserves_cursor_and_external_store(self) -> None:
        graph.save_config(graph.config_from_mapping(self.portable_mapping()))
        self.paths["SYNC_STATE_PATH"].parent.mkdir(parents=True, exist_ok=True)
        self.paths["SYNC_STATE_PATH"].write_text("{}", "utf-8")
        marker = self.store / "keep.db"
        marker.write_text("store", "utf-8")

        result = graph.deactivate()

        self.assertTrue(result["deactivated"])
        self.assertTrue(result["local_sync_state_preserved"])
        self.assertFalse(self.paths["CONFIG_PATH"].exists())
        self.assertTrue(self.paths["SYNC_STATE_PATH"].exists())
        self.assertTrue(marker.exists())

    def test_plugin_uses_library_ids_and_generates_document_operations(self) -> None:
        config = graph.config_from_mapping(self.portable_mapping(source_roots=[]))
        graph.save_config(config)
        context = {"root": str(self.root), "user": "alice"}
        mapped = graph_guide("libraries", context=context)
        self.assertEqual(mapped["libraries"][0]["id"], "project_docs")
        self.assertEqual(mapped["libraries"][0]["scope"], "knowledge.user")

        query = graph_guide(
            "operation_guide",
            operation="query",
            library_ids=["project_docs"],
            query="项目规则是什么？",
            context=context,
        )
        self.assertEqual(query["arguments"]["params"]["library_ids"], ["project_docs"])
        self.assertEqual(query["arguments"]["params"]["mode"], "hybrid")

        document = graph_guide(
            "operation_guide",
            operation="documents",
            library_ids=["project_docs"],
            document_action="delete",
            source_id="source-1",
            context=context,
        )
        self.assertEqual(document["arguments"]["params"]["confirm"], "delete")

    def test_library_acl_filters_private_paths_and_write_operations(self) -> None:
        config = graph.config_from_mapping(self.portable_mapping(source_roots=[]))
        graph.save_config(config)
        self.assertEqual(graph.resolve_libraries(config, caller_user="alice")[0].id, "project_docs")
        with self.assertRaisesRegex(graph.GraphExpandError, "未知、禁用或未注册"):
            graph.resolve_libraries(config, ["project_docs"], caller_user="bob")
        bob = graph_guide("libraries", context={"root": str(self.root), "user": "bob"})
        self.assertEqual(bob["libraries"], [])
        with self.assertRaises(PermissionError):
            graph_guide(
                "operation_guide",
                operation="sync",
                library_ids=["project_docs"],
                context={"root": str(self.root), "user": "bob"},
            )

    def test_public_library_is_rendered_in_global_catalog(self) -> None:
        mapping = self.portable_mapping()
        mapping["libraries"][0]["allowed_users"] = ["*"]
        graph.save_config(graph.config_from_mapping(mapping))
        render.refresh_catalog()
        text = self.paths["INPUT_PATH"].read_text("utf-8")
        self.assertIn("project_docs", text)
        self.assertIn(str(self.store), text)


if __name__ == "__main__":
    unittest.main()
