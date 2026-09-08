"""Compression, redaction, and blob storage for task-plan revisions."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
import zlib
from typing import Any

from .errors import PlanError, PlanValidationError


_COMPRESSED_SNAPSHOT_PREFIX = "zlib-base64:"
_SNAPSHOT_COMPRESSION_THRESHOLD = 4096
_REVISION_BLOB_THRESHOLD = 4096
_REVISION_BLOB_KEY = "$task_plan_blob"
_REVISION_BLOB_VERSION_KEY = "$task_plan_blob_version"
_REVISION_REDACTED_KEY = "$task_plan_redacted"
_REVISION_REDACTED_TEXT = "[task-plan-secret-redacted]"
_MAX_SNAPSHOT_DECOMPRESSED_BYTES = 16 * 1024 * 1024
_SENSITIVE_ARGUMENT_KEYS = frozenset({
    "authorization",
    "cookie",
    "api_key",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "secret_key",
    "signing_key",
    "encryption_key",
    "client_key",
    "private_key",
    "token",
})
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?ix)\b(?:authorization|api[_ -]?key|access[_ -]?token|refresh[_ -]?token|"
    r"password|secret(?:[_ -]?key)?|signing[_ -]?key|encryption[_ -]?key|"
    r"client[_ -]?key|cookie|private[_ -]?key|token)\b"
    r"\s*(?:=|:|：|是)\s*[^\s,;\]}]{8,}"
)
_BEARER_SECRET_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_OPENAI_SECRET_RE = re.compile(r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{16,}")
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_value(value: Any, default: Any) -> Any:
    if not isinstance(value, str):
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _snapshot_text(plan: Any) -> str:
    """Serialize a revision snapshot without changing the SQLite schema."""

    raw = _json_text(plan)
    if len(raw) < _SNAPSHOT_COMPRESSION_THRESHOLD:
        return raw
    compressed = zlib.compress(raw.encode("utf-8"), level=6)
    encoded = base64.b64encode(compressed).decode("ascii")
    rendered = f"{_COMPRESSED_SNAPSHOT_PREFIX}{encoded}"
    return rendered if len(rendered) < len(raw) else raw


def _snapshot_value(value: Any) -> Any:
    if not isinstance(value, str):
        return None
    if not value.startswith(_COMPRESSED_SNAPSHOT_PREFIX):
        return _json_value(value, None)
    encoded = value[len(_COMPRESSED_SNAPSHOT_PREFIX):]
    try:
        compressed = base64.b64decode(encoded, validate=True)
        decompressor = zlib.decompressobj()
        raw_bytes = decompressor.decompress(
            compressed,
            _MAX_SNAPSHOT_DECOMPRESSED_BYTES + 1,
        )
        if (
            len(raw_bytes) > _MAX_SNAPSHOT_DECOMPRESSED_BYTES
            or decompressor.unconsumed_tail
        ):
            return None
        raw_bytes += decompressor.flush()
        if len(raw_bytes) > _MAX_SNAPSHOT_DECOMPRESSED_BYTES:
            return None
        raw = raw_bytes.decode("utf-8")
    except (ValueError, zlib.error, UnicodeDecodeError):
        return None
    return _json_value(raw, None)


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().casefold()).strip("_")


def _is_sensitive_key(value: Any) -> bool:
    key = _normalized_key(value)
    return (
        key in _SENSITIVE_ARGUMENT_KEYS
        or key.endswith("_token")
        or key.endswith("_secret")
    )


def _sensitive_argument_values(plan: dict[str, Any]) -> dict[str, str]:
    found: dict[str, str] = {}

    def visit(value: Any, path: tuple[str, ...]) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = (*path, str(key))
                if _is_sensitive_key(key):
                    found[".".join(child_path)] = _json_text(child)
                else:
                    visit(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, (*path, str(index)))

    for step in plan.get("steps") or []:
        if not isinstance(step, dict):
            continue
        arguments = step.get("tool_arguments")
        if isinstance(arguments, dict):
            visit(arguments, (str(step.get("step_id") or "step"), "tool_arguments"))
    return found


def _validate_sensitive_argument_change(
    updated: dict[str, Any],
    *,
    current: dict[str, Any] | None = None,
) -> None:
    updated_values = _sensitive_argument_values(updated)
    if not updated_values:
        return
    current_values = _sensitive_argument_values(current or {})
    changed = sorted(
        path
        for path, serialized in updated_values.items()
        if current_values.get(path) != serialized
    )
    if changed:
        preview = ", ".join(changed[:3])
        if len(changed) > 3:
            preview += f" 等 {len(changed)} 项"
        raise PlanValidationError(
            "任务计划不得持久化密码、Token、Cookie、API Key 或私钥；"
            f"请改用环境变量名或安全引用：{preview}"
        )


def _redact_secret_text(value: str) -> str:
    if (
        _SECRET_ASSIGNMENT_RE.search(value)
        or _BEARER_SECRET_RE.search(value)
        or _OPENAI_SECRET_RE.search(value)
        or _PRIVATE_KEY_RE.search(value)
    ):
        return _REVISION_REDACTED_TEXT
    return value


def _redact_revision_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                {_REVISION_REDACTED_KEY: True}
                if _is_sensitive_key(key)
                else _redact_revision_secrets(child)
            )
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_redact_revision_secrets(item) for item in value]
    if isinstance(value, str):
        return _redact_secret_text(value)
    return value


def _blob_reference(digest: str) -> dict[str, Any]:
    return {
        _REVISION_BLOB_KEY: digest,
        _REVISION_BLOB_VERSION_KEY: 1,
    }


def _is_blob_reference(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {_REVISION_BLOB_KEY, _REVISION_BLOB_VERSION_KEY}
        and value.get(_REVISION_BLOB_VERSION_KEY) == 1
        and isinstance(value.get(_REVISION_BLOB_KEY), str)
        and re.fullmatch(r"[0-9a-f]{64}", value[_REVISION_BLOB_KEY]) is not None
    )


def _externalize_revision_values(
    database: sqlite3.Connection,
    plan_id: str,
    value: Any,
) -> Any:
    if isinstance(value, dict):
        rendered: dict[str, Any] = {}
        for key, child in value.items():
            if key in {"tool_arguments", "result", "error"} and child is not None:
                raw = _json_text(child).encode("utf-8")
                if len(raw) >= _REVISION_BLOB_THRESHOLD:
                    digest = hashlib.sha256(raw).hexdigest()
                    database.execute(
                        """
                        INSERT OR IGNORE INTO task_plan_revision_blobs(
                            plan_id, digest, payload
                        ) VALUES(?, ?, ?)
                        """,
                        (plan_id, digest, _snapshot_text(child)),
                    )
                    rendered[str(key)] = _blob_reference(digest)
                    continue
            rendered[str(key)] = _externalize_revision_values(
                database,
                plan_id,
                child,
            )
        return rendered
    if isinstance(value, list):
        return [
            _externalize_revision_values(database, plan_id, item)
            for item in value
        ]
    return value


def _restore_revision_values(
    database: sqlite3.Connection,
    plan_id: str,
    value: Any,
) -> Any:
    if _is_blob_reference(value):
        digest = str(value[_REVISION_BLOB_KEY])
        row = database.execute(
            """
            SELECT payload FROM task_plan_revision_blobs
            WHERE plan_id=? AND digest=?
            """,
            (plan_id, digest),
        ).fetchone()
        if row is None:
            raise PlanError(f"计划 {plan_id} 的 revision 大字段 {digest[:12]} 缺失")
        restored = _snapshot_value(row["payload"])
        if restored is None:
            raise PlanError(f"计划 {plan_id} 的 revision 大字段 {digest[:12]} 损坏")
        return restored
    if isinstance(value, dict):
        return {
            str(key): _restore_revision_values(database, plan_id, child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [
            _restore_revision_values(database, plan_id, item)
            for item in value
        ]
    return value


def _contains_revision_redaction(value: Any) -> bool:
    if isinstance(value, dict):
        if (
            set(value) == {_REVISION_REDACTED_KEY}
            and value.get(_REVISION_REDACTED_KEY) is True
        ):
            return True
        return any(_contains_revision_redaction(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_revision_redaction(item) for item in value)
    if isinstance(value, str):
        return value == _REVISION_REDACTED_TEXT
    return False


__all__ = [
    "_contains_revision_redaction",
    "_externalize_revision_values",
    "_json_text",
    "_json_value",
    "_normalized_key",
    "_redact_secret_text",
    "_redact_revision_secrets",
    "_restore_revision_values",
    "_snapshot_text",
    "_snapshot_value",
    "_validate_sensitive_argument_change",
]
