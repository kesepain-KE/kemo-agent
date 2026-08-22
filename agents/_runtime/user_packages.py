"""Safe creation of data-only hot-pluggable user agent packages."""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from agents._runtime.schema import AgentManifestError, discover_agents
from run.config import validate_user_name


_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


class UserAgentPackageError(RuntimeError):
    pass


def _object_schema(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("type") != "object":
        raise UserAgentPackageError(f"{field} 必须是 object JSON Schema")
    return value


def create_user_agent_package(
    root: Path,
    user: str,
    definition: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(definition, dict):
        raise UserAgentPackageError("definition 必须是对象")
    name = str(definition.get("name") or "").strip()
    if not _NAME_RE.fullmatch(name):
        raise UserAgentPackageError(f"子代理名称无效：{name!r}")
    description = str(definition.get("description") or "").strip()
    instruction = str(definition.get("instruction") or "").strip()
    if not description or not instruction:
        raise UserAgentPackageError("description 和 instruction 必须是非空字符串")
    input_schema = (
        _object_schema(definition.get("input_schema"), "input_schema")
        if definition.get("input_schema") is not None
        else {"type": "object", "additionalProperties": True}
    )
    output_schema = (
        _object_schema(definition.get("output_schema"), "output_schema")
        if definition.get("output_schema") is not None
        else {"type": "object", "additionalProperties": True}
    )
    base = root.resolve()
    user_name = validate_user_name(user)
    if name in discover_agents(base).agents:
        raise UserAgentPackageError(f"用户子代理不得覆盖内置名称：{name}")
    agents_dir = base / "users" / user_name / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    target = agents_dir / name
    if target.exists():
        raise UserAgentPackageError(f"用户子代理已存在：{name}")
    agent_config = definition.get("agent_config")
    if agent_config is None:
        agent_config = {
            "schema_version": 1,
            "internal_mode": False,
            "allowed_callers": ["main_agent"],
            "tools": {
                "plugins": {"allow": []},
                "shared_skills": {"allow": []},
                "max_iterations": 20,
            },
            "global_knowledge": False,
            "shared_knowledge": False,
            "inherit_main_history": False,
        }
    if not isinstance(agent_config, dict):
        raise UserAgentPackageError("agent_config 必须是对象")
    if "internal_mode" not in agent_config and "exposure" in agent_config:
        exposure = agent_config.get("exposure") or {}
        tools = agent_config.get("tools") or {}
        prompts = agent_config.get("prompt_sources") or {}
        skills = prompts.get("skills") or {}
        knowledge = agent_config.get("knowledge") or {}
        context = agent_config.get("context") or {}
        scopes = knowledge.get("scopes") or []
        agent_config = {
            "schema_version": 1,
            "internal_mode": str(exposure.get("mode") or "internal") != "tool",
            "allowed_callers": exposure.get("allowed_callers") or [],
            "tools": {
                "plugins": (tools.get("plugins") or {"allow": []}),
                "shared_skills": {"allow": skills.get("shared") or []},
                "max_iterations": tools.get("max_iterations", 20),
            },
            "global_knowledge": "global" in scopes,
            "shared_knowledge": "shared" in scopes,
            "inherit_main_history": bool(
                context.get("inherit_main_history", False)
                or context.get("inherit_current_request", False)
            ),
        }
    manifest = {
        "name": name,
        "version": str(definition.get("version") or "1.0.0"),
        "description": description,
        "trigger": "trigger.md",
    }
    trigger_condition = str(
        definition.get("trigger_condition")
        or f"当任务符合“{description}”且需要独立处理时"
    ).strip()
    trigger_content = (
        "# 注册信息\n\n"
        f"- **名称**: {name}\n"
        f"- **触发**: {trigger_condition}\n"
        f"- **职责**: {description}\n"
        "- **模型**: default\n"
        "\n# 操作信息\n\n"
        "## 调用约定\n\n"
        "仅处理调用方显式传入的数据，具体行为遵循同目录 `AGENT.md`。\n\n"
        "## 输入参考\n\n```json\n"
        + json.dumps(input_schema, ensure_ascii=False, indent=2)
        + "\n```\n\n## 输出参考\n\n```json\n"
        + json.dumps(output_schema, ensure_ascii=False, indent=2)
        + "\n```\n"
    )
    temporary = agents_dir / f".{name}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.mkdir()
        (temporary / "AGENT.md").write_text(
            f"# {name}\n\n{instruction}\n",
            "utf-8",
        )
        (temporary / "agent.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            "utf-8",
        )
        (temporary / "agent-config.json").write_text(
            json.dumps(agent_config, ensure_ascii=False, indent=2) + "\n",
            "utf-8",
        )
        (temporary / "trigger.md").write_text(trigger_content, "utf-8")
        os.replace(temporary, target)
        try:
            loaded = discover_agents(base, user_name).get(name)
        except AgentManifestError as exc:
            shutil.rmtree(target, ignore_errors=True)
            raise UserAgentPackageError(f"用户子代理包校验失败：{exc}") from exc
        return {
            "name": loaded.name,
            "version": loaded.version,
            "description": loaded.description,
            "source": loaded.source,
            "execution": loaded.execution,
            "path": f"users/{user_name}/agents/{name}",
        }
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
