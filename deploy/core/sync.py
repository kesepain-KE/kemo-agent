"""Pure planning plus durable, recoverable file replacement transactions."""
from __future__ import annotations

import json
import os
import shutil
import stat
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .common import DeployError, atomic, digest, json_bytes, read_json, target
from .config import merge_defaults
from .policy import local_module, merge_file, protected
from .source import Bundle
from .yamllite import loads


@dataclass
class Change:
    name: str
    content: bytes | None
    mode: int = 0o644


def installed(root: Path) -> dict:
    path = target(root, '.kemo-install.json')
    return read_json(path) if path.exists() else {}


def plan(root: Path, bundle: Bundle, config: dict, old: dict, overwrite=False) -> tuple[list[Change], dict]:
    changes = []
    managed = {}
    previous = old.get('files', {})
    extras = config['mapping'].get('preserve', [])
    for required in ('version.json', 'requirements.txt', 'start_web.py', 'setup.py', 'deploy/deploy.py', 'deploy/core/cli.py', 'web/frontend/dist/index.html'):
        if protected(required, extras):
            raise DeployError(f'preserve cannot disable release-critical file: {required}')
    for name, data in bundle.files.items():
        if protected(name, extras):
            continue
        path = target(root, name)
        if path.exists() and not path.is_file():
            raise DeployError(f'File/directory collision at {name}')
        before = path.read_bytes() if path.exists() else None
        if before is not None:
            if name == 'deploy/deploy.yaml':
                defaults, local = loads(data.decode()), loads(before.decode())
                if defaults.get('schema_version') != local.get('schema_version'):
                    raise DeployError('Deploy configuration schema changed; manual migration required')
                merged = merge_defaults(defaults, local)
                data = before if merged == local else json_bytes(merged)
            elif merge_file(name):
                try:
                    defaults, local = json.loads(data), json.loads(before)
                    if not isinstance(defaults, dict) or not isinstance(local, dict):
                        raise DeployError(f'Expected object in {name}')
                    if defaults.get('schema_version') != local.get('schema_version'):
                        raise DeployError(f'Config schema changed: {name}; manual migration required')
                    merged = merge_defaults(defaults, local)
                    data = before if merged == local else json_bytes(merged)
                except ValueError as exc:
                    raise DeployError(f'Cannot merge invalid JSON: {name}') from exc
            elif name not in previous and local_module(name):
                print(f'KEEP locally owned module file: {name}')
                continue
            elif name not in previous and before != data:
                raise DeployError(f'Unmanaged file collides with new release: {name}; move or reconcile it before updating')
            elif name in previous and digest(before) != previous[name] and not overwrite:
                raise DeployError(f'Locally modified managed file: {name}; review, then use --overwrite-modified if intended')
        managed[name] = digest(data)
        if before != data:
            changes.append(Change(name, data, bundle.modes[name]))
    for name, old_hash in previous.items():
        if name in bundle.files or protected(name, extras):
            continue
        path = target(root, name)
        if path.is_file():
            if digest(path.read_bytes()) != old_hash:
                print(f'KEEP modified obsolete file (ownership released): {name}')
                continue
            changes.append(Change(name, None))
    return changes, managed


def _restore(root: Path, directory: Path, journal: dict) -> None:
    for item in reversed(journal['entries']):
        destination = target(root, item['name'])
        if item['existed']:
            backup = target(directory, 'backup/' + item['name'])
            content = backup.read_bytes()
            if digest(content) != item['sha256']:
                raise DeployError(f'Corrupt rollback backup: {item["name"]}')
            atomic(destination, content, item['mode'])
        elif destination.exists():
            if not destination.is_file():
                raise DeployError(f'Refusing to remove non-file during rollback: {destination}')
            destination.unlink()
    for name in sorted(journal.get('created_dirs', []), key=lambda x: len(x.split('/')), reverse=True):
        path = target(root, name)
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    journal['state'] = 'rolled_back'
    atomic(directory / 'journal.json', json_bytes(journal))


def recover(root: Path) -> int:
    count = 0
    base = target(root, '.kemo-deploy/transactions')
    if not base.exists():
        return count
    for directory in sorted(base.iterdir()):
        journal_path = target(root, str((directory / 'journal.json').relative_to(root)).replace('\\', '/'))
        if not journal_path.is_file():
            continue  # only backup preparation happened; no target files touched
        journal = read_json(journal_path)
        if journal.get('state') == 'applying':
            _restore(root, directory, journal)
            print(f'Recovered incomplete transaction: {directory.name}')
            count += 1
    return count


def commit(root: Path, changes: list[Change], record: dict, after_write=None) -> None:
    changes = [*changes, Change('.kemo-install.json', json_bytes(record), 0o600)]
    directory = target(root, '.kemo-deploy/transactions/' + f'{time.time_ns()}-{uuid.uuid4().hex[:8]}')
    directory.mkdir(parents=True, mode=0o700)
    entries = []
    created_dirs = set()
    for change in changes:
        path = target(root, change.name)
        if path.exists() and not path.is_file():
            raise DeployError(f'Not a regular target file: {change.name}')
        for parent in path.parents:
            if parent == root:
                break
            if not parent.exists():
                created_dirs.add(parent.relative_to(root).as_posix())
        exists = path.is_file()
        data = path.read_bytes() if exists else b''
        mode = stat.S_IMODE(path.stat().st_mode) if exists else change.mode
        entries.append({'name': change.name, 'existed': exists, 'mode': mode, 'sha256': digest(data)})
        if exists:
            atomic(target(directory, 'backup/' + change.name), data, mode)
    journal = {'schema_version': 1, 'state': 'applying', 'entries': entries, 'created_dirs': sorted(created_dirs)}
    atomic(directory / 'journal.json', json_bytes(journal))
    try:
        for index, change in enumerate(changes):
            path = target(root, change.name)
            if change.content is None:
                path.unlink(missing_ok=True)
            else:
                atomic(path, change.content, change.mode)
            if after_write:
                after_write(index)
        journal['state'] = 'committed'
        atomic(directory / 'journal.json', json_bytes(journal))
    except BaseException as original:
        try:
            _restore(root, directory, journal)
        except Exception as rollback:
            raise DeployError(f'Update failed: {original}; ROLLBACK FAILED: {rollback}; backups: {directory}') from original
        raise
    try:
        _prune(root, directory.parent)
    except (OSError, DeployError) as exc:
        # Cleanup cannot turn a committed successful install into a failure.
        print(f'Backup cleanup deferred: {exc}')


def _prune(root: Path, transactions: Path) -> None:
    # Never prune failed/incomplete transactions. Retain two successful snapshots.
    completed = []
    for item in transactions.iterdir():
        checked = target(root, item.relative_to(root).as_posix())
        journal_path = checked / 'journal.json'
        if journal_path.is_file() and read_json(journal_path).get('state') == 'committed':
            completed.append(checked)
    for item in sorted(completed)[:-2]:
        # All descendants were created by this updater; do not follow links.
        from .common import no_links
        try:
            for child in item.rglob('*'):
                no_links(child)
            shutil.rmtree(item)
        except (OSError, DeployError) as exc:
            print(f'Backup cleanup deferred: {exc}')
