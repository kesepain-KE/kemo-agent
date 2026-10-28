from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path

import pytest

from run.tools import (
    ToolCancelledError,
    ToolDefinition,
    ToolProcessError,
    ToolTimeoutError,
    execute_tool,
)


PLUGIN_SOURCE = """
import asyncio
import threading
import types
from pathlib import Path

calls = 0
awaits = 0

def result(value, context):
    return {
        "value": value,
        "marker": context["marker"],
        "has_cancel_event": context["cancel_event"] is not None,
        "calls": calls,
        "awaits": awaits,
    }

def sync_entry(value, *, context):
    global calls
    calls += 1
    return result(value, context)

async def native_entry(value, *, context):
    global calls, awaits
    calls += 1
    awaits += 1
    await asyncio.sleep(0)
    return result(value, context)

class CustomAwaitable:
    def __init__(self, value, context):
        self.value = value
        self.context = context

    def __await__(self):
        async def work():
            global awaits
            awaits += 1
            await asyncio.sleep(0)
            if self.value == "error":
                raise ValueError("awaitable failed")
            if self.value == "cancel":
                raise asyncio.CancelledError("awaitable cancelled")
            if self.value == "wait":
                ready = Path(self.context["ready"])
                ready.write_text("ready", encoding="utf-8")
                try:
                    while not self.context["cancel_event"].is_set():
                        await asyncio.sleep(0.01)
                finally:
                    Path(self.context["cleaned"]).write_text("cleaned", encoding="utf-8")
            return result(self.value, self.context)
        return work().__await__()

def custom_entry(value, *, context):
    global calls
    calls += 1
    return CustomAwaitable(value, context)

@types.coroutine
def generator_entry(value, *, context):
    global calls, awaits
    calls += 1
    awaits += 1
    yield from asyncio.sleep(0).__await__()
    return result(value, context)

def no_context_entry(value):
    return CustomAwaitable(value, {
        "marker": "no-context",
        "cancel_event": threading.Event(),
    })
"""


@pytest.fixture(params=["thread", "process"])
def mode(request: pytest.FixtureRequest) -> str:
    return request.param


def make_tool(tmp_path: Path, mode: str, entrypoint: str) -> ToolDefinition:
    (tmp_path / "tool.py").write_text(PLUGIN_SOURCE, encoding="utf-8")
    return ToolDefinition(
        name="awaitable_test",
        description="Awaitable execution regression",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        version="1.0.0",
        enabled=True,
        entrypoint=f"tool.py:{entrypoint}",
        source="test",
        directory=tmp_path,
        execution_mode=mode,
    )


@pytest.mark.parametrize(
    "entrypoint", ["sync_entry", "native_entry", "custom_entry", "generator_entry"]
)
def test_tool_resolves_result_once(
    tmp_path: Path, mode: str, entrypoint: str
) -> None:
    result = execute_tool(
        make_tool(tmp_path, mode, entrypoint),
        {"value": "payload"},
        context={"marker": "context-preserved"},
        timeout=10,
    )
    assert result == {
        "value": "payload",
        "marker": "context-preserved",
        "has_cancel_event": True,
        "calls": 1,
        "awaits": 0 if entrypoint == "sync_entry" else 1,
    }


def test_tool_without_context_parameter_can_return_awaitable(
    tmp_path: Path, mode: str
) -> None:
    result = execute_tool(
        make_tool(tmp_path, mode, "no_context_entry"),
        {"value": "payload"},
        context={},
        timeout=10,
    )
    assert result["value"] == "payload"
    assert result["marker"] == "no-context"
    assert result["awaits"] == 1


@pytest.mark.parametrize("value", ["error", "cancel"])
def test_awaitable_exceptions_keep_execution_boundary(
    tmp_path: Path, mode: str, value: str
) -> None:
    expected = ValueError if value == "error" else asyncio.CancelledError
    with pytest.raises(ToolProcessError if mode == "process" else expected) as raised:
        execute_tool(
            make_tool(tmp_path, mode, "custom_entry"),
            {"value": value},
            context={"marker": "test"},
            timeout=10,
        )
    message = str(raised.value)
    if value == "error":
        assert message == "awaitable failed"
    elif sys.version_info >= (3, 11):
        assert message == "awaitable cancelled"
    else:
        # CPython 3.10 的 asyncio.run 会把任务内自抛的 CancelledError 重新构造成
        # 「取消」，消息随之丢失，3.11 起才原样保留。这里只放宽消息文本，
        # 取消语义本身仍由异常类型与 remote_exception_type 校验。
        assert message in {"", "隔离工具执行失败"}
    if isinstance(raised.value, ToolProcessError):
        assert raised.value.remote_exception_type == expected.__name__


def test_custom_awaitable_observes_timeout_cleanup(tmp_path: Path, mode: str) -> None:
    ready, cleaned = tmp_path / "ready", tmp_path / "cleaned"
    with pytest.raises(ToolTimeoutError) as raised:
        execute_tool(
            make_tool(tmp_path, mode, "custom_entry"),
            {"value": "wait"},
            context={"marker": "test", "ready": str(ready), "cleaned": str(cleaned)},
            timeout=2,
        )
    assert not raised.value.still_running
    assert ready.read_text(encoding="utf-8") == "ready"
    assert cleaned.read_text(encoding="utf-8") == "cleaned"


def test_custom_awaitable_observes_user_cancel(tmp_path: Path, mode: str) -> None:
    ready, cleaned = tmp_path / "ready", tmp_path / "cleaned"
    cancel_event = threading.Event()
    stop = threading.Event()

    def cancel_when_started() -> None:
        deadline = time.monotonic() + 10
        while not stop.wait(0.01):
            if ready.exists() or time.monotonic() >= deadline:
                cancel_event.set()
                return

    controller = threading.Thread(target=cancel_when_started)
    controller.start()
    try:
        with pytest.raises(ToolCancelledError):
            execute_tool(
                make_tool(tmp_path, mode, "custom_entry"),
                {"value": "wait"},
                context={"marker": "test", "ready": str(ready), "cleaned": str(cleaned)},
                timeout=15,
                cancel_event=cancel_event,
            )
        assert ready.read_text(encoding="utf-8") == "ready"
    finally:
        stop.set()
        cancel_event.set()
        controller.join(timeout=2)


def test_runner_does_not_migrate_foreign_pending_future() -> None:
    from run.infra import run_awaitable

    loop = asyncio.new_event_loop()
    pending = loop.create_future()
    try:
        with pytest.raises(RuntimeError, match="different loop"):
            run_awaitable(pending)
    finally:
        pending.cancel()
        loop.close()
