"""Independent deployment ownership contract. Mandatory protections are additive."""
from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath

PROTECTED = (
    'users/', 'runtime/', 'tmp/', 'kemo.yaml', '.git/', '.venv/', 'venv/',
    '.kemo-deploy/', '.kemo-install.json', '.update.lock', '.update.maintenance',
    '.backups/', 'message/out/', 'knowledge/global/kemo-graph-storage/',
    'config/message_config.json',
)
LOCAL_NAMES = {
    'panel.values.json', 'status.json', 'input_data.md',
    'config.json', 'users.json', 'credential_registry.json',
    'gateway_config.json', 'graph_config.json',
}
# 发布包携带的默认数据文件：缺失时补入，已存在时一律保留本机内容。
# 逐条精确列出，避免把运行期数据库整体放进发布包。
# 接受旧发布包中的分类默认数据；新打包器已停止分发，已有本机文件仍不覆盖。
RELEASE_DATA_FILES = frozenset({
    'knowledge/global/classify.sqlite3',
    'knowledge/share/classify.sqlite3',
})


def preserved_seed(name: str) -> bool:
    """True for default data files shipped in the release but never overwritten."""

    return name in RELEASE_DATA_FILES


def protected(name: str, extra=()) -> bool:
    for item in (*PROTECTED, *extra):
        if name == item.rstrip('/') or (item.endswith('/') and name.startswith(item)):
            return True
    parts = PurePosixPath(name).parts
    base = parts[-1]
    if any(p in {'__pycache__', 'node_modules', '.pytest_cache', '.kemo-deploy'} for p in parts):
        return True
    if base.startswith('.env') and base != '.env.example':
        return True
    if any(fnmatch.fnmatch(base, pattern) for pattern in ('*.sqlite3*', '*.db', '*.lock', '*.pid', '*.pyc', '*.log')):
        return True
    module_files: tuple[str, ...] = ()
    if parts[0] == 'expand' and len(parts) >= 3 and parts[1] in {'global', 'share'}:
        module_files = parts[3:]
    elif parts[0] == 'sense' and len(parts) >= 2:
        module_files = parts[2:]
    elif parts[0] == 'skills' and len(parts) >= 2:
        module_files = parts[1:]
    if module_files:
        if base in LOCAL_NAMES or base.startswith('_') and base not in {'__init__.py', '__main__.py'}:
            return True
        if any(p in {'data', 'artifacts', 'logs'} for p in module_files):
            return True
    return False


def merge_file(name: str) -> bool:
    if name == 'config/global_config.yaml':
        return True
    parts = PurePosixPath(name).parts
    expand_manifest = (
        parts[0] == 'expand'
        and len(parts) >= 4
        and parts[1] in {'global', 'share'}
        and parts[-1] == 'module.yaml'
    )
    sense_manifest = (
        parts[0] == 'sense'
        and len(parts) >= 3
        and parts[-1] == 'module.yaml'
    )
    return (expand_manifest or sense_manifest
            or name.startswith('cron/task_cron_system/') and name.endswith('.json'))


def local_module(name: str) -> bool:
    parts = PurePosixPath(name).parts
    return (
        parts[0] == 'expand' and len(parts) >= 3 and parts[1] in {'global', 'share'}
        or parts[0] == 'sense'
        or parts[0] == 'skills'
    )
