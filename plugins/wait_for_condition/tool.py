"""可取消、可提前唤醒的长时等待工具。"""

from __future__ import annotations

from pathlib import Path
import socket
import time
from typing import Any

from run.infra import process_identity_matches, process_snapshot
from run.tools import (
    JOB_TERMINAL_STATUSES,
    assert_background_job_access,
    public_background_job,
    read_background_job,
    reconcile_background_job,
)


MAX_WAIT_SECONDS = 7200.0
DEFAULT_CHECK_INTERVAL = 5.0
MIN_CHECK_INTERVAL = 0.1
MAX_CHECK_INTERVAL = 60.0
CONDITIONS = frozenset(
    {
        "duration",
        "job_exit",
        "process_exit",
        "path_exists",
        "path_missing",
        "path_changed",
        "tcp_open",
        "tcp_closed",
    }
)


def _resolve_path(value: str, root: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("该等待条件需要非空 path")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.resolve(strict=False)


def _path_snapshot(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {"exists": False}
    except OSError as exc:
        return {"exists": False, "error": str(exc)}
    return {
        "exists": True,
        "is_dir": path.is_dir(),
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _process_exists(pid: int) -> bool:
    return bool(process_snapshot(pid).get("exists"))


def _public_process_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    allowed = (
        "pid",
        "exists",
        "query_status",
        "identity_available",
        "process_started_at",
        "process_name",
    )
    return {key: snapshot.get(key) for key in allowed if key in snapshot}


def _tcp_open(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except socket.gaierror as exc:
        raise ValueError(f"无法解析 host: {host}（{exc}）") from exc
    except (ConnectionError, OSError, TimeoutError):
        return False


def _validate(
    condition: str,
    timeout: float,
    check_interval: float,
    pid: int,
    path: str,
    host: str,
    port: int,
    process_started_at: str,
    process_name: str,
    job_id: str,
    root: Path,
) -> tuple[float, float, Path | None, str]:
    if condition not in CONDITIONS:
        raise ValueError(
            f"未知 condition: {condition}，可选: {', '.join(sorted(CONDITIONS))}"
        )
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout 必须是 1..7200 秒的数字")
    wait_seconds = float(timeout)
    if not 1 <= wait_seconds <= MAX_WAIT_SECONDS:
        raise ValueError("timeout 必须在 1..7200 秒之间")
    if isinstance(check_interval, bool) or not isinstance(check_interval, (int, float)):
        raise ValueError("check_interval 必须是数字")
    interval = float(check_interval)
    if not MIN_CHECK_INTERVAL <= interval <= MAX_CHECK_INTERVAL:
        raise ValueError("check_interval 必须在 0.1..60 秒之间")
    resolved_path = None
    normalized_host = ""
    if condition == "process_exit" and (
        isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0
    ):
        raise ValueError("process_exit 需要 pid 为正整数")
    if not isinstance(process_started_at, str):
        raise ValueError("process_started_at 必须是字符串")
    if not isinstance(process_name, str):
        raise ValueError("process_name 必须是字符串")
    if condition == "job_exit" and (
        not isinstance(job_id, str) or not job_id.strip()
    ):
        raise ValueError("job_exit 需要非空 job_id")
    if condition.startswith("path_"):
        resolved_path = _resolve_path(path, root)
    if condition.startswith("tcp_"):
        if not isinstance(host, str) or not host.strip():
            raise ValueError(f"{condition} 需要非空 host")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError(f"{condition} 需要 1..65535 的 port")
        normalized_host = host.strip()
    return wait_seconds, interval, resolved_path, normalized_host


def run(
    condition: str,
    timeout: float,
    check_interval: float = DEFAULT_CHECK_INTERVAL,
    pid: int = 0,
    path: str = "",
    host: str = "",
    port: int = 0,
    process_started_at: str = "",
    process_name: str = "",
    job_id: str = "",
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(context, dict) or not context.get("root"):
        raise ValueError("工具上下文缺少 root")
    root = Path(str(context["root"])).resolve()
    cancel_event = context.get("cancel_event")
    if cancel_event is None or not hasattr(cancel_event, "wait"):
        raise ValueError("工具上下文缺少 cancel_event")
    wait_seconds, interval, resolved_path, normalized_host = _validate(
        condition,
        timeout,
        check_interval,
        pid,
        path,
        host,
        port,
        process_started_at,
        process_name,
        job_id,
        root,
    )

    user = str(context.get("user") or "").strip()
    source = str(context.get("source") or context.get("caller") or "")
    session_id = str(context.get("session_id") or context.get("task_id") or "")
    if condition == "job_exit" and not user:
        raise ValueError("job_exit 需要工具上下文 user")
    if condition == "job_exit":
        initial_job = read_background_job(root, user, job_id)
        assert_background_job_access(
            initial_job,
            source=source,
            session_id=session_id,
        )

    started = time.monotonic()
    deadline = started + wait_seconds
    initial_path = _path_snapshot(resolved_path) if resolved_path is not None else None
    requested_path = str(path).strip()
    path_was_relative = bool(requested_path) and not Path(
        requested_path
    ).expanduser().is_absolute()
    initial_process_exists: bool | None = None
    ever_observed_alive = False
    checks = 0
    last_observation: dict[str, Any] = {}

    while True:
        if cancel_event.is_set():
            return {
                "ok": False,
                "status": "cancelled",
                "condition": condition,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "checks": checks,
            }

        checks += 1
        triggered = False
        trigger = ""
        if condition == "duration":
            triggered = time.monotonic() >= deadline
            trigger = "duration_elapsed"
        elif condition == "job_exit":
            current_job = read_background_job(root, user, job_id)
            assert_background_job_access(
                current_job,
                source=source,
                session_id=session_id,
            )
            current_job = reconcile_background_job(root, user, job_id)
            last_observation = public_background_job(current_job, root=root)
            triggered = current_job.get("status") in JOB_TERMINAL_STATUSES
            trigger = "job_exit"
        elif condition == "process_exit":
            snapshot = process_snapshot(pid)
            exists = bool(snapshot.get("exists"))
            has_expected_identity = bool(
                process_started_at.strip() or process_name.strip()
            )
            identity_match = (
                process_identity_matches(
                    snapshot,
                    process_started_at=process_started_at,
                    process_name=process_name,
                )
                if has_expected_identity
                else None
            )
            if initial_process_exists is None:
                initial_process_exists = exists
            if exists and identity_match is not False:
                ever_observed_alive = True
            last_observation = {
                **_public_process_snapshot(snapshot),
                "process_exists": exists,
                "identity_match": identity_match,
                "initial_exists": initial_process_exists,
                "ever_observed_alive": ever_observed_alive,
            }
            if not exists:
                triggered = True
                trigger = (
                    "process_already_absent"
                    if initial_process_exists is False
                    else "process_exit"
                )
            elif identity_match is False:
                triggered = True
                trigger = "process_replaced"
        elif resolved_path is not None:
            current_path = _path_snapshot(resolved_path)
            last_observation = {
                "requested_path": requested_path,
                "resolved_path": str(resolved_path),
                "path_was_relative": path_was_relative,
                "path_base": str(root),
                "snapshot": current_path,
            }
            if condition == "path_exists":
                triggered = bool(current_path.get("exists"))
            elif condition == "path_missing":
                triggered = not bool(current_path.get("exists"))
            else:
                triggered = current_path != initial_path
                last_observation["initial_snapshot"] = initial_path
            trigger = condition
        else:
            probe_timeout = max(
                0.01,
                min(1.0, interval / 2, max(0.01, deadline - time.monotonic())),
            )
            is_open = _tcp_open(normalized_host, port, probe_timeout)
            last_observation = {
                "host": normalized_host,
                "port": port,
                "tcp_open": is_open,
            }
            triggered = is_open if condition == "tcp_open" else not is_open
            trigger = condition

        elapsed = time.monotonic() - started
        if triggered:
            return {
                "ok": True,
                "status": "triggered",
                "condition": condition,
                "trigger": trigger,
                "elapsed_seconds": round(elapsed, 3),
                "checks": checks,
                "observation": last_observation,
            }
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {
                "ok": True,
                "status": "timeout",
                "condition": condition,
                "triggered": False,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "checks": checks,
                "observation": last_observation,
            }
        cancel_event.wait(min(interval, remaining))
