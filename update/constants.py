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
    "shared_skills/",
    "runtime/",
    "tmp/",
    "message/out/",
    "cron/task_cron_system/",
    "global_knowledge/kemo-graph-storage/",
    "web/node_modules/",
    "web/frontend/node_modules/",
    ".env",
    "__pycache__/",
    ".module.execution.lock",
    ".update.lock",
    ".update.maintenance",
    # Built-in expands contain credentials, leases, databases and operator
    # activation state.  Their static Python files are still updated by the
    # core board; these local files remain in place during rollback.
    "global_expand/kemo_app/config.json",
    "global_expand/kemo_app/users.json",
    "global_expand/kemo_app/credential_registry.json",
    "global_expand/kemo_app/_connections.json",
    "global_expand/kemo_app/_device_commands.json",
    "global_expand/kemo_app/_device_commands.json.lock",
    "global_expand/kemo_app/_app_runs.sqlite3",
    "global_expand/kemo_app/_app_runs.sqlite3-shm",
    "global_expand/kemo_app/_app_runs.sqlite3-wal",
    "global_expand/kemo_app/_runtime.json",
    "global_expand/kemo_app/_activated.json",
    "global_expand/kemo_app/_server.pid",
    "global_expand/kemo_app/_lifecycle.lock",
    "global_expand/kemo_app/_last_run.json",
    "global_expand/kemo_app/.runtime.lock",
    "global_expand/kemo_app/logs/",
    "global_expand/kemo_app/expand.json",
    "global_expand/kemo_app/input_data.md",
    "global_expand/kemo_gateway_status/gateway_config.json",
    "global_expand/kemo_gateway_status/_runtime.json",
    "global_expand/kemo_gateway_status/_last_run.json",
    "global_expand/kemo_gateway_status/.runtime.lock",
    "global_expand/kemo_gateway_status/data/",
    "global_expand/kemo_gateway_status/artifacts/",
    "global_expand/kemo_gateway_status/expand.json",
    "global_expand/kemo_gateway_status/input_data.md",
    "global_expand/kemo_graph/graph_config.json",
    "global_expand/kemo_graph/_runtime.json",
    "global_expand/kemo_graph/_last_run.json",
    "global_expand/kemo_graph/.runtime.lock",
    "global_expand/kemo_graph/data/",
    "global_expand/kemo_graph/artifacts/",
    "global_expand/kemo_graph/expand.json",
    "global_expand/kemo_graph/input_data.md",
)

# A backup is a restorable source snapshot, not a second user-data store.
# Keep this alias for callers and older tests that used BACKUP_EXCLUDES.
BACKUP_EXCLUDES = RUNTIME_EXCLUDES + (".git/", ".venv/", "venv/", ".backups/")

# Existing source directories that a normal release may update and therefore
# must be restored after a failed write.  User/runtime-owned paths are handled
# separately through RUNTIME_EXCLUDES and board-specific merge rules.
ROLLBACK_MANAGED_PATHS = (
    "run",
    "provider",
    "cron",
    "template",
    "tests",
    "update",
    "message",
    "global_knowledge",
    "global_expand",
    "global_sense",
    "shared_expand",
    "shared_skills",
    "agents",
    "plugins",
    "web",
    "config",
)

# Root-level source files which updater boards are allowed to replace.  A
# failed update must also remove one of these files when it did not exist in
# the pre-update snapshot; otherwise a newly introduced entrypoint can remain
# active beside restored old directories.  This is deliberately a whitelist
# so unrelated operator files at the repository root are never deleted.
ROLLBACK_MANAGED_FILES = (
    "version.json",
    "cli.py",
    "events.py",
    "setup.py",
    "update.py",
    "requirements.txt",
    "requirements-dev.txt",
    ".env.example",
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
