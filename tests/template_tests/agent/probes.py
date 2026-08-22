"""Agent-specific schema sampling and fake-provider executor probes."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from tests.template_tests.common import (
    ContractDependencyMissing,
    ContractValidationError,
    PROJECT_ROOT,
    hidden_startupinfo,
)


def sample_from_schema(schema: Any) -> Any:
    """Build a deterministic, side-effect-free example for common JSON Schema shapes."""

    if not isinstance(schema, dict):
        return {}
    if "const" in schema:
        return schema["const"]
    if "default" in schema:
        return schema["default"]
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]
    examples = schema.get("examples")
    if isinstance(examples, list) and examples:
        return examples[0]
    for keyword in ("oneOf", "anyOf"):
        choices = schema.get(keyword)
        if isinstance(choices, list) and choices:
            return sample_from_schema(choices[0])
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((item for item in kind if item != "null"), "null")
    if kind == "object" or isinstance(schema.get("properties"), dict):
        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        return {
            name: sample_from_schema(properties.get(name, {}))
            for name in required
            if isinstance(name, str)
        }
    if kind == "array":
        minimum = max(0, int(schema.get("minItems") or 0))
        maximum = schema.get("maxItems")
        if isinstance(maximum, int):
            minimum = min(minimum, max(0, maximum))
        return [sample_from_schema(schema.get("items") or {}) for _ in range(minimum)]
    if kind == "string":
        minimum = max(0, int(schema.get("minLength") or 0))
        maximum = schema.get("maxLength")
        value = "contract-probe"
        if len(value) < minimum:
            value += "x" * (minimum - len(value))
        if isinstance(maximum, int):
            value = value[: max(0, maximum)]
        return value
    if kind == "integer":
        if "minimum" in schema:
            return int(schema["minimum"])
        if "exclusiveMinimum" in schema:
            return int(schema["exclusiveMinimum"]) + 1
        return 0
    if kind == "number":
        if "minimum" in schema:
            return float(schema["minimum"])
        if "exclusiveMinimum" in schema:
            return float(schema["exclusiveMinimum"]) + 1.0
        return 0.0
    if kind == "boolean":
        return False
    if kind == "null":
        return None
    return {}


_AGENT_EXECUTOR_PROBE = r'''
import inspect
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

MARKER = "KEMO_TEMPLATE_TEST_RESULT="
project_root = Path(sys.argv[1]).resolve()
sandbox_root = Path(sys.argv[2]).resolve()
user = sys.argv[3]
name = sys.argv[4]
input_data = json.loads(sys.argv[5])
output_data = json.loads(sys.argv[6])
sys.path.insert(0, str(sandbox_root))
sys.path.insert(0, str(project_root))

try:
    from agents._runtime.schema import discover_agents
    from run.agents import AgentRunResult, _load_executor, validate_json_schema

    definition = discover_agents(sandbox_root, user).get(name)
    validate_json_schema(input_data, definition.input_schema)
    validate_json_schema(output_data, definition.output_schema)
    if definition.executor == "builtin:llm":
        result = {
            "ok": True,
            "mode": "builtin:llm",
            "output_keys": sorted(output_data),
        }
    else:
        class ContractContext:
            def __init__(self):
                self.definition = definition
                self.runner = SimpleNamespace(
                    root=sandbox_root,
                    user=user,
                    config={},
                )
                self.prompt_bundle = SimpleNamespace(text="", diagnostics={})
                self.tool_registry = SimpleNamespace()
                self.cancel_event = threading.Event()
                self.model_override = None
                self.max_tokens = None
                self.task_id = "template-contract-probe"
                self.structured_output_tool = False

            def run_model(self, _input_data):
                return AgentRunResult(
                    agent=definition.name,
                    data=output_data,
                    raw_text=json.dumps(output_data, ensure_ascii=False),
                    usage={"total_tokens": 0},
                    model="contract-fake-provider",
                    metadata={"contract_probe": True},
                )

        function = _load_executor(definition)
        value = function(ContractContext(), input_data)
        if inspect.isawaitable(value):
            raise TypeError("自定义 executor 必须同步返回 AgentRunResult")
        if not isinstance(value, AgentRunResult):
            raise TypeError("自定义 executor 必须返回 AgentRunResult")
        if value.agent != definition.name:
            raise ValueError("AgentRunResult.agent 必须与清单 name 一致")
        validate_json_schema(value.data, definition.output_schema)
        json.dumps(value.data, ensure_ascii=False)
        result = {
            "ok": True,
            "mode": "custom",
            "output_keys": sorted(value.data),
        }
except ModuleNotFoundError as exc:
    result = {
        "ok": False,
        "category": "dependency_missing",
        "dependency": exc.name or "unknown",
        "error": str(exc),
    }
except BaseException as exc:
    result = {
        "ok": False,
        "category": "contract_error",
        "exception_type": type(exc).__name__,
        "error": str(exc) or type(exc).__name__,
    }
print(MARKER + json.dumps(result, ensure_ascii=False, default=str))
'''


def probe_agent_executor(
    *,
    sandbox_root: Path,
    user: str,
    name: str,
    input_data: dict[str, Any],
    output_data: dict[str, Any],
    repository_root: Path = PROJECT_ROOT,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Invoke one agent executor with a deterministic fake model in a child process."""

    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                _AGENT_EXECUTOR_PROBE,
                str(repository_root),
                str(sandbox_root),
                user,
                name,
                json.dumps(input_data, ensure_ascii=False),
                json.dumps(output_data, ensure_ascii=False),
            ],
            cwd=sandbox_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(0.1, float(timeout)),
            startupinfo=hidden_startupinfo(),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ContractValidationError(
            f"子代理执行入口超过 {float(timeout):g} 秒"
        ) from exc
    marker = "KEMO_TEMPLATE_TEST_RESULT="
    payload_line = next(
        (
            line[len(marker) :]
            for line in reversed(completed.stdout.splitlines())
            if line.startswith(marker)
        ),
        "",
    )
    if not payload_line:
        detail = (completed.stderr or completed.stdout).strip()[-800:]
        raise ContractValidationError(
            "子代理执行探测没有返回合同结果" + (f"：{detail}" if detail else "")
        )
    try:
        payload = json.loads(payload_line)
    except json.JSONDecodeError as exc:
        raise ContractValidationError("子代理执行探测结果不是有效 JSON") from exc
    if payload.get("ok") is True:
        return payload
    if payload.get("category") == "dependency_missing":
        raise ContractDependencyMissing(
            str(payload.get("dependency") or "unknown"),
            str(payload.get("error") or ""),
        )
    raise ContractValidationError(str(payload.get("error") or "子代理执行合同不符合要求"))

