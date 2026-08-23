from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from update import core as core_update
from update import cli as cli_update
from update import _utils as update_utils
from update import manifest as manifest_update
from update._utils import UpdateError, format_command, redact_text
from update.lock import UpdateLock


ROOT = Path(__file__).resolve().parents[2]


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, value: object) -> None:
    _write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _version_document(version: str) -> dict[str, object]:
    return {
        "name": "kemo-agent",
        "version": version,
        "schema_version": 1,
        "components": {
            name: {"version": version}
            for name in ("core", "agents", "plugins", "web")
        },
    }


def _copy_updater(target: Path) -> None:
    shutil.copy2(ROOT / "update.py", target / "update.py")
    shutil.copytree(
        ROOT / "update",
        target / "update",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def _create_remote_repository(
    root: Path,
    boards: dict[str, str],
    *,
    version: str = "2.0.0",
) -> Path:
    remote = root / "remote"
    remote.mkdir()
    _write_json(remote / "version.json", _version_document(version))
    _write(remote / "update" / "__init__.py", "")
    for name, source in boards.items():
        _write(remote / "update" / f"{name}.py", source)

    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=remote,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    for key, value in {
        "user.name": "kemo-agent updater test",
        "user.email": "updater-test@example.invalid",
    }.items():
        subprocess.run(
            ["git", "config", key, value],
            cwd=remote,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    subprocess.run(
        ["git", "add", "."],
        cwd=remote,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "test remote updater source"],
        cwd=remote,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return remote


def _run_updater(
    target: Path,
    remote: Path,
    *extra_args: str,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(target / "update.py"),
        "--repo-url",
        str(remote),
        "--branch",
        "main",
        "--remote-version-url",
        (remote / "version.json").as_uri(),
        "--yes",
        *extra_args,
    ]
    return subprocess.run(
        command,
        cwd=target,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )


class UpdateSafetyContractTests(unittest.TestCase):
    def test_updater_command_logs_redact_credentials(self) -> None:
        rendered = format_command(
            [
                "git",
                "clone",
                "https://operator:super-secret@example.invalid/repo.git?access_token=url-secret",
                "Authorization: Bearer bearer-secret-value",
                "--api-key=flag-secret",
            ]
        )
        self.assertNotIn("super-secret", rendered)
        self.assertNotIn("url-secret", rendered)
        self.assertNotIn("bearer-secret-value", rendered)
        self.assertNotIn("flag-secret", rendered)
        self.assertIn("***", rendered)
        diagnostic = redact_text(
            "remote=https://operator:super-secret@example.invalid/repo.git "
            "api_token=url-secret sk-1234567890abcdef1234567890"
        )
        self.assertNotIn("super-secret", diagnostic)
        self.assertNotIn("url-secret", diagnostic)
        self.assertNotIn("sk-1234567890abcdef1234567890", diagnostic)

        masked = update_utils.redact_json(
            {
                "device_token": "device-secret",
                "client_id": "safe-to-show",
                "private_key": "-----BEGIN PRIVATE KEY-----secret-----END PRIVATE KEY-----",
            }
        )
        self.assertEqual(masked["device_token"], "***")
        self.assertEqual(masked["client_id"], "safe-to-show")
        self.assertEqual(masked["private_key"], "***")

    def test_remote_manifest_errors_redact_url_credentials(self) -> None:
        url = (
            "https://operator:super-secret@example.invalid/version.json"
            "?access_token=url-secret"
        )
        with mock.patch.object(
            update_utils.urllib.request,
            "urlopen",
            side_effect=OSError("offline"),
        ):
            with self.assertRaises(UpdateError) as captured:
                update_utils.fetch_json(url)

        message = str(captured.exception)
        self.assertNotIn("super-secret", message)
        self.assertNotIn("url-secret", message)
        self.assertIn("***", message)

    def test_read_only_check_does_not_require_git_on_windows(self) -> None:
        with (
            mock.patch.object(cli_update.platform, "system", return_value="Windows"),
            mock.patch.object(cli_update, "command_exists", return_value=False),
        ):
            # A public version manifest can be checked on a fresh deployment
            # before Git for Windows has been installed.
            cli_update._print_platform_warning(require_git=False)

            with self.assertRaises(SystemExit):
                cli_update._print_platform_warning(require_git=True)

    def test_full_update_rejects_component_downgrade_even_when_root_is_newer(
        self,
    ) -> None:
        local = _version_document("1.2.1")
        remote = _version_document("1.2.2")
        local["components"]["plugins"]["version"] = "1.3.0"

        with self.assertRaises(UpdateError) as captured:
            manifest_update.ensure_no_downgrade(local, remote, "all")

        self.assertIn("插件生态 1.3.0 -> 1.2.2", str(captured.exception))

    def test_root_update_entrypoint_is_a_thin_cli_compatibility_shim(self) -> None:
        source = (ROOT / "update.py").read_text(encoding="utf-8")
        tree = ast.parse(source, filename="update.py")
        self.assertLessEqual(len(source.splitlines()), 20)
        self.assertFalse(
            any(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                for node in tree.body
            ),
            "根 update.py 只应保留 CLI 兼容入口，不应继续承载更新业务实现",
        )
        imported_main = False
        for node in tree.body:
            if not isinstance(node, ast.ImportFrom) or node.module != "update.cli":
                continue
            imported_main |= any(alias.name == "main" for alias in node.names)
        self.assertTrue(imported_main)
        self.assertTrue(
            any(
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name)
                and node.test.left.id == "__name__"
                for node in tree.body
            ),
            "根入口必须保留 python update.py ... 的执行合同",
        )

    def test_root_script_and_module_entrypoints_share_the_same_cli_contract(self) -> None:
        for command in (
            [sys.executable, str(ROOT / "update.py"), "--help"],
            [sys.executable, "-m", "update", "--help"],
        ):
            result = subprocess.run(
                command,
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--module", result.stdout)
            self.assertIn("--dry-run", result.stdout)

    def test_core_update_preserves_cron_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            target = root / "target"
            _write(source / "cron" / "scheduler.py", "new scheduler")
            _write_json(
                source / "cron" / "task_cron_system" / "expand_update.json",
                {
                    "task_id": "expand_update",
                    "title": "remote definition",
                    "next_run_at": "remote-next",
                    "latest_run_at": "remote-latest",
                    "status": "enabled",
                    "interval_seconds": 10,
                },
            )
            _write(target / "cron" / "scheduler.py", "old scheduler")
            _write(target / "cron" / "obsolete.py", "remove this source file")
            _write_json(
                target / "cron" / "task_cron_system" / "expand_update.json",
                {
                    "task_id": "expand_update",
                    "title": "old definition",
                    "next_run_at": "local-next",
                    "latest_run_at": "local-latest",
                    "status": "paused",
                    "interval_seconds": 5,
                },
            )
            _write(target / "cron" / "task_cron_system" / "log" / "run.log", "local log")

            core_update.update(source, target, assume_yes=True)

            self.assertEqual(
                (target / "cron" / "scheduler.py").read_text(encoding="utf-8"),
                "new scheduler",
            )
            self.assertFalse((target / "cron" / "obsolete.py").exists())
            stored = json.loads(
                (target / "cron" / "task_cron_system" / "expand_update.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(stored["title"], "remote definition")
            self.assertEqual(stored["interval_seconds"], 10)
            self.assertEqual(stored["next_run_at"], "local-next")
            self.assertEqual(stored["latest_run_at"], "local-latest")
            self.assertEqual(stored["status"], "paused")
            self.assertEqual(
                (target / "cron" / "task_cron_system" / "log" / "run.log").read_text(
                    encoding="utf-8"
                ),
                "local log",
            )

    def test_builtin_expand_source_timestamp_is_not_installed_on_fresh_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source" / "global_expand" / "kemo_gateway_status"
            target_root = root / "target"
            _write_json(
                source / "expand.json",
                {
                    "name": "Kemo 网关运行状态",
                    "explain": "只读状态",
                    "open_input": False,
                    "input_data": "input_data.md",
                    "input_health": "正常",
                    "start_update": "data_update.py",
                    "open_control": True,
                    "start_expand": "start_expand.py",
                    "start_control": "expand_control.md",
                    "recent_update": "2026-08-23 01:56:44",
                },
            )
            details: list[str] = []
            warnings: list[str] = []

            core_update._update_builtin_global_expand(
                root / "source",
                target_root,
                relative="global_expand/kemo_gateway_status",
                source_files=(),
                config_file="gateway_config.json",
                obsolete_files=(),
                dry_run=False,
                details=details,
                warnings=warnings,
            )

            installed = json.loads(
                (
                    target_root
                    / "global_expand"
                    / "kemo_gateway_status"
                    / "expand.json"
                ).read_text(encoding="utf-8")
            )
            self.assertNotIn("recent_update", installed)

    def test_core_update_does_not_overwrite_existing_global_config_values_by_default(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            target = root / "target"
            _write_json(
                source / "config" / "global_config.json",
                {
                    "schema_version": 1,
                    "runtime": {"max_workers": 8},
                    "new_default": {"enabled": True},
                },
            )
            _write_json(
                target / "config" / "global_config.json",
                {
                    "schema_version": 1,
                    "runtime": {"max_workers": 2},
                    "local_only": {"operator_choice": "keep"},
                },
            )

            core_update.update(source, target, assume_yes=True)
            stored = json.loads(
                (target / "config" / "global_config.json").read_text(encoding="utf-8")
            )

            self.assertEqual(stored["runtime"]["max_workers"], 2)
            self.assertEqual(stored["local_only"]["operator_choice"], "keep")
            self.assertTrue(stored["new_default"]["enabled"])

    def test_core_update_rejects_unmigrated_global_config_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            target = root / "target"
            _write_json(
                source / "config" / "global_config.json",
                {"schema_version": 2, "runtime": {"max_workers": 8}},
            )
            _write_json(
                target / "config" / "global_config.json",
                {"schema_version": 1, "runtime": {"max_workers": 2}},
            )

            with self.assertRaises(UpdateError):
                core_update.update(source, target, assume_yes=True)

            stored = json.loads(
                (target / "config" / "global_config.json").read_text(encoding="utf-8")
            )
            self.assertEqual(stored["schema_version"], 1)
            self.assertEqual(stored["runtime"]["max_workers"], 2)

    def test_core_update_requires_explicit_flag_to_replace_global_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            target = root / "target"
            _write_json(
                source / "config" / "global_config.json",
                {"schema_version": 1, "runtime": {"max_workers": 8}},
            )
            _write_json(
                target / "config" / "global_config.json",
                {"schema_version": 1, "runtime": {"max_workers": 2}},
            )

            core_update.update(
                source,
                target,
                assume_yes=True,
                replace_global_config=True,
            )
            stored = json.loads(
                (target / "config" / "global_config.json").read_text(encoding="utf-8")
            )
            self.assertEqual(stored["runtime"]["max_workers"], 8)

    def test_failed_board_stops_following_boards_and_restores_previous_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "installed"
            target.mkdir()
            _copy_updater(target)
            _write_json(target / "version.json", _version_document("1.0.0"))
            _write(target / "sentinel.txt", "old")

            boards = {
                "core": (
                    "def update(source_root, target_root, **kwargs):\n"
                    "    (target_root / 'sentinel.txt').write_text('new', encoding='utf-8')\n"
                    "    return {'module': 'core', 'status': 'failed', 'details': [], 'warnings': ['test failure']}\n"
                ),
                "agents": (
                    "def update(source_root, target_root, **kwargs):\n"
                    "    (target_root / 'following-board-ran.txt').write_text('bad', encoding='utf-8')\n"
                    "    return {'module': 'agents', 'status': 'ok', 'details': [], 'warnings': []}\n"
                ),
                "plugins": "def update(*args, **kwargs):\n    return {'module': 'plugins', 'status': 'ok', 'details': [], 'warnings': []}\n",
                "web": "def update(*args, **kwargs):\n    return {'module': 'web', 'status': 'ok', 'details': [], 'warnings': []}\n",
            }
            remote = _create_remote_repository(root, boards)

            result = _run_updater(target, remote, "--module", "all")

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((target / "sentinel.txt").read_text(encoding="utf-8"), "old")
            self.assertFalse((target / "following-board-ran.txt").exists())

    def test_failed_update_does_not_delete_unmanaged_local_root_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "installed"
            target.mkdir()
            _copy_updater(target)
            _write_json(target / "version.json", _version_document("1.0.0"))
            _write(target / "custom-local" / "operator.txt", "keep")

            boards = {
                "core": (
                    "def update(source_root, target_root, **kwargs):\n"
                    "    (target_root / 'run').mkdir(parents=True, exist_ok=True)\n"
                    "    (target_root / 'run' / 'new.py').write_text('new', encoding='utf-8')\n"
                    "    return {'module': 'core', 'status': 'failed', 'details': [], 'warnings': ['test failure']}\n"
                ),
                "agents": "def update(*args, **kwargs):\n    return {'module': 'agents', 'status': 'ok', 'details': [], 'warnings': []}\n",
                "plugins": "def update(*args, **kwargs):\n    return {'module': 'plugins', 'status': 'ok', 'details': [], 'warnings': []}\n",
                "web": "def update(*args, **kwargs):\n    return {'module': 'web', 'status': 'ok', 'details': [], 'warnings': []}\n",
            }
            remote = _create_remote_repository(root, boards)

            result = _run_updater(target, remote, "--module", "all")

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(
                (target / "custom-local" / "operator.txt").read_text(encoding="utf-8"),
                "keep",
            )

    def test_failed_update_removes_new_managed_root_files_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "installed"
            target.mkdir()
            _copy_updater(target)
            _write_json(target / "version.json", _version_document("1.0.0"))
            _write(target / "operator-note.txt", "keep")

            boards = {
                "core": (
                    "def update(source_root, target_root, **kwargs):\n"
                    "    (target_root / 'README_EN.md').write_text('new release file', encoding='utf-8')\n"
                    "    return {'module': 'core', 'status': 'failed', 'details': [], 'warnings': ['test failure']}\n"
                ),
                "agents": "def update(*args, **kwargs):\n    return {'module': 'agents', 'status': 'ok', 'details': [], 'warnings': []}\n",
                "plugins": "def update(*args, **kwargs):\n    return {'module': 'plugins', 'status': 'ok', 'details': [], 'warnings': []}\n",
                "web": "def update(*args, **kwargs):\n    return {'module': 'web', 'status': 'ok', 'details': [], 'warnings': []}\n",
            }
            remote = _create_remote_repository(root, boards)

            result = _run_updater(target, remote, "--module", "all")

            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse((target / "README_EN.md").exists())
            self.assertEqual(
                (target / "operator-note.txt").read_text(encoding="utf-8"),
                "keep",
            )

    def test_update_lock_rejects_a_live_second_holder_and_releases_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = UpdateLock(root=root)
            first.acquire()
            try:
                with self.assertRaises(UpdateError):
                    UpdateLock(root=root).acquire()
            finally:
                first.release()

            second = UpdateLock(root=root)
            second.acquire()
            second.release()
            # The marker is intentionally retained for diagnostics.  The
            # descriptor-held OS lock, not marker deletion, provides mutual
            # exclusion and is released above.
            self.assertTrue((root / ".update.lock").exists())

    def test_update_lock_rejects_a_live_holder_in_another_process(self) -> None:
        """The lock must be process-wide, not only a same-process convention."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ready = root / "child-ready"
            child_script = (
                "import sys, time\n"
                "from pathlib import Path\n"
                "from update.lock import UpdateLock\n"
                "root = Path(sys.argv[1])\n"
                "lock = UpdateLock(root=root)\n"
                "lock.acquire()\n"
                "(root / 'child-ready').write_text('ready', encoding='utf-8')\n"
                "try:\n"
                "    time.sleep(30)\n"
                "finally:\n"
                "    lock.release()\n"
            )
            child = subprocess.Popen(
                [sys.executable, "-c", child_script, str(root)],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            try:
                deadline = time.monotonic() + 10
                while not ready.exists() and child.poll() is None:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.05)
                if not ready.exists():
                    stderr = ""
                    if child.poll() is not None:
                        try:
                            _, stderr = child.communicate(timeout=5)
                        except subprocess.TimeoutExpired:
                            stderr = "子进程已退出但无法读取错误输出"
                    self.fail(f"锁子进程未能就绪（退出码={child.poll()}）：{stderr}")
                with self.assertRaises(UpdateError):
                    UpdateLock(root=root).acquire()
            finally:
                if child.poll() is None:
                    child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
