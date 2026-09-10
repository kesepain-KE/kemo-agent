from __future__ import annotations

import json
from io import BytesIO
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
MODULE_ROOT = ROOT / "global_expand" / "kemo_graph"
sys.path.insert(0, str(MODULE_ROOT))

import graph_core as graph  # noqa: E402
import client as graph_client  # noqa: E402
import library_sync as sync  # noqa: E402
import operations  # noqa: E402
import registry  # noqa: E402
import render  # noqa: E402
import start_expand  # noqa: E402

from plugins.kemo_graph import tool as graph_tool  # noqa: E402
from plugins.kemo_graph.tool import run as graph_guide  # noqa: E402
from plugins.manifest import parse_plugin_manifest  # noqa: E402
from run.config import read_expand_meta  # noqa: E402


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

    def test_saved_missing_source_is_visible_and_new_registration_stays_strict(self) -> None:
        config = graph.config_from_mapping(self.portable_mapping())
        graph.save_config(config)
        self.source.rmdir()

        loaded = graph.load_config()
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(
            registry.unavailable_source_roots(loaded.libraries[0]),
            [str(self.source.resolve())],
        )
        status = graph.configuration_status("alice")
        self.assertEqual(
            status["libraries"][0]["unavailable_source_roots"],
            [str(self.source.resolve())],
        )
        with self.assertRaisesRegex(graph.GraphExpandError, "不存在或无法访问"):
            graph.config_from_mapping(self.portable_mapping())

    def test_saved_source_that_becomes_link_is_reported_unavailable(self) -> None:
        config = graph.config_from_mapping(self.portable_mapping())
        graph.save_config(config)

        def reports_source_as_link(path: Path) -> bool:
            try:
                return os.path.samefile(path, self.source)
            except (OSError, ValueError):
                return False

        with patch.object(
            registry,
            "_has_link_component",
            side_effect=reports_source_as_link,
        ):
            loaded = graph.load_config()
            assert loaded is not None
            self.assertEqual(
                registry.unavailable_source_roots(loaded.libraries[0]),
                [str(self.source.resolve())],
            )
            with self.assertRaisesRegex(
                graph.GraphExpandError,
                "符号链接或目录联接",
            ):
                graph.config_from_mapping(self.portable_mapping())

    def test_plugin_source_availability_check_fails_closed_on_io_error(self) -> None:
        with patch.object(Path, "is_dir", side_effect=OSError("offline")):
            self.assertFalse(graph_tool._source_root_available(str(self.source)))

    def test_missing_source_blocks_scan_sync_deletion_and_cursor_progress(self) -> None:
        source_file = self.source / "tracked.md"
        source_file.write_text("tracked", "utf-8")
        config = graph.config_from_mapping(self.portable_mapping())
        graph.save_config(config)
        state = {
            "schema_version": 2,
            "libraries": {
                "project_docs": {
                    "registry_signature": registry.library_signature(config.libraries[0]),
                    "store_id": "store-1",
                    "files": {
                        str(source_file.resolve()): {
                            "sha256": "old",
                            "source_id": "source-1",
                            "missing": False,
                        }
                    },
                }
            },
        }
        self.paths["SYNC_STATE_PATH"].parent.mkdir(parents=True, exist_ok=True)
        self.paths["SYNC_STATE_PATH"].write_text(
            json.dumps(state, ensure_ascii=False),
            "utf-8",
        )
        previous_state = self.paths["SYNC_STATE_PATH"].read_bytes()
        source_file.unlink()
        self.source.rmdir()
        loaded = graph.load_config()
        assert loaded is not None

        with patch.object(sync, "api_request") as request:
            scanned = sync.scan_libraries(loaded, {"library_ids": ["project_docs"]})
            synchronized = sync.sync_libraries(
                loaded,
                {"library_ids": ["project_docs"], "confirm_deletions": True},
            )

        request.assert_not_called()
        self.assertFalse(scanned["ok"])
        self.assertEqual(scanned["libraries"][0]["status"], "source_unavailable")
        self.assertEqual(scanned["libraries"][0]["summary"]["deleted"], 0)
        self.assertFalse(synchronized["ok"])
        self.assertEqual(
            synchronized["libraries"][0]["status"],
            "source_unavailable",
        )
        self.assertEqual(synchronized["libraries"][0]["deleted"], 0)
        self.assertEqual(self.paths["SYNC_STATE_PATH"].read_bytes(), previous_state)

    def test_source_detaching_after_precheck_does_not_initialize_store(self) -> None:
        config = graph.config_from_mapping(self.portable_mapping())
        library = config.libraries[0]

        with (
            patch.object(
                sync,
                "unavailable_source_roots",
                side_effect=[[], [library.source_roots[0]]],
            ),
            patch.object(sync, "api_request") as request,
        ):
            result = sync.sync_libraries(
                config,
                {"library_ids": [library.id]},
                caller_user="alice",
            )

        request.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual(result["libraries"][0]["status"], "error")
        self.assertEqual(result["libraries"][0]["deleted"], 0)

    def test_incomplete_file_scan_never_deletes_or_advances_cursor(self) -> None:
        source_file = self.source / "tracked.md"
        source_file.write_text("tracked", "utf-8")
        config = graph.config_from_mapping(self.portable_mapping())
        library = config.libraries[0]
        state = {
            "schema_version": 2,
            "libraries": {
                library.id: {
                    "registry_signature": registry.library_signature(library),
                    "store_id": "store-1",
                    "files": {
                        str(source_file.resolve()): {
                            "sha256": "old",
                            "source_id": "source-1",
                            "missing": False,
                        }
                    },
                }
            },
        }
        self.paths["SYNC_STATE_PATH"].parent.mkdir(parents=True, exist_ok=True)
        self.paths["SYNC_STATE_PATH"].write_text(json.dumps(state), "utf-8")
        previous_state = self.paths["SYNC_STATE_PATH"].read_bytes()
        with (
            patch.object(
                sync,
                "_library_files",
                side_effect=PermissionError("temporary read failure"),
            ),
            patch.object(sync, "api_request") as request,
        ):
            result = sync.sync_libraries(
                config,
                {"library_ids": [library.id], "confirm_deletions": True},
                caller_user="alice",
            )

        request.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual(result["libraries"][0]["status"], "error")
        self.assertEqual(result["libraries"][0]["deleted"], 0)
        self.assertEqual(self.paths["SYNC_STATE_PATH"].read_bytes(), previous_state)

    def test_file_replaced_between_lstat_and_hash_fails_closed(self) -> None:
        source_file = self.source / "tracked.md"
        source_file.write_text("original", "utf-8")
        replacement = self.root / "replacement.md"
        replacement.write_text("replaced", "utf-8")
        config = graph.config_from_mapping(self.portable_mapping())
        self.paths["SYNC_STATE_PATH"].parent.mkdir(parents=True, exist_ok=True)
        self.paths["SYNC_STATE_PATH"].write_text(
            '{"schema_version": 2, "libraries": {}}\n',
            "utf-8",
        )
        previous_state = self.paths["SYNC_STATE_PATH"].read_bytes()
        original_open = sync.os.open
        replaced = False

        def replace_before_open(path, flags, *args, **kwargs):
            nonlocal replaced
            if Path(path) == source_file.resolve() and not replaced:
                os.replace(replacement, source_file)
                replaced = True
            return original_open(path, flags, *args, **kwargs)

        with (
            patch.object(sync.os, "open", side_effect=replace_before_open),
            patch.object(sync, "api_request") as request,
        ):
            result = sync.sync_libraries(config, {})

        self.assertTrue(replaced)
        request.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual(result["libraries"][0]["status"], "error")
        self.assertEqual(self.paths["SYNC_STATE_PATH"].read_bytes(), previous_state)

    def test_file_replaced_after_scan_before_initialize_fails_closed(self) -> None:
        source_file = self.source / "tracked.md"
        source_file.write_text("original", "utf-8")
        replacement = self.root / "replacement.md"
        replacement.write_text("replaced", "utf-8")
        config = graph.config_from_mapping(self.portable_mapping())
        self.paths["SYNC_STATE_PATH"].parent.mkdir(parents=True, exist_ok=True)
        self.paths["SYNC_STATE_PATH"].write_text(
            '{"schema_version": 2, "libraries": {}}\n',
            "utf-8",
        )
        previous_state = self.paths["SYNC_STATE_PATH"].read_bytes()
        original_scan = sync._library_files

        def replace_after_scan(library):
            scan = original_scan(library)
            os.replace(replacement, source_file)
            return scan

        with (
            patch.object(sync, "_library_files", side_effect=replace_after_scan),
            patch.object(sync, "api_request") as request,
        ):
            result = sync.sync_libraries(config, {})

        request.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual(result["libraries"][0]["status"], "error")
        self.assertEqual(self.paths["SYNC_STATE_PATH"].read_bytes(), previous_state)

    def test_file_replaced_while_sidecar_request_records_confirmed_snapshot(self) -> None:
        source_file = self.source / "tracked.md"
        source_file.write_text("original", "utf-8")
        scanned_hash = sync.hashlib.sha256(source_file.read_bytes()).hexdigest()
        replacement = self.root / "replacement.md"
        replacement.write_text("replaced", "utf-8")
        config = graph.config_from_mapping(self.portable_mapping())
        self.paths["SYNC_STATE_PATH"].parent.mkdir(parents=True, exist_ok=True)
        self.paths["SYNC_STATE_PATH"].write_text(
            '{"schema_version": 2, "libraries": {}}\n',
            "utf-8",
        )
        previous_state = self.paths["SYNC_STATE_PATH"].read_bytes()

        def request(_config, endpoint, payload, **_kwargs):
            if endpoint == "/stores/initialize":
                return {"manifest": {"store_id": "store-1"}}
            self.assertEqual(endpoint, "/stores/import-path")
            self.assertEqual(payload["expected_origin_hash"], scanned_hash)
            os.replace(replacement, source_file)
            return {
                "result": {
                    "source_id": "source-1",
                    "markdown_relative_path": "tracked.md",
                    "origin_hash": scanned_hash,
                }
            }

        with patch.object(sync, "api_request", side_effect=request) as api:
            result = sync.sync_libraries(config, {})

        self.assertEqual(
            [call.args[1] for call in api.call_args_list],
            ["/stores/initialize", "/stores/import-path"],
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["libraries"][0]["status"], "error")
        self.assertEqual(result["libraries"][0]["deleted"], 0)
        self.assertNotEqual(self.paths["SYNC_STATE_PATH"].read_bytes(), previous_state)
        state = json.loads(self.paths["SYNC_STATE_PATH"].read_text("utf-8"))
        saved = state["libraries"]["project_docs"]["files"]
        self.assertEqual(saved[str(source_file.resolve())]["sha256"], scanned_hash)
        rescanned = sync.scan_libraries(config, {})
        self.assertEqual(rescanned["libraries"][0]["summary"]["modified"], 1)

    def test_import_without_matching_origin_hash_does_not_advance_cursor(self) -> None:
        source_file = self.source / "tracked.md"
        source_file.write_text("original", "utf-8")
        config = graph.config_from_mapping(self.portable_mapping())
        self.paths["SYNC_STATE_PATH"].parent.mkdir(parents=True, exist_ok=True)
        self.paths["SYNC_STATE_PATH"].write_text(
            '{"schema_version": 2, "libraries": {}}\n',
            "utf-8",
        )
        previous_state = self.paths["SYNC_STATE_PATH"].read_bytes()

        def request(_config, endpoint, _payload, **_kwargs):
            if endpoint == "/stores/initialize":
                return {"manifest": {"store_id": "store-1"}}
            return {
                "result": {
                    "source_id": "source-1",
                    "markdown_relative_path": "tracked.md",
                    "origin_hash": "0" * 64,
                }
            }

        with patch.object(sync, "api_request", side_effect=request):
            result = sync.sync_libraries(config, {})

        self.assertFalse(result["ok"])
        self.assertEqual(result["libraries"][0]["imported"], 0)
        self.assertIn("未确认", result["libraries"][0]["failures"][0]["error"])
        self.assertEqual(self.paths["SYNC_STATE_PATH"].read_bytes(), previous_state)

    def test_import_without_source_id_does_not_advance_cursor(self) -> None:
        source_file = self.source / "tracked.md"
        source_file.write_text("original", "utf-8")
        origin_hash = sync.hashlib.sha256(source_file.read_bytes()).hexdigest()
        config = graph.config_from_mapping(self.portable_mapping())
        self.paths["SYNC_STATE_PATH"].parent.mkdir(parents=True, exist_ok=True)
        self.paths["SYNC_STATE_PATH"].write_text(
            '{"schema_version": 2, "libraries": {}}\n',
            "utf-8",
        )
        previous_state = self.paths["SYNC_STATE_PATH"].read_bytes()

        def request(_config, endpoint, _payload, **_kwargs):
            if endpoint == "/stores/initialize":
                return {"manifest": {"store_id": "store-1"}}
            return {
                "result": {
                    "markdown_relative_path": "tracked.md",
                    "origin_hash": origin_hash,
                }
            }

        with patch.object(sync, "api_request", side_effect=request):
            result = sync.sync_libraries(config, {})

        self.assertFalse(result["ok"])
        self.assertEqual(result["libraries"][0]["imported"], 0)
        self.assertIn("source_id", result["libraries"][0]["failures"][0]["error"])
        self.assertEqual(self.paths["SYNC_STATE_PATH"].read_bytes(), previous_state)

    def test_nested_directory_replaced_with_link_before_import_fails_closed(self) -> None:
        nested = self.source / "nested"
        nested.mkdir()
        (nested / "tracked.md").write_text("inside", "utf-8")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "tracked.md").write_text("outside", "utf-8")
        probe = self.root / "symlink-probe"
        try:
            probe.symlink_to(outside, target_is_directory=True)
            probe.unlink()
        except OSError as exc:
            self.skipTest(f"当前环境不允许创建目录符号链接：{exc}")
        config = graph.config_from_mapping(self.portable_mapping())
        self.paths["SYNC_STATE_PATH"].parent.mkdir(parents=True, exist_ok=True)
        self.paths["SYNC_STATE_PATH"].write_text(
            '{"schema_version": 2, "libraries": {}}\n',
            "utf-8",
        )
        previous_state = self.paths["SYNC_STATE_PATH"].read_bytes()
        original_scan = sync._library_files
        original_nested = self.source / "nested-original"

        def replace_directory_after_scan(library):
            scan = original_scan(library)
            os.replace(nested, original_nested)
            nested.symlink_to(outside, target_is_directory=True)
            return scan

        try:
            with (
                patch.object(
                    sync,
                    "_library_files",
                    side_effect=replace_directory_after_scan,
                ),
                patch.object(sync, "api_request") as request,
            ):
                result = sync.sync_libraries(config, {})
        finally:
            if nested.is_symlink():
                nested.unlink()
            if original_nested.exists():
                os.replace(original_nested, nested)

        request.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual(result["libraries"][0]["status"], "error")
        self.assertEqual(self.paths["SYNC_STATE_PATH"].read_bytes(), previous_state)

    def test_original_admin_can_replace_a_path_invalid_saved_registry(self) -> None:
        config = graph.config_from_mapping(self.portable_mapping())
        graph.save_config(config)
        self.source.rmdir()
        self.store.rmdir()
        replacement = {
            "schema_version": 2,
            "base_url": "http://127.0.0.1:8000/api/v1",
            "admin_users": ["alice"],
            "libraries": [{
                "id": "kemo_graph_builtin",
                "kind": "service_default",
                "display_name": "内置文档库",
                "allowed_users": ["alice"],
            }],
        }
        with self.assertRaises(PermissionError):
            start_expand.execute("activate", replacement, context={"user": "bob"})

        result = start_expand.execute(
            "activate",
            replacement,
            context={"user": "alice"},
        )

        self.assertTrue(result["active"])
        repaired = graph.load_config()
        assert repaired is not None
        self.assertEqual(repaired.libraries[0].id, "kemo_graph_builtin")

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

    def test_manifest_recent_update_never_moves_backwards(self) -> None:
        self.paths["MANIFEST_PATH"].write_text(
            json.dumps(
                {
                    "recent_update": "2099-08-16 12:03:00",
                    "open_input": True,
                    "input_health": "正常",
                },
                ensure_ascii=False,
            ),
            "utf-8",
        )
        render._update_manifest(active=True, healthy=True)  # noqa: SLF001
        manifest = json.loads(self.paths["MANIFEST_PATH"].read_text("utf-8"))
        self.assertEqual(manifest["recent_update"], "2099-08-16 12:03:00")

    def test_public_catalog_uses_cached_document_counters_without_network(self) -> None:
        mapping = self.portable_mapping(source_roots=[])
        mapping["libraries"][0]["allowed_users"] = ["*"]
        config = graph.config_from_mapping(mapping)
        graph.save_config(config)
        self.paths["STATUS_PATH"].parent.mkdir(parents=True, exist_ok=True)
        self.paths["STATUS_PATH"].write_text(
            json.dumps({
                "schema_version": 2,
                "generated_at": "2026-09-08T12:00:00+08:00",
                "base_url": config.base_url,
                "libraries": [{
                    "id": "project_docs",
                    "registry_signature": registry.library_signature(config.libraries[0]),
                    "status": "degraded",
                    "document_failures": 2,
                    "document_processing": 1,
                    "document_pending": 3,
                    "result": {"sources": {"active": 7, "total": 9}},
                }],
            }, ensure_ascii=False),
            "utf-8",
        )

        with patch.object(operations, "api_request") as request:
            render.refresh_catalog()

        request.assert_not_called()
        text = self.paths["INPUT_PATH"].read_text("utf-8")
        self.assertIn("文档统计（上次已知）", text)
        self.assertIn("活动 7 / 总计 9；失败 2；处理中 1；待处理 3", text)
        self.assertIn("2026-09-08T12:00:00+08:00", text)

        changed_mapping = self.portable_mapping(source_roots=[])
        changed_mapping["base_url"] = "http://127.0.0.1:8001/api/v1"
        changed_mapping["libraries"][0]["allowed_users"] = ["*"]
        graph.save_config(graph.config_from_mapping(changed_mapping))
        render.refresh_catalog()
        changed_text = self.paths["INPUT_PATH"].read_text("utf-8")
        self.assertNotIn("活动 7 / 总计 9", changed_text)

    def test_partial_failed_status_keeps_each_library_last_success(self) -> None:
        second_store = self.root / "stores" / "public-two"
        second_store.mkdir(parents=True)
        config = graph.config_from_mapping({
            "schema_version": 2,
            "base_url": "http://127.0.0.1:8000/api/v1",
            "admin_users": ["alice"],
            "libraries": [
                {
                    "id": "public_one",
                    "kind": "service_default",
                    "display_name": "公共库一",
                    "allowed_users": ["*"],
                },
                {
                    "id": "public_two",
                    "kind": "portable",
                    "display_name": "公共库二",
                    "store_root": str(second_store),
                    "source_roots": [],
                    "scope": "knowledge.global",
                    "allowed_users": ["*"],
                },
            ],
        })
        graph.save_config(config)
        service = {
            "initialized": True,
            "sources": {
                "active": 4,
                "total": 5,
                "pending_graph": 0,
                "pending_rag": 0,
            },
            "rag": {"faiss_healthy": True},
        }
        with (
            patch.object(operations, "verify_service", return_value=service),
            patch.object(operations, "api_request", return_value=service),
            patch.object(
                operations,
                "_default_document_health",
                return_value=(1, 0, 2),
            ),
            patch.object(
                operations,
                "_portable_document_health",
                return_value=(1, 0, 2),
            ),
        ):
            initial = operations.status_libraries(config, {}, caller_user="alice")
        self.assertEqual(len(initial["libraries"]), 2)

        with (
            patch.object(operations, "verify_service", return_value=service),
            patch.object(
                operations,
                "_default_document_health",
                side_effect=RuntimeError("temporary status failure"),
            ),
        ):
            failed = operations.status_libraries(
                config,
                {"library_ids": ["public_one"]},
                caller_user="alice",
            )

        self.assertEqual(len(failed["libraries"]), 1)
        self.assertEqual(failed["libraries"][0]["status"], "error")
        self.assertEqual(failed["libraries"][0]["last_success"]["active"], 4)
        self.assertEqual(failed["libraries"][0]["last_success"]["failed"], 1)
        snapshot = json.loads(self.paths["STATUS_PATH"].read_text("utf-8"))
        saved = {row["id"]: row for row in snapshot["libraries"]}
        self.assertEqual(set(saved), {"public_one", "public_two"})
        self.assertEqual(saved["public_one"]["status"], "error")
        self.assertEqual(saved["public_two"]["document_pending"], 2)

        render.refresh_catalog()
        text = self.paths["INPUT_PATH"].read_text("utf-8")
        self.assertIn("上次成功于", text)
        self.assertIn("活动 4 / 总计 5；失败 1；处理中 0；待处理 2", text)

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
            return {
                "result": {
                    "source_id": "source-1",
                    "markdown_relative_path": "guide.md",
                    "origin_hash": sync.hashlib.sha256(document.read_bytes()).hexdigest(),
                }
            }

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
                    "origin_hash": sync.hashlib.sha256(
                        Path(payload["path"]).read_bytes()
                    ).hexdigest(),
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
                    "requested": 2,
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

        def malformed_delete(_config, path, _payload, **_kwargs):
            if path == "/stores/initialize":
                return {"manifest": {"store_id": "store-1"}}
            return {
                "result": {
                    "requested": 1,
                    "deleted": 1,
                    "failed": 0,
                    "failures": [],
                }
            }

        with patch.object(sync, "api_request", side_effect=malformed_delete):
            malformed = sync.sync_libraries(config, {"confirm_deletions": True})

        self.assertFalse(malformed["ok"])
        self.assertEqual(malformed["libraries"][0]["deleted"], 0)
        self.assertIn(
            "documents/failures",
            malformed["libraries"][0]["failures"][0]["error"],
        )
        state = json.loads(self.paths["SYNC_STATE_PATH"].read_text("utf-8"))
        self.assertTrue(
            state["libraries"]["project_docs"]["files"][
                str(second.resolve())
            ]["missing"]
        )

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

    def test_ingest_paths_are_bounded_deduplicated_and_forwarded(self) -> None:
        config = graph.config_from_mapping(self.portable_mapping(source_roots=[]))
        captured: dict = {}

        def request(_config, endpoint, payload, **kwargs):
            captured.update({"endpoint": endpoint, "payload": payload, **kwargs})
            return {"result": {"processed": 2, "failed": 0, "details": []}}

        with patch.object(operations, "api_request", side_effect=request):
            result = operations.ingest_library(config, {
                "library_ids": ["project_docs"],
                "mode": "graph",
                "paths": [" failed.md ", "failed.md", "nested/second.md"],
            })

        self.assertTrue(result["ok"])
        self.assertEqual(captured["endpoint"], "/stores/ingest")
        self.assertEqual(
            captured["payload"]["paths"],
            ["failed.md", "nested/second.md"],
        )
        for invalid in (
            [],
            [""],
            [1],
            ["x" * 4097],
            ["x"] * 1001,
            ["x" * 1000] * 201,
        ):
            with self.subTest(invalid_type=type(invalid[0]).__name__ if invalid else "empty"):
                with self.assertRaisesRegex(graph.GraphExpandError, "paths"):
                    operations.ingest_library(config, {
                        "library_ids": ["project_docs"],
                        "paths": invalid,
                    })

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

        ingest = graph_guide(
            "operation_guide",
            operation="ingest",
            library_ids=["project_docs"],
            paths=[" failed.md ", "failed.md"],
            context=context,
        )
        self.assertEqual(ingest["arguments"]["params"]["paths"], ["failed.md"])
        with self.assertRaisesRegex(ValueError, "总长度"):
            graph_guide(
                "operation_guide",
                operation="ingest",
                library_ids=["project_docs"],
                paths=["x" * 1000] * 201,
                context=context,
            )
        manifest = parse_plugin_manifest(
            ROOT / "plugins" / "kemo_graph" / "SKILL.md",
            root=ROOT,
        )
        paths_schema = manifest.tool["input_schema"]["properties"]["paths"]
        self.assertEqual(paths_schema["minItems"], 1)
        self.assertEqual(paths_schema["maxItems"], 1000)

        document = graph_guide(
            "operation_guide",
            operation="documents",
            library_ids=["project_docs"],
            document_action="delete",
            source_id="source-1",
            context=context,
        )
        self.assertEqual(document["arguments"]["params"]["confirm"], "delete")

        source = self.root / "upload.txt"
        source.write_text("upload body", "utf-8")
        imported = graph_guide(
            "operation_guide",
            operation="import_file",
            library_ids=["project_docs"],
            path=str(source),
            context=context,
        )
        self.assertEqual(imported["arguments"]["command"], "import_file")
        self.assertEqual(imported["arguments"]["params"]["path"], str(source))
        self.assertFalse(imported["arguments"]["params"]["ingest_after_import"])
        self.assertEqual(imported["arguments"]["timeout"], 3600)

    def test_multipart_client_keeps_store_root_out_of_url(self) -> None:
        config = graph.config_from_mapping(self.portable_mapping(source_roots=[]))
        source = self.root / "说明.txt"
        source.write_bytes(b"portable upload")
        captured: dict = {}

        def perform(_config, request, *, timeout=None):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["body"] = bytes(request.data)
            captured["timeout"] = timeout
            return {"result": {"source_id": "source-1"}}

        with patch.object(graph_client, "_perform_request", side_effect=perform):
            result = graph_client.api_upload_file(
                config,
                "/stores/import",
                source,
                fields={"store_root": str(self.store)},
                query={"ingest": "false"},
                timeout=123,
            )

        self.assertEqual(result["result"]["source_id"], "source-1")
        self.assertIn("/stores/import?ingest=false", captured["url"])
        self.assertNotIn("store_root", captured["url"])
        self.assertIn("multipart/form-data", captured["headers"]["Content-type"])
        self.assertIn(str(self.store).encode("utf-8"), captured["body"])
        self.assertIn(b"portable upload", captured["body"])
        self.assertEqual(captured["timeout"], 123)

    def test_graph_client_classifies_http_and_transport_failures(self) -> None:
        config = graph.config_from_mapping(self.portable_mapping(source_roots=[]))

        def request_error(
            status: int,
            error: dict,
            *,
            headers: dict[str, str] | None = None,
        ) -> graph.GraphAPIError:
            body = json.dumps({
                "ok": False,
                "error": {**error, "ignored_secret": "must-not-leak"},
            }).encode("utf-8")
            failure = graph_client.urllib.error.HTTPError(
                config.base_url,
                status,
                "failure",
                headers or {},
                BytesIO(body),
            )
            opener = Mock()
            opener.open.side_effect = failure
            with (
                patch.object(
                    graph_client.urllib.request,
                    "build_opener",
                    return_value=opener,
                ),
                self.assertRaises(graph.GraphAPIError) as raised,
            ):
                graph_client.api_request(config, "/status")
            self.assertNotIn("must-not-leak", str(raised.exception))
            return raised.exception

        transient = request_error(
            503,
            {"code": "TEMPORARY", "message": "temporary outage"},
            headers={"Retry-After": "999999999999999999999999999999"},
        )
        self.assertTrue(transient.retryable)
        self.assertEqual(transient.status, 503)
        self.assertEqual(transient.status_code, 503)
        self.assertEqual(transient.category, "upstream_error")
        self.assertEqual(transient.retry_after_ms, 120_000)

        declared_final = request_error(
            503,
            {
                "code": "TEMPORARY",
                "message": "service says replay is unsafe",
                "retryable": False,
            },
        )
        self.assertFalse(declared_final.retryable)

        invalid = request_error(
            422,
            {
                "code": "INVALID_PARAM",
                "message": "bad request",
                "retryable": True,
            },
        )
        self.assertFalse(invalid.retryable)
        self.assertEqual(invalid.category, "request_error")

        opener = Mock()
        opener.open.side_effect = graph_client.urllib.error.URLError(
            TimeoutError("socket timeout")
        )
        with (
            patch.object(
                graph_client.urllib.request,
                "build_opener",
                return_value=opener,
            ),
            self.assertRaises(graph.GraphExpandError) as raised,
        ):
            graph_client.api_request(config, "/status")
        self.assertEqual(raised.exception.category, "connection_error")
        self.assertTrue(raised.exception.retryable)

        opener = Mock()
        opener.open.side_effect = graph_client.urllib.error.URLError(
            graph_client.ssl.CertificateError("certificate mismatch must not leak")
        )
        with (
            patch.object(
                graph_client.urllib.request,
                "build_opener",
                return_value=opener,
            ),
            self.assertRaises(graph.GraphExpandError) as tls_failure,
        ):
            graph_client.api_request(config, "/status")
        self.assertEqual(tls_failure.exception.category, "tls_error")
        self.assertFalse(tls_failure.exception.retryable)
        self.assertNotIn("certificate mismatch", str(tls_failure.exception))

    def test_graph_client_reads_only_bounded_structured_error_details(self) -> None:
        body = json.dumps({
            "ok": False,
            "error": {
                "code": "TEMPORARY",
                "message": "temporary outage",
                "details": {
                    "category": "upstream_error",
                    "status_code": 503,
                    "retryable": True,
                    "retry_after_ms": 999_999,
                    "raw_response": "must-not-leak",
                },
            },
        }).encode("utf-8")

        with self.assertRaises(graph.GraphAPIError) as raised:
            graph_client._decode_response(body)

        error = raised.exception
        self.assertEqual(error.category, "upstream_error")
        self.assertEqual(error.status_code, 503)
        self.assertTrue(error.retryable)
        self.assertEqual(error.retry_after_ms, 120_000)
        self.assertNotIn("must-not-leak", str(error))

        contradictory = json.dumps({
            "ok": False,
            "error": {
                "code": "INVALID_PARAM",
                "message": "invalid",
                "status_code": 422,
                "retryable": True,
            },
        }).encode("utf-8")
        with self.assertRaises(graph.GraphAPIError) as invalid:
            graph_client._decode_response(contradictory)
        self.assertFalse(invalid.exception.retryable)

    def test_status_library_failure_keeps_retry_metadata(self) -> None:
        config = graph.config_from_mapping(self.portable_mapping(source_roots=[]))
        temporary = graph.GraphAPIError(
            503,
            "TEMPORARY",
            "temporary outage",
            retry_after_ms=750,
        )
        with (
            patch.object(operations, "verify_service", return_value={}),
            patch.object(operations, "api_request", side_effect=temporary),
        ):
            result = operations.status_libraries(config, {})

        row = result["libraries"][0]
        self.assertFalse(result["ok"])
        self.assertEqual(row["status"], "error")
        self.assertEqual(row["category"], "upstream_error")
        self.assertEqual(row["status_code"], 503)
        self.assertTrue(row["retryable"])
        self.assertEqual(row["retry_after_ms"], 750)

    def test_import_file_routes_portable_upload_and_rejects_bad_inputs(self) -> None:
        config = graph.config_from_mapping(self.portable_mapping(source_roots=[]))
        source = self.root / "manual.pdf"
        source.write_bytes(b"%PDF-test")
        with patch.object(
            operations,
            "api_upload_file",
            return_value={"result": {"source_id": "source-1"}},
        ) as upload:
            result = operations.import_file(
                config,
                {
                    "library_ids": ["project_docs"],
                    "path": str(source),
                    "ingest_after_import": False,
                },
                caller_user="alice",
            )

        self.assertTrue(result["import_started"])
        self.assertFalse(result["ingest_started"])
        upload.assert_called_once()
        call = upload.call_args
        self.assertEqual(call.args[1], "/stores/import")
        self.assertEqual(call.args[2], source.resolve())
        fields = call.kwargs["fields"]
        self.assertEqual(set(fields), {"store_root"})
        self.assertTrue(Path(fields["store_root"]).samefile(self.store))
        self.assertEqual(call.kwargs["query"], {"ingest": "false"})

        unsupported = self.root / "payload.exe"
        unsupported.write_bytes(b"MZ")
        with self.assertRaisesRegex(graph.GraphExpandError, "不支持"):
            operations.import_file(
                config,
                {"library_ids": ["project_docs"], "path": str(unsupported)},
                caller_user="alice",
            )
        with self.assertRaisesRegex(graph.GraphExpandError, "绝对路径"):
            operations.import_file(
                config,
                {"library_ids": ["project_docs"], "path": "relative.pdf"},
                caller_user="alice",
            )
        with patch.object(graph_client, "MAX_UPLOAD_BYTES", 4):
            with self.assertRaisesRegex(graph.GraphExpandError, "50 MB"):
                graph_client.api_upload_file(config, "/stores/import", source)

    def test_start_expand_dispatches_import_file_as_admin_write(self) -> None:
        config = graph.config_from_mapping(self.portable_mapping(source_roots=[]))
        graph.save_config(config)
        with patch.object(
            start_expand,
            "import_file",
            return_value={"ok": True, "library_id": "project_docs"},
        ) as imported:
            result = start_expand.execute(
                "import_file",
                {"library_ids": ["project_docs"], "path": str(self.root / "a.pdf")},
                context={"user": "alice"},
            )
        self.assertTrue(result["ok"])
        imported.assert_called_once()

        with self.assertRaises(PermissionError):
            start_expand.execute(
                "import_file",
                {"library_ids": ["project_docs"], "path": str(self.root / "a.pdf")},
                context={"user": "bob"},
            )

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
        config = graph.config_from_mapping(mapping)
        graph.save_config(config)
        render.refresh_catalog()
        text = self.paths["INPUT_PATH"].read_text("utf-8")
        self.assertIn("project_docs", text)
        self.assertIn(config.libraries[0].store_root, text)


if __name__ == "__main__":
    unittest.main()
