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
from run.config import load_config
from run.users import validate_user_name


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
    input_schema = _object_schema(definition.get("input_schema"), "input_schema")
    output_schema = _object_schema(definition.get("output_schema"), "output_schema")
    base = root.resolve()
    user_name = validate_user_name(user)
    if name in discover_agents(base).agents:
        raise UserAgentPackageError(f"用户子代理不得覆盖内置名称：{name}")
    agents_dir = base / "users" / user_name / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    target = agents_dir / name
    if target.exists():
        raise UserAgentPackageError(f"用户子代理已存在：{name}")
    execution = str(definition.get("execution") or "sync")
    model_profile = "default"
    runtime = load_config(user_name, base).get("agent_runtime") or {}
    timeout = definition.get("timeout", runtime.get("default_timeout", 600))
    write_policy = str(definition.get("write_policy") or "none")
    agent_config = definition.get("agent_config")
    if agent_config is None:
        agent_config = {
            "schema_version": 1,
            "exposure": {"mode": "tool", "allowed_callers": ["main_agent"]},
            "tools": {"plugins": {"allow": []}, "max_iterations": 1},
            "prompt_sources": {
                "skills": {"shared": [], "user": []},
                "expand": {"global": [], "shared": [], "user": []},
            },
            "knowledge": {
                "scopes": [],
                "index_enabled": False,
                "body_access": "none",
                "max_index_chars": 0,
            },
            "context": {
                "inherit_main_history": False,
                "inherit_current_request": False,
            },
        }
    if not isinstance(agent_config, dict):
        raise UserAgentPackageError("agent_config 必须是对象")
    manifest = {
        "schema_version": 2,
        "name": name,
        "version": str(definition.get("version") or "1.0.0"),
        "description": description,
        "enabled": True,
        "instruction": "AGENT.md",
        "executor": "builtin:llm",
        "config": "agent-config.json",
        "model_profile": model_profile,
        "timeout": timeout,
        "execution": execution,
        "write_policy": write_policy,
        "input_schema": input_schema,
        "output_schema": output_schema,
    }
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
