"""Read-only archive query primitives; never initialize or migrate a user DB."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from run.config import validate_user_name
from run.history.store_core import HISTORY_DB_FILENAME

TIMEZONE = "Asia/Shanghai"
DATE_BASIS = "updated_at_then_created_at_then_legacy_window_date"
_SHANGHAI = ZoneInfo(TIMEZONE)

# No runtime, uncommitted payload, deleted session or tombstoned physical name.
VISIBLE = """
    w.window_kind='archive'
    AND json_extract(w.data_json, '$.complete')=1
    AND NOT EXISTS (SELECT 1 FROM history_deleted_sessions AS d
        WHERE d.source=w.source AND d.session_id=w.session_id)
    AND NOT EXISTS (SELECT 1 FROM history_deleted_windows AS d
        WHERE d.window_kind='archive' AND d.window_name=w.window_name)
"""
FROM_ARCHIVES = """FROM history_windows AS w
    LEFT JOIN history_sessions AS s ON s.source=w.source AND s.session_id=w.session_id"""
METADATA = """
    w.window_name, w.source, w.session_id, substr(w.title, 1, 200) AS title,
    w.created_at, w.updated_at, w.rounds,
    substr(COALESCE(s.summary, ''), 1, 500) AS summary,
    length(COALESCE(s.summary, ''))>500 AS summary_truncated,
    archive_day(w.updated_at, w.created_at, w.window_name) AS archive_date
"""


def serialized_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def encode_token(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_token(value: str, prefix: str) -> list[Any]:
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        parsed = json.loads(raw)
        if not isinstance(parsed, list) or not parsed or parsed[0] != prefix:
            raise ValueError
        return parsed
    except (ValueError, UnicodeError):
        raise ValueError(f"{prefix} 标识或游标无效") from None


def user_scope(root: Path, user: str) -> str:
    name = validate_user_name(user)
    path = str(
        (root.resolve() / "users" / name / "history" / HISTORY_DB_FILENAME).resolve()
    ).casefold()
    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:24]


def archive_id(root: Path, user: str, row: sqlite3.Row) -> str:
    return encode_token(["archive-v1", user_scope(root, user),
                         row["window_name"], row["source"], row["session_id"]])


def archive_identity(root: Path, user: str, token: str) -> tuple[str, str, str]:
    parsed = decode_token(token, "archive-v1")
    if (len(parsed) != 5 or not all(isinstance(item, str) for item in parsed)
            or parsed[1] != user_scope(root, user) or not parsed[2]):
        raise ValueError("archive_id 不属于当前用户或格式无效")
    # Identifiers are SQL values, never filesystem paths.
    return parsed[2], parsed[3], parsed[4]


def archive_day(updated: str, created: str, window: str) -> str | None:
    for value in (updated, created):
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(_SHANGHAI).date().isoformat()
        except (ValueError, OverflowError):
            continue
    if re.match(r"^\d{4}-\d{2}-\d{2}(?:-|$)", window):
        try:
            return date.fromisoformat(window[:10]).isoformat()
        except ValueError:
            pass
    return None


def archive_time(updated: str, created: str, window: str) -> float:
    for value in (updated, created):
        if value:
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).timestamp()
            except (ValueError, OverflowError, OSError):
                continue
    day = archive_day("", "", window)
    return datetime.fromisoformat(day).replace(tzinfo=_SHANGHAI).timestamp() if day else -1e30


@contextmanager
def archive_database(root: Path, user: str) -> Iterator[sqlite3.Connection | None]:
    name = validate_user_name(user)
    base = root.resolve()
    users_dir = (base / "users").resolve()
    user_dir = base / "users" / name
    if not user_dir.is_dir():
        yield None
        return
    resolved_user = user_dir.resolve()
    try:
        resolved_user.relative_to(users_dir)
    except ValueError:
        raise ValueError("当前用户历史目录超出项目 users 根目录") from None
    path = resolved_user / "history" / HISTORY_DB_FILENAME
    if not path.is_file():
        yield None
        return
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(resolved_user)
    except ValueError:
        raise ValueError("历史数据库路径超出当前用户目录") from None
    database = sqlite3.connect(resolved_path.as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA query_only=ON")
        database.create_function("archive_day", 3, archive_day, deterministic=True)
        database.create_function("archive_time", 3, archive_time, deterministic=True)
        database.execute("BEGIN")  # Metadata, counts and message chunks share one snapshot.
        yield database
    finally:
        database.close()


def metadata_entry(root: Path, user: str, row: sqlite3.Row) -> dict[str, Any]:
    return {
        "archive_id": archive_id(root, user, row),
        "title": row["title"], "summary": row["summary"],
        "summary_source": "session" if row["summary"] else None,
        "summary_truncated": bool(row["summary_truncated"]),
        "source": row["source"], "session_id": row["session_id"],
        "created_at": row["created_at"] or None, "updated_at": row["updated_at"] or None,
        "rounds": row["rounds"], "date": row["archive_date"],
    }
