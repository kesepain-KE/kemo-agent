from __future__ import annotations

import copy
import os
from pathlib import Path
from .common import DeployError, no_links, relative
from .yamllite import loads

CHANNELS = ('windows', 'linux', 'npm', 'docker')
BOOTSTRAP_DEFAULTS = {
    'schema_version': 1,
    'deploy': {
        'repo': 'kesepain-KE/kemo-agent', 'asset': 'kemo-agent-release-{version}.zip',
        'tag': 'v{version}', 'mirrors': ['https://ghfast.top/', 'https://gh-proxy.com/'],
    },
    'platforms': {
        'windows': {'install_root': '%USERPROFILE%\\.kemo-agent'},
        'linux': {'install_root': '~/.kemo-agent'},
        'npm': {'install_root': '~/.kemo-agent'},
        'docker': {'install_root': '/data'},
    },
    'mapping': {'tree/': '{install_root}/', 'preserve': ['users/', 'runtime/', 'tmp/', '.env'], 'interactive': ['deploy/deploy.yaml']},
}


def merge_defaults(defaults, local, path=''):
    if isinstance(defaults, dict) and isinstance(local, dict):
        result = copy.deepcopy(local)
        for key, value in defaults.items():
            result[key] = merge_defaults(value, local[key], f'{path}.{key}') if key in local else copy.deepcopy(value)
        return result
    if type(defaults) is not type(local) and defaults is not None and local is not None:
        raise DeployError(f'Configuration type changed at {path}; manual migration required')
    return copy.deepcopy(local)


def load(path: Path) -> dict:
    try:
        doc = loads(path.read_text(encoding='utf-8-sig'))
    except OSError as exc:
        raise DeployError(f'Cannot read config: {path}') from exc
    if doc.get('schema_version', 1) != 1:
        raise DeployError('Unsupported deploy configuration schema')
    # Missing discovery keys must not prevent fetching the release which repairs
    # the persisted configuration. Existing values are NEVER replaced here.
    doc = merge_defaults(BOOTSTRAP_DEFAULTS, doc)
    if not isinstance(doc.get('deploy'), dict) or not isinstance(doc.get('platforms'), dict):
        raise DeployError('deploy and platforms mappings are required')
    mapping = doc.get('mapping', {})
    if mapping.get('tree/') != '{install_root}/':
        raise DeployError('Only tree/ -> {install_root}/ mapping is supported in schema 1')
    if set(mapping) - {'tree/', 'preserve', 'interactive'}:
        raise DeployError('Unknown mapping keys; refusing to silently ignore them')
    for key in ('preserve', 'interactive'):
        values = mapping.get(key, [])
        if not isinstance(values, list) or any(not isinstance(x, str) for x in values):
            raise DeployError(f'mapping.{key} must be a list of paths')
        for value in values:
            relative(value.rstrip('/'))
    if mapping.get('interactive', ['deploy/deploy.yaml']) != ['deploy/deploy.yaml']:
        raise DeployError('Only deploy/deploy.yaml supports interactive defaults merging')
    return doc


def install_root(doc: dict, channel: str, override: str | None = None) -> Path:
    try:
        text = override or doc['platforms'][channel]['install_root']
    except (KeyError, TypeError) as exc:
        raise DeployError(f'Missing install_root for {channel}') from exc
    if not isinstance(text, str) or not text.strip():
        raise DeployError('install_root must be a nonempty string')
    expanded = os.path.expanduser(os.path.expandvars(text))
    if '%' in expanded or '${' in expanded:
        raise DeployError(f'Unresolved environment variable in install_root: {text}')
    path = Path(expanded).absolute()
    no_links(path)
    path = path.resolve()
    if path == Path(path.anchor) or path == Path.home().resolve():
        raise DeployError('Refusing filesystem root or home directory as install_root')
    return path
