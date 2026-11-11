"""Attach the existing workspace contract to folder-based message transports."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from message.plugin_contracts import MessagePluginConfig
from run.modules.paths import module_path
from web.errors import InvalidRequestError


@dataclass(frozen=True)
class MessageWorkspaceSpec:
    module_dir: Path
    ui_workspace: Path | None = None
    ui_action_entry: Path | None = None
    kind: Literal["message"] = "message"
    input_open: bool = False
    control_entry: Path | None = None
    control_script: Path | None = None

    @classmethod
    def load(cls, root: Path, directory: Path) -> "MessageWorkspaceSpec":
        config = MessagePluginConfig.load(root, directory)
        ui = config.raw.get("ui", {})
        if not isinstance(ui, dict) or set(ui) - {"workspace", "action_entry"}:
            raise InvalidRequestError("message.json ui 只支持 workspace 和 action_entry")
        return cls(
            module_dir=directory,
            ui_workspace=module_path(directory, ui["workspace"]) if "workspace" in ui else None,
            ui_action_entry=module_path(directory, ui["action_entry"]) if "action_entry" in ui else None,
        )
