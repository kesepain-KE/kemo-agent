"""Durable per-user metadata for managed Shell background jobs."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import stat
import time
from typing import Any, Callable
import uuid

from run.history import index_lock
from run.infra import (
    process_identity_matches,
    process_snapshot,
    replace_with_retry,
    terminate_pid_tree,
)
from run.tools.background_job_files import (
    _JOB_ID_RE,
    _jobs_dir,
    _remove_job_artifacts,
    _storage_bytes,
    _validate_job_id,
    job_paths,
)


JOB_SCHEMA_VERSION = 1
FOREGROUND_HANDOFF = "foreground_handoff"
MAX_BACKGROUND_JOB_RECORD_BYTES = 128 * 1024
JOB_TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "interrupted"}
)
JOB_ACTIVE_STATUSES = frozenset({"starting", "running", "cancelling"})
_UNKNOWN_EXIT = {"background_identity_unknown", "background_timeout_identity_unknown"}
MAX_ACTIVE_BACKGROUND_JOBS_PER_USER = 8
MAX_BACKGROUND_JOB_RECORDS_PER_USER = 256
MAX_BACKGROUND_JOB_STORAGE_BYTES = 256 * 1024 * 1024
BACKGROUND_JOB_RETENTION_SECONDS = 7 * 24 * 60 * 60
BACKGROUND_JOB_RECONCILE_GRACE_SECONDS = 30.0
MAX_BACKGROUND_JOB_LOG_BYTES = 16 * 1024 * 1024
_RECORD_READ_RETRY_DELAYS = (0.02, 0.05, 0.1)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _parse_timestamp(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def required_shell_state(record: dict[str, Any], *, expects_receipt: bool = False) -> str:
    if record.get("completion_mode") != "required":
        return ""
    if (record.get("source") == "web" and not record.get("execution_exit_confirmed")
            and (record.get("stop_reason") in _UNKNOWN_EXIT or record.get("execution_exit_unverified"))):
        return "owner_unknown"
    if record.get("status") in JOB_ACTIVE_STATUSES:
        return "required_shell"
    if record.get("status") not in JOB_TERMINAL_STATUSES:
        return "owner_unknown"
    if (expects_receipt or record.get("cardstream_integration_id")) and not record.get("card_result_seq"):
        return "result_pending"
    return ""


def _job_collectible(record: dict[str, Any]) -> bool:
    return (record.get("status") in JOB_TERMINAL_STATUSES
            and not required_shell_state(record, expects_receipt=_legacy_web_binding(record)))


def _legacy_web_binding(record: dict[str, Any]) -> bool:
    return bool(record.get("source") == "web" and record.get("notification_enabled")
                and (record.get("owner_task_id") or record.get("root_task_id")))


def web_shell_sessions(root: Path, user: str) -> set[str]:
    return {record["session_id"] for _, record in _iter_job_records(_jobs_dir(root, user, create=False))
            if record.get("source") == "web" and record.get("user") == user
            and record.get("session_id") and record.get("notification_enabled")
            and required_shell_state(record, expects_receipt=_legacy_web_binding(record))}


def _cleanup_background_jobs(directory: Path, *, now: float | None = None) -> None:
    current_time = time.time() if now is None else float(now)
    records = _iter_job_records(directory)
    cutoff = current_time - BACKGROUND_JOB_RETENTION_SECONDS
    for _, record in records:
        if not _job_collectible(record):
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
        if _job_collectible(item[1])
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


def _stale_active_job_ids(
    records: list[tuple[Path, dict[str, Any]]],
    *,
    now: float | None = None,
) -> list[str]:
    """Return old active records that are safe to reconcile outside the lock.

    A fresh ``starting`` record is deliberately left alone: another process may
    still be launching its detached worker and registering the worker identity.
    """

    current_time = time.time() if now is None else float(now)
    cutoff = current_time - BACKGROUND_JOB_RECONCILE_GRACE_SECONDS
    result: list[str] = []
    for _, record in records:
        if (record.get("owner_kind") == FOREGROUND_HANDOFF
                or record.get("status") not in JOB_ACTIVE_STATUSES):
            continue
        updated_at = _parse_timestamp(record.get("updated_at")) or _parse_timestamp(
            record.get("started_at")
        )
        if updated_at is not None and updated_at > cutoff:
            continue
        try:
            result.append(_validate_job_id(record.get("job_id")))
        except ValueError:
            continue
    return result


def _job_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(_job_json(payload), "utf-8")
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
    attempt = 0
    while True:
        try:
            value = json.loads(path.read_text("utf-8"))
            break
        except FileNotFoundError:
            raise KeyError("后台作业不存在") from None
        except PermissionError as exc:
            # A Windows atomic replacement can briefly deny new read handles.
            # Retry only access failures, never malformed JSON or missing jobs.
            if attempt >= len(_RECORD_READ_RETRY_DELAYS):
                raise RuntimeError("后台作业记录损坏或不可读") from exc
            time.sleep(_RECORD_READ_RETRY_DELAYS[attempt])
            attempt += 1
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("后台作业记录损坏或不可读") from exc
    if not isinstance(value, dict) or value.get("schema_version") != JOB_SCHEMA_VERSION:
        raise RuntimeError("后台作业记录版本无效")
    return value


def _reconcile_stale_jobs(root: Path, user: str, directory: Path) -> None:
    with index_lock(root, user):
        _cleanup_background_jobs(directory)
        stale_active = _stale_active_job_ids(_iter_job_records(directory))
    # Reconciliation can inspect and stop a legacy worker. Do it outside the
    # directory lock; reserving a slot still requires a fresh check under it.
    for job_id in stale_active:
        try:
            reconcile_background_job(root, user, job_id)
        except (KeyError, OSError, RuntimeError, ValueError):
            continue


def _require_active_job_slot(directory: Path) -> None:
    active_count = sum(
        1 for _, record in _iter_job_records(directory) if record.get("status") in JOB_ACTIVE_STATUSES
    )
    if active_count >= MAX_ACTIVE_BACKGROUND_JOBS_PER_USER:
        raise ValueError("后台活动作业数量已达到用户上限")


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
    ownership: dict[str, Any] | None = None,
    completion_mode: str = "required",
    label: str = "",
) -> tuple[dict[str, Any], dict[str, Path]]:
    if completion_mode not in {"required", "detached"}:
        raise ValueError("completion_mode 必须是 required/detached")
    from run.infra import diagnostic_text
    ownership = dict(ownership or {})
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
        "root_task_id": str((ownership or {}).get("root_task_id") or ""),
        "owner_task_id": str((ownership or {}).get("owner_task_id") or ""),
        "notification_enabled": bool(ownership and source and session_id),
        "notification_handled": False,
        "completion_mode": completion_mode,
        "label": diagnostic_text(label)[:160],
        **{key: ownership[key] for key in (
            "cardstream_integration_id", "cardstream_generation", "cardstream_meter_key",
            "original_call", "tool_call_id",
        ) if key in ownership},
    }
    directory = paths["record"].parent
    _reconcile_stale_jobs(root, user, directory)
    with index_lock(root, user):
        _bind_job_owner(root, user, record)
        _cleanup_background_jobs(directory)
        _require_active_job_slot(directory)
        reserved = MAX_BACKGROUND_JOB_LOG_BYTES * 2
        if _storage_bytes(directory) + reserved > MAX_BACKGROUND_JOB_STORAGE_BYTES:
            raise ValueError("后台作业日志空间已达到用户上限，请先清理历史作业")
        _atomic_json(paths["record"], record)
    return record, paths


def _bind_job_owner(root: Path, user: str, record: dict[str, Any]) -> None:
    """Runs under the same cross-process index lock as Web retirement."""
    source, sid, task_id = record["source"], record["session_id"], record["owner_task_id"]
    task = None
    web_required = source == "web" and record["completion_mode"] == "required"
    if task_id and (not record.get("cardstream_integration_id") or web_required):
        from run.agents import TaskLedger
        task = TaskLedger(root, user).peek(task_id, source, sid)
        if task and not record.get("cardstream_integration_id"):
            for key in ("cardstream_integration_id", "cardstream_generation", "cardstream_meter_key"):
                record[key] = task.get(key, "")
    if not web_required or not record.get("cardstream_integration_id"):
        return
    from run.cards import CardConflictError, CardStore, IntegrationStore
    child_live = False
    if task_id:
        child_live = bool(task and task.get("root_task_id") == record["root_task_id"]
                          and task.get("cardstream_integration_id") == record["cardstream_integration_id"]
                          and task.get("cardstream_generation") == record.get("cardstream_generation")
                          and (task.get("execution_live") or task.get("status") == "running"))
        if not child_live:
            raise CardConflictError("子任务已退出或归属已改变，不能登记后台作业")
    IntegrationStore(CardStore(root, user), source, sid, record["cardstream_integration_id"]).require_dependency_owner(
        record.get("cardstream_generation"), tool_call_id=record.get("tool_call_id", ""), child_live=child_live,
    )


def write_job_request(path: Path, payload: dict[str, Any]) -> None:
    _atomic_json(path, payload)


def background_job_visible(root: Path, user: str, record: dict[str, Any]) -> bool:
    if record.get("owner_kind") != FOREGROUND_HANDOFF:
        return True
    from run.tools.foreground_handoff import find_handoff_receipt
    return record.get("user") == user and find_handoff_receipt(
        root, user, record["source"], record["session_id"],
        record["cardstream_integration_id"], record["cardstream_generation"],
        record["execution_id"], job_id=record["job_id"],
    ) is not None


def read_background_job(root: Path, user: str, job_id: str) -> dict[str, Any]:
    paths = job_paths(root, user, job_id)
    # Atomic replace gives readers a complete snapshot without a write lock.
    record = _read_record(paths["record"])
    if record.get("owner_kind") == FOREGROUND_HANDOFF and (
        record.get("job_id") != paths["record"].stem or not background_job_visible(root, user, record)
    ):
        raise KeyError("后台作业不存在")
    return record


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
    *,
    expected: dict[str, Any] | None = None,
) -> dict[str, Any]:
    paths = job_paths(root, user, job_id)
    with index_lock(root, user):
        current = _read_record(paths["record"])
        if expected is not None and current != expected:
            return current
        rendered = mutator(dict(current))
        if not isinstance(rendered, dict):
            raise TypeError("后台作业 mutator 必须返回对象")
        if current.get("status") not in JOB_ACTIVE_STATUSES and rendered.get("status") != current.get("status"):
            raise ValueError("后台作业终态不可变，拒绝状态复活")
        rendered["schema_version"] = JOB_SCHEMA_VERSION
        rendered["job_id"] = job_id
        rendered["user"] = user
        rendered["updated_at"] = _now()
        _atomic_json(paths["record"], rendered)
    # The job file is the execution fact. Publishing its history happens after
    # releasing the directory lock, and can be repaired without re-execution.
    return publish_background_result(root, user, rendered)


def publish_background_result(root: Path, user: str, record: dict[str, Any]) -> dict[str, Any]:
    if (record.get("status") not in JOB_TERMINAL_STATUSES
            or record.get("completion_mode") == "detached"
            or not record.get("cardstream_integration_id")
            or record.get("card_result_seq")
            or required_shell_state(record) == "owner_unknown"):
        return record
    from run.tools.background_notifications import persist_shell_result
    try:
        seq = persist_shell_result(root, user, record)
        changes = {"card_result_seq": seq, "card_result_error_type": ""}
    except Exception as error:
        logging.getLogger(__name__).exception("后台 Shell 结果卡提交失败：%s", record["job_id"])
        changes = {"card_result_error_type": type(error).__name__}
    path = job_paths(root, user, record["job_id"])["record"]
    with index_lock(root, user):
        current = _read_record(path)
        # Another publisher may already have committed the same event.
        if not current.get("card_result_seq"):
            current.update(changes)
            _atomic_json(path, current)
        return current


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


def _record_worker_lost(record: dict[str, Any]) -> dict[str, Any]:
    cancelled = bool(record.get("cancel_requested")) and not record.get("timeout_requested")
    record.update(status="cancelled" if cancelled else "interrupted", finished_at=_now(),
                  stop_reason="cancelled_without_live_process" if cancelled else "background_worker_lost",
                  error=None if cancelled else {"code": "background_worker_lost",
                        "message": "已登记的后台命令及管理进程均已退出；未重放命令"})
    return record


def settle_required_shell_exit(root: Path, user: str, record: dict[str, Any], *, now: float) -> dict[str, Any]:
    """Recover an orphan without enforcing the job deadline or signalling a PID."""
    stamp = _parse_timestamp(record.get("updated_at")) or _parse_timestamp(record.get("started_at"))
    if stamp is None or now - stamp < BACKGROUND_JOB_RECONCILE_GRACE_SECONDS:
        return record
    # Between Popen and identity publication a missing PID says nothing about
    # whether the worker or its command started. Check both stored identities.
    for pid_key, start_key in (("pid", "process_started_at"), ("worker_pid", "worker_started_at")):
        started = record.get(start_key)
        if not started or not record.get(pid_key):
            return record
        try:
            observed = process_snapshot(int(record[pid_key]))
        except (OSError, ValueError, TypeError, OverflowError):
            return record
        if process_identity_matches(observed, process_started_at=started) is not False:
            return record

    def settle(current):
        if current["status"] in JOB_ACTIVE_STATUSES:
            _record_worker_lost(current)
        current["execution_exit_confirmed"] = True
        return current

    return update_background_job(root, user, record["job_id"], settle, expected=record)


def reconcile_background_job(root: Path, user: str, job_id: str) -> dict[str, Any]:
    current = read_background_job(root, user, job_id)
    if current.get("owner_kind") == FOREGROUND_HANDOFF:
        # The original execution still owns the deadline and process tree.
        return current
    if current.get("status") in JOB_TERMINAL_STATUSES:
        return publish_background_result(root, user, current)
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

        if _needs_web_exit_evidence(current, process, worker):
            return current
        return update_background_job(root, user, job_id, settle_timeout, expected=current)
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

    if _needs_web_exit_evidence(current, process, worker):
        return current
    return update_background_job(root, user, job_id, _record_worker_lost, expected=current)


def _needs_web_exit_evidence(record, *snapshots):
    if not (record.get("source") == "web" and record.get("completion_mode") == "required"
            and record.get("cardstream_integration_id")):
        return False
    for snapshot, key in zip(snapshots, ("process_started_at", "worker_started_at")):
        if not record.get(key) or process_identity_matches(snapshot, process_started_at=record[key]) is not False:
            return True
    return False


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

    if current.get("owner_kind") == FOREGROUND_HANDOFF:
        def request_owner_stop(record: dict[str, Any]) -> dict[str, Any]:
            if record.get("status") in JOB_ACTIVE_STATUSES:
                record["cancel_requested"] = True
            return record

        return update_background_job(root, user, current["job_id"], request_owner_stop)

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
        "root_task_id",
        "owner_task_id",
        "label",
        "completion_mode",
        "notification_handled",
        "notification_enabled",
        "output_tail",
        "output_tail_complete",
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
    if record.get("owner_kind") == FOREGROUND_HANDOFF and record.get("status") in JOB_TERMINAL_STATUSES:
        result["output"] = record["output"]
        result["output_truncated"] = record["output_truncated"]
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
