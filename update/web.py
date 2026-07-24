"""Web frontend and backend update board."""

from __future__ import annotations

from pathlib import Path

from ._utils import copy_file_safe, paths_differ, sync_directory


MODULE_NAME = "web"
WEB_EXCLUDES = (
    "node_modules/",
    "dist/",
    "frontend/node_modules/",
    "frontend/dist/",
)


def _sync_legacy_core_omissions(
    source_root: Path,
    target_root: Path,
    *,
    dry_run: bool,
    details: list[str],
    warnings: list[str],
) -> None:
    """Finish a 0.1.x -> 0.2.x all-module update in one pass.

    The 0.1.x core updater refreshes ``update/`` before the web board is
    imported, but its own directory list does not contain ``provider/``.
    Keeping this compatibility bridge in the newly loaded web board prevents
    the first 0.2.x update from requiring a second forced run.
    """
    provider_source = source_root / "provider"
    provider_target = target_root / "provider"
    if not provider_source.is_dir():
        warnings.append("源缺少目录: provider/")
    elif not provider_target.is_dir() or paths_differ(provider_source, provider_target):
        sync_directory(provider_source, provider_target, delete=True, dry_run=dry_run)
        details.append("兼容迁移: 覆盖 provider/")

    readme_source = source_root / "README_EN.md"
    if not readme_source.is_file():
        warnings.append("源缺少文件: README_EN.md")
    elif copy_file_safe(readme_source, target_root / "README_EN.md", dry_run=dry_run):
        details.append("兼容迁移: 覆盖 README_EN.md")


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
    details = ["完全同步 web/（保留 node_modules/ 与 dist/）"]
    warnings: list[str] = []
    _sync_legacy_core_omissions(
        source_root,
        target_root,
        dry_run=dry_run,
        details=details,
        warnings=warnings,
    )
    return {
        "module": MODULE_NAME,
        "status": "partial" if warnings else "ok",
        "details": details,
        "warnings": warnings,
    }
