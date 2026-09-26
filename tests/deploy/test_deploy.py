"""Formal unit and fault-path tests for the independent deployment subsystem.

All temporary state is confined to ``deploy/.test-work``; no network or real
installation is used. ``deploy/tests/test_deploy.py`` is only a compatibility
entry point for maintainers who run the deployment directory in isolation.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
sys.path.insert(0, str(DEPLOY))
from core import cli, config, runtime, sync
from core.common import DeployError, digest, json_bytes, target, version_key
from core.locking import Lock
from core.policy import protected
from core.source import Bundle, obtain, parse_archive, release
from core.yamllite import loads
from pack import publishable, write_bundle


def fixture(ver='1.3.0', extra=None):
    files = {
        'version.json': json_bytes({'name': 'kemo-agent', 'version': ver}),
        'start_web.py': b'print("web")\n', 'setup.py': b'print("setup")\n',
        'requirements.txt': b'', 'deploy/deploy.py': b'# deploy\n',
        'deploy/core/cli.py': b'# cli fixture\n', 'deploy/core/common.py': b'# common fixture\n',
        'deploy/deploy.yaml': (DEPLOY / 'deploy.yaml').read_bytes(),
        'web/frontend/dist/index.html': b'<html></html>',
        'config/global_config.json': json_bytes({'schema_version': 1, 'local_value': False}),
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
        with self.assertRaises(DeployError):
            self.archive(extra={'.env': b'SECRET=not-real'})

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
        secrets = {'.env': b'LOCAL=1', 'users/u/memory.json': b'local memory', 'global_expand/kemo_app/credential_registry.json': b'local registry', 'global_expand/kemo_graph/module/panel.values.json': b'local panel', 'message/out/q.json': b'queue'}
        for name, data in secrets.items():
            p = target(self.root, name)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        changed = self.bundle('1.3.1', {'config/global_config.json': json_bytes({'schema_version': 1, 'local_value': True, 'new': 4})})
        self.install(changed)
        self.assertEqual(json.loads((self.root / 'config/global_config.json').read_bytes()), {'schema_version': 1, 'local_value': False, 'new': 4})
        for name, data in secrets.items():
            self.assertEqual((self.root / name).read_bytes(), data)

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
        (self.root / '.env').write_bytes(b'LOCAL=preserved')
        archive = self.archive('1.3.1', {'run/example.py': b'VALUE = 2\n'})
        with mock.patch.dict(os.environ, {'KEMO_DEPLOY_CONTAINER': '1'}), mock.patch('core.runtime.prepare', return_value=sys.executable):
            self.assertEqual(self.invoke('install', 'docker', '--install-root', str(self.root), '--source', str(archive), '--runtime-python', sys.executable, '--yes'), 0)
        self.assertEqual(read_version(self.root), '1.3.1')
        self.assertEqual((self.root / '.env').read_bytes(), b'LOCAL=preserved')

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
        changed = self.bundle('1.3.1', {'config/global_config.json': json_bytes({'schema_version': 2})})
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
        for name in ('.env', 'users/a/data.txt', 'global_expand/kemo_app/users.json', 'deploy/artifacts/release.zip', 'web/frontend/node_modules/a.js'):
            self.assertFalse(publishable(name), name)
        self.assertTrue(publishable('template/expand/module/panel.values.json'))
        self.assertTrue(publishable('web/frontend/dist/index.html'))

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


def read_version(root):
    return json.loads((root / 'version.json').read_bytes())['version']


if __name__ == '__main__':
    unittest.main()
