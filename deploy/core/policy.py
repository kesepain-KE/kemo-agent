"""Independent deployment ownership contract. Mandatory protections are additive."""
from __future__ import annotations

import fnmatch
from pathlib import Path, PurePosixPath

from .common import DeployError, target
from .yamllite import loads

PROTECTED = (
    'users/', 'runtime/', 'tmp/', 'kemo.yaml', '.git/', '.venv/', 'venv/',
    '.kemo-deploy/', '.kemo-install.json', '.update.lock', '.update.maintenance',
    '.backups/', 'message/out/', 'knowledge/global/kemo-graph-storage/',
    'knowledge/share/',
    'config/message_config.json',
    'config/module_ui_grants.json',
)
LOCAL_NAMES = {
    'panel.values.json', 'status.json', 'input_data.md',
    'config.json', 'users.json', 'credential_registry.json',
    'gateway_config.json', 'graph_config.json',
}
# 发布包携带的默认数据文件：缺失时补入，已存在时一律保留本机内容。
# 逐条精确列出，避免把运行期数据库整体放进发布包。
RELEASE_DATA_FILES = frozenset({
    'knowledge/share/.gitkeep',
})


def plugin_origin(data: bytes, name: str) -> str:
    """Read ownership without importing the application or plugin code."""
    try:
        origin = loads(data.decode('utf-8-sig')).get('origin', 'builtin')
    except (UnicodeError, DeployError) as exc:
        raise DeployError(f'Invalid plugin metadata: {name}: {exc}') from exc
    if origin not in ('builtin', 'user'):
        raise DeployError(f'Plugin origin must be builtin or user: {name}')
    return origin


def user_plugin_directories(root: Path) -> tuple[str, ...]:
    base = target(root, 'plugins')
    if not base.is_dir():
        return ()
    result = []
    for directory in sorted(base.iterdir()):
        if not directory.is_dir() or directory.name == '__pycache__':
            continue
        name = directory.relative_to(root).as_posix() + '/plugin.yaml'
        metadata = target(root, name)
        if metadata.is_file() and plugin_origin(metadata.read_bytes(), name) == 'user':
            result.append(directory.relative_to(root).as_posix() + '/')
    return tuple(result)


def preserved_seed(name: str) -> bool:
    """True for default data files shipped in the release but never overwritten."""

    return name in RELEASE_DATA_FILES


def protected(name: str, extra=()) -> bool:
    for item in (*PROTECTED, *extra):
        if name == item.rstrip('/') or (item.endswith('/') and name.startswith(item)):
            return True
    parts: tuple[str, ...] = PurePosixPath(name).parts
    base = parts[-1]
    is_plugin = parts[0] == 'plugins'
    if any(p in {'__pycache__', 'node_modules', '.pytest_cache', '.kemo-deploy'} for p in parts):
        return True
    if base.startswith('.env') and base != '.env.example':
        return True
    if any(fnmatch.fnmatch(base, pattern) for pattern in ('*.sqlite3*', '*.db', '*.lock', '*.pid', '*.pyc', '*.log')):
        return True
    module_files: tuple[str, ...] = ()
    if len(parts) >= 3 and parts[0] == 'expand' and parts[1] in {'global', 'share'}:
        module_files = parts[3:]
    elif len(parts) >= 2 and parts[0] == 'sense':
        module_files = parts[2:]
    elif len(parts) >= 2 and parts[0] == 'skills':
        module_files = parts[1:]
    elif len(parts) >= 3 and is_plugin:
        module_files = parts[2:]
    if module_files:
        if (base in LOCAL_NAMES
                or is_plugin and base in {'config.yaml', 'config.yml'}
                or not is_plugin and base.startswith('_') and base not in {'__init__.py', '__main__.py'}):
            return True
        if any(p in {'data', 'artifacts', 'logs'} for p in module_files):
            return True
    return False


def merge_file(name: str) -> bool:
    if name == 'config/global_config.yaml':
        return True
    parts: tuple[str, ...] = PurePosixPath(name).parts
    expand_manifest = (
        len(parts) >= 4
        and parts[0] == 'expand'
        and parts[1] in {'global', 'share'}
        and parts[-1] == 'module.yaml'
    )
    sense_manifest = (
        len(parts) >= 3
        and parts[0] == 'sense'
        and parts[-1] == 'module.yaml'
    )
    return (expand_manifest or sense_manifest
            or name.startswith('cron/task_cron_system/') and name.endswith('.json'))


def local_module(name: str) -> bool:
    parts: tuple[str, ...] = PurePosixPath(name).parts
    return (
        len(parts) >= 3 and parts[0] == 'expand' and parts[1] in {'global', 'share'}
        or parts[:1] in {('sense',), ('skills',)}
        or len(parts) >= 3 and parts[0] == 'plugins'
    )
