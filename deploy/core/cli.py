from __future__ import annotations

import argparse
import datetime
import os
import sys
from pathlib import Path

from . import config, runtime, sync
from .common import DeployError, read_json, target, version, version_key
from .locking import Lock
from .source import obtain, release

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / 'deploy.yaml'


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description='Self-contained kemo-agent deployment. The framework version is the only release version.')
    result.add_argument('command', choices=('install', 'update', 'check', 'recover', 'init', 'start'))
    result.add_argument('channel', nargs='?', choices=(*config.CHANNELS, 'all'))
    result.add_argument('--platform', choices=(*config.CHANNELS, 'all'))
    result.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    result.add_argument('--install-root', help='First-install destination; an existing installation is locked to its recorded root')
    result.add_argument('--source', help='Trusted local release ZIP, or HTTPS URL with --sha256')
    result.add_argument('--sha256', help='Expected SHA256 for an explicit source')
    result.add_argument('--version', help='Framework SemVer; default is latest stable GitHub release')
    result.add_argument('--dry-run', action='store_true')
    result.add_argument('--yes', action='store_true')
    result.add_argument('--force', action='store_true', help='Reinstall same version (does not allow downgrading)')
    result.add_argument('--allow-downgrade', action='store_true', help='Explicitly allow a lower version; data schema rollback is not provided')
    result.add_argument('--overwrite-modified', action='store_true', help='Replace locally edited managed code after backup; never replaces protected data')
    result.add_argument('--files-only', action='store_true', help='Prepare code only, without dependencies; installation is NOT runnable until reprovisioned')
    result.add_argument('--runtime-python', help='Use pre-provisioned interpreter (for container image); does not install dependencies')
    return result


def _unavailable(channel):
    if channel == 'windows' and os.name != 'nt' or channel == 'linux' and os.name == 'nt':
        return f'{channel} is not a native channel on this host'
    if channel == 'docker' and os.environ.get('KEMO_DEPLOY_CONTAINER') != '1':
        return 'Docker installation is inspected/updated inside its container, not via a host /data path'
    return None


def _native(channel):
    reason = _unavailable(channel)
    if reason:
        raise DeployError(reason)


def _state(root, channel):
    old = sync.installed(root)
    if old:
        if old.get('schema_version') != 1:
            raise DeployError('Unsupported installation record schema')
        if old.get('install_root') != str(root):
            raise DeployError('Installation was moved or install_root changed; automatic path migration is not supported')
        if old.get('install_kind') != channel:
            raise DeployError(f'This root is owned by {old.get("install_kind")}; refusing cross-channel takeover')
        actual = read_json(target(root, 'version.json')).get('version')
        if actual != old.get('version'):
            raise DeployError('Framework version differs from deploy record; another updater changed this tree. Reconcile before deploying.')
    return old


def _pending(root):
    base = target(root, '.kemo-deploy/transactions')
    if base.is_dir():
        for path in base.glob('*/journal.json'):
            checked = target(root, path.relative_to(root).as_posix())
            if read_json(checked).get('state') == 'applying':
                return True
    return False


def _show(changes):
    for item in changes:
        print(f'{"DELETE" if item.content is None else "WRITE "} {item.name}')
    print(f'{len(changes)} file changes (private runtime data is excluded)')


def _run_one(args, doc, channel, bundle=None, web_args=None, available=None):
    if args.command == 'check' and _unavailable(channel):
        print(f'{channel}: unavailable here; {_unavailable(channel)}')
        return 0
    root = config.install_root(doc, channel, args.install_root)
    if args.command == 'check':
        old = sync.installed(root)
        remote = bundle.version if bundle else available or release(doc, args.version)['version']
        actual = read_json(target(root, 'version.json')).get('version') if (root / 'version.json').is_file() else None
        state = 'not installed' if not old else 'current' if old.get('version') == remote else 'different version'
        if _pending(root):
            state = 'recovery required'
        elif old and actual != old.get('version'):
            state = 'installation record mismatch'
        print(f'{channel}: root={root}; owner={old.get("install_kind", "-")}; installed={actual or "-"}; target={remote}; {state}')
        return 0
    _native(channel)
    if args.command in ('start', 'init', 'recover'):
        if args.dry_run:
            print(f'[dry-run] {args.command} {root}; no files or processes changed')
            return 0
        if not root.is_dir():
            raise DeployError('Installation does not exist')
        with Lock(target(root, '.update.lock')):
            sync.recover(root)
            if args.command == 'recover':
                print('Recovery complete (user databases are not rolled back)')
                return 0
            old = _state(root, channel)
            python = old.get('python')
            if not python:
                raise DeployError('Runtime not prepared; rerun install --force without --files-only')
            runtime.initialize(root, python, args.yes)
            if args.command == 'start':
                return runtime.supervise(root, python, web_args or [])
            return 0
    if args.dry_run:
        if _pending(root):
            raise DeployError('Incomplete transaction; run recover before planning another update')
        old = _state(root, channel)
        _validate_update(args, root, old, bundle)
        changes, _ = sync.plan(root, bundle, doc, old, args.overwrite_modified)
        _show(changes)
        print('[dry-run] No target files, locks, environments or temporary files were written')
        return 0
    if not args.yes:
        try:
            answer = input(f'{args.command} kemo-agent {bundle.version} at {root}? Stop all directly started app processes first. [y/N] ')
        except EOFError:
            raise DeployError('No interactive input; use --yes after stopping the application')
        if answer.strip().lower() not in ('y', 'yes'):
            raise DeployError('Cancelled')
    # Validate non-deploy existing trees before creating any installation metadata.
    if not _pending(root):
        _validate_update(args, root, _state(root, channel), bundle)
    root.mkdir(parents=True, exist_ok=True)
    with Lock(target(root, '.update.lock')):
        sync.recover(root)
        old = _state(root, channel)
        _validate_update(args, root, old, bundle)
        same_interpreter = not args.runtime_python or old.get('python') == str(Path(args.runtime_python).absolute())
        if old.get('version') == bundle.version and not args.force and old.get('runtime_ready') and same_interpreter:
            print(f'Already installed: {bundle.version}')
            return 0
        changes, files = sync.plan(root, bundle, doc, old, args.overwrite_modified)
        _show(changes)
        python = runtime.prepare(root, bundle.files['requirements.txt'], args.runtime_python, args.files_only)
        record = {
            'schema_version': 1, 'install_kind': channel, 'install_root': str(root),
            'version': bundle.version, 'archive_sha256': bundle.checksum,
            'installed_at': old.get('installed_at') or datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'updated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'files': files, 'python': python, 'runtime_ready': python is not None,
        }
        sync.commit(root, changes, record)
        print(f'Installed framework {bundle.version}. ' + ('Run deploy start to initialize configuration/users and launch Web.' if python else 'FILES ONLY: runtime is not prepared.'))
    return 0


def _validate_update(args, root, old, bundle):
    if args.command == 'update' and not old:
        raise DeployError('Not installed; use install first')
    if not old and root.exists():
        existing = [p.name for p in root.iterdir() if p.name not in {'.update.lock', '.kemo-deploy'}]
        if existing:
            raise DeployError('Refusing to overwrite an unmanaged/nonempty directory; choose an empty install_root')
    if old and version_key(old['version']) > version_key(bundle.version) and not args.allow_downgrade:
        raise DeployError('Downgrading requires --allow-downgrade; database rollback is not supported')


def main(argv=None):
    raw = list(sys.argv[1:] if argv is None else argv)
    web_args = []
    if '--' in raw:
        split = raw.index('--')
        raw, web_args = raw[:split], raw[split + 1:]
    args = parser().parse_args(raw)
    try:
        if web_args and args.command != 'start':
            raise DeployError('Arguments after -- are supported only for start')
        if args.channel and args.platform and args.channel != args.platform:
            raise DeployError('Conflicting positional channel and --platform')
        requested_channel = args.platform or args.channel
        local_record = {}
        # A deployed entrypoint should find its own custom installation without
        # requiring the user to repeat --install-root on every invocation.
        own_root = DEFAULT_CONFIG.parent.parent
        if args.config == DEFAULT_CONFIG and requested_channel != 'all' and (own_root / '.kemo-install.json').is_file():
            local_record = sync.installed(own_root)
            if not args.install_root:
                args.install_root = str(own_root)
        channel = requested_channel or local_record.get('install_kind') or ('windows' if os.name == 'nt' else 'linux')
        if channel not in (*config.CHANNELS, 'all'):
            raise DeployError('Invalid installation channel in local record')
        if args.version:
            version(args.version)
        if args.files_only and args.runtime_python:
            raise DeployError('--files-only and --runtime-python are mutually exclusive')
        doc = config.load(args.config)
        if channel == 'all' and (args.command not in ('check', 'update') or args.install_root):
            raise DeployError('all supports check/update of configured roots only; select a channel for other operations')
        bundle = None
        if args.command in ('install', 'update') or args.command == 'check' and args.source:
            bundle = obtain(doc, args.source, args.version, args.sha256)
        if channel != 'all':
            return _run_one(args, doc, channel, bundle, web_args)
        available = release(doc, args.version)['version'] if args.command == 'check' and not bundle else None
        seen = set()
        failures = 0
        for item in config.CHANNELS:
            try:
                if args.command == 'check':
                    _run_one(args, doc, item, bundle, available=available)
                    continue
                root = config.install_root(doc, item)
                if args.command == 'update':
                    old = sync.installed(root)
                    if not old or old.get('install_kind') != item or root in seen:
                        print(f'{item}: skipped (not an owned installed instance)')
                        continue
                seen.add(root)
                _run_one(args, doc, item, bundle, available=available)
            except DeployError as exc:
                print(f'{item}: {exc}', file=sys.stderr)
                failures += 1
        return 1 if failures else 0
    except (DeployError, OSError, ValueError) as exc:
        print(f'Deploy failed: {exc}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Interrupted. Run recover before retrying if the process was forcibly terminated.', file=sys.stderr)
        return 130
