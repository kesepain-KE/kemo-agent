"""Shared, immutable configuration for the kemo-agent updater.

The updater is intentionally split into small modules.  This file only holds
paths and stable contracts; it must not perform I/O or import any update board.
"""

from __future__ import annotations

from pathlib import Path


APP_NAME = "kemo-agent"
DEFAULT_REPO_URL = "https://github.com/kesepain-KE/kemo-agent.git"
DEFAULT_BRANCH = "main"
DEFAULT_REPOSITORY_SLUG = "kesepain-KE/kemo-agent"
VERSION_URL_TEMPLATE = (
    "https://raw.githubusercontent.com/kesepain-KE/kemo-agent/{branch}/version.json"
)
BACKUP_KEEP = 2

# The package lives at <root>/update/.  ``parents[1]`` is the project root;
# using ``parent`` here would accidentally make an update target its own
# implementation directory.
ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web" / "frontend"

# Files and directories owned by a running deployment.  They are not part of
# a source release and must not be copied into a rollback snapshot or removed
# while restoring one.
RUNTIME_EXCLUDES = (
    "users/",
    "skills/",
    "runtime/",
    "tmp/",
    "message/out/",
    "cron/task_cron_system/",
    "knowledge/global/kemo-graph-storage/",
    "knowledge/global/classify.sqlite3",
    "knowledge/global/classify.sql",
    "knowledge/share/classify.sqlite3",
    "knowledge/share/classify.sql",
    "web/node_modules/",
    "web/frontend/node_modules/",
    "kemo.yaml",
    "__pycache__/",
    ".module.execution.lock",
    ".update.lock",
    ".update.maintenance",
    # Built-in expands contain credentials, leases, databases and operator
    # activation state.  Their static Python files are still updated by the
    # core board; these local files remain in place during rollback.
    "expand/global/kemo_app/config.json",
    "expand/global/kemo_app/users.json",
    "expand/global/kemo_app/credential_registry.json",
    "expand/global/kemo_app/_connections.json",
    "expand/global/kemo_app/_device_commands.json",
    "expand/global/kemo_app/_device_commands.json.lock",
    "expand/global/kemo_app/_app_runs.sqlite3",
    "expand/global/kemo_app/_app_runs.sqlite3-shm",
    "expand/global/kemo_app/_app_runs.sqlite3-wal",
    "expand/global/kemo_app/_runtime.json",
    "expand/global/kemo_app/_activated.json",
    "expand/global/kemo_app/_server.pid",
    "expand/global/kemo_app/_lifecycle.lock",
    "expand/global/kemo_app/_last_run.json",
    "expand/global/kemo_app/.runtime.lock",
    "expand/global/kemo_app/logs/",
    "expand/global/kemo_app/module.yaml",
    "expand/global/kemo_app/input_data.md",
    "expand/global/kemo_gateway_status/gateway_config.json",
    "expand/global/kemo_gateway_status/_runtime.json",
    "expand/global/kemo_gateway_status/_last_run.json",
    "expand/global/kemo_gateway_status/.runtime.lock",
    "expand/global/kemo_gateway_status/data/",
    "expand/global/kemo_gateway_status/artifacts/",
    "expand/global/kemo_gateway_status/module.yaml",
    "expand/global/kemo_gateway_status/input_data.md",
    "expand/global/kemo_graph/graph_config.json",
    "expand/global/kemo_graph/_runtime.json",
    "expand/global/kemo_graph/_last_run.json",
    "expand/global/kemo_graph/.runtime.lock",
    "expand/global/kemo_graph/data/",
    "expand/global/kemo_graph/artifacts/",
    "expand/global/kemo_graph/module.yaml",
    "expand/global/kemo_graph/input_data.md",
)

# A backup is a restorable source snapshot, not a second user-data store.
# Keep this alias for callers and older tests that used BACKUP_EXCLUDES.
BACKUP_EXCLUDES = RUNTIME_EXCLUDES + (".git/", ".venv/", "venv/", ".backups/")

# Release-managed files inside otherwise runtime-owned directories.
BACKUP_INCLUDED_FILES = ("skills/register.py",)
BACKUP_ABSENT_FILE_MARKERS = {
    "skills/register.py": "skills/.register.py.absent",
}

# Source directories that a normal release may update and therefore must be
# restored after a failed write. User/runtime-owned paths are handled
# separately through RUNTIME_EXCLUDES and board-specific merge rules.
ROLLBACK_MANAGED_PATHS = (
    "run",
    "provider",
    "cron",
    "template",
    "tests",
    "update",
    "message",
    "knowledge/global",
    "knowledge/share",
    "expand/global",
    "expand/share",
    "sense",
    "agents",
    "plugins",
    "web",
    "config",
)

# Source files outside managed directories which updater boards are allowed to
# replace. A failed update must also remove one of these files when it did not
# exist in the pre-update snapshot; otherwise a newly introduced entrypoint can
# remain active beside restored old directories. This is deliberately a
# whitelist so unrelated operator files are never deleted.
ROLLBACK_MANAGED_FILES = (
    "version.json",
    "cli.py",
    "events.py",
    "setup.py",
    "update.py",
    "requirements.txt",
    "requirements-dev.txt",
    "kemo.yaml.example",
    "LICENSE",
    "kemo-agent.ico",
    "kemo-agent.jpg",
    "kemo-web-UI.png",
    "agents.md",
    "restart.py",
    "start_web.py",
    "user_create.py",
    "README.md",
    "readme.md",
    "README_EN.md",
)

MODULES = {
    "core": ("核心引擎", "update.core"),
    "agents": ("智能体系统", "update.agents"),
    "plugins": ("插件生态", "update.plugins"),
    "web": ("Web 服务", "update.web"),
}

REMOTE_UPDATE_PACKAGE = "_kemo_agent_remote_update"
