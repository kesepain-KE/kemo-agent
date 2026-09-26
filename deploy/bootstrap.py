#!/usr/bin/env python3
"""Small standalone trusted-origin bootstrap. No application imports required."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

REPO = 'kesepain-KE/kemo-agent'
LIMIT = 512 * 1024 * 1024


def download(url, limit):
    if not url.startswith('https://'):
        raise RuntimeError('HTTPS required')
    req = urllib.request.Request(url, headers={'User-Agent': 'kemo-agent-bootstrap'})
    with urllib.request.urlopen(req, timeout=60) as response:
        if not response.url.startswith('https://'):
            raise RuntimeError('HTTPS redirect required')
        data = response.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError('Download too large')
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--platform', required=True, choices=('windows', 'linux'))
    parser.add_argument('--version')
    parser.add_argument('--install-root')
    parser.add_argument('--yes', action='store_true')
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        raise RuntimeError('Install Python 3.10+ first')
    suffix = 'tags/' + urllib.parse.quote('v' + args.version, safe='') if args.version else 'latest'
    release = json.loads(download(f'https://api.github.com/repos/{REPO}/releases/{suffix}', 4 * 1024 * 1024))
    version = release['tag_name'].removeprefix('v')
    if args.version and version != args.version:
        raise RuntimeError('Release does not match the requested framework version')
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?', version):
        raise RuntimeError('Invalid release version')
    name = f'kemo-agent-release-{version}.zip'
    asset = next((a for a in release['assets'] if a['name'] == name), None)
    if not asset:
        raise RuntimeError(f'Release asset not published: {name}')
    expected = str(asset.get('digest') or '').removeprefix('sha256:')
    if not re.fullmatch('[a-fA-F0-9]{64}', expected):
        checksum = next((a for a in release['assets'] if a['name'] == name + '.sha256'), None)
        if not checksum:
            raise RuntimeError('Release SHA256 sidecar missing')
        expected = download(checksum['browser_download_url'], 4096).decode().split()[0]
    if not re.fullmatch('[a-fA-F0-9]{64}', expected):
        raise RuntimeError('Invalid release SHA256')
    data = None
    errors = []
    for prefix in ('', 'https://ghfast.top/', 'https://gh-proxy.com/'):
        try:
            candidate = download(prefix + asset['browser_download_url'], LIMIT)
            if hashlib.sha256(candidate).hexdigest() != expected.lower():
                raise RuntimeError('SHA256 mismatch')
            data = candidate
            break
        except Exception as exc:
            errors.append(f'{prefix or "GitHub"}: {type(exc).__name__}')
    if data is None:
        raise RuntimeError('All downloads failed: ' + '; '.join(errors))
    # The outer bootstrap is the only component using an OS temporary directory.
    with tempfile.TemporaryDirectory(prefix='kemo-bootstrap-') as temporary:
        temp = Path(temporary)
        archive_path = temp / name
        archive_path.write_bytes(data)
        with zipfile.ZipFile(archive_path) as archive:
            if len(archive.infolist()) > 30000 or sum(i.file_size for i in archive.infolist()) > 768 * 1024 * 1024:
                raise RuntimeError('Archive expansion limit exceeded')
            seen = set()
            for info in archive.infolist():
                name = info.filename.rstrip('/')
                parts = name.split('/')
                if any(p in ('', '.', '..') or ':' in p or '\\' in p or p.endswith((' ', '.')) or any(ord(c) < 32 or c in '<>"|?*' for c in p) or re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', p, re.I) for p in parts):
                    raise RuntimeError('Unsafe archive path')
                if name.casefold() in seen:
                    raise RuntimeError('Duplicate archive path')
                seen.add(name.casefold())
                if stat.S_IFMT(info.external_attr >> 16) not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise RuntimeError('Archive links are not supported')
                if not name.startswith('tree/deploy/') or info.is_dir():
                    continue
                dest = temp.joinpath(*parts)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(archive.read(info))
        command = [sys.executable, str(temp / 'tree/deploy/deploy.py'), 'install', '--platform', args.platform, '--source', str(archive_path), '--sha256', expected, '--version', version]
        if args.install_root:
            command += ['--install-root', args.install_root]
        if args.yes:
            command.append('--yes')
        return subprocess.call(command)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'Bootstrap failed: {exc}', file=sys.stderr)
        raise SystemExit(1)
