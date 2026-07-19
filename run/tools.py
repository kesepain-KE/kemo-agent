"""工具发现、清单验证和执行。"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import os
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from plugins.manifest import PluginManifest, PluginManifestError, discover_plugin_manifests
from run.source_policy import MainAgentSourcePolicy


class ToolError(RuntimeError):
    pass


class ToolValidationError(ToolError):
    pass


class ToolTimeoutError(ToolError):
    pass


@dataclass(slots=True)
class ConsecutiveToolFailureTracker:
    """Track only an uninterrupted failure streak for the same tool."""

    limit: int
    tool_name: str = ""
    count: int = 0
    unavailable: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if isinstance(self.limit, bool) or self.limit < 1:
            raise ValueError("consecutive_tool_fail_limit 必须是正整数")

    def is_unavailable(self, name: str) -> bool:
        return name in self.unavailable

    def record(self, name: str, *, succeeded: bool) -> int:
        if succeeded:
            self.tool_name = ""
            self.count = 0
            return 0
        if name == self.tool_name:
            self.count += 1
        else:
            self.tool_name = name
            self.count = 1
        if self.count >= self.limit:
            self.unavailable.add(name)
        return self.count


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
    plugin_manifests: tuple[PluginManifest, ...] = ()

    def enabled_tools(self, *, exclude: set[str] | None = None) -> list[ToolDefinition]:
        blocked = exclude or set()
        return [
            tool
            for tool in self.tools.values()
            if tool.enabled and tool.name not in blocked
        ]

    def schemas(self, *, exclude: set[str] | None = None) -> list[dict[str, Any]]:
        return [tool.openai_schema() for tool in self.enabled_tools(exclude=exclude)]

    def selected(self, names: set[str]) -> "ToolRegistry":
        selected = {
            name: tool
            for name, tool in self.tools.items()
            if name in names and tool.enabled
        }
        manifests = tuple(
            manifest
            for manifest in self.plugin_manifests
            if str(manifest.tool.get("name") or "") in selected
        )
        return ToolRegistry(selected, manifests)

    def get(self, name: str) -> ToolDefinition:
        tool = self.tools.get(name)
        if tool is None:
            raise ToolError(f"未知工具：{name}")
        if not tool.enabled:
            raise ToolError(f"工具已禁用：{name}")
        return tool


def _definition(manifest: PluginManifest) -> ToolDefinition:
    raw = manifest.tool
    return ToolDefinition(
        name=str(raw["name"]),
        description=str(raw["description"]),
        input_schema=raw["input_schema"],
        version=str(raw["version"]),
        enabled=bool(raw["enabled"]),
        entrypoint=str(raw["entrypoint"]),
        source="plugins",
        directory=manifest.descriptor.path.parent,
    )


def discover_tools(root: Path, user: str) -> ToolRegistry:
    """Discover executable tools exclusively from plugins/*/SKILL.md."""

    del user  # The executable-tool surface is intentionally user-independent.
    try:
        manifests = discover_plugin_manifests(root)
    except PluginManifestError as exc:
        raise ToolError(str(exc)) from exc
    tools = {manifest.tool["name"]: _definition(manifest) for manifest in manifests}
    return ToolRegistry(tools, manifests)


def apply_runtime_tool_policy(
    registry: ToolRegistry, config: dict[str, Any]
) -> ToolRegistry:
    """Apply global runtime switches that control individual registered tools."""

    source_policy = MainAgentSourcePolicy.from_config(config)
    memory = config.get("memory") or {}
    if not isinstance(memory, dict):
        raise ToolError("memory 配置必须是对象")
    history_read_enabled = memory.get("history_read_enabled", True)
    if not isinstance(history_read_enabled, bool):
        raise ToolError("memory.history_read_enabled 必须是布尔值")
    names = set(registry.tools)
    if not source_policy.plugins.unrestricted:
        names &= set(source_policy.plugins.names)
    if not os.getenv("TAVILY_API_KEY", "").strip():
        names.discard("web_search")
    if not history_read_enabled:
        names.discard("history_search")
    return registry.selected(names)


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
