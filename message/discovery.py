"""Discovery of installed file-based message transports."""

from __future__ import annotations

from pathlib import Path

from message.plugin import FileMessageTransport
from message.plugin_contracts import (
    MessagePluginConfig,
    MessagePluginError,
    MessagePluginIssue,
)
from run.retry import raise_if_host_exception


def discover_message_plugins(
    root: Path,
) -> tuple[list[FileMessageTransport], list[MessagePluginIssue]]:
    """Discover valid direct child plugins without executing unrelated files."""
    resolved_root = root.resolve()
    base = resolved_root / "message" / "out"
    if not base.is_dir():
        return [], []
    transports: list[FileMessageTransport] = []
    issues: list[MessagePluginIssue] = []
    machine_ids: set[str] = set()
    platforms: set[str] = set()
    for directory in sorted(
        (item for item in base.iterdir() if item.is_dir()),
        key=lambda item: item.name.casefold(),
    ):
        try:
            config = MessagePluginConfig.load(resolved_root, directory)
            if config.machine_id in machine_ids:
                raise MessagePluginError(f"machine_id 重复：{config.machine_id}")
            if config.platform in platforms:
                raise MessagePluginError(f"platform 重复：{config.platform}")
            transport = FileMessageTransport(config)
            transports.append(transport)
            machine_ids.add(config.machine_id)
            platforms.add(config.platform)
        except BaseException as exc:
            raise_if_host_exception(exc)
            issues.append(MessagePluginIssue(directory.name, directory, str(exc)))
    return transports, issues
