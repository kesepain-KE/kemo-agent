"""Post-source-update user skeleton and SQLite initialization steps."""

from __future__ import annotations

import sys
from pathlib import Path

from ._utils import UpdateError, green
from .constants import ROOT


def _user_directories(root: Path) -> list[Path]:
    users_dir = root / "users"
    if not users_dir.is_dir():
        return []
    return [
        path
        for path in sorted(users_dir.iterdir())
        if path.is_dir()
        and not path.name.startswith("_")
        and (path / "user_config.json").is_file()
    ]


def _ensure_root_importable(root: Path) -> None:
    value = str(root)
    if value not in sys.path:
        sys.path.insert(0, value)


def migrate_user_skeletons(*, root: Path = ROOT, dry_run: bool) -> None:
    candidates = _user_directories(root)
    if not candidates:
        return
    if dry_run:
        print(f"[dry-run] 将补齐 {len(candidates)} 个用户的目录骨架")
        return
    try:
        _ensure_root_importable(root)
        from run.config import ensure_user

        for user_dir in candidates:
            ensure_user(user_dir.name, root)
        print(green(f"用户目录骨架已补齐: {len(candidates)} 个用户"))
    except Exception as exc:
        raise UpdateError(f"用户目录骨架补齐失败：{exc}") from exc


def initialize_user_memory_databases(*, root: Path = ROOT, dry_run: bool) -> None:
    """Initialize the SQLite memory store without importing legacy files."""

    candidates = _user_directories(root)
    if not candidates:
        return
    if dry_run:
        print(f"[dry-run] 将初始化 {len(candidates)} 个用户的 SQLite 记忆库")
        return
    try:
        _ensure_root_importable(root)
        from run.memory import connection

        for path in candidates:
            with connection(root, path.name):
                pass
        print(green(f"用户 SQLite 记忆库已就绪: {len(candidates)} 个用户"))
    except Exception as exc:
        raise UpdateError(f"用户 SQLite 记忆库初始化失败：{exc}") from exc


def initialize_runtime_state_databases(*, root: Path = ROOT, dry_run: bool) -> None:
    """Initialize current SQLite stores without scanning obsolete formats."""

    users = _user_directories(root)
    if dry_run:
        print(f"[dry-run] 将初始化 {len(users)} 个用户的运行状态数据库")
        return
    try:
        _ensure_root_importable(root)
        from message.state import ProcessedMessageStore
        from run.history import connection
        from run.infra import LogStore
        from run.tasks import PlanStore

        for user_path in users:
            with connection(root, user_path.name):
                pass
            ProcessedMessageStore(root, user_path.name).recover_interrupted()
            PlanStore(root, user_path.name).list_plans()
        LogStore(root).list_cron("__system__", limit=1)
        print(green("运行状态 SQLite 数据库已就绪"))
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError(f"运行状态 SQLite 初始化失败：{exc}") from exc
