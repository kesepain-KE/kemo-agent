"""Discover and invoke external agents through explicitly bound Expand modules.

An external agent is never addressed by an arbitrary URL from a model request.
It is exposed by a trusted, user-authorized Expand module which owns the
transport and any credentials.  The optional ``agent_bridge.json`` file only
describes the public agent contract; the module's ``start_expand.py`` performs
the actual call in the existing isolated Expand process.
"""

from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from provider.protocol.diagnostics import redact_diagnostic_text
from run.config import MainAgentSourcePolicy, load_config, validate_user_name
from run.extensions import invoke_expand, resolve_expand
from run.agents.runner import validate_json_schema


BRIDGE_FILENAME = "agent_bridge.json"
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_COMMAND_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_HANDLE_RE = re.compile(
    r"^external:(global|shared|user):([A-Za-z][A-Za-z0-9_-]{0,63}):([A-Za-z][A-Za-z0-9_-]{0,63})$"
)
_MAX_BRIDGE_BYTES = 256 * 1024
_MAX_BRIDGE_AGENTS = 64
_MAX_DESCRIPTION_CHARS = 2_000
_MAX_TIMEOUT_SECONDS = 3_600.0
_MAX_EXECUTION_TIMEOUT_SECONDS = _MAX_TIMEOUT_SECONDS + 3_600.0
_DEFAULT_SCHEMA = {"type": "object", "additionalProperties": True}


class ExternalAgentError(RuntimeError):
    """A safe, user-facing failure from an external-agent bridge."""


@dataclass(frozen=True, slots=True)
class ExternalAgentBinding:
    handle: str
    name: str
    description: str
    scope: str
    module: str
    command: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    timeout: float
    bridge_path: Path


def _is_link(path: Path) -> bool:
    try:
        return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())
    except OSError:
        return True


def _safe_object_schema(value: Any, *, field: str, path: Path) -> dict[str, Any]:
    if value is None:
        return copy.deepcopy(_DEFAULT_SCHEMA)
    if not isinstance(value, dict) or value.get("type") != "object":
        raise ExternalAgentError(f"{field} 必须是 object JSON Schema：{path.name}")
    # Keep bridge metadata bounded even when a module is user-authored.
    try:
        if len(json.dumps(value, ensure_ascii=False, default=str)) > 64 * 1024:
            raise ExternalAgentError(f"{field} 过大")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ExternalAgentError(f"{field} 无法序列化") from exc
    return copy.deepcopy(value)


def _load_bindings(path: Path, *, scope: str, module: str) -> list[ExternalAgentBinding]:
    if not path.is_file() or _is_link(path):
        return []
    try:
        if path.stat().st_size > _MAX_BRIDGE_BYTES:
            raise ExternalAgentError("agent_bridge.json 超过大小限制")
        raw = json.loads(path.read_text("utf-8-sig"))
    except ExternalAgentError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExternalAgentError(f"agent_bridge.json 不可读或 JSON 无效：{path.name}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ExternalAgentError("agent_bridge.json schema_version 必须为 1")
    if set(raw) - {"schema_version", "agents"}:
        raise ExternalAgentError("agent_bridge.json 包含未知字段")
    agents = raw.get("agents")
    if not isinstance(agents, list) or len(agents) > _MAX_BRIDGE_AGENTS:
        raise ExternalAgentError("agent_bridge.json agents 必须是有限数组")
    bindings: list[ExternalAgentBinding] = []
    seen: set[str] = set()
    for item in agents:
        if not isinstance(item, dict):
            raise ExternalAgentError("agent_bridge.json 的代理项必须是对象")
        allowed = {"name", "description", "command", "input_schema", "output_schema", "timeout"}
        if set(item) - allowed:
            raise ExternalAgentError("agent_bridge.json 代理项包含未知字段")
        raw_name = item.get("name")
        raw_description = item.get("description")
        raw_command = item.get("command")
        if not isinstance(raw_name, str) or not _NAME_RE.fullmatch(raw_name.strip()):
            raise ExternalAgentError("agent_bridge.json 代理 name 无效")
        if not isinstance(raw_description, str):
            raise ExternalAgentError("agent_bridge.json 代理 description 无效")
        if not isinstance(raw_command, str):
            raise ExternalAgentError("agent_bridge.json 代理 command 无效")
        name = raw_name.strip()
        description = raw_description.strip()
        command = raw_command.strip()
        if not description or len(description) > _MAX_DESCRIPTION_CHARS:
            raise ExternalAgentError("agent_bridge.json 代理 description 无效")
        if not _COMMAND_RE.fullmatch(command):
            raise ExternalAgentError("agent_bridge.json 代理 command 无效")
        handle = f"external:{scope}:{module}:{name}"
        if handle in seen:
            raise ExternalAgentError(f"外部代理句柄重复：{handle}")
        seen.add(handle)
        raw_timeout = item.get("timeout", 600.0)
        if isinstance(raw_timeout, bool):
            raise ExternalAgentError("agent_bridge.json 代理 timeout 无效")
        try:
            timeout = float(raw_timeout)
        except (TypeError, ValueError) as exc:
            raise ExternalAgentError("agent_bridge.json 代理 timeout 无效") from exc
        if not math.isfinite(timeout) or timeout <= 0 or timeout > _MAX_TIMEOUT_SECONDS:
            raise ExternalAgentError("agent_bridge.json 代理 timeout 必须在 0 到 3600 秒内")
        bindings.append(
            ExternalAgentBinding(
                handle=handle,
                name=name,
                description=description,
                scope=scope,
                module=module,
                command=command,
                input_schema=_safe_object_schema(
                    item.get("input_schema"), field="input_schema", path=path
                ),
                output_schema=_safe_object_schema(
                    item.get("output_schema"), field="output_schema", path=path
                ),
                timeout=timeout,
                bridge_path=path,
            )
        )
    return bindings


def _scope_roots(root: Path, user: str) -> tuple[tuple[str, Path], ...]:
    safe_user = validate_user_name(user)
    base = root.resolve()
    return (
        ("global", base / "global_expand"),
        ("shared", base / "shared_expand"),
        ("user", base / "users" / safe_user / "expand"),
    )


def _safe_scope_root(root: Path, scope_root: Path) -> bool:
    """Reject a scope directory that escapes the project through a link."""

    base = root.resolve()
    try:
        relative = scope_root.relative_to(base)
    except ValueError:
        return False
    current = base
    try:
        for part in relative.parts:
            current = current / part
            if current.exists() and _is_link(current):
                return False
        scope_root.resolve().relative_to(base)
    except (OSError, ValueError):
        return False
    return True


def discover_external_agents(root: Path, user: str) -> tuple[ExternalAgentBinding, ...]:
    """Return only valid, currently authorized external-agent bindings."""

    base = root.resolve()
    config = load_config(user, base)
    policy = MainAgentSourcePolicy.from_config(config)
    bindings: list[ExternalAgentBinding] = []
    for scope, scope_root in _scope_roots(base, user):
        if not _safe_scope_root(base, scope_root) or not scope_root.is_dir():
            continue
        if scope == "global" and not policy.global_expand.unrestricted:
            allowed = policy.global_expand.names
        elif scope == "shared" and not policy.shared_expand.unrestricted:
            allowed = policy.shared_expand.names
        else:
            allowed = None
        for module_dir in sorted(scope_root.iterdir(), key=lambda item: (item.name.casefold(), item.name)):
            if (
                not module_dir.is_dir()
                or module_dir.name.startswith(".")
                or not _NAME_RE.fullmatch(module_dir.name)
                or _is_link(module_dir)
                or (allowed is not None and module_dir.name not in allowed)
            ):
                continue
            bridge_path = module_dir / BRIDGE_FILENAME
            if not bridge_path.is_file() or _is_link(bridge_path):
                continue
            try:
                _, meta = resolve_expand(
                    base, user, scope, module_dir.name, require_control=True
                )
                if not meta.open_control:
                    continue
                bindings.extend(
                    _load_bindings(bridge_path, scope=scope, module=module_dir.name)
                )
            except (ExternalAgentError, OSError, ValueError, RuntimeError):
                # A malformed bridge must not hide local agents or break the
                # whole dispatch tool. Calls revalidate the selected binding.
                continue
    return tuple(bindings)


def resolve_external_agent(root: Path, user: str, handle: str) -> ExternalAgentBinding:
    normalized = str(handle or "").strip()
    if not _HANDLE_RE.fullmatch(normalized):
        raise ExternalAgentError("外部子代理句柄必须是 external:<scope>:<module>:<name>")
    for binding in discover_external_agents(root, user):
        if binding.handle == normalized:
            return binding
    raise ExternalAgentError(f"外部子代理未公开或不可用：{normalized}")


def _safe_failure(exc: BaseException) -> str:
    text = redact_diagnostic_text(str(exc or "")).strip()
    return (text[:400] if text else "外部子代理调用失败")


def _external_result_data(
    raw_result: Any,
    binding: ExternalAgentBinding,
) -> Any:
    """Validate a bridge result before Expand publishes any artifacts."""

    if not isinstance(raw_result, dict):
        raise ExternalAgentError("外部子代理返回的 result 必须是对象")
    status = str(raw_result.get("status") or "completed").strip().casefold()
    if raw_result.get("ok") is False or status in {"error", "failed", "failure"}:
        raise ExternalAgentError(
            _safe_failure(raw_result.get("error") or raw_result.get("message"))
        )
    if status not in {"completed", "complete", "success", "succeeded", "ok"}:
        raise ExternalAgentError(f"外部子代理返回未完成状态：{status or 'unknown'}")
    data = raw_result.get("data", raw_result.get("output"))
    if data is None and "data" not in raw_result and "output" not in raw_result:
        data = raw_result
    try:
        validate_json_schema(data, binding.output_schema)
    except Exception as exc:
        raise ExternalAgentError(
            f"外部子代理输出不符合 Schema：{_safe_failure(exc)}"
        ) from exc
    return data


def call_external_agent(
    root: Path,
    user: str,
    handle: str,
    input_data: dict[str, Any] | None,
    *,
    timeout: float | None = None,
    cancel_event: Any = None,
) -> dict[str, Any]:
    """Synchronously call one bound external agent and validate its result."""

    binding = resolve_external_agent(root, user, handle)
    payload = {} if input_data is None else input_data
    if not isinstance(payload, dict):
        raise ExternalAgentError("外部子代理 input 必须是对象")
    try:
        validate_json_schema(payload, binding.input_schema)
    except Exception as exc:
        raise ExternalAgentError(f"外部子代理输入不符合 Schema：{_safe_failure(exc)}") from exc
    effective_timeout = binding.timeout if timeout is None else timeout
    if isinstance(effective_timeout, bool):
        raise ExternalAgentError("外部子代理 timeout 必须是正数")
    try:
        effective_timeout = float(effective_timeout)
    except (TypeError, ValueError) as exc:
        raise ExternalAgentError("外部子代理 timeout 必须是正数") from exc
    if (
        not math.isfinite(effective_timeout)
        or effective_timeout <= 0
        or effective_timeout > _MAX_EXECUTION_TIMEOUT_SECONDS
    ):
        raise ExternalAgentError("外部子代理 timeout 必须是正数")
    effective_timeout = min(effective_timeout, _MAX_EXECUTION_TIMEOUT_SECONDS)
    try:
        response = invoke_expand(
            root=root.resolve(),
            user=user,
            scope=binding.scope,
            module=binding.module,
            command=binding.command,
            params={
                "agent": binding.name,
                "input": copy.deepcopy(payload),
                "protocol": "kemo-agent-external-agent-v1",
            },
            timeout=effective_timeout,
            cancel_event=cancel_event,
            result_validator=lambda result: _external_result_data(result, binding),
        )
    except Exception as exc:
        raise ExternalAgentError(_safe_failure(exc)) from exc
    raw_result = response.get("result") if isinstance(response, dict) else None
    data = _external_result_data(raw_result, binding)
    usage = raw_result.get("usage")
    model = raw_result.get("model")
    return {
        "status": "completed",
        "agent": binding.handle,
        "source": "external",
        "data": copy.deepcopy(data),
        "usage": copy.deepcopy(usage) if isinstance(usage, dict) else {},
        "model": str(model or "external")[:160],
        "metadata": {
            "external": True,
            "scope": binding.scope,
            "module": binding.module,
            "command": binding.command,
            "duration_ms": response.get("duration_ms") if isinstance(response, dict) else None,
        },
    }
