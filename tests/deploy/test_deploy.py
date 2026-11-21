"""Formal unit and fault-path tests for the independent deployment subsystem.

All temporary state is confined to ``deploy/.test-work``; no network or real
installation is used. ``deploy/tests/test_deploy.py`` is only a compatibility
entry point for maintainers who run the deployment directory in isolation.
"""
from __future__ import annotations

import contextlib
import io
import yaml
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
sys.path.insert(0, str(DEPLOY))
from core import cli, config, runtime, sync
from core.common import DeployError, digest, json_bytes, target, version_key
from core.locking import Lock
from core.policy import local_module, protected, user_plugin_directories
from core.source import Bundle, obtain, parse_archive, release
from core.yamllite import dumps, loads
from pack import clean_definition, publishable, write_bundle


def fixture(ver='1.3.0', extra=None):
    files = {
        'version.json': json_bytes({'name': 'kemo-agent', 'version': ver}),
        'start_web.py': b'print("web")\n', 'setup.py': b'print("setup")\n',
        'requirements.txt': b'', 'deploy/deploy.py': b'# deploy\n',
        'deploy/core/cli.py': b'# cli fixture\n', 'deploy/core/common.py': b'# common fixture\n',
        'deploy/deploy.yaml': (DEPLOY / 'deploy.yaml').read_bytes(),
        'web/frontend/dist/index.html': b'<html></html>',
        'config/global_config.yaml': json_bytes({'schema_version': 1, 'local_value': False}),
        'run/example.py': b'VALUE = 1\n',
    }
    files.update(extra or {})
    return files


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        base = DEPLOY / '.test-work'
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'install'
        self.doc = config.load(DEPLOY / 'deploy.yaml')
        self.channel = 'windows' if os.name == 'nt' else 'linux'

    def bundle(self, ver='1.3.0', extra=None):
        files = fixture(ver, extra)
        return Bundle(ver, files, {name: 0o644 for name in files}, 'a' * 64)

    def archive(self, ver='1.3.0', extra=None):
        path = self.base / f'kemo-agent-release-{ver}.zip'
        write_bundle(fixture(ver, extra), ver, path)
        return path

    def install(self, bundle=None, after_write=None):
        bundle = bundle or self.bundle()
        self.root.mkdir(exist_ok=True)
        old = sync.installed(self.root)
        changes, files = sync.plan(self.root, bundle, self.doc, old)
        record = {'schema_version': 1, 'version': bundle.version, 'install_kind': self.channel,
                  'install_root': str(self.root.resolve()), 'files': files}
        sync.commit(self.root, changes, record, after_write)
        return record

    def invoke(self, *args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return cli.main(list(args))

    def _install_windows_web(self, python, web):
        deploy_files = {
            path.relative_to(ROOT).as_posix(): path.read_bytes()
            for path in (DEPLOY / 'deploy.py', *(DEPLOY / 'core').glob('*.py'))
        }
        record = self.install(self.bundle(extra={**deploy_files, 'start_web.py': web}))
        record['python'] = str(python)
        (self.root / '.kemo-install.json').write_bytes(json_bytes(record))

    def test_serve_windows_selects_launcher_and_creation_flags(self):
        python = self.base / 'python.exe'
        gui_python = python.with_name('pythonw.exe')
        self._install_windows_web(python, b'')
        # Stub only the process boundary, including flags unavailable on Unix.
        windows = SimpleNamespace(
            Popen=mock.Mock(), DEVNULL=subprocess.DEVNULL, PIPE=subprocess.PIPE, STDOUT=subprocess.STDOUT,
            DETACHED_PROCESS=0x00000008, CREATE_NO_WINDOW=0x08000000, CREATE_NEW_PROCESS_GROUP=0x00000200,
        )
        for executable, flags in ((python, windows.CREATE_NO_WINDOW), (gui_python, windows.DETACHED_PROCESS)):
            with self.subTest(executable=executable.name):
                executable.touch()
                windows.Popen.reset_mock()
                windows.Popen.return_value = SimpleNamespace(stdout=io.BytesIO(), wait=lambda: 0)
                with mock.patch.object(runtime.Path, 'cwd', return_value=self.root), mock.patch.object(runtime, 'subprocess', windows):
                    self.assertEqual(runtime.serve_windows('test-receipt', str(python), []), 0)
                windows.Popen.assert_called_once()
                self.assertEqual(windows.Popen.call_args.args[0][0], str(executable))
                self.assertEqual(windows.Popen.call_args.kwargs['creationflags'], flags | windows.CREATE_NEW_PROCESS_GROUP)

    def test_copy_web_log_trims_split_utf8_character(self):
        log_path = self.base / 'web.log'
        retained = '好' * 20 + '\n\n'
        incoming = '新增日志\n'
        # At 128 bytes, the tail starts at byte 64: the second byte of 中.
        log_path.write_bytes(b'A' * 63 + ('中' + retained).encode('utf-8'))
        with mock.patch.object(runtime, '_WEB_LOG_MAX_BYTES', 128), log_path.open('r+b') as log:
            runtime._copy_web_log(io.BytesIO(incoming.encode('utf-8')), log)

        data = log_path.read_bytes()
        self.assertLessEqual(len(data), 128)
        self.assertEqual(data.decode('utf-8', errors='strict'), retained + incoming)

    @unittest.skipUnless(os.name == 'nt', 'requires a native Windows console and venv launcher')
    def test_windows_start_detaches_and_keeps_update_lock(self):
        import psutil

        venv = self.base / 'venv'
        subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(venv)], check=True)
        interpreter = venv / 'Scripts' / 'python.exe'
        web = b'''
import ctypes
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ready")

def main(argv, *, on_ready):
    assert argv == ["--smoke-test"]
    assert ctypes.windll.kernel32.GetConsoleWindow() == 0
    print("old marker below a large burst")
    chunk = b"x" * (64 * 1024 - 1) + b"\\n"
    for _ in range(200):
        os.write(1, chunk)
    print("detached stdout")
    print("detached stderr", file=sys.stderr)
    with HTTPServer(("127.0.0.1", 0), Health) as server:
        server.timeout = 12
        on_ready(f"http://127.0.0.1:{server.server_port}")
        server.handle_request()
    return 0
'''
        self._install_windows_web(interpreter, web)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        # Redirection does not remove the caller's OS console.
        caller = (
            'import ctypes,subprocess,sys; '
            'assert ctypes.windll.kernel32.GetConsoleWindow(); '
            'raise SystemExit(subprocess.call([sys.executable,*sys.argv[1:]]))'
        )
        result = subprocess.run(
            [sys.executable, '-c', caller, str(self.root / 'deploy/deploy.py'),
             'start', 'windows', '--yes', '--', '--smoke-test'],
            cwd=self.root, capture_output=True, encoding='utf-8', errors='replace',
            creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startupinfo, timeout=90,
        )
        log_path = self.root / '.kemo-deploy/web.log'
        self.assertEqual(result.returncode, 0, result.stderr)
        match = re.search(r'Web is listening at (http://127\.0\.0\.1:\d+) \(PID (\d+)\)', result.stdout)
        assert match is not None, result.stdout
        url, pid = match.groups()
        child = psutil.Process(int(pid))
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with log_path.open('rb') as log:
                    log.seek(-min(log_path.stat().st_size, 512), os.SEEK_END)
                    if b'detached stderr' in log.read():
                        break
                time.sleep(0.05)
            else:
                self.fail('background stderr did not reach the log')
            self.assertLessEqual(log_path.stat().st_size, 10 * 1024 * 1024)
            log = log_path.read_bytes()
            self.assertIn(b'detached stdout', log)
            self.assertNotIn(b'old marker below a large burst', log)
            with self.assertRaisesRegex(DeployError, 'holds .update.lock'):
                with Lock(self.root / '.update.lock'):
                    pass
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(url + '/api/health', timeout=5) as response:
                self.assertEqual(response.read(), b'ready')
        finally:
            child.wait(timeout=15)
        deadline = time.monotonic() + 5
        while True:
            try:
                with Lock(self.root / '.update.lock'):
                    break
            except DeployError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

    @unittest.skipUnless(os.name == 'nt', 'requires the Windows deployment launcher')
    def test_windows_start_rejects_lock_conflict_and_replaced_runtime(self):
        web = b'raise AssertionError("must not import Web during a failed handoff")\\n'
        self._install_windows_web(self.base / 'replaced-python.exe', web)

        with Lock(self.root / '.update.lock'):
            with self.assertRaisesRegex(DeployError, r'holds \.update\.lock'):
                runtime.start_windows(self.root, sys.executable, [])

        with self.assertRaisesRegex(DeployError, 'runtime changed during startup'):
            runtime.start_windows(self.root, sys.executable, [])
        # Rejected handoffs never start a second writer for a live service's log.
        self.assertFalse((self.root / '.kemo-deploy/web.log').exists())
        with Lock(self.root / '.update.lock'):
            pass

    @unittest.skipUnless(os.name == 'nt', 'requires the Windows deployment launcher')
    def test_windows_start_surfaces_stderr_when_web_exits_without_a_receipt(self):
        self._install_windows_web(sys.executable, b'''
import os

def main(argv, *, on_ready):
    os.write(2, b"startup diagnostics\\n")
    return 17
''')
        with self.assertRaisesRegex(DeployError, r'exit 17.*web\.log'):
            runtime.start_windows(self.root, sys.executable, [])
        self.assertIn(b'startup diagnostics', (self.root / '.kemo-deploy/web.log').read_bytes())
        with Lock(self.root / '.update.lock'):
            pass

    @unittest.skipUnless(os.name == 'nt', 'requires native Windows process-tree termination')
    def test_windows_start_timeout_reaps_the_web_process_tree(self):
        import psutil

        self._install_windows_web(sys.executable, b'''
import json
import os
from pathlib import Path
import subprocess
import sys

def main(argv, *, on_ready):
    sleeper = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    marker = Path("test-pids.tmp")
    marker.write_text(json.dumps([os.getpid(), sleeper.pid]))
    marker.replace("test-pids.json")
    return sleeper.wait()
''')
        marker = self.root / 'test-pids.json'
        # Advance the launch clock only after both real children are running.
        launch_clock = SimpleNamespace(
            sleep=time.sleep,
            monotonic=lambda: time.monotonic() + (61 if marker.is_file() else 0),
        )
        with mock.patch.object(runtime, 'time', launch_clock):
            with self.assertRaisesRegex(DeployError, r'within 60 seconds.*web\.log'):
                runtime.start_windows(self.root, sys.executable, [])

        pids = json.loads(marker.read_text(encoding='utf-8'))
        _, alive = psutil.wait_procs(
            [psutil.Process(pid) for pid in pids if psutil.pid_exists(pid)], timeout=5,
        )
        self.assertFalse(alive, f'launch timed out but children survived: {alive}')
        with Lock(self.root / '.update.lock'):
            pass

    def test_default_config_loads(self):
        self.assertEqual(self.doc['deploy']['asset'], 'kemo-agent-release-{version}.zip')
        self.assertEqual(self.doc['mapping']['tree/'], '{install_root}/')

    def test_missing_discovery_keys_have_read_only_bootstrap_defaults(self):
        path = self.base / 'partial.yaml'
        content = b'schema_version: 1\ndeploy:\n  repo: custom/project\n'
        path.write_bytes(content)
        doc = config.load(path)
        self.assertEqual(doc['deploy']['repo'], 'custom/project')
        self.assertEqual(doc['deploy']['asset'], 'kemo-agent-release-{version}.zip')
        self.assertEqual(path.read_bytes(), content)

    def test_yaml_quotes_comments_and_booleans(self):
        self.assertEqual(loads('a: "value # literal" # comment\nb:\n  - false\n  - 0\n'), {'a': 'value # literal', 'b': [False, 0]})

    def test_repository_yaml_matches_deployer_parser_and_round_trips(self):
        path = ROOT / "config" / "global_config.yaml"
        original = yaml.safe_load(path.read_text(encoding="utf-8"))
        for manifest in (path, *sorted((ROOT / "plugins").glob("*/plugin.yaml"))):
            with self.subTest(manifest=manifest.relative_to(ROOT)):
                text = manifest.read_text(encoding="utf-8")
                self.assertEqual(loads(text), yaml.safe_load(text))
        merged = {
            **original,
            "yaml_roundtrip_probe": {
                "enabled": False,
                "count": 0,
                "empty": "",
                "missing": None,
                "ratio": 1.25,
                "items": [1, "two", False],
            },
        }
        self.assertEqual(loads(dumps(merged)), merged)

    def test_yaml_rejects_unsupported_and_duplicate_keys(self):
        for text in ('a: 1\na: 2', 'a:\n   b: 2', 'a: &ref', 'a: |', 'a:\n\tb: 1', '{"a":1,"a":2}'):
            with self.subTest(text=text), self.assertRaises(DeployError):
                loads(text)

    def test_defaults_preserve_false_zero_empty_and_unknown(self):
        local = {'a': False, 'b': 0, 'c': '', 'd': [], 'custom': 'yes', 'nested': {'x': 1}}
        merged = config.merge_defaults({'a': True, 'b': 5, 'c': 'x', 'd': ['a'], 'nested': {'x': 2, 'y': 3}}, local)
        self.assertEqual(merged, {**local, 'nested': {'x': 1, 'y': 3}})

    def test_type_change_is_not_silently_merged(self):
        with self.assertRaises(DeployError):
            config.merge_defaults({'a': {}}, {'a': []})

    def test_semver_prerelease_order(self):
        versions = ['1.3.0-alpha.2', '1.3.0-alpha.10', '1.3.0-beta', '1.3.0', '1.10.0']
        self.assertEqual(sorted(reversed(versions), key=version_key), versions)

    def test_portable_path_rejection(self):
        for value in ('../outside', '/absolute', 'C:/windows', 'x\\y', 'file:stream', 'a/../b', 'NUL.txt', 'a./b'):
            with self.subTest(value=value), self.assertRaises(DeployError):
                target(self.root, value)

    def test_symlink_escape_rejected(self):
        self.root.mkdir()
        outside = self.base / 'outside'
        outside.mkdir()
        try:
            (self.root / 'link').symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('OS does not permit test symlink creation')
        with self.assertRaises(DeployError):
            target(self.root, 'link/payload')

    def test_valid_archive_is_verified(self):
        archive = self.archive()
        result = parse_archive(archive.read_bytes(), '1.3.0')
        self.assertEqual(result.version, '1.3.0')
        self.assertEqual(result.checksum, digest(archive.read_bytes()))

    def test_target_version_mismatch_rejected(self):
        with self.assertRaises(DeployError):
            parse_archive(self.archive().read_bytes(), '1.3.1')

    def test_tampered_file_rejected(self):
        path = self.archive()
        with zipfile.ZipFile(path) as original:
            items = {name: original.read(name) for name in original.namelist()}
        items['tree/run/example.py'] = b'tampered'
        with zipfile.ZipFile(path, 'w') as archive:
            for name, content in items.items():
                archive.writestr(name, content)
        with self.assertRaises(DeployError):
            parse_archive(path.read_bytes())

    def test_traversal_duplicate_and_symlink_archives_rejected(self):
        for name, mode in (('../escape', 0), ('tree/../escape', 0), ('tree/link', 0o120777 << 16)):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as archive:
                info = zipfile.ZipInfo(name)
                info.external_attr = mode
                archive.writestr(info, b'x')
            with self.subTest(name=name), self.assertRaises(DeployError):
                parse_archive(stream.getvalue())

    def test_case_colliding_archive_rejected(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('tree/Foo', b'a')
            archive.writestr('tree/foo', b'b')
        with self.assertRaises(DeployError):
            parse_archive(stream.getvalue())

    def test_private_data_cannot_be_shipped(self):
        for name in ('kemo.yaml', 'knowledge/share/custom/doc.md'):
            with self.subTest(name=name), self.assertRaises(DeployError):
                self.archive(extra={name: b'private data'})

    def test_local_checksum_mismatch_rejected(self):
        with self.assertRaises(DeployError):
            obtain(self.doc, str(self.archive()), None, '0' * 64)

    def test_url_override_requires_checksum(self):
        with self.assertRaises(DeployError):
            obtain(self.doc, 'https://example.invalid/release.zip', None)

    def test_download_mirror_must_match_official_digest(self):
        data = self.archive().read_bytes()
        meta = {'version': '1.3.0', 'url': 'https://example.invalid/release.zip', 'sha256': digest(data)}
        with mock.patch('core.source.release', return_value=meta), mock.patch('core.source.fetch', side_effect=[b'corrupt', data]) as fetch:
            bundle = obtain(self.doc, None, None)
        self.assertEqual(bundle.version, '1.3.0')
        self.assertEqual(fetch.call_count, 2)

    def test_missing_release_asset_is_actionable(self):
        with mock.patch('core.source.fetch', return_value=json_bytes({'tag_name': 'v1.3.0', 'assets': []})):
            with self.assertRaisesRegex(DeployError, 'publish'):
                release(self.doc, '1.3.0')

    def test_files_only_install_and_update(self):
        path = self.archive()
        args = ['--platform', self.channel, '--install-root', str(self.root), '--source', str(path), '--yes', '--files-only']
        self.assertEqual(self.invoke('install', *args), 0)
        self.assertFalse(sync.installed(self.root)['runtime_ready'])
        new = self.archive('1.3.1', {'run/example.py': b'VALUE = 2\n'})
        args[args.index(str(path))] = str(new)
        self.assertEqual(self.invoke('update', *args), 0)
        self.assertEqual(read_version(self.root), '1.3.1')

    def test_dry_run_does_not_create_root(self):
        self.assertEqual(self.invoke('install', self.channel, '--install-root', str(self.root), '--source', str(self.archive()), '--dry-run'), 0)
        self.assertFalse(self.root.exists())

    def test_dry_run_preserves_existing_bytes_and_mtime(self):
        self.install()
        before = {p.relative_to(self.root): (p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else None) for p in [self.root, *self.root.rglob('*')]}
        self.assertEqual(self.invoke('update', self.channel, '--install-root', str(self.root), '--source', str(self.archive('1.3.1')), '--dry-run'), 0)
        after = {p.relative_to(self.root): (p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else None) for p in [self.root, *self.root.rglob('*')]}
        self.assertEqual(before, after)

    def test_private_data_and_config_survive_upgrade(self):
        old = self.install()
        secrets = {'kemo.yaml': b'LOCAL=1', 'users/u/memory.json': b'local memory', 'expand/global/kemo_app/credential_registry.json': b'local registry', 'expand/global/kemo_graph/module/panel.values.json': b'local panel', 'message/out/q.json': b'queue'}
        for name, data in secrets.items():
            p = target(self.root, name)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        changed = self.bundle('1.3.1', {'config/global_config.yaml': json_bytes({'schema_version': 1, 'local_value': True, 'new': 4})})
        self.install(changed)
        self.assertEqual(yaml.safe_load((self.root / 'config/global_config.yaml').read_bytes()), {'schema_version': 1, 'local_value': False, 'new': 4})
        for name, data in secrets.items():
            self.assertEqual((self.root / name).read_bytes(), data)

    def test_upgrade_releases_previously_managed_shared_knowledge(self):
        old = self.install()
        shared = {
            'knowledge/share/custom/doc.md': b'operator knowledge',
            'knowledge/share/removed-from-release.md': b'keep this document',
        }
        for name, data in shared.items():
            path = target(self.root, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            old['files'][name] = digest(data)
        (self.root / '.kemo-install.json').write_bytes(json_bytes(old))

        incoming = {
            'knowledge/share/custom/doc.md': b'release replacement',
            'knowledge/share/source-only.md': b'not framework content',
        }
        record = self.install(self.bundle('1.3.1', incoming))

        for name, data in shared.items():
            self.assertEqual((self.root / name).read_bytes(), data)
        self.assertFalse((self.root / 'knowledge/share/source-only.md').exists())
        self.assertFalse(any(name.startswith('knowledge/share/') for name in record['files']))

    def test_upgrade_migrates_only_the_legacy_session_idle_default(self):
        for local_value, expected in ((86400, 5400), (7200, 7200)):
            with self.subTest(local_value=local_value):
                if self.root.exists():
                    shutil.rmtree(self.root)
                old_config = {
                    'schema_version': 1,
                    'cron': {'session_idle_close_seconds': 86400},
                }
                self.install(self.bundle('1.3.1', {
                    'config/global_config.yaml': json_bytes(old_config),
                }))
                local_config = yaml.safe_load((self.root / 'config/global_config.yaml').read_bytes())
                local_config['cron']['session_idle_close_seconds'] = local_value
                (self.root / 'config/global_config.yaml').write_bytes(json_bytes(local_config))

                new_config = {
                    'schema_version': 1,
                    'cron': {'session_idle_close_seconds': 5400},
                }
                self.install(self.bundle('1.3.2', {
                    'config/global_config.yaml': json_bytes(new_config),
                }))
                stored = yaml.safe_load((self.root / 'config/global_config.yaml').read_bytes())

                self.assertEqual(stored['cron']['session_idle_close_seconds'], expected)

    def test_obsolete_managed_files_removed_unmanaged_kept(self):
        self.install(self.bundle(extra={'run/obsolete.py': b'old'}))
        (self.root / 'run/local.py').write_bytes(b'custom')
        self.install(self.bundle('1.3.1'))
        self.assertFalse((self.root / 'run/obsolete.py').exists())
        self.assertEqual((self.root / 'run/local.py').read_bytes(), b'custom')

    def test_modified_code_requires_explicit_override(self):
        old = self.install()
        (self.root / 'run/example.py').write_bytes(b'local changes')
        with self.assertRaisesRegex(DeployError, 'Locally modified'):
            sync.plan(self.root, self.bundle('1.3.1'), self.doc, old)
        changes, _ = sync.plan(self.root, self.bundle('1.3.1'), self.doc, old, overwrite=True)
        self.assertTrue(any(x.name == 'run/example.py' for x in changes))

    def test_new_official_path_cannot_overwrite_unmanaged_file(self):
        old = self.install()
        (self.root / 'run/user_note.txt').write_bytes(b'user data')
        changed = self.bundle('1.3.1', {'run/user_note.txt': b'new official data'})
        with self.assertRaisesRegex(DeployError, 'Unmanaged file collides'):
            sync.plan(self.root, changed, self.doc, old, overwrite=True)
        self.assertEqual((self.root / 'run/user_note.txt').read_bytes(), b'user data')

    def test_same_version_skips_preparation_unless_forced(self):
        old = self.install()
        old.update(runtime_ready=True, python=sys.executable)
        (self.root / '.kemo-install.json').write_bytes(json_bytes(old))
        args = ['install', self.channel, '--install-root', str(self.root), '--source', str(self.archive()), '--yes']
        with mock.patch('core.runtime.prepare', return_value=sys.executable) as prepare:
            self.assertEqual(self.invoke(*args), 0)
            prepare.assert_not_called()
            self.assertEqual(self.invoke(*args, '--force'), 0)
            prepare.assert_called_once()

    def test_container_channel_updates_existing_volume_code(self):
        old = self.install()
        old['install_kind'] = 'docker'
        (self.root / '.kemo-install.json').write_bytes(json_bytes(old))
        (self.root / 'kemo.yaml').write_bytes(b'LOCAL=preserved')
        archive = self.archive('1.3.1', {'run/example.py': b'VALUE = 2\n'})
        with mock.patch.dict(os.environ, {'KEMO_DEPLOY_CONTAINER': '1'}), mock.patch('core.runtime.prepare', return_value=sys.executable):
            self.assertEqual(self.invoke('install', 'docker', '--install-root', str(self.root), '--source', str(archive), '--runtime-python', sys.executable, '--yes'), 0)
        self.assertEqual(read_version(self.root), '1.3.1')
        self.assertEqual((self.root / 'kemo.yaml').read_bytes(), b'LOCAL=preserved')

    def test_failure_restores_existing_and_removes_new_files(self):
        old = self.install()
        before = (self.root / 'run/example.py').read_bytes()
        bundle = self.bundle('1.3.1', {'run/new.py': b'new', 'run/example.py': b'new code'})
        def fail(index):
            if index == 2:
                raise OSError('injected interruption')
        with self.assertRaises(OSError):
            self.install(bundle, fail)
        self.assertEqual(sync.installed(self.root), old)
        self.assertEqual((self.root / 'run/example.py').read_bytes(), before)
        self.assertFalse((self.root / 'run/new.py').exists())

    def test_hard_process_exit_can_be_recovered(self):
        self.install()
        script = '''
import os,sys
sys.dont_write_bytecode=True
sys.path.insert(0,sys.argv[1])
from pathlib import Path
from core.sync import Change,commit,installed
root=Path(sys.argv[2]); old=installed(root)
commit(root,[Change('run/example.py',b'broken'),Change('run/new.py',b'new')],old,lambda index: os._exit(77) if index==1 else None)
'''
        result = subprocess.run([sys.executable, '-B', '-c', script, str(DEPLOY), str(self.root)])
        self.assertEqual(result.returncode, 77)
        self.assertEqual(sync.recover(self.root), 1)
        self.assertEqual((self.root / 'run/example.py').read_bytes(), b'VALUE = 1\n')
        self.assertFalse((self.root / 'run/new.py').exists())

    def test_two_successful_backups_retained(self):
        for ver in ('1.3.0', '1.3.1', '1.3.2'):
            self.install(self.bundle(ver))
        self.assertEqual(len(list((self.root / '.kemo-deploy/transactions').iterdir())), 2)

    def test_update_lock_excludes_another_process(self):
        self.root.mkdir()
        script = 'import sys;sys.dont_write_bytecode=True;sys.path.insert(0,sys.argv[1]);from core.locking import Lock;from pathlib import Path;Lock(Path(sys.argv[2])).__enter__()'
        with Lock(self.root / '.update.lock'):
            result = subprocess.run([sys.executable, '-B', '-c', script, str(DEPLOY), str(self.root / '.update.lock')], capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_downgrade_and_cross_channel_takeover_rejected(self):
        self.install(self.bundle('1.3.1'))
        path = self.archive()
        args = ['--install-root', str(self.root), '--source', str(path), '--yes', '--files-only']
        self.assertEqual(self.invoke('update', self.channel, *args), 1)
        self.assertEqual(self.invoke('install', 'npm', *args, '--allow-downgrade'), 1)

    def test_unmanaged_tree_is_never_overwritten(self):
        self.root.mkdir()
        (self.root / 'mine.txt').write_bytes(b'mine')
        self.assertEqual(self.invoke('install', self.channel, '--install-root', str(self.root), '--source', str(self.archive()), '--yes', '--files-only'), 1)
        self.assertEqual(list(self.root.iterdir()), [self.root / 'mine.txt'])

    def test_version_record_drift_rejected(self):
        self.install()
        (self.root / 'version.json').write_bytes(json_bytes({'version': '1.4.0'}))
        self.assertEqual(self.invoke('update', self.channel, '--install-root', str(self.root), '--source', str(self.archive('1.3.1')), '--yes'), 1)

    def test_runtime_external_python_keeps_venv_path(self):
        interpreter = self.base / 'venv/bin/python'
        with mock.patch('core.runtime.probe'):
            self.assertEqual(runtime.prepare(self.root, b'', str(interpreter), False), str(interpreter.absolute()))

    def test_runtime_child_forces_utf8_and_private_caches(self):
        self.root.mkdir()
        env = runtime.child_env(self.root)
        self.assertEqual(env['PYTHONUTF8'], '1')
        self.assertEqual(env['PYTHONIOENCODING'], 'utf-8')
        self.assertTrue(Path(env['TEMP']).is_relative_to(self.root))

    def test_release_critical_files_cannot_be_preserved(self):
        self.doc['mapping']['preserve'].append('version.json')
        with self.assertRaisesRegex(DeployError, 'release-critical'):
            sync.plan(self.root, self.bundle(), self.doc, {})

    def test_non_object_manifest_is_actionable(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('release-manifest.json', b'[]')
        with self.assertRaisesRegex(DeployError, 'release-manifest'):
            parse_archive(stream.getvalue())

    def test_config_equal_key_count_does_not_hide_new_keys(self):
        self.install()
        file = self.root / 'deploy/deploy.yaml'
        local = loads(file.read_text())
        local['deploy'].pop('tag')
        local['deploy']['custom'] = 'keep me'
        local['platforms'][self.channel]['install_root'] = str(self.root)
        file.write_bytes(json_bytes(local))
        self.install(self.bundle('1.3.1'))
        merged = loads(file.read_text())
        self.assertEqual(merged['deploy']['custom'], 'keep me')
        self.assertEqual(merged['deploy']['tag'], 'v{version}')
        self.assertEqual(merged['platforms'][self.channel]['install_root'], str(self.root))

    def test_config_schema_change_stops_before_writes(self):
        old = self.install()
        changed = self.bundle('1.3.1', {'config/global_config.yaml': json_bytes({'schema_version': 2})})
        with self.assertRaisesRegex(DeployError, 'schema changed'):
            sync.plan(self.root, changed, self.doc, old)
        self.assertEqual(read_version(self.root), '1.3.0')

    def test_dependency_failure_leaves_old_version_and_files(self):
        self.install()
        with mock.patch('core.runtime.prepare', side_effect=DeployError('pip unavailable')):
            self.assertEqual(self.invoke('update', self.channel, '--install-root', str(self.root), '--source', str(self.archive('1.3.1')), '--yes'), 1)
        self.assertEqual(read_version(self.root), '1.3.0')
        self.assertEqual(sync.installed(self.root)['version'], '1.3.0')

    def test_pack_excludes_runtime_and_includes_templates(self):
        for name in ('kemo.yaml', 'users/a/data.txt', 'deploy/artifacts/release.zip', 'web/frontend/node_modules/a.js'):
            self.assertFalse(publishable(name), name)
        for name in ('config.json', 'users.json', 'credential_registry.json', '_server.pid'):
            path = f'expand/global/kemo_app/{name}'
            self.assertTrue(protected(path), path)
            self.assertFalse(publishable(path), path)
        self.assertTrue(publishable('template/expand/module/panel.values.json'))
        self.assertTrue(publishable('web/frontend/dist/index.html'))
        self.assertTrue(publishable('provider/assets/deepseek_v4_tokenizer.zip'))

    def test_module_yaml_release_merges_local_runtime_with_new_definition(self):
        name = 'expand/global/demo/module.yaml'
        definition = {
            'kind': 'expand', 'name': 'demo', 'explain': 'module definition with a long description ' * 8,
            'health': '正常', 'recent_update': '2026-11-07 12:00:00',
            'input_open': True, 'input_file': 'data.md', 'control_open': True,
            'control_script': 'control.py', 'control_entry': 'control.md',
            'update_script': 'update.py',
        }
        release = clean_definition(name, yaml.safe_dump(definition, allow_unicode=True).encode('utf-8'))
        packaged = loads(release.decode('utf-8'))
        self.assertEqual(packaged['explain'], definition['explain'])
        self.assertNotIn('recent_update', packaged)
        self.assertEqual(packaged['health'], '异常')
        self.assertFalse(packaged['input_open'])
        self.install(self.bundle(extra={name: release}))
        local = {**packaged, 'input_open': True, 'health': '正常',
                 'recent_update': '2026-11-07 13:00:00'}
        (self.root / name).write_text(dumps(local), encoding='utf-8')
        updated = {**packaged, 'explain': 'updated module',
                   'update_script': 'new_update.py'}
        self.install(self.bundle('1.3.1', extra={
            name: dumps(updated).encode('utf-8'),
        }))
        installed = loads((self.root / name).read_text(encoding='utf-8'))
        self.assertEqual(installed['explain'], 'updated module')
        self.assertEqual(installed['update_script'], 'new_update.py')
        for field in ('input_open', 'health', 'recent_update'):
            self.assertEqual(installed[field], local[field])

    def test_shared_skills_use_the_new_runtime_root(self):
        self.assertTrue(local_module('skills/example'))
        self.assertTrue(protected('skills/example/config.json'))
        self.assertTrue(protected('skills/example/data/state.json'))
        self.assertFalse(protected('skills/example/SKILL.md'))

    def test_all_check_is_read_only_and_reports_shared_roots(self):
        doc = json.loads(json_bytes(self.doc))
        for channel in config.CHANNELS:
            doc['platforms'][channel]['install_root'] = str(self.root)
        path = self.base / 'config.json'
        path.write_bytes(json_bytes(doc))
        self.assertEqual(self.invoke('check', 'all', '--config', str(path), '--source', str(self.archive())), 0)
        self.assertFalse(self.root.exists())

    def test_installed_entrypoint_discovers_its_custom_root(self):
        self.install()
        out = io.StringIO()
        with mock.patch.object(cli, 'DEFAULT_CONFIG', self.root / 'deploy/deploy.yaml'), contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(['check', '--source', str(self.archive())]), 0)
        self.assertIn(str(self.root), out.getvalue())
        self.assertIn('installed=1.3.0', out.getvalue())

    def test_retired_classification_metadata_is_not_publishable(self):
        for name in ('knowledge/global/classify.sqlite3', 'knowledge/share/classify.sqlite3',
                     'knowledge/global/classify.sql', 'users/alice/improve/memory.sqlite3',
                     'runtime/logs.sqlite3', 'knowledge/share/other.sqlite3'):
            self.assertFalse(publishable(name), name)

    def test_shared_knowledge_is_not_publishable(self):
        for name in ('knowledge/share/README.md', 'knowledge/share/custom/doc.md'):
            with self.subTest(name=name):
                self.assertFalse(publishable(name), name)
        self.assertTrue(publishable('knowledge/global/engineering/knowledge-and-user-data.md'))

    def test_shared_directory_seed_is_created_preserved_and_recreated_when_missing(self):
        name = 'knowledge/share/.gitkeep'
        seed = {name: b''}
        self.assertTrue(publishable(name))
        bundle = parse_archive(self.archive(extra=seed).read_bytes(), '1.3.0')
        self.assertIn(name, bundle.files)
        self.install(self.bundle(extra=seed))
        self.assertTrue((self.root / name).is_file())
        self.assertNotIn(name, sync.installed(self.root)['files'])
        (self.root / name).write_bytes(b'local marker')
        (self.root / 'knowledge/share/notes.md').write_bytes(b'local document')
        for extra in (seed, {}):
            changes, files = sync.plan(self.root, self.bundle('1.3.1', extra=extra), self.doc, sync.installed(self.root))
            self.assertFalse(any(c.name.startswith('knowledge/share/') for c in changes))
            self.assertNotIn(name, files)
            self.assertEqual((self.root / name).read_bytes(), b'local marker')
        (self.root / name).unlink()
        changes, _ = sync.plan(self.root, self.bundle('1.3.1', extra=seed), self.doc, sync.installed(self.root))
        self.assertEqual(next(c.content for c in changes if c.name == name), b'')
        self.assertEqual((self.root / 'knowledge/share/notes.md').read_bytes(), b'local document')

    def test_retired_classification_seeds_and_user_plugins_are_rejected_in_releases(self):
        for name, data in (
            ('knowledge/global/classify.sqlite3', b'SQLite format 3\x00seed'),
            ('knowledge/share/classify.sqlite3', b'SQLite format 3\x00seed'),
            ('plugins/custom/plugin.yaml', b'origin: user\n'),
        ):
            with self.subTest(name=name), self.assertRaises(DeployError):
                self.archive(extra={name: data})

    def test_plugin_private_files_are_protected_but_framework_and_helpers_are_publishable(self):
        for name in (
            'config.json', 'config.yaml', 'module/config.yml', '.env.local',
            'module/panel.values.json', 'data/state.json', 'artifacts/result.txt',
        ):
            path = 'plugins/example/' + name
            self.assertTrue(protected(path), path)
            self.assertFalse(publishable(path), path)
        for path in (
            'plugins/manifest.py', 'plugins/_creator_security.py', 'plugins/__init__.py',
            'plugins/kemo_graph/tool.py', 'plugins/example/_helper.py',
            'plugins/example/.env.example', 'plugins/example/plugin.yaml',
        ):
            self.assertTrue(publishable(path), path)

    def test_user_plugin_ownership_protects_updates_deletions_and_packaging(self):
        original = {
            'plugins/example/plugin.yaml': b'origin: builtin\n',
            'plugins/example/tool.py': b'VALUE = 1\n',
            'plugins/removed/plugin.yaml': b'origin: builtin\n',
            'plugins/removed/tool.py': b'VALUE = 1\n',
        }
        old = self.install(self.bundle(extra=original))
        for module in ('example', 'removed', 'custom'):
            directory = self.root / 'plugins' / module
            directory.mkdir(exist_ok=True)
            (directory / 'plugin.yaml').write_bytes(b'origin: user\n')
            (directory / 'tool.py').write_bytes(b'VALUE = "user"\n')
        users = user_plugin_directories(self.root)
        for name in original:
            self.assertFalse(publishable(name, user_plugins=users), name)
        self.assertTrue(publishable('plugins/file/tool.py', user_plugins=users))
        incoming = {
            'plugins/example/plugin.yaml': b'origin: builtin\n',
            'plugins/example/tool.py': b'VALUE = 2\n',
            'plugins/file/plugin.yaml': b'origin: builtin\n',
            'plugins/file/tool.py': b'VALUE = 2\n',
        }
        changes, managed = sync.plan(self.root, self.bundle('1.3.1', extra=incoming), self.doc, old, overwrite=True)
        for module in ('example', 'removed', 'custom'):
            prefix = f'plugins/{module}/'
            self.assertFalse(any(c.name.startswith(prefix) for c in changes))
            self.assertFalse(any(name.startswith(prefix) for name in managed))
        self.assertTrue(any(c.name == 'plugins/file/tool.py' for c in changes))

    def test_invalid_plugin_origin_stops_planning_before_changes(self):
        old = self.install()
        directory = self.root / 'plugins/custom'
        directory.mkdir(parents=True)
        for metadata in (b'origin: [broken', b'origin: unknown\n'):
            (directory / 'plugin.yaml').write_bytes(metadata)
            with self.subTest(metadata=metadata), self.assertRaises(DeployError):
                sync.plan(self.root, self.bundle('1.3.1'), self.doc, old, overwrite=True)
            self.assertEqual(read_version(self.root), '1.3.0')


def read_version(root):
    return json.loads((root / 'version.json').read_bytes())['version']


if __name__ == '__main__':
    unittest.main()
