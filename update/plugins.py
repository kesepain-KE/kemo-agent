"""Merge framework plugins while preserving user-owned modules and data."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from tempfile import TemporaryDirectory

from ._utils import UpdateError, copy_file_safe, paths_differ, read_yaml, sync_directory

MODULE_NAME = "plugins"
META = "plugin.yaml"
FRAMEWORK_DIRECTORIES = frozenset({"kemo_graph"})
LOCAL_NAMES = frozenset({
    "config.json", "config.yaml", "config.yml", "users.json",
    "credential_registry.json", "gateway_config.json", "graph_config.json",
    "panel.values.json", "status.json", "input_data.md",
})


def local_origin(directory: Path) -> str | None:
    """Unknown ownership is never authorization to remove a directory."""
    if directory.is_symlink() or getattr(directory, "is_junction", lambda: False)():
        raise UpdateError(f"插件目录不得是链接：{directory}")
    metadata = directory / META
    if not metadata.is_file():
        return None
    origin = read_yaml(metadata).get("origin", "builtin")
    if origin not in ("builtin", "user"):
        raise UpdateError(f"插件 origin 必须是 builtin 或 user：{metadata}")
    return origin


def local_data_excludes(directory: Path) -> tuple[str, ...]:
    excludes = [*LOCAL_NAMES, "data/", "artifacts/", "logs/", "__pycache__/"]
    for path in directory.rglob("*"):
        if path.name in LOCAL_NAMES or (
            path.is_dir() and path.name in {"data", "artifacts", "logs", "__pycache__"}
        ) or (path.name.startswith(".env") and path.name != ".env.example") or any(
            fnmatch.fnmatch(path.name, pattern)
            for pattern in ("*.sqlite3*", "*.db", "*.lock", "*.pid", "*.pyc", "*.log")
        ):
            suffix = "/" if path.is_dir() else ""
            excludes.append(path.relative_to(directory).as_posix() + suffix)
    return tuple(excludes)


def runtime_excludes(root: Path, *, snapshot: Path | None = None) -> tuple[str, ...]:
    """Root-relative exclusions shared by source backup and restore."""
    base = root / "plugins"
    if not base.is_dir():
        return ()
    excludes = []
    for directory in base.iterdir():
        if not directory.is_dir() or directory.name == "__pycache__":
            continue
        origin = local_origin(directory)
        if origin is None and snapshot is not None:
            origin = local_origin(snapshot / "plugins" / directory.name)
        relative = directory.relative_to(root).as_posix()
        if origin == "user" or origin is None and directory.name not in FRAMEWORK_DIRECTORIES:
            excludes.append(relative + "/")
        else:
            excludes.extend(relative + "/" + item for item in local_data_excludes(directory))
    return tuple(excludes)


def update(
    source_root: Path,
    target_root: Path,
    *,
    dry_run: bool = False,
    assume_yes: bool = False,
) -> dict:
    del assume_yes
    source, target = source_root / "plugins", target_root / "plugins"
    if not source.is_dir():
        return {"module": MODULE_NAME, "status": "failed",
                "details": [], "warnings": ["源缺少目录: plugins/"]}

    # Validate ownership and release completeness before changing any files.
    incoming = {
        entry.name: entry for entry in source.iterdir()
        if entry.is_dir() and entry.name != "__pycache__"
    }
    for name, directory in incoming.items():
        origin = local_origin(directory)
        if origin == "user" or (
            name not in FRAMEWORK_DIRECTORIES
            and (origin != "builtin" or not (directory / "SKILL.md").is_file())
        ):
            raise UpdateError(f"发布插件必须包含 SKILL.md 和内置 plugin.yaml：{directory}")
    origins = {
        entry.name: local_origin(entry) for entry in target.iterdir() if entry.is_dir()
        and entry.name != "__pycache__"
    } if target.is_dir() else {}
    details = []
    changed = False
    for path in sorted(source.glob("*.py")):
        if copy_file_safe(path, target / path.name, dry_run=dry_run):
            details.append(f"更新插件框架: {path.name}")
            changed = True
    for name, directory in sorted(incoming.items()):
        destination = target / name
        if origins.get(name) == "user":
            details.append(f"保留用户插件: {name}")
            continue
        excludes = (*local_data_excludes(directory), *local_data_excludes(destination))
        if destination.is_dir() and not paths_differ(directory, destination, excludes=excludes):
            continue
        sync_directory(directory, destination, delete=True, excludes=excludes, dry_run=dry_run)
        details.append(f"更新内置插件或共用实现: {name}")
        changed = True
    for name, origin in sorted(origins.items()):
        if name in incoming or origin != "builtin":
            continue
        directory = target / name
        if not directory.resolve().is_relative_to(target.resolve()):
            raise UpdateError(f"插件目录越界：{directory}")
        # Retire framework files using the existing deletion engine; local
        # credentials/data may leave a directory behind and remain user-owned.
        with TemporaryDirectory(prefix="kemo-retired-plugin-") as empty:
            sync_directory(
                Path(empty), directory, delete=True,
                excludes=local_data_excludes(directory), dry_run=dry_run,
            )
        if not dry_run and not any(directory.iterdir()):
            directory.rmdir()
        details.append(f"删除已移除的内置插件: {name}")
        changed = True
    return {"module": MODULE_NAME, "status": "ok" if changed else "skipped",
            "details": details or ["plugins/ 无变化"], "warnings": []}
