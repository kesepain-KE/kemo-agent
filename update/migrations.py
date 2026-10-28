"""Post-source-update user skeleton and SQLite initialization steps."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

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


@dataclass(frozen=True)
class DatabaseBackups:
    root: Path
    directory: Path | None
    dry_run: bool = False


def backup_runtime_databases(*, root: Path = ROOT, dry_run: bool) -> DatabaseBackups:
    """Finish all per-database snapshots before any update initialization.

    This is not a cross-database transaction snapshot. Updates must already
    have stopped application writers. Never prune these recovery artifacts.
    """

    root = root.resolve()
    paths = [
        user / relative
        for user in _user_directories(root)
        for relative in (
            "history/history.sqlite3", "improve/memory.sqlite3",
            "task_plan/task_plans.sqlite3",
        )
        if (user / relative).is_file()
    ]
    log_path = root / "runtime/logs.sqlite3"
    if log_path.is_file():
        paths.append(log_path)
    if dry_run:
        print(f"[dry-run] 将在初始化前使用 SQLite backup API 备份 {len(paths)} 个现有数据库")
        return DatabaseBackups(root, None, dry_run=True)
    if not paths:
        return DatabaseBackups(root, None)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    directory = root / "runtime/db_backups" / f"update-{stamp}-{uuid4().hex[:8]}"
    try:
        _ensure_root_importable(root)
        from run.infra import backup_database

        directory.mkdir(parents=True, mode=0o700)
        entries = []
        for source in paths:
            relative = source.relative_to(root)
            destination = directory / relative
            backup_database(source, destination)
            entries.append({
                "source": relative.as_posix(), "backup": relative.as_posix(),
            })
        (directory / "manifest.json").write_text(
            json.dumps({"status": "complete", "databases": entries}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(green(f"更新前 SQLite 备份已完成：{directory}（{len(entries)} 个库）"))
        return DatabaseBackups(root, directory)
    except Exception as exc:
        raise UpdateError(
            f"更新前 SQLite 备份失败，未开始数据库初始化；已生成文件保留于 {directory}；原因：{exc}"
        ) from exc


def _database_backups(
    root: Path, backups: DatabaseBackups | None
) -> DatabaseBackups:
    if backups is None:
        return backup_runtime_databases(root=root, dry_run=False)
    if backups.root != root.resolve():
        raise UpdateError("数据库备份与初始化的安装根不匹配")
    if backups.dry_run:
        raise UpdateError("dry-run 数据库备份不能用于真实初始化")
    if backups.directory is not None:
        manifest = backups.directory / "manifest.json"
        if not manifest.is_file() or json.loads(manifest.read_text("utf-8")).get("status") != "complete":
            raise UpdateError("数据库备份集不完整，拒绝初始化")
    return backups


def _recovery_hint(backups: DatabaseBackups | None) -> str:
    if backups is None or backups.directory is None:
        return ""
    return (
        f"；更新前数据库备份：{backups.directory}。恢复前停止所有框架进程，"
        "保留现有数据库及 WAL/SHM，按 manifest.json 对应路径使用 SQLite backup API 恢复；"
        "源码自动回滚不等于数据库已经恢复。单库恢复命令示例见 "
        "global_knowledge/storage-and-persistence.md 的“手工恢复单库”"
    )


def migrate_user_skeletons(*, root: Path = ROOT, dry_run: bool) -> None:
    candidates = _user_directories(root)
    if not candidates:
        return
    try:
        _ensure_root_importable(root)
        from run.config import (
            ensure_user_skeleton,
            inspect_user_skeleton,
        )

        if dry_run:
            missing_count = 0
            blocked: list[str] = []
            for user_dir in candidates:
                missing, issues = inspect_user_skeleton(user_dir)
                missing_count += len(missing)
                blocked.extend(f"{user_dir.name}/{item}" for item in issues)
            print(
                f"[dry-run] 将检查 {len(candidates)} 个用户，"
                f"补齐 {missing_count} 项目录骨架"
            )
            if blocked:
                print("[dry-run] 跳过不安全或无效路径：" + ", ".join(blocked))
            return

        created_count = 0
        blocked: list[str] = []
        for user_dir in candidates:
            issues: list[str] = []
            created_count += len(ensure_user_skeleton(user_dir, issues=issues))
            blocked.extend(f"{user_dir.name}/{item}" for item in issues)
        print(
            green(
                f"用户目录骨架已补齐: {len(candidates)} 个用户，"
                f"新增 {created_count} 项目录"
            )
        )
        if blocked:
            print("警告: 跳过不安全或无效路径：" + ", ".join(blocked))
    except Exception as exc:
        raise UpdateError(f"用户目录骨架补齐失败：{exc}") from exc


def initialize_user_memory_databases(
    *, root: Path = ROOT, dry_run: bool, backups: DatabaseBackups | None = None
) -> None:
    """Initialize the SQLite memory store without importing legacy files."""

    candidates = _user_directories(root)
    if not candidates:
        return
    if dry_run:
        print(f"[dry-run] 将初始化 {len(candidates)} 个用户的 SQLite 记忆库")
        return
    try:
        backups = _database_backups(root, backups)
        _ensure_root_importable(root)
        from run.memory import connection

        for path in candidates:
            with connection(root, path.name):
                pass
        print(green(f"用户 SQLite 记忆库已就绪: {len(candidates)} 个用户"))
    except Exception as exc:
        raise UpdateError(f"用户 SQLite 记忆库初始化失败：{exc}{_recovery_hint(backups)}") from exc


def initialize_runtime_state_databases(
    *, root: Path = ROOT, dry_run: bool, backups: DatabaseBackups | None = None
) -> None:
    """Initialize current SQLite stores without scanning obsolete formats."""

    users = _user_directories(root)
    if dry_run:
        print(f"[dry-run] 将初始化 {len(users)} 个用户的运行状态数据库")
        return
    try:
        backups = _database_backups(root, backups)
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
        raise UpdateError(f"运行状态 SQLite 初始化失败：{exc}{_recovery_hint(backups)}") from exc
