from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from update import agents as agents_update
from update import core as core_update
from update import plugins as plugins_update
from update import web as web_update
from update._utils import UpdateError, sync_directory, write_json_atomic


ROOT = Path(__file__).resolve().parents[1]


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_json(path: Path, value: dict) -> None:
    write(path, json.dumps(value, ensure_ascii=False, indent=2))


class UpdateModuleTests(unittest.TestCase):
    @staticmethod
    def load_dispatcher(name: str):
        spec = importlib.util.spec_from_file_location(name, ROOT / "update.py")
        if spec is None or spec.loader is None:
            raise AssertionError("无法加载 update.py")
        dispatcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dispatcher)
        return dispatcher

    def test_sync_directory_dry_run_does_not_create_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            target = root / "target"
            write(source / "file.txt", "new")

            sync_directory(source, target, delete=True, dry_run=True)

            self.assertFalse(target.exists())

    def test_write_json_atomic_replaces_manifest_without_temporary_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "version.json"
            write_json(path, {"version": "1.0.0"})

            write_json_atomic(path, {"version": "2.0.0", "name": "kemo-agent"})

            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["version"], "2.0.0")
            self.assertEqual(list(path.parent.glob(".version.json.*.tmp")), [])

    def test_runtime_state_initialization_creates_current_databases(self) -> None:
        dispatcher = self.load_dispatcher("kemo_update_runtime_state_initialization")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            user_dir = root / "users" / "alice"
            write_json(user_dir / "user_config.json", {})

            with mock.patch.object(dispatcher, "ROOT", root):
                dispatcher.initialize_runtime_state_databases(dry_run=False)

            database = sqlite3.connect(user_dir / "task_plan" / "task_plans.sqlite3")
            try:
                self.assertEqual(database.execute("SELECT COUNT(*) FROM task_plans").fetchone()[0], 0)
            finally:
                database.close()
            database = sqlite3.connect(user_dir / "history" / "history.sqlite3")
            try:
                self.assertEqual(
                    database.execute("SELECT COUNT(*) FROM message_processed_messages").fetchone()[0],
                    0,
                )
            finally:
                database.close()
            self.assertTrue((root / "runtime" / "logs.sqlite3").is_file())

    def test_build_requires_npm_even_when_stale_dist_exists(self) -> None:
        dispatcher = self.load_dispatcher("kemo_update_build_requires_npm")
        with tempfile.TemporaryDirectory() as temporary:
            web_dir = Path(temporary) / "frontend"
            write_json(web_dir / "package.json", {"name": "web"})
            write(web_dir / "dist" / "index.html", "stale")
            with (
                mock.patch.object(dispatcher, "WEB_DIR", web_dir),
                mock.patch.object(dispatcher, "_resolve_npm_command", return_value=None),
                self.assertRaises(UpdateError),
            ):
                dispatcher.build_web_frontend(dry_run=False)

    def test_core_preserves_message_out_and_only_updates_register_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            target = root / "target"
            write(source / "message" / "router.py", "new router")
            write(source / "message" / "out" / "remote.txt", "remote queue")
            write(target / "message" / "router.py", "old router")
            write(target / "message" / "obsolete.py", "remove me")
            write(target / "message" / "out" / "local.txt", "local queue")
            for relative in core_update.REGISTER_FILES:
                write(source / relative, f"new {relative}")
                write(target / relative, f"old {relative}")
                write((target / relative).parent / "local-data.md", "preserve")
            write_json(source / "config" / "global_config.json", {"schema_version": 2, "new": True})
            write_json(target / "config" / "global_config.json", {"schema_version": 1, "local": True})

            result = core_update.update(source, target, assume_yes=True)

            self.assertEqual((target / "message" / "router.py").read_text(encoding="utf-8"), "new router")
            self.assertFalse((target / "message" / "obsolete.py").exists())
            self.assertEqual((target / "message" / "out" / "local.txt").read_text(encoding="utf-8"), "local queue")
            self.assertFalse((target / "message" / "out" / "remote.txt").exists())
            for relative in core_update.REGISTER_FILES:
                self.assertEqual((target / relative).read_text(encoding="utf-8"), f"new {relative}")
                self.assertTrue(((target / relative).parent / "local-data.md").is_file())
            self.assertEqual(json.loads((target / "config" / "global_config.json").read_text(encoding="utf-8"))["schema_version"], 2)
            self.assertEqual(result["module"], "core")
            self.assertEqual(result["status"], "partial")

    def test_core_syncs_provider_and_english_readme(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            target = root / "target"
            write(source / "provider" / "protocol" / "assets.py", "new assets")
            write(target / "provider" / "protocol" / "assets.py", "old assets")
            write(target / "provider" / "obsolete.py", "remove me")
            write(source / "README_EN.md", "new readme")
            write(target / "README_EN.md", "old readme")

            core_update.update(source, target, assume_yes=True)

            self.assertEqual(
                (target / "provider" / "protocol" / "assets.py").read_text(encoding="utf-8"),
                "new assets",
            )
            self.assertFalse((target / "provider" / "obsolete.py").exists())
            self.assertEqual((target / "README_EN.md").read_text(encoding="utf-8"), "new readme")

    def test_core_contains_runtime_entrypoints_but_does_not_commit_version(self) -> None:
        expected = {
            "start_web.py",
            "restart.py",
            "requirements-dev.txt",
            "kemo-agent.ico",
            "kemo-web-UI.png",
        }
        self.assertTrue(expected.issubset(set(core_update.FILES)))
        self.assertNotIn("version.json", core_update.FILES)

    def test_core_installs_gateway_status_expand_and_preserves_local_activation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            target = root / "target"
            module = Path(core_update.GATEWAY_STATUS_EXPAND)
            for name in core_update.GATEWAY_STATUS_EXPAND_FILES:
                write(source / module / name, f"new {name}")
            write_json(source / module / "expand.json", {
                "name": "Kemo 网关运行状态",
                "open_input": False,
                "input_health": "正常",
            })
            write(source / module / "input_data.md", "source inactive")
            write_json(target / module / "gateway_config.json", {
                "base_url": "http://127.0.0.1:7531",
                "status_token": "local-secret",
            })
            write_json(target / module / "expand.json", {
                "name": "old",
                "open_input": True,
                "input_health": "异常",
                "recent_update": "2026-07-28 12:00:00",
            })
            write(target / module / "input_data.md", "local runtime status")
            write(target / module / "data" / "gateway_status.json", "local snapshot")

            result = core_update.update(source, target, assume_yes=True)

            self.assertIn("更新内置拓展", "\n".join(result["details"]))
            self.assertEqual((target / module / "gateway_status.py").read_text("utf-8"), "new gateway_status.py")
            self.assertEqual((target / module / "input_data.md").read_text("utf-8"), "local runtime status")
            self.assertEqual((target / module / "data" / "gateway_status.json").read_text("utf-8"), "local snapshot")
            self.assertEqual(
                json.loads((target / module / "gateway_config.json").read_text("utf-8"))["status_token"],
                "local-secret",
            )
            manifest = json.loads((target / module / "expand.json").read_text("utf-8"))
            self.assertTrue(manifest["open_input"])
            self.assertEqual(manifest["input_health"], "异常")
            self.assertEqual(manifest["recent_update"], "2026-07-28 12:00:00")

    def test_agents_merge_does_not_delete_local_only_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            target = root / "target"
            write(source / "agents" / "__init__.py", "REMOTE = True")
            write(source / "agents" / "_runtime" / "runtime.py", "new runtime")
            write(source / "agents" / "builtin" / "agent.json", '{"version": "2.0.0"}')
            write(source / "agents" / "builtin" / "executor.py", "new executor")
            write(target / "agents" / "_runtime" / "obsolete.py", "remove")
            write(target / "agents" / "builtin" / "agent.json", '{"version": "1.0.0"}')
            write(target / "agents" / "builtin" / "local-only.py", "remove from built-in")
            write(target / "agents" / "custom" / "agent.json", '{"version": "9.0.0"}')

            result = agents_update.update(source, target)

            self.assertEqual(result["status"], "ok")
            self.assertFalse((target / "agents" / "_runtime" / "obsolete.py").exists())
            self.assertEqual((target / "agents" / "_runtime" / "runtime.py").read_text(encoding="utf-8"), "new runtime")
            self.assertFalse((target / "agents" / "builtin" / "local-only.py").exists())
            self.assertEqual((target / "agents" / "builtin" / "executor.py").read_text(encoding="utf-8"), "new executor")
            self.assertTrue((target / "agents" / "custom" / "agent.json").is_file())
            self.assertTrue(any("1.0.0 -> 2.0.0" in detail for detail in result["details"]))

    def test_plugins_are_replaced_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            target = root / "target"
            write(source / "plugins" / "file" / "tool.py", "new")
            write(target / "plugins" / "file" / "tool.py", "old")
            write(target / "plugins" / "local-extra" / "tool.py", "delete")

            result = plugins_update.update(source, target)

            self.assertEqual(result["status"], "ok")
            self.assertEqual((target / "plugins" / "file" / "tool.py").read_text(encoding="utf-8"), "new")
            self.assertFalse((target / "plugins" / "local-extra").exists())

    def test_web_preserves_dependency_and_dist_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            target = root / "target"
            write(source / "web" / "app.py", "new app")
            write(source / "web" / "frontend" / "src" / "main.tsx", "new frontend")
            write(source / "provider" / "protocol" / "assets.py", "new assets")
            write(source / "README_EN.md", "new readme")
            write(target / "web" / "app.py", "old app")
            write(target / "web" / "obsolete.py", "delete")
            write(target / "web" / "node_modules" / "local.js", "preserve")
            write(target / "web" / "dist" / "index.html", "preserve")
            write(target / "web" / "frontend" / "node_modules" / "local.js", "preserve")
            write(target / "web" / "frontend" / "dist" / "index.html", "preserve")
            write(target / "provider" / "protocol" / "assets.py", "old assets")
            write(target / "provider" / "obsolete.py", "remove")
            write(target / "README_EN.md", "old readme")

            result = web_update.update(source, target)

            self.assertEqual(result["status"], "ok")
            self.assertEqual((target / "web" / "app.py").read_text(encoding="utf-8"), "new app")
            self.assertFalse((target / "web" / "obsolete.py").exists())
            self.assertTrue((target / "web" / "node_modules" / "local.js").is_file())
            self.assertTrue((target / "web" / "dist" / "index.html").is_file())
            self.assertTrue((target / "web" / "frontend" / "node_modules" / "local.js").is_file())
            self.assertTrue((target / "web" / "frontend" / "dist" / "index.html").is_file())
            self.assertEqual(
                (target / "provider" / "protocol" / "assets.py").read_text(encoding="utf-8"),
                "new assets",
            )
            self.assertFalse((target / "provider" / "obsolete.py").exists())
            self.assertEqual((target / "README_EN.md").read_text(encoding="utf-8"), "new readme")

    def test_dispatcher_parses_module_and_component_versions(self) -> None:
        dispatcher = self.load_dispatcher("kemo_update_dispatcher")

        args = dispatcher.parse_args(["--module", "plugins", "--dry-run"])
        self.assertEqual(args.module, "plugins")
        self.assertTrue(args.dry_run)
        document = {
            "version": "1.2.0",
            "components": {"plugins": {"version": "1.3.0"}},
        }
        self.assertEqual(dispatcher.version_for_module(document, "all"), "1.2.0")
        self.assertEqual(dispatcher.version_for_module(document, "plugins"), "1.3.0")

    def test_dispatcher_rejects_incomplete_version_manifest(self) -> None:
        dispatcher = self.load_dispatcher("kemo_update_version_validation")
        document = {
            "version": "2.0.0",
            "components": {"core": {"version": "2.0.0"}},
        }

        with self.assertRaises(UpdateError):
            dispatcher.validate_version_document(document, "远程")

    def test_dispatcher_loads_update_board_from_cloned_source(self) -> None:
        dispatcher = self.load_dispatcher("kemo_update_remote_board")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            target = root / "target"
            write(source / "update" / "__init__.py", "")
            write(source / "update" / "_utils.py", "MARKER = 'remote-package'\n")
            write(
                source / "update" / "core.py",
                "from ._utils import MARKER\n"
                "def update(source_root, target_root, **kwargs):\n"
                "    target_root.mkdir(parents=True, exist_ok=True)\n"
                "    (target_root / 'remote-board-used').write_text(MARKER, encoding='utf-8')\n"
                "    return {'module': 'core', 'status': 'ok', 'details': [], 'warnings': []}\n",
            )

            results = dispatcher.run_modules(
                ["core"],
                source,
                target,
                dry_run=False,
                assume_yes=True,
            )

            self.assertEqual(results[0]["status"], "ok")
            self.assertEqual(
                (target / "remote-board-used").read_text(encoding="utf-8"),
                "remote-package",
            )

    def test_dispatcher_disables_legacy_bridge_for_remote_web_board(self) -> None:
        dispatcher = self.load_dispatcher("kemo_update_remote_web_board")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            target = root / "target"
            write(source / "update" / "__init__.py", "")
            write(
                source / "update" / "web.py",
                "def update(source_root, target_root, *, legacy_core_compat=True, **kwargs):\n"
                "    return {'module': 'web', 'status': 'ok', "
                "'details': [str(legacy_core_compat)], 'warnings': []}\n",
            )

            results = dispatcher.run_modules(
                ["web"],
                source,
                target,
                dry_run=False,
                assume_yes=True,
            )

            self.assertEqual(results[0]["status"], "ok")
            self.assertEqual(results[0]["details"], ["False"])

    def test_component_update_preserves_root_and_other_component_versions(self) -> None:
        dispatcher = self.load_dispatcher("kemo_update_version_merge")
        local = {
            "version": "1.0.0",
            "components": {
                "core": {"version": "1.0.0"},
                "plugins": {"version": "1.0.0"},
            },
        }
        remote = {
            "version": "2.0.0",
            "components": {
                "core": {"version": "2.0.0"},
                "plugins": {"version": "2.1.0"},
            },
        }

        merged = dispatcher.version_document_after_update(local, remote, "plugins")

        self.assertEqual(merged["version"], "1.0.0")
        self.assertEqual(merged["components"]["core"]["version"], "1.0.0")
        self.assertEqual(merged["components"]["plugins"]["version"], "2.1.0")
        self.assertEqual(local["components"]["plugins"]["version"], "1.0.0")

    def test_finalize_version_writes_only_after_explicit_commit(self) -> None:
        dispatcher = self.load_dispatcher("kemo_update_version_commit")
        local = {
            "version": "1.0.0",
            "components": {"plugins": {"version": "1.0.0"}},
        }
        remote = {
            "version": "2.0.0",
            "components": {"plugins": {"version": "2.0.0"}},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_json(root / "version.json", local)
            with mock.patch.object(dispatcher, "ROOT", root):
                dispatcher.finalize_version_document(
                    local,
                    remote,
                    "plugins",
                    dry_run=False,
                )

            stored = json.loads((root / "version.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["version"], "1.0.0")
            self.assertEqual(stored["components"]["plugins"]["version"], "2.0.0")

    def test_plugins_only_dispatch_does_not_run_core_migrations_or_builds(self) -> None:
        dispatcher = self.load_dispatcher("kemo_update_plugins_only")
        version = {
            "version": "1.0.0",
            "components": {
                name: {"version": "1.0.0"}
                for name in dispatcher.MODULES
            },
        }

        with (
            mock.patch.object(dispatcher, "_print_platform_warning"),
            mock.patch.object(dispatcher, "load_version_documents", return_value=(version, version)),
            mock.patch.object(dispatcher, "should_update", return_value=True),
            mock.patch.object(dispatcher, "require_commands"),
            mock.patch.object(dispatcher, "clone_latest", return_value=ROOT),
            mock.patch.object(dispatcher, "make_backup"),
            mock.patch.object(
                dispatcher,
                "run_modules",
                return_value=[{"module": "plugins", "status": "ok", "details": [], "warnings": []}],
            ) as run_modules,
            mock.patch.object(dispatcher, "migrate_user_skeletons") as migrate_skeletons,
            mock.patch.object(
                dispatcher, "initialize_user_memory_databases"
            ) as initialize_memories,
            mock.patch.object(
                dispatcher, "initialize_runtime_state_databases"
            ) as initialize_runtime_state,
            mock.patch.object(dispatcher, "build_web_frontend") as build_web,
            mock.patch.object(dispatcher, "refresh_dependencies") as refresh_dependencies,
            mock.patch.object(dispatcher, "finalize_version_document") as finalize_version,
        ):
            result = dispatcher.main(["--module", "plugins", "--yes"])

        self.assertEqual(result, 0)
        self.assertEqual(run_modules.call_args.args[0], ["plugins"])
        migrate_skeletons.assert_not_called()
        initialize_memories.assert_not_called()
        initialize_runtime_state.assert_not_called()
        build_web.assert_not_called()
        refresh_dependencies.assert_not_called()
        finalize_version.assert_called_once_with(
            version,
            version,
            "plugins",
            dry_run=False,
        )

    def test_failed_web_build_does_not_commit_version(self) -> None:
        dispatcher = self.load_dispatcher("kemo_update_failed_build")
        local = {
            "version": "1.0.0",
            "components": {name: {"version": "1.0.0"} for name in dispatcher.MODULES},
        }
        remote = {
            "version": "2.0.0",
            "components": {name: {"version": "2.0.0"} for name in dispatcher.MODULES},
        }
        results = [
            {"module": name, "status": "ok", "details": [], "warnings": []}
            for name in dispatcher.MODULES
        ]

        with (
            mock.patch.object(dispatcher, "_print_platform_warning"),
            mock.patch.object(dispatcher, "load_version_documents", return_value=(local, remote)),
            mock.patch.object(dispatcher, "should_update", return_value=True),
            mock.patch.object(dispatcher, "require_commands"),
            mock.patch.object(dispatcher, "clone_latest", return_value=ROOT),
            mock.patch.object(dispatcher, "make_backup", return_value=ROOT / ".backups" / "test"),
            mock.patch.object(dispatcher, "run_modules", return_value=results),
            mock.patch.object(dispatcher, "migrate_user_skeletons"),
            mock.patch.object(dispatcher, "initialize_user_memory_databases"),
            mock.patch.object(dispatcher, "initialize_runtime_state_databases"),
            mock.patch.object(
                dispatcher,
                "build_web_frontend",
                side_effect=UpdateError("frontend build failed"),
            ),
            mock.patch.object(dispatcher, "refresh_dependencies") as refresh_dependencies,
            mock.patch.object(dispatcher, "finalize_version_document") as finalize_version,
        ):
            result = dispatcher.main(["--module", "all", "--yes"])

        self.assertEqual(result, 1)
        refresh_dependencies.assert_not_called()
        finalize_version.assert_not_called()


if __name__ == "__main__":
    unittest.main()
