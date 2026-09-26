"""Release discovery, verified downloads and bounded archive parsing."""
from __future__ import annotations

import io
import json
import re
import stat
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .common import DeployError, digest, relative, version
from .policy import protected

MAX_ARCHIVE = 512 * 1024 * 1024
MAX_EXPANDED = 768 * 1024 * 1024
MAX_FILE = 128 * 1024 * 1024
MAX_FILES = 30000


def fetch(url: str, limit=MAX_ARCHIVE) -> bytes:
    if not url.startswith('https://'):
        raise DeployError('Downloads require HTTPS')
    request = urllib.request.Request(url, headers={'User-Agent': 'kemo-agent-deploy', 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=45) as response:
        if not response.url.startswith('https://'):
            raise DeployError('Refusing redirect to non-HTTPS URL')
        data = response.read(limit + 1)
    if len(data) > limit:
        raise DeployError(f'Download exceeds {limit} bytes')
    return data


def release(config: dict, requested: str | None) -> dict:
    repo = config['deploy'].get('repo', '')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise DeployError('Invalid GitHub owner/repository')
    suffix = 'latest'
    if requested:
        version(requested)
        tag = config['deploy'].get('tag', 'v{version}').format(version=requested)
        suffix = 'tags/' + urllib.parse.quote(tag, safe='')
    try:
        doc = json.loads(fetch(f'https://api.github.com/repos/{repo}/releases/{suffix}', 4 * 1024 * 1024))
        actual = version(str(doc['tag_name']).removeprefix('v'))
        if requested and actual != requested:
            raise DeployError('Release tag does not match requested framework version')
        name = config['deploy']['asset'].format(version=actual)
        asset = next((x for x in doc['assets'] if x['name'] == name), None)
        if not asset:
            raise DeployError(f'Release {actual} has no asset {name}; publish the release package first')
        expected = str(asset.get('digest') or '').removeprefix('sha256:')
        if not re.fullmatch('[a-fA-F0-9]{64}', expected):
            # Obtain the trust anchor from the official origin, never a mirror.
            checksum = next((x for x in doc['assets'] if x['name'] == name + '.sha256'), None)
            if not checksum:
                raise DeployError(f'Release requires SHA256 digest or {name}.sha256')
            expected = fetch(checksum['browser_download_url'], 4096).decode().split()[0]
        if not re.fullmatch('[a-fA-F0-9]{64}', expected):
            raise DeployError('Malformed release checksum')
        return {'version': actual, 'url': asset['browser_download_url'], 'sha256': expected.lower()}
    except urllib.error.HTTPError as exc:
        raise DeployError(f'Release lookup failed (HTTP {exc.code}); package may not be published yet') from exc
    except (KeyError, ValueError, StopIteration) as exc:
        raise DeployError(f'Malformed release metadata: {exc}') from exc


@dataclass
class Bundle:
    version: str
    files: dict[str, bytes]
    modes: dict[str, int]
    checksum: str


def parse_archive(data: bytes, expected_version: str | None = None) -> Bundle:
    if len(data) > MAX_ARCHIVE:
        raise DeployError('Archive is too large')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_FILES:
                raise DeployError('Archive contains too many entries')
            seen = set()
            total = 0
            files = {}
            modes = {}
            manifest_data = None
            for info in infos:
                name = relative(info.filename.rstrip('/'))
                folded = name.casefold()
                if folded in seen:
                    raise DeployError(f'Duplicate/case-colliding archive path: {name}')
                seen.add(folded)
                filetype = stat.S_IFMT(info.external_attr >> 16)
                if filetype not in (0, stat.S_IFREG, stat.S_IFDIR) or info.flag_bits & 1:
                    raise DeployError(f'Link, special or encrypted entry: {name}')
                if info.is_dir():
                    continue
                total += info.file_size
                if total > MAX_EXPANDED or info.file_size > MAX_FILE:
                    raise DeployError('Archive expansion limit exceeded')
                if name != 'release-manifest.json' and not name.startswith('tree/'):
                    raise DeployError(f'Unexpected release entry: {name}')
                content = archive.read(info)
                if name == 'release-manifest.json':
                    manifest_data = content
                else:
                    rel = relative(name[5:])
                    if protected(rel):
                        raise DeployError(f'Release contains private/runtime path: {rel}')
                    files[rel] = content
                    modes[rel] = 0o755 if info.external_attr >> 16 & 0o111 else 0o644
            manifest = json.loads(manifest_data or b'{}')
            if not isinstance(manifest, dict) or manifest.get('schema_version') != 1 or manifest.get('name') != 'kemo-agent':
                raise DeployError('Missing or unsupported release-manifest.json')
            actual = version(manifest['version'])
            if manifest.get('files') != {name: digest(content) for name, content in files.items()}:
                raise DeployError('Release manifest file set or SHA256 mismatch')
            root_doc = json.loads(files.get('version.json', b'{}'))
            if not isinstance(root_doc, dict) or root_doc.get('version') != actual or expected_version and actual != expected_version:
                raise DeployError('Package filename/request/manifest/framework version mismatch')
            for required in ('start_web.py', 'setup.py', 'requirements.txt', 'deploy/deploy.py', 'deploy/deploy.yaml', 'deploy/core/cli.py', 'deploy/core/common.py', 'web/frontend/dist/index.html'):
                if required not in files:
                    raise DeployError(f'Release missing required file: {required}')
            # Reject a file which is also used as another file's parent.
            keys = {name.casefold() for name in files}
            for name in keys:
                if any(str(parent) in keys for parent in PurePosixPath(name).parents if str(parent) != '.'):
                    raise DeployError(f'Archive file/directory collision: {name}')
            for name, content in files.items():
                if name.endswith('.py'):
                    try:
                        compile(content, name, 'exec')
                    except (SyntaxError, ValueError) as exc:
                        raise DeployError(f'Python source is incompatible with this interpreter: {name}: {exc}') from exc
            return Bundle(actual, files, modes, digest(data))
    except (zipfile.BadZipFile, KeyError, ValueError, RuntimeError) as exc:
        if isinstance(exc, DeployError):
            raise
        raise DeployError(f'Invalid release archive: {exc}') from exc


def obtain(config: dict, source: str | None, requested: str | None, sha256: str | None = None) -> Bundle:
    if source and not source.startswith('https://'):
        path = Path(source).expanduser()
        if not path.is_file() or path.stat().st_size > MAX_ARCHIVE:
            raise DeployError('Local source must be an existing bounded ZIP file')
        data = path.read_bytes()
        if sha256 and digest(data) != sha256.lower():
            raise DeployError('Local archive SHA256 mismatch')
        return parse_archive(data, requested)
    if source:
        if not sha256 or not re.fullmatch('[a-fA-F0-9]{64}', sha256):
            raise DeployError('--source HTTPS overrides require --sha256')
        metadata = {'url': source, 'sha256': sha256.lower(), 'version': requested}
    else:
        metadata = release(config, requested)
    urls = [metadata['url']]
    for mirror in config['deploy'].get('mirrors', []):
        if not isinstance(mirror, str) or not mirror.startswith('https://'):
            raise DeployError('Mirror prefixes must use HTTPS')
        urls.append(mirror.rstrip('/') + '/' + metadata['url'])
    failures = []
    for url in urls:
        try:
            data = fetch(url)
            if digest(data) != metadata['sha256']:
                raise DeployError('SHA256 mismatch')
            return parse_archive(data, metadata['version'])
        except (OSError, DeployError) as exc:
            # Avoid echoing private query strings from source URLs.
            host = urllib.parse.urlsplit(url).hostname
            failures.append(f'{host}: {type(exc).__name__}: {exc}' if isinstance(exc, DeployError) else f'{host}: {type(exc).__name__}')
    raise DeployError('All download sources failed:\n' + '\n'.join(failures))
