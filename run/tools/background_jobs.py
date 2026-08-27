"""Durable per-user metadata for managed Shell background jobs."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import time
from typing import Any, Callable
import uuid

from run.config import user_dir
from run.history import index_lock
from run.infra import (
    process_identity_matches,
    process_snapshot,
    replace_with_retry,
    terminate_pid_tree,
)


JOB_SCHEMA_VERSION = 1
JOB_TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "interrupted"}
)
JOB_ACTIVE_STATUSES = frozenset({"starting", "running", "cancelling"})
MAX_ACTIVE_BACKGROUND_JOBS_PER_USER = 8
MAX_BACKGROUND_JOB_RECORDS_PER_USER = 256
MAX_BACKGROUND_JOB_STORAGE_BYTES = 256 * 1024 * 1024
BACKGROUND_JOB_RETENTION_SECONDS = 7 * 24 * 60 * 60
MAX_BACKGROUND_JOB_LOG_BYTES = 16 * 1024 * 1024
_JOB_ID_RE = re.compile(r"^job_[a-f0-9]{32}$")
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_JOB_ARTIFACT_SUFFIXES = (".json", ".request.json", ".stdout.log", ".stderr.log")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_reparse_point(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ValueError("后台作业目录不可检查") from exc
    return bool(stat.S_ISLNK(info.st_mode)) or bool(
        getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
    )


def _assert_safe_directory_chain(root: Path, user: str) -> Path:
    root = root.resolve()
    users_root = root / "users"
    base = user_dir(user, root)
    for candidate in (users_root, base):
        if _is_reparse_point(candidate):
            raise ValueError("用户目录不能是符号链接或目录联接")
    try:
        resolved_users = users_root.resolve(strict=True)
        resolved_base = base.resolve(strict=True)
        resolved_base.relative_to(resolved_users)
    except (FileNotFoundError, ValueError) as exc:
        raise ValueError("用户目录越出 users 根目录") from exc
    return resolved_base


def _jobs_dir(root: Path, user: str) -> Path:
    base = _assert_safe_directory_chain(root, user)
    directory = base / "runtime" / "background_jobs"
    runtime = base / "runtime"
    if _is_reparse_point(runtime) or _is_reparse_point(directory):
        raise ValueError("后台作业目录不能是符号链接或目录联接")
    directory.mkdir(parents=True, exist_ok=True)
    resolved = directory.resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ValueError("后台作业目录越出用户目录") from exc
    return resolved


def _validate_job_id(job_id: Any) -> str:
    value = str(job_id or "").strip()
    if not _JOB_ID_RE.fullmatch(value):
        raise ValueError("job_id 格式无效")
    return value


def job_paths(root: Path, user: str, job_id: str) -> dict[str, Path]:
    normalized = _validate_job_id(job_id)
    directory = _jobs_dir(root, user)
    return {
        "record": directory / f"{normalized}.json",
        "request": directory / f"{normalized}.request.json",
        "stdout": directory / f"{normalized}.stdout.log",
        "stderr": directory / f"{normalized}.stderr.log",
    }


def _iter_job_records(directory: Path) -> list[tuple[Path, dict[str, Any]]]:
    records: list[tuple[Path, dict[str, Any]]] = []
    try:
        candidates = tuple(directory.glob("job_*.json"))
    except OSError:
        return records
    for path in candidates:
        try:
            info = os.lstat(path)
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode) or not _JOB_ID_RE.fullmatch(path.stem):
            continue
        try:
            records.append((path, _read_record(path)))
        except (KeyError, OSError, RuntimeError, ValueError):
            continue
    return records


def _artifact_paths(directory: Path, job_id: str) -> tuple[Path, ...]:
    normalized = _validate_job_id(job_id)
    return tuple(directory / f"{normalized}{suffix}" for suffix in _JOB_ARTIFACT_SUFFIXES)


def _remove_job_artifacts(directory: Path, job_id: str) -> None:
    for path in _artifact_paths(directory, job_id):
        try:
            if path.is_file() or path.is_symlink():
                path.unlink()
        except OSError:
            continue


def _parse_timestamp(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def _storage_bytes(directory: Path) -> int:
    total = 0
    try:
        candidates = tuple(directory.iterdir())
    except OSError:
        return 0
    for path in candidates:
        try:
            info = os.lstat(path)
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode):
            total += max(0, int(info.st_size))
    return total


def _cleanup_background_jobs(directory: Path, *, now: float | None = None) -> None:
    current_time = time.time() if now is None else float(now)
    records = _iter_job_records(directory)
    cutoff = current_time - BACKGROUND_JOB_RETENTION_SECONDS
    for _, record in records:
        if record.get("status") not in JOB_TERMINAL_STATUSES:
            continue
        finished_at = _parse_timestamp(record.get("finished_at"))
        if finished_at is not None and finished_at <= cutoff:
            try:
                _remove_job_artifacts(directory, str(record.get("job_id") or ""))
            except ValueError:
                continue

    records = _iter_job_records(directory)
    if len(records) <= MAX_BACKGROUND_JOB_RECORDS_PER_USER:
        return
    terminal = [
        item
        for item in records
        if item[1].get("status") in JOB_TERMINAL_STATUSES
    ]
    terminal.sort(
        key=lambda item: (
            _parse_timestamp(item[1].get("finished_at"))
            or _parse_timestamp(item[1].get("updated_at"))
            or 0.0
        )
    )
    excess = len(records) - MAX_BACKGROUND_JOB_RECORDS_PER_USER
    for _, record in terminal[:excess]:
        try:
            _remove_job_artifacts(directory, str(record.get("job_id") or ""))
        except ValueError:
            continue


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            "utf-8",
        )
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        replace_with_retry(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _read_record(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        raise KeyError("后台作业不存在") from None
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("后台作业记录损坏或不可读") from exc
    if not isinstance(value, dict) or value.get("schema_version") != JOB_SCHEMA_VERSION:
        raise RuntimeError("后台作业记录版本无效")
    return value


def prepare_background_job(
    root: Path,
    user: str,
    *,
    source: str = "",
    session_id: str = "",
    working_dir: str,
    shell_type: str,
    command_digest: str,
    timeout_seconds: float | None = None,
) -> tuple[dict[str, Any], dict[str, Path]]:
    job_id = f"job_{uuid.uuid4().hex}"
    paths = job_paths(root, user, job_id)
    now = _now()
    normalized_timeout = (
        max(1.0, min(float(timeout_seconds), 3600.0))
        if timeout_seconds is not None
        else None
    )
    record: dict[str, Any] = {
        "schema_version": JOB_SCHEMA_VERSION,
        "job_id": job_id,
        "user": user,
        "source": str(source or ""),
        "session_id": str(session_id or ""),
        "status": "starting",
        "pid": 0,
        "process_started_at": "",
        "process_name": "",
        "worker_pid": 0,
        "worker_started_at": "",
        "started_at": now,
        "updated_at": now,
        "finished_at": "",
        "timeout_seconds": normalized_timeout,
        "deadline_at": (
            time.time() + normalized_timeout
            if normalized_timeout is not None
            else None
        ),
        "exit_code": None,
        "cancel_requested": False,
        "timeout_requested": False,
        "stop_reason": "",
        "error": None,
        "working_dir": str(working_dir),
        "shell_type": str(shell_type),
        "command_digest": str(command_digest),
        "stdout_path": str(paths["stdout"]),
        "stderr_path": str(paths["stderr"]),
    }
    directory = paths["record"].parent
    with index_lock(root, user):
        _cleanup_background_jobs(directory)
        records = _iter_job_records(directory)
        active_count = sum(
            1 for _, existing in records if existing.get("status") in JOB_ACTIVE_STATUSES
        )
        if active_count >= MAX_ACTIVE_BACKGROUND_JOBS_PER_USER:
            raise ValueError("后台活动作业数量已达到用户上限")
        reserved = MAX_BACKGROUND_JOB_LOG_BYTES * 2
        if _storage_bytes(directory) + reserved > MAX_BACKGROUND_JOB_STORAGE_BYTES:
            raise ValueError("后台作业日志空间已达到用户上限，请先清理历史作业")
        _atomic_json(paths["record"], record)
    return record, paths


def write_job_request(path: Path, payload: dict[str, Any]) -> None:
    _atomic_json(path, payload)


def read_background_job(root: Path, user: str, job_id: str) -> dict[str, Any]:
    paths = job_paths(root, user, job_id)
    with index_lock(root, user):
        return _read_record(paths["record"])


def assert_background_job_access(
    record: dict[str, Any],
    *,
    source: str = "",
    session_id: str = "",
) -> None:
    expected_source = str(record.get("source") or "")
    expected_session = str(record.get("session_id") or "")
    actual_source = str(source or "")
    actual_session = str(session_id or "")
    if expected_source and expected_source != actual_source:
        raise KeyError("后台作业不存在")
    if expected_session and expected_session != actual_session:
        raise KeyError("后台作业不存在")


def update_background_job(
    root: Path,
    user: str,
    job_id: str,
    mutator: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    paths = job_paths(root, user, job_id)
    with index_lock(root, user):
        current = _read_record(paths["record"])
        rendered = mutator(dict(current))
        if not isinstance(rendered, dict):
            raise TypeError("后台作业 mutator 必须返回对象")
        rendered["schema_version"] = JOB_SCHEMA_VERSION
        rendered["job_id"] = job_id
        rendered["user"] = user
        rendered["updated_at"] = _now()
        _atomic_json(paths["record"], rendered)
        return rendered


def _identity_alive(record: dict[str, Any], *, prefix: str = "") -> tuple[bool, dict[str, Any]]:
    if prefix:
        pid_key = f"{prefix}pid"
        started_key = f"{prefix}started_at"
        name_key = f"{prefix}name"
    else:
        pid_key = "pid"
        started_key = "process_started_at"
        name_key = "process_name"
    try:
        pid = int(record.get(pid_key) or 0)
    except (TypeError, ValueError):
        return False, {
            "pid": 0,
            "exists": False,
            "query_status": "invalid_pid",
            "identity_match": False,
        }
    if pid <= 0:
        return False, {"pid": pid, "exists": False, "query_status": "not_started"}
    snapshot = process_snapshot(pid)
    expected_started = str(record.get(started_key) or "").strip()
    expected_name = str(record.get(name_key) or "").strip()
    matched = process_identity_matches(
        snapshot,
        process_started_at=expected_started,
        process_name=expected_name,
    )
    # A persisted PID is safe to treat as live only when its creation time is
    # available and matches.  A name-only or identity-less record is unknown,
    # never a basis for a destructive operation.
    safe_alive = bool(snapshot.get("exists") and expected_started and matched is True)
    if snapshot.get("exists") and not expected_started:
        matched = None
    return safe_alive, {
        **snapshot,
        "identity_match": matched,
        "identity_strong": bool(expected_started),
    }


def _identity_unknown(snapshot: dict[str, Any]) -> bool:
    return bool(
        snapshot.get("exists")
        and (
            snapshot.get("identity_match") is None
            or snapshot.get("identity_strong") is not True
        )
    )


def _deadline_expired(record: dict[str, Any], *, now: float | None = None) -> bool:
    raw = record.get("deadline_at")
    if raw in (None, ""):
        return False
    try:
        deadline = float(raw)
    except (TypeError, ValueError, OverflowError):
        return False
    current_time = time.time() if now is None else float(now)
    return current_time >= deadline


def _terminate_known_identity(
    record: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    prefix: str = "",
) -> bool:
    if snapshot.get("identity_match") is not True:
        return False
    if snapshot.get("identity_strong") is not True:
        return False
    pid_key = f"{prefix}pid" if prefix else "pid"
    started_key = f"{prefix}started_at" if prefix else "process_started_at"
    name_key = f"{prefix}name" if prefix else "process_name"
    try:
        pid = int(record.get(pid_key) or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        return bool(
            terminate_pid_tree(
                pid,
                expected_process_started_at=str(record.get(started_key) or ""),
                expected_process_name=str(record.get(name_key) or ""),
            )
        )
    except (OSError, ValueError):
        return False


def _settle_identity_unknown(
    record: dict[str, Any],
    *,
    timed_out: bool = False,
) -> dict[str, Any]:
    if record.get("status") in JOB_TERMINAL_STATUSES:
        return record
    cancelled = bool(record.get("cancel_requested")) and not timed_out
    record["status"] = "cancelled" if cancelled else "failed"
    record["finished_at"] = _now()
    if timed_out:
        record["timeout_requested"] = True
        record["stop_reason"] = "background_timeout_identity_unknown"
        message = "后台作业已超时，但无法确认进程身份，未执行不安全的 PID 终止"
    else:
        record["stop_reason"] = "background_identity_unknown"
        message = "后台作业进程身份无法确认，未执行不安全的 PID 终止"
    record["error"] = {
        "code": "background_identity_unknown",
        "message": message,
    }
    return record


def reconcile_background_job(root: Path, user: str, job_id: str) -> dict[str, Any]:
    current = read_background_job(root, user, job_id)
    if current.get("status") in JOB_TERMINAL_STATUSES:
        return current
    process_alive, process = _identity_alive(current)
    worker_alive, worker = _identity_alive(current, prefix="worker_")
    if _deadline_expired(current):
        _terminate_known_identity(current, process)
        _terminate_known_identity(current, worker, prefix="worker_")
        process_alive, process = _identity_alive(current)
        worker_alive, worker = _identity_alive(current, prefix="worker_")
        if process_alive or worker_alive:
            def mark_timeout(record: dict[str, Any]) -> dict[str, Any]:
                if record.get("status") in JOB_TERMINAL_STATUSES:
                    return record
                record["status"] = "cancelling"
                record["timeout_requested"] = True
                record["stop_reason"] = "background_timeout"
                record["error"] = {
                    "code": "background_timeout",
                    "message": "后台作业已超过截止时间，正在终止",
                }
                return record

            return update_background_job(root, user, job_id, mark_timeout)

        if _identity_unknown(process) or _identity_unknown(worker):
            return update_background_job(
                root,
                user,
                job_id,
                lambda record: _settle_identity_unknown(record, timed_out=True),
            )

        def settle_timeout(record: dict[str, Any]) -> dict[str, Any]:
            if record.get("status") in JOB_TERMINAL_STATUSES:
                return record
            record["status"] = "failed"
            record["finished_at"] = _now()
            record["timeout_requested"] = True
            record["stop_reason"] = "background_timeout"
            record["error"] = {
                "code": "background_timeout",
                "message": "后台作业超过截止时间",
            }
            return record

        return update_background_job(root, user, job_id, settle_timeout)
    if process_alive or worker_alive:
        return {
            **current,
            "observation": {"process": process, "worker": worker},
        }

    if _identity_unknown(process) or _identity_unknown(worker):
        return update_background_job(
            root,
            user,
            job_id,
            _settle_identity_unknown,
        )

    def settle(record: dict[str, Any]) -> dict[str, Any]:
        if record.get("status") in JOB_TERMINAL_STATUSES:
            return record
        cancelled = bool(record.get("cancel_requested")) and not bool(
            record.get("timeout_requested")
        )
        record["status"] = "cancelled" if cancelled else "interrupted"
        record["finished_at"] = _now()
        record["stop_reason"] = (
            "cancelled_without_live_process" if cancelled else "background_worker_lost"
        )
        record["error"] = (
            None
            if cancelled
            else {
                "code": "background_worker_lost",
                "message": "后台作业及其管理进程均已不存在",
            }
        )
        return record

    return update_background_job(root, user, job_id, settle)


def cancel_background_job(root: Path, user: str, job_id: str) -> dict[str, Any]:
    current = read_background_job(root, user, job_id)
    if current.get("status") in JOB_TERMINAL_STATUSES:
        return current

    def request(record: dict[str, Any]) -> dict[str, Any]:
        if record.get("status") not in JOB_TERMINAL_STATUSES:
            record["status"] = "cancelling"
            record["cancel_requested"] = True
            record["stop_reason"] = "user_cancel"
        return record

    current = update_background_job(root, user, job_id, request)
    alive, snapshot = _identity_alive(current)
    if alive and snapshot.get("identity_match") is True:
        # Re-check immediately before the destructive PID operation.  A missing
        # or indeterminate identity is never sufficient to kill a persisted PID.
        latest = process_snapshot(int(current.get("pid") or 0))
        latest_match = process_identity_matches(
            latest,
            process_started_at=str(current.get("process_started_at") or ""),
            process_name=str(current.get("process_name") or ""),
        )
        if latest_match is True and str(current.get("process_started_at") or "").strip():
            terminate_pid_tree(
                int(current.get("pid") or 0),
                expected_process_started_at=str(
                    current.get("process_started_at") or ""
                ),
                expected_process_name=str(current.get("process_name") or ""),
            )
    return reconcile_background_job(root, user, job_id)


def public_background_job(
    record: dict[str, Any], *, root: Path | None = None
) -> dict[str, Any]:
    allowed = (
        "job_id",
        "status",
        "pid",
        "process_started_at",
        "process_name",
        "started_at",
        "updated_at",
        "finished_at",
        "exit_code",
        "cancel_requested",
        "timeout_requested",
        "stop_reason",
        "error",
        "shell_type",
        "timeout_seconds",
        "deadline_at",
        "observation",
    )
    result = {key: record.get(key) for key in allowed if key in record}
    if root is not None:
        base = root.resolve()
        for key in ("working_dir", "stdout_path", "stderr_path"):
            value = record.get(key)
            if not value:
                continue
            try:
                resolved = Path(str(value)).resolve(strict=False)
                relative = resolved.relative_to(base)
            except (OSError, ValueError):
                continue
            result[key] = relative.as_posix()
    return result


__all__ = [
    "BACKGROUND_JOB_RETENTION_SECONDS",
    "JOB_ACTIVE_STATUSES",
    "JOB_TERMINAL_STATUSES",
    "MAX_ACTIVE_BACKGROUND_JOBS_PER_USER",
    "MAX_BACKGROUND_JOB_LOG_BYTES",
    "MAX_BACKGROUND_JOB_RECORDS_PER_USER",
    "MAX_BACKGROUND_JOB_STORAGE_BYTES",
    "assert_background_job_access",
    "cancel_background_job",
    "job_paths",
    "prepare_background_job",
    "public_background_job",
    "read_background_job",
    "reconcile_background_job",
    "update_background_job",
    "write_job_request",
]
