"""工具发现、清单验证和执行。"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import math
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from plugins.manifest import PluginManifest, PluginManifestError, discover_plugin_manifests
from run.tools.execution_watchdog import (
    ExecutionCapacityError,
    abandon_execution,
    attach_execution,
    release_execution,
    reserve_execution,
)
from run.infra import start_isolated_tool
from run.config import MainAgentSourcePolicy


class ToolError(RuntimeError):
    pass


class ToolValidationError(ToolError):
    pass


class ToolTimeoutError(ToolError):
    category = "timeout"
    retryable = False

    def __init__(self, message: str, *, still_running: bool = False) -> None:
        super().__init__(message)
        self.still_running = bool(still_running)


class ToolExecutionCapacityError(ToolError):
    category = "execution_capacity"
    retryable = False


class ToolProcessError(ToolError):
    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(str(detail.get("message") or "隔离工具执行失败"))
        self.remote_exception_type = str(
            detail.get("exception_type") or "ToolProcessError"
        )
        for field in (
            "category",
            "status_code",
            "retryable",
            "retry_after_ms",
            "attempt_count",
        ):
            value = detail.get(field)
            if value is not None:
                setattr(self, field, value)


class ToolCancelledError(ToolError):
    """The user explicitly cancelled the run while a tool was executing."""


class ToolResultTooLargeError(ToolError):
    """A tool completed, but its inline result is unsafe to place in context."""

    category = "result_too_large"
    retryable = False
    counts_as_failure = False

    def __init__(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        result_chars: int,
        limit_chars: int,
    ) -> None:
        self.tool_name = str(tool_name or "unknown_tool")
        self.result_chars = max(0, int(result_chars))
        self.limit_chars = max(1, int(limit_chars))
        self.action = str(arguments.get("action") or "").strip()
        self.path = str(arguments.get("path") or "").strip()[:512]
        self.instruction = _oversized_result_instruction(
            self.tool_name,
            self.action,
        )
        super().__init__(
            f"工具 {self.tool_name} 返回约 {self.result_chars} 字符，"
            f"超过 {self.limit_chars} 字符上限；正文未返回"
        )

    def error_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "message": str(self),
            "exception_type": type(self).__name__,
            "category": self.category,
            "retryable": self.retryable,
            "result_chars": self.result_chars,
            "limit_chars": self.limit_chars,
            "content_omitted": True,
            "instruction": self.instruction,
        }
        if self.action:
            payload["action"] = self.action
        if self.path:
            payload["path"] = self.path
        return payload


_TIMEOUT_POLICIES = frozenset({"argument_or_default", "agent_runtime"})
_TOOL_TIMEOUT_CLEANUP_GRACE = 1.0
_TOOL_CANCEL_CLEANUP_GRACE = 0.1
_AGENT_TOOL_WATCHDOG_GRACE = 5.0
MAX_TOOL_RESULT_CHARS = 100_000


def _oversized_result_instruction(tool_name: str, action: str) -> str:
    if tool_name == "file" and action == "read":
        return (
            "请先使用 file.stat 查看文件大小，再使用 file.read_range，并通过 "
            "start_line/end_line 或 tail 分段读取"
        )
    if tool_name == "file" and action == "read_range":
        return (
            "请缩小 file.read_range 的 start_line/end_line、max_lines 或 tail 范围后重试"
        )
    if tool_name == "file":
        return (
            "请先使用 file.stat 检查目标，再使用 file.read_range 并缩小 "
            "start_line/end_line、max_lines 或 tail 范围"
        )
    if tool_name == "shell":
        return "请缩小命令输出范围，或将完整输出写入文件后使用 file.read_range 分段读取"
    return (
        "请缩小查询或读取范围、降低 limit/max_results，"
        "或将完整结果保存为文件后分段读取"
    )


def _enforce_tool_result_limit(
    tool_name: str,
    arguments: dict[str, Any],
    value: Any,
) -> Any:
    rendered = json.dumps(value, ensure_ascii=False, default=str)
    if len(rendered) > MAX_TOOL_RESULT_CHARS:
        raise ToolResultTooLargeError(
            tool_name,
            arguments,
            result_chars=len(rendered),
            limit_chars=MAX_TOOL_RESULT_CHARS,
        )
    return value


def tool_call_signature(name: str, arguments: dict[str, Any]) -> str:
    """Return a stable identity for one tool name and its complete arguments."""

    normalized = json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{name}:{normalized}"


@dataclass(slots=True)
class ConsecutiveIdenticalToolCallTracker:
    """Track uninterrupted requests with the same tool name and arguments."""

    limit: int
    signature: str = ""
    count: int = 0

    def __post_init__(self) -> None:
        if (
            isinstance(self.limit, bool)
            or not isinstance(self.limit, int)
            or self.limit < 1
        ):
            raise ValueError("tools.consecutive_identical_call_limit 必须是正整数")

    def record(self, name: str, arguments: dict[str, Any]) -> int:
        signature = tool_call_signature(name, arguments)
        if signature == self.signature:
            self.count += 1
        else:
            self.signature = signature
            self.count = 1
        return self.count

    def is_blocked(self, count: int) -> bool:
        return count > self.limit


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
    strict: bool = False
    timeout_policy: str = "argument_or_default"
    timeout_grace_seconds: float = 0.0
    execution_mode: str = "thread"
    overrides: list[str] = field(default_factory=list)
    _callable: Callable[..., Any] | None = field(default=None, repr=False)

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
                "strict": self.strict,
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
        strict=bool(raw.get("strict", False)),
        timeout_policy=str(raw.get("timeout_policy") or "argument_or_default"),
        timeout_grace_seconds=float(raw.get("timeout_grace_seconds") or 0),
        execution_mode=str(raw.get("execution_mode") or "process"),
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


def _positive_timeout(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise ToolValidationError(f"{field} 必须是正数")
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise ToolValidationError(f"{field} 必须是正数") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise ToolValidationError(f"{field} 必须是正数")
    return timeout


def resolve_tool_timeout(
    tool: ToolDefinition,
    arguments: dict[str, Any],
    *,
    default_timeout: float,
    context: dict[str, Any] | None = None,
) -> float:
    """Resolve one tool watchdog deadline from its declared runtime policy."""

    default = _positive_timeout(default_timeout, field="tools.timeout")
    if tool.timeout_policy not in _TIMEOUT_POLICIES:
        raise ToolValidationError(
            f"工具 {tool.name} timeout_policy 无效：{tool.timeout_policy}"
        )
    if tool.timeout_policy == "agent_runtime":
        runtime_timeout = (context or {}).get("agent_timeout", default)
        return _positive_timeout(
            runtime_timeout,
            field="agent_runtime.default_timeout",
        ) + _AGENT_TOOL_WATCHDOG_GRACE

    properties = tool.input_schema.get("properties") or {}
    timeout_rule = properties.get("timeout")
    if "timeout" in arguments and isinstance(timeout_rule, dict):
        requested = _positive_timeout(arguments["timeout"], field="参数 timeout")
        grace = float(tool.timeout_grace_seconds)
        if not math.isfinite(grace) or grace < 0 or grace > 30:
            raise ToolValidationError(
                f"工具 {tool.name} timeout_grace_seconds 必须在 0..30 秒之间"
            )
        return requested + grace
    return default


def _invoke(function: Callable[..., Any], arguments: dict[str, Any], context: dict[str, Any]) -> Any:
    signature = inspect.signature(function)
    kwargs = dict(arguments)
    if "context" in signature.parameters:
        kwargs["context"] = context
    result = function(**kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def _execute_tool_process(
    tool: ToolDefinition,
    arguments: dict[str, Any],
    *,
    context: dict[str, Any],
    effective_timeout: float,
    cancel_event: threading.Event | None,
) -> Any:
    file_name, separator, function_name = tool.entrypoint.partition(":")
    if not separator or not file_name or not function_name:
        raise ToolError(f"工具 {tool.name} entrypoint 无效：{tool.entrypoint}")
    try:
        call = start_isolated_tool(
            module_path=tool.directory / file_name,
            function_name=function_name,
            arguments=arguments,
            context={**context, "tool_timeout": effective_timeout},
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise ToolError(f"工具 {tool.name} 隔离进程启动失败：{exc}") from exc
    deadline = time.monotonic() + effective_timeout
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                call.request_cancel()
                if not call.wait(_TOOL_CANCEL_CLEANUP_GRACE):
                    call.terminate()
                raise ToolCancelledError("工具调用因用户紧急停止而取消")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                call.request_cancel()
                if not call.wait(_TOOL_TIMEOUT_CLEANUP_GRACE):
                    call.terminate()
                raise ToolTimeoutError(
                    f"工具 {tool.name} 执行超时（{effective_timeout:g}s）；隔离进程已终止",
                    still_running=False,
                )
            payload = call.receive(min(0.05, remaining))
            if payload is None:
                if not call.process.is_alive():
                    payload = call.receive(0)
                    if payload is None:
                        raise ToolProcessError({
                            "message": f"工具 {tool.name} 隔离进程异常退出",
                            "exception_type": "ToolProcessExited",
                        })
                else:
                    continue
            call.wait(1)
            if payload.get("ok") is True:
                return _enforce_tool_result_limit(
                    tool.name, arguments, payload.get("value")
                )
            detail = payload.get("error")
            raise ToolProcessError(detail if isinstance(detail, dict) else {})
    finally:
        if call.process.is_alive():
            call.terminate()
        call.close()


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
        raise ToolCancelledError("工具调用因用户紧急停止而取消")
    effective_timeout = resolve_tool_timeout(
        tool,
        arguments,
        default_timeout=timeout,
        context=context,
    )
    if tool.execution_mode == "process" and tool._callable is None:
        return _execute_tool_process(
            tool,
            arguments,
            context=context,
            effective_timeout=effective_timeout,
            cancel_event=cancel_event,
        )
    tool_cancel_event = threading.Event()
    invocation_context = dict(context)
    invocation_context["tool_timeout"] = effective_timeout
    invocation_context["cancel_event"] = tool_cancel_event
    try:
        execution_id = reserve_execution(
            f"tool:{context.get('root', '')}:{context.get('user', '')}:{tool.name}"
        )
    except ExecutionCapacityError as exc:
        raise ToolExecutionCapacityError(str(exc)) from exc
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"tool-{tool.name}")
    try:
        future = executor.submit(
            _invoke, tool.load_callable(), arguments, invocation_context
        )
        attach_execution(execution_id, future, executor)
    except BaseException:
        release_execution(execution_id)
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    deadline = time.monotonic() + effective_timeout
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                tool_cancel_event.set()
                future.cancel()
                cleanup_deadline = time.monotonic() + _TOOL_CANCEL_CLEANUP_GRACE
                while not future.done() and time.monotonic() < cleanup_deadline:
                    time.sleep(0.05)
                abandon_execution(execution_id)
                raise ToolCancelledError("工具调用因用户紧急停止而取消")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                tool_cancel_event.set()
                future.cancel()
                cleanup_deadline = time.monotonic() + _TOOL_TIMEOUT_CLEANUP_GRACE
                while not future.done():
                    cleanup_remaining = cleanup_deadline - time.monotonic()
                    if cleanup_remaining <= 0:
                        break
                    try:
                        future.result(timeout=min(0.05, cleanup_remaining))
                    except FutureTimeout:
                        continue
                    except BaseException:
                        break
                still_running = abandon_execution(execution_id)
                state = "；执行线程仍在后台退出中，禁止立即重试" if still_running else ""
                raise ToolTimeoutError(
                    f"工具 {tool.name} 执行超时（{effective_timeout:g}s）{state}",
                    still_running=still_running,
                )
            try:
                value = future.result(timeout=min(0.1, remaining))
                return _enforce_tool_result_limit(tool.name, arguments, value)
            except FutureTimeout:
                continue
    finally:
        if future.done():
            release_execution(execution_id)


_DOMAIN_MODULES = ("execution_watchdog", "provider_tool_recovery")


def __getattr__(name: str):
    from importlib import import_module

    for module_name in _DOMAIN_MODULES:
        module = import_module(f"run.tools.{module_name}")
        if hasattr(module, name):
            return getattr(module, name)
    raise AttributeError(name)
