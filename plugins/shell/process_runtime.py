"""Subprocess lifecycle implementation for the shell plugin."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any

@dataclass(frozen=True)
class ProcessRuntimeDependencies:
    prepare_process_command: Any
    subprocess: Any
    time: Any
    hashlib: Any
    sys: Any
    decode_output: Any
    truncate: Any
    visible_subprocess_kwargs: Any
    hidden_subprocess_kwargs: Any
    cancellable_subprocess_kwargs: Any
    detached_subprocess_kwargs: Any
    terminate_process_tree: Any
    process_snapshot: Any
    prepare_background_job: Any
    write_job_request: Any
    update_background_job: Any
    read_background_job: Any
    cancel_background_job: Any
    reconcile_background_job: Any
    public_background_job: Any


def run_process(
    command: str,
    *,
    cwd: Path,
    env_extra: dict[str, str],
    stdin: str,
    timeout: float,
    cancel_event: threading.Event | None,
    shell_type: str = "auto",
    show_terminal: bool = False,
    deps: ProcessRuntimeDependencies,
) -> dict[str, Any]:
    process_command, use_shell, resolved_shell_type, environment = (
        deps.prepare_process_command(
            command,
            shell_type,
            env_extra=env_extra,
            cwd=cwd,
        )
    )
    if cancel_event is None:
        try:
            completed = deps.subprocess.run(
                process_command,
                shell=use_shell,
                cwd=str(cwd),
                env=environment,
                input=stdin.encode("utf-8") if stdin else None,
                timeout=timeout,
                capture_output=True,
                **(
                    deps.visible_subprocess_kwargs()
                    if show_terminal
                    else deps.hidden_subprocess_kwargs()
                ),
            )
        except deps.subprocess.TimeoutExpired as exc:
            stdout = deps.decode_output(exc.stdout or b"")
            stderr = deps.decode_output(exc.stderr or b"")
            output, truncated = deps.truncate(
                "\n".join(value for value in (stdout, stderr) if value).strip()
            )
            return {
                "ok": False,
                "output": output or f"命令超时 ({timeout:g}s)",
                "exit_code": -1,
                "timed_out": True,
                "truncated": truncated,
                "shell_type": resolved_shell_type,
            }
        stdout = deps.decode_output(completed.stdout).strip()
        stderr = deps.decode_output(completed.stderr).strip()
        if stdout and stderr:
            output = f"STDOUT:\n{stdout}\n\nSTDERR:\n{stderr}"
        elif stderr:
            output = f"STDERR:\n{stderr}"
        else:
            output = stdout or "(无输出)"
        output, truncated = deps.truncate(output)
        return {
            "ok": completed.returncode == 0,
            "output": output,
            "exit_code": completed.returncode,
            "timed_out": False,
            "truncated": truncated,
            "shell_type": resolved_shell_type,
        }
    process = deps.subprocess.Popen(
        process_command,
        shell=use_shell,
        cwd=str(cwd),
        env=environment,
        stdin=deps.subprocess.PIPE if stdin else deps.subprocess.DEVNULL,
        stdout=deps.subprocess.PIPE,
        stderr=deps.subprocess.PIPE,
        **deps.cancellable_subprocess_kwargs(show_terminal=show_terminal),
    )
    input_data = stdin.encode("utf-8") if stdin else None
    deadline = deps.time.monotonic() + timeout
    cancelled = False
    timed_out = False
    while True:
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            deps.terminate_process_tree(process)
        remaining = deadline - deps.time.monotonic()
        if remaining <= 0 and process.poll() is None:
            timed_out = True
            deps.terminate_process_tree(process)
        try:
            stdout_data, stderr_data = process.communicate(
                input=input_data, timeout=max(0.01, min(0.1, max(0.0, remaining)))
            )
            break
        except deps.subprocess.TimeoutExpired:
            input_data = None
            continue
    if cancelled or timed_out:
        stdout = deps.decode_output(stdout_data or b"")
        stderr = deps.decode_output(stderr_data or b"")
        output, truncated = deps.truncate(
            "\n".join(value for value in (stdout, stderr) if value).strip()
        )
        return {
            "ok": False,
            "output": output
            or (
                "命令因用户紧急停止而取消" if cancelled else f"命令超时 ({timeout:g}s)"
            ),
            "exit_code": -1,
            "timed_out": timed_out,
            "cancelled": cancelled,
            "truncated": truncated,
            "shell_type": resolved_shell_type,
        }
    stdout = deps.decode_output(stdout_data).strip()
    stderr = deps.decode_output(stderr_data).strip()
    if stdout and stderr:
        output = f"STDOUT:\n{stdout}\n\nSTDERR:\n{stderr}"
    elif stderr:
        output = f"STDERR:\n{stderr}"
    else:
        output = stdout or "(无输出)"
    output, truncated = deps.truncate(output)
    return {
        "ok": process.returncode == 0,
        "output": output,
        "exit_code": process.returncode,
        "timed_out": False,
        "truncated": truncated,
        "shell_type": resolved_shell_type,
    }


def start_background(
    command: str,
    *,
    cwd: Path,
    environment: dict[str, str],
    shell_type: str,
    context: dict[str, Any],
    timeout: float,
    cancel_event: threading.Event | None,
    show_terminal: bool = False,
    deps: ProcessRuntimeDependencies,
    source_root: Path,
) -> dict[str, Any]:
    root = Path(str(context.get("root") or Path.cwd())).resolve()
    user = str(context.get("user") or "").strip()
    if not user:
        raise ValueError("后台模式需要工具上下文 user")
    process_command, use_shell, resolved_shell_type, worker_environment = (
        deps.prepare_process_command(
            command,
            shell_type,
            env_extra=environment,
            cwd=cwd,
        )
    )
    record, paths = deps.prepare_background_job(
        root,
        user,
        source=str(context.get("source") or context.get("caller") or ""),
        session_id=str(context.get("session_id") or context.get("task_id") or ""),
        working_dir=str(cwd),
        shell_type=resolved_shell_type,
        command_digest=deps.hashlib.sha256(command.encode("utf-8")).hexdigest(),
        timeout_seconds=timeout,
    )
    request = {
        "schema_version": 1,
        "root": str(root),
        "user": user,
        "job_id": record["job_id"],
        "cwd": str(cwd),
        "process_command": process_command,
        "use_shell": use_shell,
        "show_terminal": show_terminal,
        "deadline_at": record.get("deadline_at"),
    }
    worker: deps.subprocess.Popen[Any] | None = None
    try:
        deps.write_job_request(paths["request"], request)
        worker = deps.subprocess.Popen(
            [
                deps.sys.executable,
                "-m",
                "run.tools.background_worker",
                str(paths["request"]),
            ],
            cwd=str(source_root),
            env=worker_environment,
            stdin=deps.subprocess.DEVNULL,
            stdout=deps.subprocess.DEVNULL,
            stderr=deps.subprocess.DEVNULL,
            **deps.detached_subprocess_kwargs(),
        )
        worker_snapshot = deps.process_snapshot(worker.pid)
        if (
            worker.poll() is None
            and worker_snapshot.get("exists")
            and not str(worker_snapshot.get("process_started_at") or "").strip()
        ):
            raise RuntimeError("后台 Worker 进程身份无法确认")

        def mark_worker(current: dict[str, Any]) -> dict[str, Any]:
            current["worker_pid"] = worker.pid
            current["worker_started_at"] = str(
                worker_snapshot.get("process_started_at") or ""
            )
            current["worker_name"] = str(worker_snapshot.get("process_name") or "")
            return current

        deps.update_background_job(root, user, record["job_id"], mark_worker)
    except BaseException as exc:
        if worker is not None and worker.poll() is None:
            deps.terminate_process_tree(worker)
        try:
            paths["request"].unlink(missing_ok=True)
        except OSError:
            pass

        def fail_start(current: dict[str, Any]) -> dict[str, Any]:
            if current.get("status") in {"completed", "failed", "cancelled", "interrupted"}:
                return current
            current["status"] = "failed"
            current["finished_at"] = deps.time.strftime("%Y-%m-%dT%H:%M:%SZ", deps.time.gmtime())
            current["stop_reason"] = "background_worker_start_failed"
            current["error"] = {
                "code": "background_worker_start_failed",
                "message": "后台作业管理进程启动失败",
                "exception_type": type(exc).__name__,
            }
            return current

        failed = deps.update_background_job(root, user, record["job_id"], fail_start)
        terminal_ok = failed.get("status") not in {"failed", "interrupted"}
        return {
            "ok": terminal_ok,
            "output": (
                f"后台作业状态：{failed.get('status')}"
                if terminal_ok
                else "后台作业启动失败"
            ),
            **deps.public_background_job(failed, root=root),
        }

    deadline = deps.time.monotonic() + min(max(0.2, timeout), 5.0)
    current = deps.read_background_job(root, user, record["job_id"])
    while current.get("status") == "starting" and deps.time.monotonic() < deadline:
        if cancel_event is not None and cancel_event.is_set():
            current = deps.cancel_background_job(root, user, record["job_id"])
            break
        deps.time.sleep(0.05)
        current = deps.read_background_job(root, user, record["job_id"])
    if current.get("status") == "starting":
        current = deps.reconcile_background_job(root, user, record["job_id"])
    public = deps.public_background_job(current, root=root)
    successful = current.get("status") not in {
        "failed",
        "interrupted",
        "cancelled",
        "cancelling",
    }
    return {
        "ok": successful,
        "output": (
            f"后台作业已登记：{record['job_id']}"
            if successful
            else "后台作业未能稳定启动"
        ),
        **public,
    }
