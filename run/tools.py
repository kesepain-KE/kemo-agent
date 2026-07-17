"""Tool discovery, manifest validation and execution."""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


class ToolError(RuntimeError):
    pass


class ToolValidationError(ToolError):
    pass


class ToolTimeoutError(ToolError):
    pass


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    version: str
    enabled: bool
    entrypoint: str
    source: str
    directory: Path
    overrides: list[str] = field(default_factory=list)
    _callable: Callable[..., Any] | None = field(default=None, repr=False)

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }

    def load_callable(self) -> Callable[..., Any]:
        if self._callable is not None:
            return self._callable
        file_name, separator, function_name = self.entrypoint.partition(":")
        if not separator or not file_name or not function_name:
            raise ToolError(f"工具 {self.name} entrypoint 无效：{self.entrypoint}")
        module_path = self.directory / file_name
        if not module_path.is_file():
            raise ToolError(f"工具 {self.name} 入口文件不存在：{module_path}")
        module_name = f"kemo_tool_{self.name}_{uuid.uuid4().hex}"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise ToolError(f"无法加载工具模块：{module_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop(module_name, None)
        function = getattr(module, function_name, None)
        if not callable(function):
            raise ToolError(f"工具 {self.name} 入口不可调用：{self.entrypoint}")
        self._callable = function
        return function


@dataclass(slots=True)
class ToolRegistry:
    tools: dict[str, ToolDefinition]

    def enabled_tools(self) -> list[ToolDefinition]:
        return [tool for tool in self.tools.values() if tool.enabled]

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.openai_schema() for tool in self.enabled_tools()]

    def get(self, name: str) -> ToolDefinition:
        tool = self.tools.get(name)
        if tool is None:
            raise ToolError(f"未知工具：{name}")
        if not tool.enabled:
            raise ToolError(f"工具已禁用：{name}")
        return tool


def _source_dirs(root: Path, user: str) -> list[tuple[str, Path]]:
    return [
        ("plugins", root / "plugins"),
        ("shared_skills", root / "shared_skills"),
        ("agent_create", root / "users" / user / "user_skills" / "agent_create"),
        ("user_create", root / "users" / user / "user_skills" / "user_create"),
    ]


def _manifest(path: Path, source: str) -> ToolDefinition:
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolError(f"工具清单不可读：{path}（{exc}）") from exc
    if not isinstance(raw, dict):
        raise ToolError(f"工具清单必须是对象：{path}")
    required = ("name", "description", "input_schema", "version", "enabled", "entrypoint")
    missing = [name for name in required if name not in raw]
    if missing:
        raise ToolError(f"工具清单缺少字段 {', '.join(missing)}：{path}")
    if not isinstance(raw["input_schema"], dict) or raw["input_schema"].get("type") != "object":
        raise ToolError(f"工具 input_schema 必须是 object JSON Schema：{path}")
    return ToolDefinition(
        name=str(raw["name"]),
        description=str(raw["description"]),
        input_schema=raw["input_schema"],
        version=str(raw["version"]),
        enabled=bool(raw["enabled"]),
        entrypoint=str(raw["entrypoint"]),
        source=source,
        directory=path.parent,
    )


def discover_tools(root: Path, user: str) -> ToolRegistry:
    tools: dict[str, ToolDefinition] = {}
    for source, base in _source_dirs(root, user):
        if not base.is_dir():
            continue
        manifests = sorted(base.glob("*/tool.json"), key=lambda item: str(item).casefold())
        for path in manifests:
            tool = _manifest(path, source)
            previous = tools.get(tool.name)
            if previous is not None:
                tool.overrides = [*previous.overrides, f"{previous.source}:{previous.directory}"]
            tools[tool.name] = tool
    return ToolRegistry(tools)


def validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> None:
    if not isinstance(arguments, dict):
        raise ToolValidationError("工具参数必须是 JSON 对象")
    properties = schema.get("properties") or {}
    required = schema.get("required") or []
    missing = [name for name in required if name not in arguments]
    if missing:
        raise ToolValidationError(f"缺少必填参数：{', '.join(missing)}")
    if schema.get("additionalProperties") is False:
        extras = [name for name in arguments if name not in properties]
        if extras:
            raise ToolValidationError(f"未知参数：{', '.join(extras)}")
    for name, value in arguments.items():
        rule = properties.get(name)
        if not isinstance(rule, dict):
            continue
        expected = rule.get("type")
        matches = {
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
            "null": value is None,
        }.get(expected, True)
        if not matches:
            raise ToolValidationError(f"参数 {name} 类型应为 {expected}")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in rule and value < rule["minimum"]:
                raise ToolValidationError(f"参数 {name} 小于最小值 {rule['minimum']}")
            if "maximum" in rule and value > rule["maximum"]:
                raise ToolValidationError(f"参数 {name} 大于最大值 {rule['maximum']}")


def _invoke(function: Callable[..., Any], arguments: dict[str, Any], context: dict[str, Any]) -> Any:
    signature = inspect.signature(function)
    kwargs = dict(arguments)
    if "context" in signature.parameters:
        kwargs["context"] = context
    result = function(**kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def execute_tool(
    tool: ToolDefinition,
    arguments: dict[str, Any],
    *,
    context: dict[str, Any],
    timeout: float,
    cancel_event: threading.Event | None = None,
) -> Any:
    validate_arguments(tool.input_schema, arguments)
    if cancel_event is not None and cancel_event.is_set():
        raise ToolError("工具调用已取消")
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"tool-{tool.name}")
    future = executor.submit(_invoke, tool.load_callable(), arguments, context)
    try:
        return future.result(timeout=timeout)
    except FutureTimeout as exc:
        future.cancel()
        raise ToolTimeoutError(f"工具 {tool.name} 执行超时（{timeout:g}s）") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
