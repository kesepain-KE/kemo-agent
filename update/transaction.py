"""Single-instance update transaction with automatic source rollback."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ._utils import UpdateError, green, yellow
from .backup import make_backup, prune_backups, restore_backup
from .constants import ROOT
from .lock import UpdateLock


def _write_maintenance_marker(path: Path) -> None:
    payload = {
        "pid": os.getpid(),
        "started_at": time.time(),
        "reason": "framework_update",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


@contextmanager
def update_transaction(
    *,
    root: Path = ROOT,
    dry_run: bool,
) -> Iterator[Path | None]:
    """Protect one update and restore the source tree after any failure.

    User directories, runtime databases, Cron runtime state, local Expand
    credentials and build/dependency caches are excluded from both backup and
    restore.  Database migrations therefore remain forward-only, while source
    code and version metadata are restored automatically.
    """

    if dry_run:
        backup_dir = make_backup(root=root, dry_run=True)
        yield backup_dir
        return

    marker = root / ".update.maintenance"
    with UpdateLock(root=root):
        _write_maintenance_marker(marker)
        backup_dir: Path | None = None
        try:
            backup_dir = make_backup(root=root, dry_run=False)
            yield backup_dir
            try:
                prune_backups(root=root)
            except OSError as exc:
                # Pruning old snapshots is housekeeping, not a reason to roll
                # back a successfully installed release.
                print(yellow(f"旧备份清理跳过: {exc}"))
        except BaseException as original:
            if backup_dir is not None:
                try:
                    print(yellow("更新失败，正在从更新前备份自动恢复源码..."))
                    restore_backup(backup_dir=backup_dir, root=root)
                    print(green("源码已恢复到更新前状态"))
                except Exception as rollback_error:
                    raise UpdateError(
                        "更新失败，且自动恢复也失败。"
                        f"原始错误: {original}; 恢复错误: {rollback_error}; "
                        f"备份位置: {backup_dir}"
                    ) from original
            raise
        finally:
            try:
                marker.unlink(missing_ok=True)
            except OSError:
                pass
