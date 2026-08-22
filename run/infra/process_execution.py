"""Spawn-isolated execution helpers for terminating uncooperative plugin code."""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import multiprocessing
import os
import pickle
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any


_PROCESS_CONTEXT = multiprocessing.get_context("spawn")


def _load_callable(module_path: str, function_name: str):
    path = Path(module_path).resolve()
    module_name = f"kemo_isolated_tool_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载隔离工具模块：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    function = getattr(module, function_name, None)
    if not callable(function):
        raise RuntimeError(f"隔离工具入口不可调用：{path.name}:{function_name}")
    return function


def _safe_error(exc: BaseException) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "message": str(exc),
        "exception_type": type(exc).__name__,
    }
    for field in (
        "category",
        "status_code",
        "retryable",
        "retry_after_ms",
        "attempt_count",
    ):
        value = getattr(exc, field, None)
        if isinstance(value, (bool, int, float)):
            detail[field] = value
        elif isinstance(value, str) and value.strip():
            detail[field] = value.strip()[:160]
    return detail


def _isolated_worker(
    module_path: str,
    function_name: str,
    arguments: dict[str, Any],
    context: dict[str, Any],
    cancel_event: Any,
    connection: Connection,
) -> None:
    if os.name != "nt":
        try:
            os.setsid()
        except OSError:
            pass
    try:
        function = _load_callable(module_path, function_name)
        invocation_context = dict(context)
        invocation_context["cancel_event"] = cancel_event
        kwargs = dict(arguments)
        if "context" in inspect.signature(function).parameters:
            kwargs["context"] = invocation_context
        value = function(**kwargs)
        if inspect.isawaitable(value):
            value = asyncio.run(value)
        payload = {"ok": True, "value": value}
    except BaseException as exc:
        payload = {"ok": False, "error": _safe_error(exc)}
    try:
        connection.send(payload)
    except BaseException as exc:
        try:
            connection.send({"ok": False, "error": _safe_error(exc)})
        except BaseException:
            pass
    finally:
        connection.close()


@dataclass(slots=True)
class IsolatedProcessCall:
    process: multiprocessing.Process
    connection: Connection
    cancel_event: Any

    def receive(self, timeout: float) -> dict[str, Any] | None:
        if not self.connection.poll(max(0.0, timeout)):
            return None
        try:
            value = self.connection.recv()
        except EOFError:
            return {
                "ok": False,
                "error": {
                    "message": "隔离执行通道在返回结果前关闭",
                    "exception_type": "IsolatedProcessDisconnected",
                },
            }
        return value if isinstance(value, dict) else {
            "ok": False,
            "error": {
                "message": "隔离执行返回了无效结果",
                "exception_type": "InvalidIsolatedResult",
            },
        }

    def request_cancel(self) -> None:
        self.cancel_event.set()

    def wait(self, timeout: float) -> bool:
        self.process.join(max(0.0, timeout))
        return not self.process.is_alive()

    def terminate(self) -> None:
        if not self.process.is_alive():
            self.process.join(timeout=0)
            return
        pid = self.process.pid
        if pid and os.name == "nt":
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creation_flags,
                    timeout=5,
                )
            except (OSError, subprocess.SubprocessError):
                pass
        elif pid:
            try:
                os.killpg(pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
        if self.process.is_alive():
            self.process.terminate()
        self.process.join(timeout=1)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(timeout=1)

    def close(self) -> None:
        self.connection.close()
        if not self.process.is_alive():
            try:
                self.process.close()
            except ValueError:
                pass


def start_isolated_tool(
    *,
    module_path: Path,
    function_name: str,
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> IsolatedProcessCall:
    safe_context = {
        key: value
        for key, value in context.items()
        if key not in {"cancel_event", "transport_registry"}
    }
    try:
        pickle.dumps((arguments, safe_context))
    except (pickle.PickleError, TypeError, AttributeError) as exc:
        raise ValueError("工具上下文不能传入隔离进程；请声明 execution_mode=thread") from exc
    parent, child = _PROCESS_CONTEXT.Pipe(duplex=False)
    cancel_event = _PROCESS_CONTEXT.Event()
    process = _PROCESS_CONTEXT.Process(
        target=_isolated_worker,
        args=(
            str(module_path.resolve()),
            function_name,
            dict(arguments),
            safe_context,
            cancel_event,
            child,
        ),
        name=f"kemo-tool-{module_path.parent.name}",
    )
    try:
        process.start()
    except BaseException:
        parent.close()
        child.close()
        raise
    child.close()
    return IsolatedProcessCall(process, parent, cancel_event)
