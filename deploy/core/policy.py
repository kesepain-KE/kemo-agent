"""Independent deployment ownership contract. Mandatory protections are additive."""
from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath

PROTECTED = (
    'users/', 'runtime/', 'tmp/', '.env', '.git/', '.venv/', 'venv/',
    '.kemo-deploy/', '.kemo-install.json', '.update.lock', '.update.maintenance',
    '.backups/', 'message/out/', 'global_knowledge/kemo-graph-storage/',
    'config/message_config.json',
)
LOCAL_NAMES = {
    'panel.values.json', 'status.json', 'input_data.md',
    'config.json', 'users.json', 'credential_registry.json',
    'gateway_config.json', 'graph_config.json',
}


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
    if parts[0] in {'global_expand', 'global_sense', 'shared_expand', 'shared_skills'}:
        if base in LOCAL_NAMES or base.startswith('_') and base not in {'__init__.py', '__main__.py'}:
            return True
        if any(p in {'data', 'artifacts', 'logs'} for p in parts[2:]):
            return True
    return False


def merge_file(name: str) -> bool:
    if name == 'config/global_config.json':
        return True
    parts = PurePosixPath(name).parts
    return (parts[0] in {'global_expand', 'global_sense', 'shared_expand'}
            and parts[-1] in {'expand.json', 'sense.json'}) or name.startswith('cron/task_cron_system/') and name.endswith('.json')


def local_module(name: str) -> bool:
    return PurePosixPath(name).parts[0] in {'global_expand', 'global_sense', 'shared_expand', 'shared_skills'}
