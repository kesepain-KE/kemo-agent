"""Spawn-isolated execution helpers for terminating uncooperative plugin code."""

from __future__ import annotations

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
from multiprocessing.connection import _ConnectionBase
from multiprocessing.connection import wait as wait_for_connections
from multiprocessing.context import SpawnProcess
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Any, Callable

from run.infra.async_utils import run_awaitable
from run.infra.runtime_diagnostics import diagnostic_text


def _windows_pythonw_executable() -> str | None:
    """Return the sibling ``pythonw.exe`` used to hide spawn workers."""

    if os.name != "nt":
        return None
    candidate = Path(sys.executable).with_name("pythonw.exe")
    try:
        return str(candidate.resolve()) if candidate.is_file() else None
    except OSError:
        return None


def _configure_isolated_process_context(context: Any) -> str | None:
    """Use a console-less interpreter for Windows spawn workers when present."""

    executable = _windows_pythonw_executable()
    if executable:
        context.set_executable(executable)
    return executable


_PROCESS_CONTEXT = multiprocessing.get_context("spawn")
# ``multiprocessing``'s Windows spawn implementation otherwise starts
# ``python.exe`` with no creation flags.  A console-less interpreter keeps
# every framework-managed process-mode plugin from flashing a console.
_ISOLATED_EXECUTABLE = _configure_isolated_process_context(_PROCESS_CONTEXT)


class _ShellGroupProcess(SpawnProcess):
    @staticmethod
    def _Popen(process_obj):
        if os.name != "posix":
            raise NotImplementedError("前台 Shell 进程组仅支持 POSIX")
        from multiprocessing.popen_spawn_posix import Popen  # pyright: ignore[reportAttributeAccessIssue]

        class PinnedPopen(Popen):
            reap_allowed = False

            def poll(self, flag=os.WNOHANG):  # pyright: ignore[reportAttributeAccessIssue]
                # CPython also calls poll() from _cleanup() when another
                # process starts. Keep our PID reserved until group cleanup.
                return super().poll(flag) if self.reap_allowed else None

            def wait(self, timeout=None):
                if not self.reap_allowed:
                    wait_for_connections([self.sentinel], timeout)
                    return None
                return super().wait(timeout)

        return PinnedPopen(process_obj)

    def release_pid(self) -> None:
        self._popen.reap_allowed = True  # pyright: ignore[reportAttributeAccessIssue]


def _ensure_worker_stdio() -> None:
    """Give console-less Windows workers safe discard streams for plugin code."""

    for name, mode in (("stdin", "r"), ("stdout", "w"), ("stderr", "w")):
        if getattr(sys, name, None) is not None:
            continue
        try:
            stream = open(os.devnull, mode, encoding="utf-8", errors="replace")
        except OSError:
            continue
        setattr(sys, name, stream)


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
        "message": diagnostic_text(exc),
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
            detail[field] = diagnostic_text(value.strip())[:160]
    for field in ("path", "url"):
        value = getattr(exc, field, None)
        if isinstance(value, str) and value.strip():
            detail[field] = diagnostic_text(value.partition("?")[0])[:512]
    return detail


def _isolated_worker(
    module_path: str,
    function_name: str,
    arguments: dict[str, Any],
    context: dict[str, Any],
    cancel_event: Any,
    connection: _ConnectionBase,
    own_shell_group: bool,
    report_shell_command_started: bool = False,
) -> None:
    _ensure_worker_stdio()
    try:
        if os.name != "nt":
            try:
                os.setsid()
            except OSError as exc:
                if own_shell_group:
                    raise RuntimeError("前台 Shell 无法建立独立进程组（setsid）") from exc
            else:
                if own_shell_group:
                    # Report ownership before any plugin code can start a command.
                    connection.send({"shell_group_ready": os.getpid()})
        function = _load_callable(module_path, function_name)
        invocation_context = dict(context)
        invocation_context["cancel_event"] = cancel_event
        invocation_context["_tool_process_isolated"] = True
        invocation_context["_shell_owner_group"] = own_shell_group
        if report_shell_command_started:
            invocation_context["_report_shell_command_started"] = connection.send
        kwargs = dict(arguments)
        if "context" in inspect.signature(function).parameters:
            kwargs["context"] = invocation_context
        value = function(**kwargs)
        if inspect.isawaitable(value):
            value = run_awaitable(value)
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
    process: BaseProcess
    connection: _ConnectionBase
    cancel_event: Any
    _shell_group: int | None = None
    _on_shell_command_started: Callable[[dict[str, Any]], None] | None = None

    def receive(self, timeout: float) -> dict[str, Any] | None:
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            # Reclassify every frame without restarting the caller's wait budget.
            value = self._receive_payload(deadline - time.monotonic())
            if self._accept_group_notice(value):
                continue
            if value is None or value.get("kind") != "shell_command_started":
                return value
            if self._on_shell_command_started is not None:
                self._on_shell_command_started(value)

    def _accept_group_notice(self, value: dict[str, Any] | None) -> bool:
        if (isinstance(self.process, _ShellGroupProcess) and value is not None
                and value.get("shell_group_ready") == self.process.pid):
            self._shell_group = self.process.pid
            return True
        return False

    def _receive_payload(self, timeout: float) -> dict[str, Any] | None:
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

    def is_alive(self) -> bool:
        if isinstance(self.process, _ShellGroupProcess):
            return not self.wait(0)
        return self.process.is_alive()

    def wait(self, timeout: float) -> bool:
        if isinstance(self.process, _ShellGroupProcess):
            timeout = max(0.0, timeout)
            if not hasattr(os, "waitid"):
                return bool(wait_for_connections([self.process.sentinel], timeout))
            # A concurrent fork can inherit the sentinel writer, and EOF
            # alone can arrive before the kernel marks the child waitable.
            deadline = time.monotonic() + timeout
            flags = os.WEXITED | os.WNOHANG | os.WNOWAIT  # pyright: ignore[reportAttributeAccessIssue]
            while os.waitid(os.P_PID, self.process.pid, flags) is None:  # pyright: ignore[reportAttributeAccessIssue]
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                time.sleep(min(0.005, remaining))
            return True
        self.process.join(max(0.0, timeout))
        return not self.process.is_alive()

    def terminate(self) -> None:
        if isinstance(self.process, _ShellGroupProcess):
            self._terminate_shell_group()
            return
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
                getattr(os, "killpg")(pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
        if self.process.is_alive():
            self.process.terminate()
        self.process.join(timeout=1)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(timeout=1)

    def _terminate_shell_group(self) -> None:
        if self._shell_group is None:
            self._accept_group_notice(self._receive_payload(0))
        if self._shell_group is None:
            if self.is_alive():
                self.process.kill()
                self.wait(1)
            # Cancellation can race with startup. A command can only have
            # started if its worker sent the group notice before it exited.
            self._accept_group_notice(self._receive_payload(0))
        pid = self._shell_group
        if pid is None:
            return
        try:
            os.killpg(pid, signal.SIGTERM)  # pyright: ignore[reportAttributeAccessIssue]
            self.wait(1)
            # The worker may already be dead while same-group descendants
            # ignore SIGTERM. Escalate before permitting any waitpid().
            os.killpg(pid, signal.SIGKILL)  # pyright: ignore[reportAttributeAccessIssue]
        except ProcessLookupError:
            pass
        self.wait(1)

    def close(self) -> None:
        self.connection.close()
        if isinstance(self.process, _ShellGroupProcess):
            self._shell_group = None
            self.process.release_pid()
            if self.process.is_alive():
                self.process.join(timeout=1)
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
    own_shell_group: bool = False,
    on_shell_command_started: Callable[[dict[str, Any]], None] | None = None,
) -> IsolatedProcessCall:
    own_shell_group = own_shell_group and os.name == "posix"
    # A SIGCHLD handler or SIG_IGN can bypass PinnedPopen and reap the PID.
    if own_shell_group and signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:  # pyright: ignore[reportAttributeAccessIssue]
        raise RuntimeError("前台 Shell 需要宿主保留隔离进程的回收权")
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
    process_type = _ShellGroupProcess if own_shell_group else _PROCESS_CONTEXT.Process
    process = process_type(
        target=_isolated_worker,
        args=(
            str(module_path.resolve()),
            function_name,
            dict(arguments),
            safe_context,
            cancel_event,
            child,
            own_shell_group,
            on_shell_command_started is not None,
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
    return IsolatedProcessCall(
        process, parent, cancel_event, _on_shell_command_started=on_shell_command_started,
    )
