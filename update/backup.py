"""Backup and restore primitives for updater transactions."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from ._utils import green, sync_directory, yellow
from .constants import (
    BACKUP_EXCLUDES,
    BACKUP_KEEP,
    ROLLBACK_MANAGED_FILES,
    ROLLBACK_MANAGED_PATHS,
    ROOT,
)


def make_backup(*, root: Path = ROOT, dry_run: bool = False) -> Path | None:
    timestamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1_000_000:06d}"
    backup_dir = root / ".backups" / f"update-{timestamp}"
    if dry_run:
        print(f"[dry-run] 将创建备份: {backup_dir}")
        return None
    backup_dir.mkdir(parents=True, exist_ok=False)
    try:
        sync_directory(root, backup_dir, delete=False, excludes=BACKUP_EXCLUDES)
    except Exception:
        # An incomplete backup must never be presented as recoverable.
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise
    print(green(f"备份已创建: {backup_dir}"))
    return backup_dir


def prune_backups(*, root: Path = ROOT) -> None:
    backups_root = root / ".backups"
    if not backups_root.is_dir():
        return
    backups = sorted(
        [
            path
            for path in backups_root.iterdir()
            if path.is_dir() and path.name.startswith("update-")
        ],
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    for old in backups[BACKUP_KEEP:]:
        shutil.rmtree(old, ignore_errors=True)
        print(yellow(f"已移除旧备份: {old}"))


def restore_backup(*, backup_dir: Path, root: Path = ROOT) -> None:
    """Restore source files while preserving deployment-owned runtime data."""

    if not backup_dir.is_dir():
        raise FileNotFoundError(f"备份不存在: {backup_dir}")
    # First restore every file present in the snapshot.  Deletion is restricted
    # to known release-managed directories so an unrelated local root path can
    # never be removed merely because backups intentionally exclude it.
    sync_directory(
        backup_dir,
        root,
        delete=False,
        excludes=BACKUP_EXCLUDES,
    )
    for relative in ROLLBACK_MANAGED_FILES:
        source = backup_dir / relative
        target = root / relative
        if source.exists() or not target.is_file():
            continue
        target.unlink()
    for relative in ROLLBACK_MANAGED_PATHS:
        source = backup_dir / relative
        target = root / relative
        if not source.is_dir() or not target.is_dir():
            continue
        sync_directory(
            source,
            target,
            delete=True,
            excludes=BACKUP_EXCLUDES,
        )
