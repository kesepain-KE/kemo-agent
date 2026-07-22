from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from update import agents as agents_update
from update import core as core_update
from update import plugins as plugins_update
from update import web as web_update
from update._utils import sync_directory


ROOT = Path(__file__).resolve().parents[1]


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_json(path: Path, value: dict) -> None:
    write(path, json.dumps(value, ensure_ascii=False, indent=2))


class UpdateModuleTests(unittest.TestCase):
    def test_sync_directory_dry_run_does_not_create_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            target = root / "target"
            write(source / "file.txt", "new")

            sync_directory(source, target, delete=True, dry_run=True)

            self.assertFalse(target.exists())

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
            write(target / "web" / "app.py", "old app")
            write(target / "web" / "obsolete.py", "delete")
            write(target / "web" / "node_modules" / "local.js", "preserve")
            write(target / "web" / "dist" / "index.html", "preserve")
            write(target / "web" / "frontend" / "node_modules" / "local.js", "preserve")
            write(target / "web" / "frontend" / "dist" / "index.html", "preserve")

            result = web_update.update(source, target)

            self.assertEqual(result["status"], "ok")
            self.assertEqual((target / "web" / "app.py").read_text(encoding="utf-8"), "new app")
            self.assertFalse((target / "web" / "obsolete.py").exists())
            self.assertTrue((target / "web" / "node_modules" / "local.js").is_file())
            self.assertTrue((target / "web" / "dist" / "index.html").is_file())
            self.assertTrue((target / "web" / "frontend" / "node_modules" / "local.js").is_file())
            self.assertTrue((target / "web" / "frontend" / "dist" / "index.html").is_file())

    def test_dispatcher_parses_module_and_component_versions(self) -> None:
        spec = importlib.util.spec_from_file_location("kemo_update_dispatcher", ROOT / "update.py")
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        dispatcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dispatcher)

        args = dispatcher.parse_args(["--module", "plugins", "--dry-run"])
        self.assertEqual(args.module, "plugins")
        self.assertTrue(args.dry_run)
        document = {
            "version": "1.2.0",
            "components": {"plugins": {"version": "1.3.0"}},
        }
        self.assertEqual(dispatcher.version_for_module(document, "all"), "1.2.0")
        self.assertEqual(dispatcher.version_for_module(document, "plugins"), "1.3.0")

    def test_plugins_only_dispatch_does_not_run_core_migrations_or_builds(self) -> None:
        spec = importlib.util.spec_from_file_location("kemo_update_plugins_only", ROOT / "update.py")
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        dispatcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dispatcher)
        version = {
            "version": "1.0.0",
            "components": {"plugins": {"version": "1.0.0"}},
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
            mock.patch.object(dispatcher, "migrate_user_memories") as migrate_memories,
            mock.patch.object(dispatcher, "build_web_frontend") as build_web,
            mock.patch.object(dispatcher, "refresh_dependencies") as refresh_dependencies,
        ):
            result = dispatcher.main(["--module", "plugins", "--yes"])

        self.assertEqual(result, 0)
        self.assertEqual(run_modules.call_args.args[0], ["plugins"])
        migrate_skeletons.assert_not_called()
        migrate_memories.assert_not_called()
        build_web.assert_not_called()
        refresh_dependencies.assert_not_called()


if __name__ == "__main__":
    unittest.main()
