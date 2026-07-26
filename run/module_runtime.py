"""Bounded, cancellable execution for untrusted directory-owned modules."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from run.process_utils import cancellable_subprocess_kwargs, terminate_process_tree


MODULE_UPDATE_RESULT_PREFIX = "__KEMO_MODULE_UPDATE_RESULT__="
_MAX_CAPTURE_BYTES = 1_000_000
_POLL_SECONDS = 0.05
DEFAULT_MODULE_UPDATE_TIMEOUT = 120.0
_EXECUTION_LOCK_FILE = ".module.execution.lock"
_EXECUTION_LOCKS: dict[str, threading.RLock] = {}
_EXECUTION_LOCKS_GUARD = threading.Lock()


MODULE_UPDATE_RUNNER = r'''
from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import sys
from pathlib import Path

PREFIX = "__KEMO_MODULE_UPDATE_RESULT__="

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def emit(payload: dict, *, stream) -> None:
    print(
        PREFIX + json.dumps(payload, ensure_ascii=False, default=str),
        file=stream,
        flush=True,
    )


try:
    update_path = Path(sys.argv[1]).resolve()
    module_root = Path(sys.argv[2]).resolve()
    sys.path.insert(0, str(module_root))
    if update_path.parent != module_root:
        sys.path.insert(0, str(update_path.parent))
    spec = importlib.util.spec_from_file_location("__kemo_module_update__", str(update_path))
    if spec is None or spec.loader is None:
        raise ImportError("无法创建模块加载器")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    updater = getattr(module, "update", None)
    if not callable(updater):
        updater = getattr(module, "main", None)
    if not callable(updater):
        raise AttributeError("更新脚本必须提供可调用的 update() 或 main()")
    result = updater()
    if inspect.isawaitable(result):
        result = asyncio.run(result)
    if result is False:
        raise RuntimeError("更新脚本返回 False")
    if isinstance(result, dict):
        status = str(result.get("status") or "").strip().casefold()
        if result.get("ok") is False or status in {"error", "failed", "failure"}:
            reason = result.get("error") or result.get("message") or result.get("reason")
            raise RuntimeError(str(reason or "更新脚本返回失败状态"))
    emit({"ok": True, "result": result}, stream=sys.stdout)
except BaseException as exc:
    emit(
        {
            "ok": False,
            "reason": str(exc) or type(exc).__name__,
            "exception_type": type(exc).__name__,
        },
        stream=sys.stderr,
    )
    raise SystemExit(1)
'''


class ModuleRuntimeError(RuntimeError):
    """A bounded module process could not complete its protocol."""


class ModuleRuntimeTimeout(ModuleRuntimeError):
    pass


class ModuleRuntimeCancelled(ModuleRuntimeError):
    pass


def _execution_thread_lock(module_root: Path) -> threading.RLock:
    key = str(module_root.resolve()).casefold()
    with _EXECUTION_LOCKS_GUARD:
        return _EXECUTION_LOCKS.setdefault(key, threading.RLock())


def _try_lock_file(handle: Any) -> bool:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        return False
    return True


def _unlock_file(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def module_execution_lock(
    module_root: Path,
    *,
    timeout: float,
    cancel_event: threading.Event | None = None,
) -> Iterator[None]:
    """Serialize one module's update and control entry points across processes."""

    deadline = time.monotonic() + max(0.1, float(timeout))
    thread_lock = _execution_thread_lock(module_root)
    thread_acquired = False
    file_acquired = False
    handle: Any = None
    try:
        while not thread_acquired:
            if cancel_event is not None and cancel_event.is_set():
                raise ModuleRuntimeCancelled("等待模块执行锁时收到用户紧急停止")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ModuleRuntimeTimeout("等待模块执行锁超时")
            thread_acquired = thread_lock.acquire(timeout=min(_POLL_SECONDS, remaining))

        lock_path = module_root / _EXECUTION_LOCK_FILE
        if _is_link(lock_path):
            raise ModuleRuntimeError("模块执行锁文件不能是符号链接或目录联接")
        handle = lock_path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()

        while not file_acquired:
            if cancel_event is not None and cancel_event.is_set():
                raise ModuleRuntimeCancelled("等待模块执行锁时收到用户紧急停止")
            if _try_lock_file(handle):
                file_acquired = True
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ModuleRuntimeTimeout("等待模块执行锁超时")
            if cancel_event is not None:
                cancel_event.wait(min(_POLL_SECONDS, remaining))
            else:
                time.sleep(min(_POLL_SECONDS, remaining))
        yield
    finally:
        if file_acquired and handle is not None:
            try:
                _unlock_file(handle)
            except OSError:
                pass
        if handle is not None:
            handle.close()
        if thread_acquired:
            thread_lock.release()


def module_update_timeout(config: dict[str, Any]) -> float:
    task_config = config.get("task_cron_system") or {}
    if not isinstance(task_config, dict):
        return DEFAULT_MODULE_UPDATE_TIMEOUT
    raw = task_config.get("module_update_timeout", DEFAULT_MODULE_UPDATE_TIMEOUT)
    if isinstance(raw, bool):
        return DEFAULT_MODULE_UPDATE_TIMEOUT
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_MODULE_UPDATE_TIMEOUT
    if seconds <= 0:
        return DEFAULT_MODULE_UPDATE_TIMEOUT
    return min(seconds, 3600.0)


def _is_link(path: Path) -> bool:
    try:
        return path.is_symlink() or bool(
            getattr(path, "is_junction", lambda: False)()
        )
    except OSError:
        return True


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def record_module_health(
    manifest_path: Path,
    category: str,
    *,
    healthy: bool,
) -> None:
    """Update framework-owned health fields after the child process exits."""

    if _is_link(manifest_path):
        raise OSError(f"{manifest_path.name} 不能是符号链接或目录联接")
    payload = json.loads(manifest_path.read_text("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{manifest_path.name} 顶层必须是 JSON 对象")
    payload["health" if category == "sense" else "input_health"] = (
        "正常" if healthy else "异常"
    )
    if healthy:
        payload["recent_update"] = datetime.now(ZoneInfo("Asia/Shanghai")).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    _atomic_json(manifest_path, payload)


def _tail(handle: Any, *, limit: int = _MAX_CAPTURE_BYTES) -> str:
    handle.flush()
    size = handle.tell()
    handle.seek(max(0, size - limit))
    return handle.read(limit).decode("utf-8", errors="replace")


def _protocol_payload(output: str, prefix: str) -> dict[str, Any] | None:
    for line in reversed(output.splitlines()):
        if not line.startswith(prefix):
            continue
        try:
            payload = json.loads(line[len(prefix):])
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None
    return None


def run_protocol_process(
    command: list[str],
    *,
    cwd: Path,
    timeout: float,
    result_prefix: str,
    stdin_payload: str = "",
    cancel_event: threading.Event | None = None,
) -> tuple[int, dict[str, Any] | None, str, str]:
    """Run one isolated protocol process without retaining unbounded output."""

    deadline = time.monotonic() + max(0.1, float(timeout))
    with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(
        mode="w+b"
    ) as stderr_file:
        try:
            process = subprocess.Popen(
                command,
                cwd=str(cwd),
                stdin=subprocess.PIPE,
                stdout=stdout_file,
                stderr=stderr_file,
                **cancellable_subprocess_kwargs(),
            )
        except OSError as exc:
            raise ModuleRuntimeError(f"无法启动模块子进程：{exc}") from exc

        try:
            assert process.stdin is not None
            try:
                process.stdin.write(stdin_payload.encode("utf-8"))
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            finally:
                process.stdin.close()

            while process.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    terminate_process_tree(process)
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
                    raise ModuleRuntimeCancelled("模块调用因用户紧急停止而取消")
                if time.monotonic() >= deadline:
                    terminate_process_tree(process)
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
                    raise ModuleRuntimeTimeout(f"模块执行超时（{timeout:g} 秒）")
                if cancel_event is not None:
                    cancel_event.wait(_POLL_SECONDS)
                else:
                    time.sleep(_POLL_SECONDS)

            stdout = _tail(stdout_file)
            stderr = _tail(stderr_file)
            payload = _protocol_payload(stdout, result_prefix)
            if payload is None:
                payload = _protocol_payload(stderr, result_prefix)
            return int(process.returncode or 0), payload, stdout, stderr
        finally:
            if process.poll() is None:
                terminate_process_tree(process)


def run_module_updater(
    update_path: Path,
    module_root: Path,
    *,
    timeout: float,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Execute update()/main() in an isolated child and retain its small result."""

    started = time.monotonic()
    try:
        with module_execution_lock(
            module_root,
            timeout=timeout,
            cancel_event=cancel_event,
        ):
            remaining_timeout = max(
                0.1,
                float(timeout) - (time.monotonic() - started),
            )
            returncode, payload, stdout, stderr = run_protocol_process(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    MODULE_UPDATE_RUNNER,
                    str(update_path),
                    str(module_root),
                ],
                cwd=module_root,
                timeout=remaining_timeout,
                result_prefix=MODULE_UPDATE_RESULT_PREFIX,
                cancel_event=cancel_event,
            )
    except ModuleRuntimeTimeout as exc:
        reason = str(exc)
        if reason.startswith("模块执行超时"):
            reason = f"模块执行超时（{timeout:g} 秒）"
        return {
            "ok": False,
            "reason": reason,
            "exception_type": "TimeoutExpired",
        }
    except ModuleRuntimeCancelled as exc:
        return {
            "ok": False,
            "reason": str(exc),
            "exception_type": type(exc).__name__,
            "cancelled": True,
        }
    except ModuleRuntimeError as exc:
        return {
            "ok": False,
            "reason": str(exc),
            "exception_type": type(exc).__name__,
        }

    if returncode != 0:
        if payload and payload.get("ok") is False:
            return payload
        detail = (stderr or stdout).strip()[-1000:]
        return {
            "ok": False,
            "reason": detail or "更新子进程未返回可识别结果",
            "exception_type": "ChildProcessError",
        }
    if not payload or payload.get("ok") is not True:
        detail = (stderr or stdout).strip()[-1000:]
        return {
            "ok": False,
            "reason": detail or "更新子进程未返回可识别结果",
            "exception_type": "ChildProcessError",
        }
    return {"ok": True, "result": payload.get("result")}
