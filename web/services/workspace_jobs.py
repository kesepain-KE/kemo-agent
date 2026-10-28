"""Read-only, bounded views of user-owned managed Shell jobs and their logs."""
from __future__ import annotations

from datetime import datetime, timezone
import codecs
import json
import locale
import os
from pathlib import Path
import stat
from typing import Any

from run.config import user_dir
from run.config import _contains_reparse_point
from run.tools import JOB_SCHEMA_VERSION, public_background_job
from web.errors import InvalidRequestError, NotFoundError

LOG_TAIL_BYTES = 64 * 1024
MAX_RECORD_BYTES = 128 * 1024
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _decode_log(data: bytes, *, truncated: bool, active: bool) -> str:
    """Prefer UTF-8, then the host's shell encoding; tolerate an in-flight suffix."""
    utf8 = data
    if truncated:
        # A UTF-8 tail may start inside a character; don't modify legacy bytes.
        while utf8 and utf8[0] & 0xC0 == 0x80:
            utf8 = utf8[1:]
    try:
        return codecs.getincrementaldecoder("utf-8")().decode(utf8, final=not active)
    except UnicodeDecodeError:
        encoding = locale.getpreferredencoding(False) or "utf-8"
        if encoding.lower().replace("-", "") != "utf8":
            for offset in range(2 if truncated else 1):
                try:
                    return codecs.getincrementaldecoder(encoding)().decode(data[offset:], final=not active)
                except (LookupError, UnicodeDecodeError):
                    continue
        return utf8.decode("utf-8", errors="replace")


def _directory(backend: Any, user: str) -> tuple[str, Path]:
    user = backend.require_user(user)
    base = user_dir(user, backend.root)
    directory = base / "runtime" / "background_jobs"
    if _contains_reparse_point(base, backend.root) or _contains_reparse_point(directory, base):
        raise InvalidRequestError("后台作业目录不能是符号链接或目录联接")
    return user, directory


def _read_safe(path: Path, limit: int, *, tail: bool = False) -> tuple[bytes, int]:
    """Never follow links or allocate according to an untrusted file's size."""
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or getattr(before, "st_file_attributes", 0) & _REPARSE):
        raise ValueError("后台作业文件不是普通独立文件")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as handle:
        current = os.fstat(handle.fileno())
        if (current.st_ino, current.st_dev) != (before.st_ino, before.st_dev):
            raise ValueError("后台作业文件在读取时发生变化")
        if tail:
            handle.seek(max(0, current.st_size - limit))
        elif current.st_size > limit:
            raise ValueError("后台作业记录过大")
        return handle.read(limit), current.st_size


def _record(directory: Path, job_id: str, user: str) -> dict[str, Any]:
    import re
    if not re.fullmatch(r"job_[a-f0-9]{32}", job_id):
        raise NotFoundError("后台作业不存在")
    try:
        raw, _ = _read_safe(directory / f"{job_id}.json", MAX_RECORD_BYTES)
        record = json.loads(raw)
        if (not isinstance(record, dict) or record.get("schema_version") != JOB_SCHEMA_VERSION
                or record.get("job_id") != job_id or record.get("user") != user):
            raise ValueError("后台作业记录不匹配")
        return record
    except (OSError, ValueError) as exc:
        raise NotFoundError("后台作业不存在或不可读取") from exc


def _public(record: dict[str, Any], root: Path) -> dict[str, Any]:
    return {
        **public_background_job(record, root=root),
        "source": str(record.get("source") or ""),
        "session_id": str(record.get("session_id") or ""),
        "command_digest": str(record.get("command_digest") or ""),
    }


def list_jobs(backend: Any, user: str, *, session_id: str = "", source: str = "",
              page: int = 1, page_size: int = 20) -> dict[str, Any]:
    user, directory = _directory(backend, user)
    records = []
    # Managed-job retention is already bounded by the executor; never perform cleanup here.
    for candidate in sorted(directory.glob("job_*.json"))[:1024]:
        if candidate.name.endswith(".request.json"):
            continue
        try:
            record = _record(directory, candidate.stem, user)
        except NotFoundError:
            continue
        if session_id and record.get("session_id") != session_id:
            continue
        if source and record.get("source") != source:
            continue
        records.append(record)
    records.sort(key=lambda item: (str(item.get("started_at") or ""), item["job_id"]), reverse=True)
    active = {"starting", "running", "cancelling"}
    total = len(records)
    return {
        "user": user,
        "jobs": [_public(record, backend.root) for record in records[(page - 1) * page_size:page * page_size]],
        "active_count": sum(record.get("status") in active for record in records),
        "total": total, "page": page, "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "read_only": True,
    }


def job_detail(backend: Any, user: str, job_id: str, *, session_id: str = "",
               source: str = "") -> dict[str, Any]:
    user, directory = _directory(backend, user)
    record = _record(directory, job_id, user)
    if ((session_id and record.get("session_id") != session_id)
            or (source and record.get("source") != source)):
        raise NotFoundError("后台作业不存在")
    streams = {}
    for stream in ("stdout", "stderr"):
        try:
            data, size = _read_safe(directory / f"{job_id}.{stream}.log", LOG_TAIL_BYTES, tail=True)
            text = _decode_log(data, truncated=size > LOG_TAIL_BYTES,
                               active=record.get("status") in {"starting", "running", "cancelling"})
            streams[stream] = {"text": text, "size": size,
                               "truncated": size > LOG_TAIL_BYTES, "error": ""}
        except FileNotFoundError:
            streams[stream] = {"text": "", "size": 0, "truncated": False, "error": ""}
        except (OSError, ValueError):
            streams[stream] = {"text": "", "size": 0, "truncated": False, "error": "日志暂不可读取"}
    return {
        "user": user, "job": _public(record, backend.root), "streams": streams,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True, "tail_bytes": LOG_TAIL_BYTES,
    }
