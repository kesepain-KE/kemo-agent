"""Web frontend and backend update board."""

from __future__ import annotations

from pathlib import Path

from ._utils import sync_directory


MODULE_NAME = "web"
WEB_EXCLUDES = (
    "node_modules/",
    "dist/",
    "frontend/node_modules/",
    "frontend/dist/",
)


def update(
    source_root: Path,
    target_root: Path,
    *,
    dry_run: bool = False,
    assume_yes: bool = False,
) -> dict:
    """Replace web/ while preserving dependency and build-product directories."""
    del assume_yes
    source = source_root / "web"
    target = target_root / "web"
    if not source.is_dir():
        return {
            "module": MODULE_NAME,
            "status": "failed",
            "details": [],
            "warnings": ["源缺少目录: web/"],
        }
    sync_directory(
        source,
        target,
        delete=True,
        excludes=WEB_EXCLUDES,
        dry_run=dry_run,
    )
    return {
        "module": MODULE_NAME,
        "status": "ok",
        "details": ["完全同步 web/（保留 node_modules/ 与 dist/）"],
        "warnings": [],
    }
