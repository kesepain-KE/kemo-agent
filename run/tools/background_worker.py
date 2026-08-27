"""Detached worker that owns one managed Shell background command."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any

from run.infra import (
    cancellable_subprocess_kwargs,
    process_snapshot,
    terminate_process_tree,
)
from run.tools import (
    JOB_TERMINAL_STATUSES,
    MAX_BACKGROUND_JOB_LOG_BYTES,
    job_paths,
    read_background_job,
    update_background_job,
)


MAX_LOG_BYTES = MAX_BACKGROUND_JOB_LOG_BYTES
_LOG_CHUNK_BYTES = 64 * 1024


def _capture_stream(stream: Any, path: Path, *, max_bytes: int = MAX_LOG_BYTES) -> None:
    """Drain one child stream while bounding the durable log size."""

    truncated = False
    written = 0
    marker = f"\n...[日志输出已截断，单个流上限 {max_bytes} 字节]...\n".encode(
        "utf-8"
    )
    content_limit = max(0, max_bytes - len(marker))
    output = None
    try:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            output = path.open("wb", buffering=0)
        except (OSError, ValueError):
            # Keep draining the child pipe even when the durable log cannot be
            # opened.  A stopped reader can fill the OS pipe and deadlock the
            # managed process forever.
            output = None
        while True:
            try:
                chunk = stream.read(_LOG_CHUNK_BYTES)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            if output is None:
                continue
            try:
                if written < content_limit:
                    portion = chunk[: content_limit - written]
                    output.write(portion)
                    written += len(portion)
                    if len(portion) < len(chunk):
                        truncated = True
                else:
                    truncated = True
            except (OSError, ValueError):
                try:
                    output.close()
                except (OSError, ValueError):
                    pass
                output = None
        if output is not None and truncated:
            try:
                output.write(marker)
            except (OSError, ValueError):
                pass
    finally:
        if output is not None:
            try:
                output.close()
            except (OSError, ValueError):
                pass


def _load_request(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text("utf-8"))
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    if not isinstance(value, dict):
        raise ValueError("后台作业请求必须是对象")
    return value


def _finish(
    root: Path,
    user: str,
    job_id: str,
    *,
    exit_code: int | None,
    error_code: str = "",
    exception_type: str = "",
) -> None:
    def mutate(record: dict[str, Any]) -> dict[str, Any]:
        if record.get("status") in JOB_TERMINAL_STATUSES:
            return record
        timeout_requested = bool(record.get("timeout_requested")) or error_code == (
            "background_timeout"
        )
        cancelled = bool(record.get("cancel_requested")) and not timeout_requested
        record["status"] = (
            "cancelled"
            if cancelled
            else "completed"
            if exit_code == 0 and not error_code and not timeout_requested
            else "failed"
        )
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        record["exit_code"] = exit_code
        record["stop_reason"] = (
            "user_cancel"
            if cancelled
            else "background_timeout"
            if timeout_requested
            else "process_exit"
            if not error_code
            else error_code
        )
        record["error"] = (
            None
            if record["status"] in {"completed", "cancelled"}
            else {
                "code": error_code
                or (
                    "background_timeout"
                    if timeout_requested
                    else "background_process_failed"
                ),
                "message": "后台命令执行失败",
                **(
                    {"exception_type": str(exception_type)[:160]}
                    if exception_type
                    else {}
                ),
            }
        )
        return record

    update_background_job(root, user, job_id, mutate)


def run(request_path: Path) -> int:
    request = _load_request(request_path)
    if request.get("schema_version") != 1:
        raise ValueError("后台作业请求版本无效")
    root = Path(str(request["root"])).resolve()
    user = str(request["user"])
    job_id = str(request["job_id"])
    current = read_background_job(root, user, job_id)
    if current.get("cancel_requested"):
        _finish(root, user, job_id, exit_code=None)
        return 0

    command = request.get("process_command")
    if not isinstance(command, (str, list)):
        raise ValueError("后台作业 process_command 无效")
    if isinstance(command, list) and not all(isinstance(item, str) for item in command):
        raise ValueError("后台作业 process_command 列表无效")
    cwd = Path(str(request["cwd"])).resolve()
    paths = job_paths(root, user, job_id)
    stdout_path = paths["stdout"]
    stderr_path = paths["stderr"]
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    deadline_at = request.get("deadline_at")
    try:
        deadline_at = float(deadline_at) if deadline_at is not None else None
    except (TypeError, ValueError):
        deadline_at = None

    process: subprocess.Popen[Any] | None = None
    capture_threads: list[threading.Thread] = []
    try:
        process = subprocess.Popen(
            command,
            shell=bool(request.get("use_shell")),
            cwd=str(cwd),
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **cancellable_subprocess_kwargs(),
        )
        if deadline_at is not None and time.time() >= deadline_at:
            terminate_process_tree(process)
            _finish(
                root,
                user,
                job_id,
                exit_code=process.returncode,
                error_code="background_timeout",
            )
            return 0
        snapshot = process_snapshot(process.pid)
        if (
            process.poll() is None
            and snapshot.get("exists")
            and not str(snapshot.get("process_started_at") or "").strip()
        ):
            terminate_process_tree(process)
            _finish(
                root,
                user,
                job_id,
                exit_code=process.returncode,
                error_code="background_process_identity_unavailable",
                exception_type="ProcessIdentityUnavailable",
            )
            return 0

        def mark_running(record: dict[str, Any]) -> dict[str, Any]:
            record["pid"] = process.pid
            record["process_started_at"] = str(
                snapshot.get("process_started_at") or ""
            )
            record["process_name"] = str(snapshot.get("process_name") or "")
            if record.get("cancel_requested"):
                record["status"] = "cancelling"
            else:
                record["status"] = "running"
            return record

        current = update_background_job(root, user, job_id, mark_running)
        if process.stdout is not None:
            stdout_thread = threading.Thread(
                target=_capture_stream,
                args=(process.stdout, stdout_path),
                daemon=True,
            )
            capture_threads.append(stdout_thread)
            stdout_thread.start()
        if process.stderr is not None:
            stderr_thread = threading.Thread(
                target=_capture_stream,
                args=(process.stderr, stderr_path),
                daemon=True,
            )
            capture_threads.append(stderr_thread)
            stderr_thread.start()
        if current.get("cancel_requested"):
            terminate_process_tree(process)
        timed_out = False
        while process.poll() is None:
            current = read_background_job(root, user, job_id)
            if current.get("cancel_requested") or current.get("status") == "cancelling":
                terminate_process_tree(process)
            elif deadline_at is not None and time.time() >= deadline_at:
                timed_out = True
                terminate_process_tree(process)
            try:
                process.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                continue
        # The process can exit in the small window between the last poll and
        # the deadline check.  Treat a late observation as a timeout as well;
        # otherwise the same explicit timeout is reported as process_exit on
        # slower CI hosts and the result is nondeterministic.
        if deadline_at is not None and time.time() >= deadline_at:
            timed_out = True
        exit_code = process.returncode
        for capture_thread in capture_threads:
            capture_thread.join(timeout=2.0)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        _finish(
            root,
            user,
            job_id,
            exit_code=exit_code,
            error_code="background_timeout" if timed_out else "",
        )
        return 0
    except BaseException as exc:
        if process is not None and process.poll() is None:
            terminate_process_tree(process)
        _finish(
            root,
            user,
            job_id,
            exit_code=(process.returncode if process is not None else None),
            error_code="background_worker_exception",
            exception_type=type(exc).__name__,
        )
        return 1


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if len(values) != 1:
        return 2
    try:
        return run(Path(values[0]).resolve())
    except BaseException:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
