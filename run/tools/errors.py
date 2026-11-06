"""Structured failures raised by tool validation and execution boundaries."""

from __future__ import annotations

from typing import Any


class ToolError(RuntimeError):
    pass


class ToolValidationError(ToolError):
    pass


class ToolTimeoutError(ToolError):
    category = "timeout"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        still_running: bool = False,
        completion_future: Any = None,
    ) -> None:
        super().__init__(message)
        self.still_running = bool(still_running)
        self.completion_future = completion_future


class ToolExecutionCapacityError(ToolError):
    category = "execution_capacity"
    retryable = False


class ToolProcessError(ToolError):
    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__(str(detail.get("message") or "隔离工具执行失败"))
        self.remote_exception_type = str(
            detail.get("exception_type") or "ToolProcessError"
        )
        for detail_field in (
            "category",
            "status_code",
            "retryable",
            "retry_after_ms",
            "attempt_count",
        ):
            value = detail.get(detail_field)
            if value is not None:
                setattr(self, detail_field, value)


class ToolCancelledError(ToolError):
    """The user explicitly cancelled the run while a tool was executing."""

    def __init__(self, message: str, *, completion_future: Any = None) -> None:
        super().__init__(message)
        self.completion_future = completion_future
        self.still_running = completion_future is not None and not completion_future.done()


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
        self.tool_name = str(tool_name or "unknown_tool").strip()[:128] or "unknown_tool"
        self.result_chars = max(0, int(result_chars))
        self.limit_chars = max(1, int(limit_chars))
        self.action = str(arguments.get("action") or "").strip()[:256]
        self.path = str(arguments.get("path") or "").strip()[:512]
        self.instruction = _oversized_result_instruction(self.tool_name, self.action)
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
            "execution_completed": True,
            "instruction": "执行已经完成，先核对副作用，禁止原样重试。 " + self.instruction,
        }
        if self.action:
            payload["action"] = self.action
        if self.path:
            payload["path"] = self.path
        return payload
