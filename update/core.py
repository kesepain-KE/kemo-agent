"""Core-engine update board."""

from __future__ import annotations

import difflib
import json
from pathlib import Path

from ._utils import (
    ask_choice,
    copy_file_safe,
    paths_differ,
    read_json,
    sync_directory,
    sync_directory_except,
    write_json_atomic,
    yellow,
)


MODULE_NAME = "core"

DIRECTORIES = (
    "run",
    "provider",
    "cron",
    "template",
    "tests",
    "update",
)

RUNTIME_PRESERVING_DIRECTORIES = {
    "global_knowledge": ("kemo-graph-storage/",),
}

FILES = (
    "cli.py",
    "events.py",
    "setup.py",
    "update.py",
    "requirements.txt",
    "requirements-dev.txt",
    "config/global_soul.md",
    ".env.example",
    "LICENSE",
    "kemo-agent.ico",
    "kemo-agent.jpg",
    "kemo-web-UI.png",
    "agents.md",
    "restart.py",
    "start_web.py",
    "user_create.py",
    "README_EN.md",
)

REGISTER_FILES = (
    "global_expand/register.py",
    "global_sense/register.py",
    "shared_expand/register.py",
    "shared_skills/register.py",
)

GATEWAY_STATUS_EXPAND = "global_expand/kemo_gateway_status"
GATEWAY_STATUS_EXPAND_FILES = (
    ".gitignore",
    "README.md",
    "gateway_config.example.json",
    "gateway_status.py",
    "data_update.py",
    "start_expand.py",
    "expand_control.md",
)

KEMO_GRAPH_EXPAND = "global_expand/kemo_graph"
KEMO_GRAPH_EXPAND_FILES = (
    ".gitignore",
    "graph_config.example.json",
    "errors.py",
    "registry.py",
    "client.py",
    "library_sync.py",
    "operations.py",
    "render.py",
    "graph_core.py",
    "data_update.py",
    "start_expand.py",
    "expand_control.md",
)
KEMO_GRAPH_OBSOLETE_FILES = (
    "sync_sources.py",
    "auto_maintenance.py",
    "auto_sync.py",
    "auto_ingest.py",
    "sync_state.json",
    "auto_update_state.json",
    "data/kemo_graph_status.json",
    "artifacts/kemo_graph_status.png",
)

KEMO_APP_EXPAND = "global_expand/kemo_app"
KEMO_APP_EXPAND_FILES = (
    ".gitignore",
    "README.md",
    "app.py",
    "auth.py",
    "config.example.json",
    "credential_registry.py",
    "daemon.py",
    "data_update.py",
    "device_commands.py",
    "events.py",
    "initialize_config.py",
    "lifecycle.py",
    "manage_device_token.py",
    "manage_user.py",
    "server.py",
    "start_expand.py",
    "expand_control.md",
    "upstream.py",
)

BUILTIN_GLOBAL_EXPANDS = (
    (GATEWAY_STATUS_EXPAND, GATEWAY_STATUS_EXPAND_FILES, "gateway_config.json", ()),
    (
        KEMO_GRAPH_EXPAND,
        KEMO_GRAPH_EXPAND_FILES,
        "graph_config.json",
        KEMO_GRAPH_OBSOLETE_FILES,
    ),
    (KEMO_APP_EXPAND, KEMO_APP_EXPAND_FILES, "config.json", ()),
)


def _preserved_directory_differs(
    source: Path,
    target: Path,
    excludes: tuple[str, ...],
) -> bool:
    """Compare distributable files while ignoring runtime-owned directories."""

    excluded_names = {item.replace("\\", "/").rstrip("/") for item in excludes}

    def snapshot(root: Path) -> dict[str, bytes]:
        if not root.is_dir():
            return {}
        result: dict[str, bytes] = {}
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if any(name in relative.parts for name in excluded_names):
                continue
            result[relative.as_posix()] = path.read_bytes()
        return result

    return snapshot(source) != snapshot(target)


def _json_diff(source: Path, target: Path) -> str:
    source_text = json.dumps(read_json(source), ensure_ascii=False, indent=2, sort_keys=True).splitlines()
    target_text = json.dumps(read_json(target), ensure_ascii=False, indent=2, sort_keys=True).splitlines()
    return "\n".join(
        difflib.unified_diff(
            target_text,
            source_text,
            fromfile="本地 config/global_config.json",
            tofile="远程 config/global_config.json",
            lineterm="",
        )
    )


def _update_global_config(
    source_root: Path,
    target_root: Path,
    *,
    dry_run: bool,
    assume_yes: bool,
) -> tuple[bool, str]:
    source = source_root / "config" / "global_config.json"
    target = target_root / "config" / "global_config.json"
    if not source.is_file():
        return False, "源缺少 config/global_config.json"
    if not target.is_file():
        changed = copy_file_safe(source, target, dry_run=dry_run)
        return changed, "安装 config/global_config.json"
    if not paths_differ(source, target):
        return False, "config/global_config.json 无变化"

    source_json = read_json(source)
    target_json = read_json(target)
    source_schema = source_json.get("schema_version")
    target_schema = target_json.get("schema_version")
    if source_schema != target_schema:
        print(yellow(
            "global_config.json schema 版本不同: "
            f"本地={target_schema!r}, 远程={source_schema!r}"
        ))
        source_only = sorted(set(source_json) - set(target_json))
        target_only = sorted(set(target_json) - set(source_json))
        print(yellow("差异字段："))
        print(f"  远程新增顶层字段: {', '.join(source_only) if source_only else '无'}")
        print(f"  本地独有顶层字段: {', '.join(target_only) if target_only else '无'}")

    choice = ask_choice(
        "global_config.json 需要更新，请选择:",
        {"o": "覆盖为最新版本", "k": "保留本地版本", "d": "显示完整差异"},
        default="o",
        assume_yes=assume_yes,
    )
    if choice == "d":
        print(_json_diff(source, target) or "文件内容无可显示差异")
        choice = ask_choice(
            "查看差异后请选择:",
            {"o": "覆盖为最新版本", "k": "保留本地版本"},
            default="o",
            assume_yes=assume_yes,
        )
    if choice == "k":
        return False, "保留本地 config/global_config.json"
    changed = copy_file_safe(source, target, dry_run=dry_run)
    return changed, "更新 config/global_config.json"


def _sync_directory_if_changed(
    source: Path,
    target: Path,
    relative: str,
    *,
    dry_run: bool,
    details: list[str],
    warnings: list[str],
) -> bool:
    if not source.is_dir():
        warnings.append(f"源缺少目录: {relative}/")
        return False
    if target.is_dir() and not paths_differ(source, target):
        return False
    sync_directory(source, target, delete=True, dry_run=dry_run)
    details.append(f"覆盖目录: {relative}/")
    return True


def _sync_file_if_changed(
    source_root: Path,
    target_root: Path,
    relative: str,
    *,
    dry_run: bool,
    details: list[str],
    warnings: list[str],
) -> bool:
    source = source_root / relative
    if not source.is_file():
        warnings.append(f"源缺少文件: {relative}")
        return False
    if not copy_file_safe(source, target_root / relative, dry_run=dry_run):
        return False
    details.append(f"覆盖文件: {relative}")
    return True


def _update_builtin_global_expand(
    source_root: Path,
    target_root: Path,
    *,
    relative: str,
    source_files: tuple[str, ...],
    config_file: str,
    obsolete_files: tuple[str, ...],
    dry_run: bool,
    details: list[str],
    warnings: list[str],
) -> bool:
    """Install one built-in Expand without overwriting local configuration or runtime data."""

    source = source_root / relative
    target = target_root / relative
    if not source.is_dir():
        warnings.append(f"源缺少目录: {relative}/")
        return False

    changed = False
    for name in source_files:
        if not (source / name).is_file():
            warnings.append(f"源缺少文件: {relative}/{name}")
            continue
        if copy_file_safe(source / name, target / name, dry_run=dry_run):
            changed = True

    for name in obsolete_files:
        obsolete = target / name
        if not obsolete.is_file():
            continue
        if dry_run:
            print(f"[dry-run]  删除旧文件  {relative}/{name}")
        else:
            obsolete.unlink()
        changed = True

    source_manifest = source / "expand.json"
    target_manifest = target / "expand.json"
    if not source_manifest.is_file():
        warnings.append(f"源缺少文件: {relative}/expand.json")
    else:
        manifest = read_json(source_manifest)
        config_path = target / config_file
        active = config_path.is_file()
        try:
            current = read_json(target_manifest) if target_manifest.is_file() else {}
        except Exception:
            current = {}
        if relative == KEMO_GRAPH_EXPAND:
            try:
                local_config = read_json(config_path)
            except Exception:
                local_config = {}
            admins = local_config.get("admin_users")
            libraries = local_config.get("libraries")
            active = (
                local_config.get("schema_version") == 2
                and isinstance(admins, list)
                and bool(admins)
                and all(isinstance(item, str) and item.strip() and item != "*" for item in admins)
                and isinstance(libraries, list)
                and any(isinstance(item, dict) and item.get("enabled", True) for item in libraries)
            )
        elif relative == KEMO_APP_EXPAND:
            # App activation is an explicit, durable operator choice.  An
            # update must not erase it merely because credentials are being
            # migrated, temporarily incomplete, or unavailable for validation
            # while files are copied.  Runtime readiness is still enforced by
            # kemo_app/start_expand.py when the bridge is actually started.
            # A fresh install and an explicitly deactivated installation both
            # remain inactive because neither has an explicit boolean true in
            # its existing local manifest.
            active = current.get("open_input") is True
        manifest["open_input"] = active
        if current:
            if current.get("input_health") in {"正常", "异常"}:
                manifest["input_health"] = current["input_health"]
            recent_update = current.get("recent_update")
            if isinstance(recent_update, str) and recent_update.strip():
                manifest["recent_update"] = recent_update
        try:
            target_manifest_value = read_json(target_manifest) if target_manifest.is_file() else None
        except Exception:
            target_manifest_value = None
        if target_manifest_value != manifest:
            if dry_run:
                print(f"[dry-run]  更新  {relative}/expand.json")
            else:
                write_json_atomic(target_manifest, manifest)
            changed = True

    source_input = source / "input_data.md"
    target_input = target / "input_data.md"
    if not source_input.is_file():
        warnings.append(f"源缺少文件: {relative}/input_data.md")
    elif relative == KEMO_GRAPH_EXPAND:
        if copy_file_safe(source_input, target_input, dry_run=dry_run):
            changed = True
    elif not target_input.is_file():
        if copy_file_safe(source_input, target_input, dry_run=dry_run):
            changed = True

    if changed:
        details.append(f"更新内置拓展: {relative}/（保留本地凭据与采集数据）")
    return changed


def _update_gateway_status_expand(
    source_root: Path,
    target_root: Path,
    *,
    dry_run: bool,
    details: list[str],
    warnings: list[str],
) -> bool:
    """Compatibility wrapper for callers that update only the gateway status Expand."""

    return _update_builtin_global_expand(
        source_root,
        target_root,
        relative=GATEWAY_STATUS_EXPAND,
        source_files=GATEWAY_STATUS_EXPAND_FILES,
        config_file="gateway_config.json",
        obsolete_files=(),
        dry_run=dry_run,
        details=details,
        warnings=warnings,
    )


def update(
    source_root: Path,
    target_root: Path,
    *,
    dry_run: bool = False,
    assume_yes: bool = False,
) -> dict:
    """Update core framework paths while preserving runtime-owned data."""
    details: list[str] = []
    warnings: list[str] = []
    changed = False

    for relative in DIRECTORIES:
        changed |= _sync_directory_if_changed(
            source_root / relative,
            target_root / relative,
            relative,
            dry_run=dry_run,
            details=details,
            warnings=warnings,
        )

    for relative in FILES:
        changed |= _sync_file_if_changed(
            source_root,
            target_root,
            relative,
            dry_run=dry_run,
            details=details,
            warnings=warnings,
        )

    readme_source = next(
        (name for name in ("README.md", "readme.md") if (source_root / name).is_file()),
        None,
    )
    if readme_source:
        changed |= _sync_file_if_changed(
            source_root,
            target_root,
            readme_source,
            dry_run=dry_run,
            details=details,
            warnings=warnings,
        )
    else:
        warnings.append("源缺少文件: README.md/readme.md")

    message_source = source_root / "message"
    if message_source.is_dir():
        sync_directory_except(
            message_source,
            target_root / "message",
            ["out/"],
            delete=True,
            dry_run=dry_run,
        )
        details.append("覆盖目录: message/（保留 out/）")
        changed = True
    else:
        warnings.append("源缺少目录: message/")

    for relative in REGISTER_FILES:
        changed |= _sync_file_if_changed(
            source_root,
            target_root,
            relative,
            dry_run=dry_run,
            details=details,
            warnings=warnings,
        )

    for relative, excludes in RUNTIME_PRESERVING_DIRECTORIES.items():
        source = source_root / relative
        if not source.is_dir():
            warnings.append(f"源缺少目录: {relative}/")
            continue
        directory_changed = _preserved_directory_differs(
            source, target_root / relative, excludes
        )
        if not directory_changed:
            continue
        sync_directory(
            source,
            target_root / relative,
            delete=True,
            excludes=excludes,
            dry_run=dry_run,
        )
        details.append(
            f"覆盖目录: {relative}/（保留 {', '.join(excludes)}）"
        )
        changed = True

    for relative, source_files, config_file, obsolete_files in BUILTIN_GLOBAL_EXPANDS:
        changed |= _update_builtin_global_expand(
            source_root,
            target_root,
            relative=relative,
            source_files=source_files,
            config_file=config_file,
            obsolete_files=obsolete_files,
            dry_run=dry_run,
            details=details,
            warnings=warnings,
        )

    config_changed, config_detail = _update_global_config(
        source_root,
        target_root,
        dry_run=dry_run,
        assume_yes=assume_yes,
    )
    changed |= config_changed
    if config_detail.startswith("源缺少"):
        warnings.append(config_detail)
    else:
        details.append(config_detail)

    if warnings:
        status = "partial"
    elif changed:
        status = "ok"
    else:
        status = "skipped"
    return {
        "module": MODULE_NAME,
        "status": status,
        "details": details,
        "warnings": warnings,
    }
