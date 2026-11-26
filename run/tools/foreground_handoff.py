"""Metadata for an existing foreground Shell, without a second process owner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from typing import Any
from uuid import uuid4

from run.cards import Card, IntegrationStore, card_read_connection, session_key
from run.history import index_lock
from run.infra import diagnostic_text
from run.tools import background_jobs
from run.tools.background_jobs import (
    FOREGROUND_HANDOFF,
    JOB_SCHEMA_VERSION,
    MAX_BACKGROUND_JOB_RECORD_BYTES,
    _atomic_json,
    _cleanup_background_jobs,
    _job_json,
    _now,
    _read_record,
    _require_active_job_slot,
    _storage_bytes,
    job_paths,
    update_background_job,
)
from run.tools.errors import ToolCancelledError, ToolTimeoutError


_OUTPUT_BYTES = 16 * 1024
_ERROR_BYTES = 2 * 1024
_TERMINAL_RECORD_RESERVE = 32 * 1024


class ShellHandoffUncertain(RuntimeError):
    """The receipt could not be verified; never retry the command or its publication."""


def find_handoff_receipt(
    root: Path, user: str, source: str, session_id: str, integration_id: str,
    generation: str, execution_id: str, *, job_id: str | None = None,
) -> Card | None:
    """Read only the immutable original result event; a prepared file proves nothing."""
    key = session_key(source, session_id)
    with card_read_connection(root, user) as db:
        if db is None:
            return None
        row = db.execute(
            "SELECT c.* FROM context_cards c "
            "JOIN card_integrations i ON i.integration_id=c.integration_id AND i.session_key=c.session_key "
            "JOIN card_sessions s ON s.session_key=i.session_key AND s.generation=i.generation "
            "WHERE c.session_key=? AND c.integration_id=? AND c.event_key=? "
            "AND c.raw_kind='tool_result' AND i.generation=? AND s.lifecycle!='deleted'",
            (key, integration_id, f"{execution_id}:result", generation),
        ).fetchone()
    if row is None:
        return None
    card = Card.from_row(row)
    payload = card.raw_payload
    execution, result = payload["execution"], payload["result"]
    if (payload["execution_id"] != execution_id or execution["status"] != "handed_off"
            or execution["execution_live"] is not False or not isinstance(result, dict)
            or result.get("status") != "handed_off" or result.get("ok") is not True
            or not result.get("job_id") or (job_id is not None and result["job_id"] != job_id)):
        return None
    return card


def _json_bytes(value: str) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _bounded_text(value: str, limit: int) -> tuple[str, bool]:
    if _json_bytes(value) <= limit:
        return value, False
    suffix = "\n…"
    lo, hi = 0, min(len(value), limit)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _json_bytes(value[:mid] + suffix) <= limit:
            lo = mid
        else:
            hi = mid - 1
    return value[:lo] + suffix, True


@dataclass(frozen=True, slots=True)
class ForegroundHandoff:
    root: Path
    user: str
    path: Path
    record: dict[str, Any]

    @property
    def job_id(self) -> str:
        return self.record["job_id"]

    @classmethod
    def prepare(cls, integration: IntegrationStore, execution_id: str, notice: dict[str, Any]):
        """Reserve a regular JSON record. The caller holds the user's metadata lock."""
        root, user = integration.store.root, integration.store.user
        job_id = f"job_{uuid4().hex}"
        path = job_paths(root, user, job_id)["record"]
        wall_offset = time.time() - time.monotonic()
        record = {
            "schema_version": JOB_SCHEMA_VERSION,
            "owner_kind": FOREGROUND_HANDOFF,
            "job_id": job_id, "user": user, "source": integration.source,
            "session_id": integration.session_id,
            "cardstream_integration_id": integration.id,
            "cardstream_generation": integration.generation,
            "execution_id": execution_id,
            "status": "running", "pid": notice["pid"],
            "working_dir": notice["cwd"], "shell_type": notice["shell_type"],
            "started_at": datetime.fromtimestamp(wall_offset + notice["started_at"], timezone.utc).isoformat(),
            "updated_at": _now(), "finished_at": "",
            "deadline_at": wall_offset + notice["deadline_at"],
            "timeout_seconds": notice["deadline_at"] - notice["started_at"],
            "exit_code": None, "error": None, "cancel_requested": False, "timeout_requested": False,
            "stop_reason": "", "completion_mode": "detached", "notification_enabled": False,
        }
        _cleanup_background_jobs(path.parent)
        _require_active_job_slot(path.parent)
        rendered = _job_json(record)
        size = len(rendered.encode("utf-8")) + (rendered.count("\n") if os.name == "nt" else 0)
        if size + _TERMINAL_RECORD_RESERVE > MAX_BACKGROUND_JOB_RECORD_BYTES:
            raise ValueError("Shell 移交记录超过大小上限")
        if _storage_bytes(path.parent) + size + _TERMINAL_RECORD_RESERVE > background_jobs.MAX_BACKGROUND_JOB_STORAGE_BYTES:
            raise ValueError("后台作业存储空间已达到用户上限")
        try:
            _atomic_json(path, record)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return cls(root, user, path, record)

    def receipt(self) -> Card | None:
        record = self.record
        return find_handoff_receipt(
            self.root, self.user, record["source"], record["session_id"],
            record["cardstream_integration_id"], record["cardstream_generation"],
            record["execution_id"], job_id=self.job_id,
        )

    def discard(self) -> None:
        """Remove only our preparation after a definitive negative receipt lookup."""
        with index_lock(self.root, self.user):
            self.path.unlink(missing_ok=True)

    def cancel_requested(self) -> bool:
        return _read_record(self.path).get("cancel_requested") is True

    def finish(self, value: Any, error: BaseException | None) -> None:
        """The execution thread calls this after process cleanup, before releasing waiters."""
        result = value if isinstance(value, dict) else {}
        exit_code = result.get("exit_code")
        timed_out = isinstance(error, ToolTimeoutError) or result.get("timed_out") is True
        cancelled = isinstance(error, ToolCancelledError) or result.get("cancelled") is True
        error_type = getattr(error, "remote_exception_type", "") or type(error).__name__
        summary, truncated = _bounded_text(str(result.get("output") or ""), _OUTPUT_BYTES)
        if cancelled:
            status, reason, failure = "cancelled", "execution_cancelled", None
        elif timed_out:
            status, reason, failure = "failed", "execution_timeout", {
                "code": "shell_timeout", "message": "Shell 命令超过原执行期限",
            }
        elif result.get("ok") is True and error is None:
            status, reason, failure = "completed", "", None
        elif error is not None:
            status = "interrupted" if error_type in {
                "IsolatedProcessDisconnected", "ToolProcessExited", "InvalidIsolatedResult",
            } else "failed"
            reason, failure = "execution_error", {
                "code": error_type, "message": _bounded_text(diagnostic_text(error), _ERROR_BYTES)[0],
            }
        else:
            status, reason, failure = "failed", "shell_exit", {
                "code": "shell_exit", "message": "Shell 命令未成功退出",
            }

        def settle(record):
            return {
                **record, "status": status, "finished_at": _now(), "exit_code": exit_code,
                "timeout_requested": timed_out,
                "stop_reason": "user_cancel" if cancelled and record["cancel_requested"] else reason,
                "error": failure, "output": summary,
                "output_truncated": truncated or result.get("truncated") is True,
            }

        update_background_job(self.root, self.user, self.job_id, settle)
