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
    yellow,
)


MODULE_NAME = "core"

DIRECTORIES = (
    "run",
    "provider",
    "cron",
    "template",
    "tests",
    "global_knowledge",
    "update",
)

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
