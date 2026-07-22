"""Executable-plugin ecosystem update board."""

from __future__ import annotations

from pathlib import Path

from ._utils import paths_differ, sync_directory


MODULE_NAME = "plugins"


def update(
    source_root: Path,
    target_root: Path,
    *,
    dry_run: bool = False,
    assume_yes: bool = False,
) -> dict:
    """Replace plugins/ exactly, including deletion of removed plugins."""
    del assume_yes
    source = source_root / "plugins"
    target = target_root / "plugins"
    if not source.is_dir():
        return {
            "module": MODULE_NAME,
            "status": "failed",
            "details": [],
            "warnings": ["源缺少目录: plugins/"],
        }
    if target.is_dir() and not paths_differ(source, target):
        return {
            "module": MODULE_NAME,
            "status": "skipped",
            "details": ["plugins/ 无变化"],
            "warnings": [],
        }
    sync_directory(source, target, delete=True, dry_run=dry_run)
    return {
        "module": MODULE_NAME,
        "status": "ok",
        "details": ["完全同步 plugins/（含删除远程已移除项）"],
        "warnings": [],
    }
