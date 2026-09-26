#!/usr/bin/env python3
"""Build release ZIP, npm staging and Docker build context. No publishing."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

sys.dont_write_bytecode = True
from core.common import DeployError, digest, json_bytes, no_links, read_json, relative, version
from core.policy import protected
from core.source import parse_archive

ROOT = Path(__file__).resolve().parent.parent
DIRECTORIES = {'run', 'provider', 'plugins', 'agents', 'config', 'cron', 'message', 'global_knowledge', 'global_expand', 'global_sense', 'shared_expand', 'shared_skills', 'template', 'tests', 'update', 'web', 'deploy'}
ROOT_FILES = {'version.json', 'cli.py', 'events.py', 'setup.py', 'update.py', 'requirements.txt', 'requirements-dev.txt', 'agents.md', '.env.example', 'LICENSE', 'kemo-agent.ico', 'kemo-agent.jpg', 'kemo-web-UI.png', 'readme.md', 'README.md', 'README_EN.md', 'restart.py', 'start_web.py', 'user_create.py'}


def publishable(name: str) -> bool:
    parts = Path(name).parts
    if parts[0] not in DIRECTORIES and name not in ROOT_FILES:
        return False
    if protected(name):
        return False
    if name.startswith(('deploy/artifacts/', 'deploy/out/', 'deploy/.test-work/')) or name == 'deploy/.baseline.json':
        return False
    if any(p in {'node_modules', '__pycache__', '.pytest_cache', '.git', 'output', '.playwright-cli'} for p in parts):
        return False
    if name.endswith(('.tsbuildinfo', '.bak', '.tmp', '.log', '.map')):
        return False
    return True


def clean_definition(name: str, data: bytes) -> bytes:
    # Tracked module manifests may contain local observations; do not ship these.
    if name.startswith(('global_expand/', 'global_sense/')) and name.endswith(('/expand.json', '/sense.json')):
        doc = json.loads(data)
        for key in ('recent_update', 'input_health'):
            doc.pop(key, None)
        if 'open_input' in doc:
            doc['open_input'] = False
        return json_bytes(doc)
    if name.startswith('cron/task_cron_system/') and name.endswith('.json'):
        doc = json.loads(data)
        for key in ('next_run_at', 'latest_run_at', 'status'):
            doc.pop(key, None)
        return json_bytes(doc)
    return data


def write_bundle(files: dict[str, bytes], release_version: str, path: Path) -> None:
    manifest = {'schema_version': 1, 'name': 'kemo-agent', 'version': version(release_version), 'files': {name: digest(content) for name, content in files.items()}}
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('release-manifest.json', json_bytes(manifest))
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo('tree/' + relative(name), date_time=(2020, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o100755 if name.endswith('.sh') or name == 'deploy/deploy' else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    data = path.read_bytes()
    parse_archive(data, release_version)
    path.with_name(path.name + '.sha256').write_text(digest(data) + '  ' + path.name + '\n', encoding='utf-8')


def stage_runtime(destination: Path, files: dict[str, bytes]) -> None:
    for name, data in files.items():
        if name in ('deploy/deploy.py', 'deploy/deploy.yaml') or name.startswith('deploy/core/'):
            path = destination / name.removeprefix('deploy/')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=ROOT)
    parser.add_argument('--out', type=Path, default=ROOT / 'deploy/artifacts')
    parser.add_argument('--include-untracked', action='store_true', help='Explicit development-only inclusion; publish from a committed checkout by default')
    args = parser.parse_args(argv)
    try:
        root = args.repo.resolve()
        release_version = version(read_json(root / 'version.json')['version'])
        command = ['git', '-C', str(root), 'ls-files', '-z', '--cached']
        if args.include_untracked:
            command += ['--others', '--exclude-standard']
        names = set(filter(None, subprocess.check_output(command).decode('utf-8').split('\0')))
        dist = root / 'web/frontend/dist'
        if not (dist / 'index.html').is_file():
            raise DeployError('Build Web first: npm ci && npm run build in web/frontend')
        names.update(p.relative_to(root).as_posix() for p in dist.rglob('*') if p.is_file())
        files = {}
        for name in sorted(names):
            if not publishable(name):
                continue
            path = root / name
            no_links(path)
            if path.is_file():
                files[relative(name)] = clean_definition(name, path.read_bytes())
        # Derived metadata is generated from version.json, never maintained separately.
        package = read_json(root / 'deploy/npm/package.json')
        package.pop('private', None)
        package['version'] = release_version
        files['deploy/npm/package.json'] = json_bytes(package)
        missing = [name for name in ('deploy/deploy.py', 'deploy/core/cli.py') if name not in files]
        if missing:
            raise DeployError('Deployment files are not tracked yet. Commit first, or use --include-untracked for a local test')
        out = args.out.resolve() / release_version
        no_links(out)
        if out.exists():
            raise DeployError(f'Output already exists; choose a new --out directory: {out}')
        out.mkdir(parents=True)
        archive = out / f'kemo-agent-release-{release_version}.zip'
        write_bundle(files, release_version, archive)
        npm = out / 'npm'
        stage_runtime(npm / 'runtime', files)
        (npm / 'bin').mkdir(parents=True)
        (npm / 'package.json').write_bytes(json_bytes(package))
        (npm / 'bin/kemo.cjs').write_bytes((root / 'deploy/npm/bin/kemo.cjs').read_bytes())
        (npm / 'README.md').write_bytes((root / 'deploy/npm/README.md').read_bytes())
        shutil.copyfile(root / 'LICENSE', npm / 'LICENSE')
        shutil.copyfile(archive, npm / 'release.zip')
        docker = out / 'docker'
        stage_runtime(docker / 'runtime', files)
        for name in ('Dockerfile', 'entrypoint.py', 'healthcheck.py', '.dockerignore'):
            shutil.copyfile(root / 'deploy/docker' / name, docker / name)
        shutil.copyfile(archive, docker / 'release.zip')
        # Image build installs requirements from the SAME verified release.
        (docker / 'requirements.txt').write_bytes(files['requirements.txt'])
        print(f'Release {release_version}: {archive}')
        print(f'npm staging: {npm}\nDocker context: {docker}')
        print('No upload/publish performed. Inspect the archive before publishing.')
        return 0
    except (DeployError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f'Packaging failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
