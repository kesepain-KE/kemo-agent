"""Built-in agent-system update board."""

from __future__ import annotations

from pathlib import Path

from ._utils import UpdateError, copy_file_safe, paths_differ, read_json, sync_directory


MODULE_NAME = "agents"
RESERVED_DIRECTORIES = {"_runtime", "__pycache__"}


def _agent_version(directory: Path) -> str:
    for filename in ("agent.json", "agent-config.json"):
        path = directory / filename
        if not path.is_file():
            continue
        try:
            value = read_json(path).get("version")
        except (OSError, ValueError, UpdateError):
            continue
        if value not in {None, ""}:
            return str(value)
    return ""


def update(
    source_root: Path,
    target_root: Path,
    *,
    dry_run: bool = False,
    assume_yes: bool = False,
) -> dict:
    """Merge remote built-in agents without deleting local-only agent packages."""
    del assume_yes
    source_agents = source_root / "agents"
    target_agents = target_root / "agents"
    details: list[str] = []
    warnings: list[str] = []
    changed = False

    if not source_agents.is_dir():
        return {
            "module": MODULE_NAME,
            "status": "failed",
            "details": details,
            "warnings": ["源缺少目录: agents/"],
        }

    runtime_source = source_agents / "_runtime"
    runtime_target = target_agents / "_runtime"
    if runtime_source.is_dir():
        if not runtime_target.is_dir() or paths_differ(runtime_source, runtime_target):
            sync_directory(runtime_source, runtime_target, delete=True, dry_run=dry_run)
            details.append("覆盖 agents/_runtime/")
            changed = True
    else:
        warnings.append("源缺少目录: agents/_runtime/")

    init_source = source_agents / "__init__.py"
    if init_source.is_file():
        if copy_file_safe(init_source, target_agents / "__init__.py", dry_run=dry_run):
            details.append("覆盖 agents/__init__.py")
            changed = True
    else:
        warnings.append("源缺少文件: agents/__init__.py")

    for source_agent in sorted(source_agents.iterdir(), key=lambda path: path.name):
        if not source_agent.is_dir() or source_agent.name in RESERVED_DIRECTORIES:
            continue
        target_agent = target_agents / source_agent.name
        existed = target_agent.is_dir()
        if target_agent.is_dir() and not paths_differ(source_agent, target_agent):
            continue
        local_version = _agent_version(target_agent) if target_agent.is_dir() else ""
        remote_version = _agent_version(source_agent)
        sync_directory(source_agent, target_agent, delete=True, dry_run=dry_run)
        action = "更新" if existed else "新增"
        version_note = ""
        if local_version or remote_version:
            version_note = f"（{local_version or '未声明'} -> {remote_version or '未声明'}）"
        details.append(f"{action}内置子代理: {source_agent.name}{version_note}")
        changed = True

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
