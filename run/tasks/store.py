"""用户隔离的任务计划存储，具有原子写入和版本检查。"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
import threading
import uuid
import zlib
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from run.config import truncate_chars

SCHEMA_VERSION = 1
ROLLBACK_SAFE_PLAN_STATUSES = frozenset({"pending", "paused", "failed"})
TASK_PLAN_DB_FILENAME = "task_plans.sqlite3"
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
    "private_key",
    "token",
})
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?ix)\b(?:authorization|api[_ -]?key|access[_ -]?token|refresh[_ -]?token|"
    r"password|secret|cookie|private[_ -]?key|token)\b"
    r"\s*(?:=|:|：|是)\s*[^\s,;\]}]{8,}"
)
_BEARER_SECRET_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_OPENAI_SECRET_RE = re.compile(r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{16,}")
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
PLAN_ID_RE = re.compile(r"^plan_[0-9a-f]{8}$")
STEP_ID_RE = re.compile(r"^step_\d+$")
PLAN_STATUSes = frozenset({
    "pending", "approved", "running", "paused", "completed", "failed", "cancelled",
})
STEP_STATUSes = frozenset({
    "pending", "running", "completed", "failed", "skipped", "cancelled",
})
# 任务计划管理工具名称绝不能显示为执行步骤。
_BLOCKED_TOOL_PREFIXES = ("task_plan_",)
_BLOCKED_TOOL_NAMES = frozenset({"task_plan"})

_STORE_LOCKS: dict[tuple[str, str], threading.RLock] = {}
_STORE_LOCKS_GUARD = threading.Lock()
_READY_DATABASES: set[str] = set()
_READY_DATABASES_GUARD = threading.Lock()


def _store_lock(root: Path, user: str) -> threading.RLock:
    key = (str(root.resolve()).casefold(), user)
    with _STORE_LOCKS_GUARD:
        return _STORE_LOCKS.setdefault(key, threading.RLock())


class PlanError(RuntimeError):
    pass


class PlanNotFoundError(PlanError):
    pass


class PlanValidationError(PlanError):
    pass


class PlanConflictError(PlanError):
    pass


@dataclass(frozen=True, slots=True)
class TaskPlanSelection:
    text: str
    source_files: tuple[str, ...]
    original_chars: int
    injected_chars: int
    original_items: int
    injected_items: int
    truncated: bool


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_plan_id() -> str:
    return f"plan_{uuid.uuid4().hex[:8]}"


def _plan_dir(root: Path, user: str) -> Path:
    return root / "users" / user / "task_plan"


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
    """Serialize a revision snapshot without changing the SQLite schema.

    Existing databases contain plain JSON. Larger new snapshots use an explicit,
    self-describing compressed representation so revision history remains append-only
    without multiplying large tool results for every state transition.
    """

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
    """Remove obvious credential-shaped text before it reaches plan storage.

    This intentionally requires a label/assignment or a recognizable token
    prefix.  Ordinary prose such as "说明 token 的使用方式" is kept intact.
    """
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
        if set(value) == {_REVISION_REDACTED_KEY} and value.get(_REVISION_REDACTED_KEY) is True:
            return True
        return any(_contains_revision_redaction(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_revision_redaction(item) for item in value)
    if isinstance(value, str):
        return value == _REVISION_REDACTED_TEXT
    return False


def _validate_step(step: dict[str, Any], index: int, tool_names: set[str] | None) -> None:
    step_id = step.get("step_id")
    if not isinstance(step_id, str) or not STEP_ID_RE.fullmatch(step_id):
        raise PlanValidationError(f"步骤 {index} 的 step_id 无效：{step_id!r}")
    title = step.get("title")
    if not isinstance(title, str) or not title.strip():
        raise PlanValidationError(f"步骤 {step_id} 缺少 title")
    description = step.get("description")
    if not isinstance(description, str) or not description.strip():
        raise PlanValidationError(f"步骤 {step_id} 缺少 description")
    status = step.get("status", "pending")
    if not isinstance(status, str) or status not in STEP_STATUSes:
        raise PlanValidationError(f"步骤 {step_id} 状态无效：{status!r}")
    depends_on = step.get("depends_on") or []
    if not isinstance(depends_on, list) or not all(isinstance(d, str) for d in depends_on):
        raise PlanValidationError(f"步骤 {step_id} 的 depends_on 必须是字符串列表")
    critical = step.get("critical")
    if not isinstance(critical, bool):
        raise PlanValidationError(f"步骤 {step_id} 的 critical 必须是布尔值")
    tool_name = step.get("tool_name")
    if tool_name is not None:
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise PlanValidationError(f"步骤 {step_id} 的 tool_name 无效")
        if tool_name in _BLOCKED_TOOL_NAMES or any(
            tool_name.startswith(prefix) for prefix in _BLOCKED_TOOL_PREFIXES
        ):
            raise PlanValidationError(
                f"步骤 {step_id} 的工具 {tool_name!r} 是管理工具，不能作为执行步骤"
            )
        if tool_names is not None and tool_name not in tool_names:
            raise PlanValidationError(f"步骤 {step_id} 的工具 {tool_name!r} 不在可用工具列表中")
    tool_arguments = step.get("tool_arguments")
    if tool_arguments is not None and not isinstance(tool_arguments, dict):
        raise PlanValidationError(f"步骤 {step_id} 的 tool_arguments 必须是对象")
    result = step.get("result")
    if result is not None and not isinstance(result, (dict, str, list, int, float, bool)):
        raise PlanValidationError(f"步骤 {step_id} 的 result 类型无效")
    error = step.get("error")
    if error is not None and not isinstance(error, dict):
        raise PlanValidationError(f"步骤 {step_id} 的 error 必须是对象")


def _check_cycle(steps: list[dict[str, Any]]) -> None:
    step_ids = {s["step_id"] for s in steps}
    for step in steps:
        for dep in step.get("depends_on") or []:
            if dep not in step_ids:
                raise PlanValidationError(
                    f"步骤 {step['step_id']} 依赖不存在的步骤：{dep!r}"
                )
        # DFS循环检测
    color: dict[str, str] = {sid: "white" for sid in step_ids}
    def dfs(sid: str) -> None:
        color[sid] = "gray"
        step = next(s for s in steps if s["step_id"] == sid)
        for dep in step.get("depends_on") or []:
            if color[dep] == "gray":
                raise PlanValidationError(f"检测到循环依赖：{sid} → {dep}")
            if color[dep] == "white":
                dfs(dep)
        color[sid] = "black"
    for sid in step_ids:
        if color[sid] == "white":
            dfs(sid)


def _validate_plan(data: dict[str, Any], *, tool_names: set[str] | None = None) -> None:
    if not isinstance(data, dict):
        raise PlanValidationError("计划必须是 JSON 对象")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise PlanValidationError(f"schema_version 必须为 {SCHEMA_VERSION}")
    plan_id = data.get("plan_id")
    if not isinstance(plan_id, str) or not PLAN_ID_RE.fullmatch(plan_id):
        raise PlanValidationError(f"plan_id 无效：{plan_id!r}")
    if not isinstance(data.get("title"), str) or not data["title"].strip():
        raise PlanValidationError("title 不能为空")
    if not isinstance(data.get("description"), str) or not data["description"].strip():
        raise PlanValidationError("description 不能为空")
    if not isinstance(data.get("user"), str) or not data["user"].strip():
        raise PlanValidationError("user 不能为空")
    status = data.get("status")
    if not isinstance(status, str) or status not in PLAN_STATUSes:
        raise PlanValidationError(f"计划状态无效：{status!r}")
    auto_accept = data.get("auto_accept")
    if not isinstance(auto_accept, bool):
        raise PlanValidationError("auto_accept 必须是布尔值")
    reminder = data.get("reminder", "")
    if not isinstance(reminder, str):
        raise PlanValidationError("reminder 必须是字符串")
    revision = data.get("revision")
    if not isinstance(revision, int) or revision < 1:
        raise PlanValidationError("revision 必须是正整数")
    steps = data.get("steps")
    if not isinstance(steps, list) or len(steps) == 0:
        raise PlanValidationError("steps 不能为空")
    seen_ids: set[str] = set()
    for i, step in enumerate(steps):
        _validate_step(step, i, tool_names)
        if step["step_id"] in seen_ids:
            raise PlanValidationError(f"步骤 ID 重复：{step['step_id']}")
        seen_ids.add(step["step_id"])
    _check_cycle(steps)


def normalize_plan(
    *,
    plan_id: str | None = None,
    title: str,
    description: str,
    user: str,
    source: str = "cli",
    session_id: str = "default",
    steps: list[dict[str, Any]],
    auto_accept: bool = False,
    reminder: str = "",
    status: str = "pending",
    revision: int = 1,
    created_at: str | None = None,
    current_step: str | None = None,
    tool_names: set[str] | None = None,
) -> dict[str, Any]:
    """Build a fully validated plan dict ready for storage."""
    pid = plan_id or _generate_plan_id()
    now = _now()
    normalized_steps: list[dict[str, Any]] = []
    for step in steps:
        normalized_steps.append({
            "step_id": step["step_id"],
            "title": step["title"],
            "description": step["description"],
            "status": step.get("status", "pending"),
            "depends_on": list(step.get("depends_on") or []),
            "tool_name": step.get("tool_name"),
            "tool_arguments": dict(step.get("tool_arguments") or {}),
            "critical": bool(step.get("critical", True)),
            "result": step.get("result"),
            "error": step.get("error"),
            "started_at": step.get("started_at", ""),
            "finished_at": step.get("finished_at", ""),
        })
    plan = {
        "schema_version": SCHEMA_VERSION,
        "plan_id": pid,
        "title": title,
        "description": description,
        "user": user,
        "source": source,
        "session_id": session_id,
        "status": status,
        "auto_accept": auto_accept,
        "reminder": reminder,
        "revision": revision,
        "created_at": created_at or now,
        "updated_at": now,
        "current_step": (
            current_step
            if current_step is not None
            else normalized_steps[0]["step_id"] if normalized_steps else ""
        ),
        "steps": normalized_steps,
    }
    _validate_plan(plan, tool_names=tool_names)
    return plan


class PlanStore:
    """SQLite-authoritative plan storage with one-time JSON migration."""

    def __init__(self, root: Path, user: str) -> None:
        self.root = root.resolve()
        self.user = user
        self._dir = _plan_dir(self.root, self.user)
        self.path = self._dir / TASK_PLAN_DB_FILENAME
        self._lock = _store_lock(self.root, self.user)

    def _ensure_database(self) -> None:
        key = str(self.path.resolve()).casefold()
        with _READY_DATABASES_GUARD:
            if key in _READY_DATABASES and self.path.is_file():
                return
            _READY_DATABASES.discard(key)
            self._dir.mkdir(parents=True, exist_ok=True)
            database = sqlite3.connect(self.path, timeout=5.0)
            database.row_factory = sqlite3.Row
            try:
                database.execute("PRAGMA journal_mode=WAL")
                database.execute("PRAGMA synchronous=NORMAL")
                database.execute("PRAGMA foreign_keys=ON")
                database.execute("PRAGMA busy_timeout=5000")
                database.executescript(
                """
                CREATE TABLE IF NOT EXISTS task_plan_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_plans (
                    plan_id TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    user TEXT NOT NULL,
                    source TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    auto_accept INTEGER NOT NULL,
                    reminder TEXT NOT NULL DEFAULT '',
                    revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    current_step TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_task_plans_status_time
                    ON task_plans(status, updated_at DESC, plan_id);
                CREATE INDEX IF NOT EXISTS idx_task_plans_claim
                    ON task_plans(status, created_at, plan_id);
                CREATE TABLE IF NOT EXISTS task_plan_steps (
                    plan_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    step_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    tool_name TEXT,
                    tool_arguments_json TEXT NOT NULL DEFAULT '{}',
                    critical INTEGER NOT NULL,
                    result_json TEXT,
                    error_json TEXT,
                    started_at TEXT NOT NULL DEFAULT '',
                    finished_at TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(plan_id, step_id),
                    UNIQUE(plan_id, position),
                    FOREIGN KEY(plan_id) REFERENCES task_plans(plan_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS task_plan_dependencies (
                    plan_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    depends_on_step_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    PRIMARY KEY(plan_id, step_id, depends_on_step_id),
                    FOREIGN KEY(plan_id, step_id)
                        REFERENCES task_plan_steps(plan_id, step_id) ON DELETE CASCADE,
                    FOREIGN KEY(plan_id, depends_on_step_id)
                        REFERENCES task_plan_steps(plan_id, step_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS task_plan_revisions (
                    plan_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    plan_json TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(plan_id, revision),
                    FOREIGN KEY(plan_id) REFERENCES task_plans(plan_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS task_plan_revision_blobs (
                    plan_id TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY(plan_id, digest),
                    FOREIGN KEY(plan_id) REFERENCES task_plans(plan_id) ON DELETE CASCADE
                );
                """
                )
                database.execute(
                    "INSERT INTO task_plan_meta(key, value) VALUES('schema_version', '1') "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
                )
                for row in database.execute(
                    """
                    SELECT plan_id FROM task_plans AS plans
                    WHERE NOT EXISTS (
                        SELECT 1 FROM task_plan_revisions AS revisions
                        WHERE revisions.plan_id=plans.plan_id
                    )
                    ORDER BY plan_id
                    """
                ).fetchall():
                    plan = self._load(database, str(row["plan_id"]))
                    if plan is not None:
                        self._save_revision(database, plan, note="迁移现有计划")
                database.commit()
            finally:
                database.close()
            _READY_DATABASES.add(key)

    @contextmanager
    def _connection(self, *, write: bool = False):
        self._ensure_database()
        database = sqlite3.connect(self.path, timeout=5.0)
        database.row_factory = sqlite3.Row
        try:
            database.execute("PRAGMA foreign_keys=ON")
            database.execute("PRAGMA busy_timeout=5000")
            if write:
                database.execute("BEGIN IMMEDIATE")
            else:
                database.execute("PRAGMA query_only=ON")
            yield database
            if write:
                database.commit()
        except BaseException:
            if write:
                database.rollback()
            raise
        finally:
            database.close()

    @staticmethod
    def _save(database: sqlite3.Connection, plan: dict[str, Any]) -> None:
        database.execute(
            """
            INSERT INTO task_plans(
                plan_id, schema_version, title, description, user, source,
                session_id, status, auto_accept, reminder, revision,
                created_at, updated_at, current_step
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(plan_id) DO UPDATE SET
                schema_version=excluded.schema_version,
                title=excluded.title,
                description=excluded.description,
                user=excluded.user,
                source=excluded.source,
                session_id=excluded.session_id,
                status=excluded.status,
                auto_accept=excluded.auto_accept,
                reminder=excluded.reminder,
                revision=excluded.revision,
                created_at=excluded.created_at,
                updated_at=excluded.updated_at,
                current_step=excluded.current_step
            """,
            (
                plan["plan_id"], int(plan["schema_version"]),
                _redact_secret_text(str(plan["title"])),
                _redact_secret_text(str(plan["description"])),
                str(plan["user"]), str(plan.get("source") or "cli"),
                str(plan.get("session_id") or "default"), plan["status"],
                1 if plan["auto_accept"] else 0,
                _redact_secret_text(str(plan.get("reminder") or "")),
                int(plan["revision"]), str(plan.get("created_at") or ""),
                str(plan.get("updated_at") or ""), str(plan.get("current_step") or ""),
            ),
        )
        database.execute(
            "DELETE FROM task_plan_dependencies WHERE plan_id=?", (plan["plan_id"],)
        )
        database.execute(
            "DELETE FROM task_plan_steps WHERE plan_id=?", (plan["plan_id"],)
        )
        for position, step in enumerate(plan["steps"]):
            database.execute(
                """
                INSERT INTO task_plan_steps(
                    plan_id, position, step_id, title, description, status,
                    tool_name, tool_arguments_json, critical, result_json,
                    error_json, started_at, finished_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan["plan_id"], position, step["step_id"],
                    _redact_secret_text(str(step["title"])),
                    _redact_secret_text(str(step["description"])),
                    step.get("status", "pending"),
                    step.get("tool_name"),
                    _json_text(_redact_revision_secrets(step.get("tool_arguments") or {})),
                    1 if step.get("critical", True) else 0,
                    _json_text(_redact_revision_secrets(step.get("result")))
                    if step.get("result") is not None else None,
                    _json_text(_redact_revision_secrets(step.get("error")))
                    if step.get("error") is not None else None,
                    str(step.get("started_at") or ""), str(step.get("finished_at") or ""),
                ),
            )
        for step in plan["steps"]:
            for position, dependency in enumerate(step.get("depends_on") or []):
                database.execute(
                    """
                    INSERT INTO task_plan_dependencies(
                        plan_id, step_id, depends_on_step_id, position
                    ) VALUES(?, ?, ?, ?)
                    """,
                    (plan["plan_id"], step["step_id"], dependency, position),
                )

    @staticmethod
    def _save_revision(
        database: sqlite3.Connection,
        plan: dict[str, Any],
        *,
        note: str = "",
    ) -> None:
        plan_id = str(plan["plan_id"])
        snapshot = _externalize_revision_values(
            database,
            plan_id,
            _redact_revision_secrets(plan),
        )
        database.execute(
            """
            INSERT INTO task_plan_revisions(
                plan_id, revision, plan_json, note, created_at
            ) VALUES(?, ?, ?, ?, ?)
            """,
            (
                plan_id,
                int(plan["revision"]),
                _snapshot_text(snapshot),
                _redact_secret_text(str(note or "")),
                _now(),
            ),
        )

    @staticmethod
    def _load(database: sqlite3.Connection, plan_id: str) -> dict[str, Any] | None:
        row = database.execute(
            "SELECT * FROM task_plans WHERE plan_id=?", (plan_id,)
        ).fetchone()
        if row is None:
            return None
        dependency_rows = database.execute(
            """
            SELECT step_id, depends_on_step_id FROM task_plan_dependencies
            WHERE plan_id=? ORDER BY step_id, position
            """,
            (plan_id,),
        ).fetchall()
        dependencies: dict[str, list[str]] = {}
        for dependency in dependency_rows:
            dependencies.setdefault(str(dependency["step_id"]), []).append(
                str(dependency["depends_on_step_id"])
            )
        steps = []
        for step in database.execute(
            "SELECT * FROM task_plan_steps WHERE plan_id=? ORDER BY position",
            (plan_id,),
        ).fetchall():
            steps.append({
                "step_id": str(step["step_id"]),
                "title": str(step["title"]),
                "description": str(step["description"]),
                "status": str(step["status"]),
                "depends_on": dependencies.get(str(step["step_id"]), []),
                "tool_name": step["tool_name"],
                "tool_arguments": _json_value(step["tool_arguments_json"], {}),
                "critical": bool(step["critical"]),
                "result": _json_value(step["result_json"], None),
                "error": _json_value(step["error_json"], None),
                "started_at": str(step["started_at"] or ""),
                "finished_at": str(step["finished_at"] or ""),
            })
        return {
            "schema_version": int(row["schema_version"]),
            "plan_id": str(row["plan_id"]),
            "title": str(row["title"]),
            "description": str(row["description"]),
            "user": str(row["user"]),
            "source": str(row["source"]),
            "session_id": str(row["session_id"]),
            "status": str(row["status"]),
            "auto_accept": bool(row["auto_accept"]),
            "reminder": str(row["reminder"] or ""),
            "revision": int(row["revision"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "current_step": str(row["current_step"] or ""),
            "steps": steps,
        }

    def create(self, plan: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            _validate_plan(plan)
            _validate_sensitive_argument_change(plan)
            if plan.get("user") != self.user:
                raise PlanValidationError(
                    f"计划用户与存储用户不一致：{plan.get('user')!r} != {self.user!r}"
                )
            try:
                with self._connection(write=True) as database:
                    if self._load(database, plan["plan_id"]) is not None:
                        raise PlanConflictError(f"计划已存在：{plan['plan_id']}")
                    self._save(database, plan)
                    self._save_revision(database, plan, note="创建计划")
                    stored = self._load(database, plan["plan_id"])
                    if stored is None:
                        raise PlanError(f"计划保存后无法读取：{plan['plan_id']}")
            except sqlite3.Error as exc:
                raise PlanError(f"任务计划数据库不可用：{exc}") from exc
            return stored

    def read(self, plan_id: str) -> dict[str, Any]:
        with self._lock:
            try:
                with self._connection() as database:
                    data = self._load(database, plan_id)
            except sqlite3.Error as exc:
                raise PlanError(f"任务计划数据库不可用：{exc}") from exc
            if data is None:
                raise PlanNotFoundError(f"计划不存在：{plan_id}")
            return data

    def list_plans(self) -> list[dict[str, Any]]:
        with self._lock:
            try:
                with self._connection() as database:
                    ids = [str(row["plan_id"]) for row in database.execute(
                        "SELECT plan_id FROM task_plans ORDER BY plan_id"
                    ).fetchall()]
                    return [plan for plan_id in ids if (plan := self._load(database, plan_id)) is not None]
            except sqlite3.Error as exc:
                raise PlanError(f"任务计划数据库不可用：{exc}") from exc

    def first_approved_plan_id(self) -> str | None:
        """Return only the next runnable id for the scheduler hot path."""

        with self._lock:
            try:
                with self._connection() as database:
                    row = database.execute(
                        """
                        SELECT plan_id FROM task_plans
                        WHERE status='approved'
                        ORDER BY created_at, plan_id
                        LIMIT 1
                        """
                    ).fetchone()
            except sqlite3.Error as exc:
                raise PlanError(f"任务计划数据库不可用：{exc}") from exc
        return str(row["plan_id"]) if row is not None else None

    def update(
        self,
        plan_id: str,
        mutator: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        note: str = "",
    ) -> dict[str, Any]:
        """Atomically read, mutate, validate and persist a plan."""
        with self._lock:
            try:
                with self._connection(write=True) as database:
                    current = self._load(database, plan_id)
                    if current is None:
                        raise PlanNotFoundError(f"计划不存在：{plan_id}")
                    expected_revision = int(current.get("revision", 1))
                    updated = mutator(current)
                    if not isinstance(updated, dict):
                        raise PlanError("mutator 必须返回 dict")
                    if updated.get("plan_id") != plan_id:
                        raise PlanValidationError("更新不能修改 plan_id")
                    if updated.get("user") != self.user:
                        raise PlanValidationError("更新不能修改计划所属用户")
                    updated["revision"] = expected_revision + 1
                    updated["updated_at"] = _now()
                    _validate_plan(updated)
                    _validate_sensitive_argument_change(updated, current=current)
                    actual = database.execute(
                        "SELECT revision FROM task_plans WHERE plan_id=?", (plan_id,)
                    ).fetchone()
                    if actual is None or int(actual["revision"]) != expected_revision:
                        raise PlanConflictError(f"计划版本冲突：{plan_id}")
                    self._save(database, updated)
                    self._save_revision(
                        database,
                        updated,
                        note=note or f"revision {updated['revision']} 更新",
                    )
                    stored = self._load(database, plan_id)
                    if stored is None:
                        raise PlanError(f"计划保存后无法读取：{plan_id}")
                    return stored
            except sqlite3.Error as exc:
                raise PlanError(f"任务计划数据库不可用：{exc}") from exc

    def delete(self, plan_id: str) -> bool:
        with self._lock:
            try:
                with self._connection(write=True) as database:
                    result = database.execute(
                        "DELETE FROM task_plans WHERE plan_id=?", (plan_id,)
                    )
                    return result.rowcount > 0
            except sqlite3.Error as exc:
                raise PlanError(f"任务计划数据库不可用：{exc}") from exc

    def list_revisions(self, plan_id: str) -> list[dict[str, Any]]:
        with self._lock:
            try:
                with self._connection() as database:
                    if self._load(database, plan_id) is None:
                        raise PlanNotFoundError(f"计划不存在：{plan_id}")
                    return [
                        {
                            "plan_id": str(row["plan_id"]),
                            "revision": int(row["revision"]),
                            "note": str(row["note"] or ""),
                            "created_at": str(row["created_at"]),
                        }
                        for row in database.execute(
                            """
                            SELECT plan_id, revision, note, created_at
                            FROM task_plan_revisions
                            WHERE plan_id=?
                            ORDER BY revision DESC
                            """,
                            (plan_id,),
                        ).fetchall()
                    ]
            except sqlite3.Error as exc:
                raise PlanError(f"任务计划数据库不可用：{exc}") from exc

    def get_revision(self, plan_id: str, revision: int) -> dict[str, Any]:
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise PlanValidationError("revision 必须是正整数")
        with self._lock:
            try:
                with self._connection() as database:
                    if self._load(database, plan_id) is None:
                        raise PlanNotFoundError(f"计划不存在：{plan_id}")
                    row = database.execute(
                        """
                        SELECT plan_json FROM task_plan_revisions
                        WHERE plan_id=? AND revision=?
                        """,
                        (plan_id, revision),
                    ).fetchone()
                    if row is None:
                        raise PlanNotFoundError(
                            f"计划 {plan_id} 的 revision {revision} 不存在"
                        )
                    plan = _snapshot_value(row["plan_json"])
                    if not isinstance(plan, dict):
                        raise PlanError(
                            f"计划 {plan_id} 的 revision {revision} 快照损坏"
                        )
                    plan = _restore_revision_values(database, plan_id, plan)
            except sqlite3.Error as exc:
                raise PlanError(f"任务计划数据库不可用：{exc}") from exc
            _validate_plan(plan)
            if plan.get("plan_id") != plan_id or plan.get("user") != self.user:
                raise PlanError(f"计划 {plan_id} 的 revision {revision} 身份不一致")
            return plan

    def rollback(
        self,
        plan_id: str,
        plan_revision: int,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        snapshot = self.get_revision(plan_id, plan_revision)
        if _contains_revision_redaction(snapshot):
            raise PlanValidationError(
                "该历史 revision 含已脱敏的旧凭据，不能安全回滚；"
                "请编辑当前计划并改用环境变量名或安全引用"
            )

        def restore(current: dict[str, Any]) -> dict[str, Any]:
            current_status = str(current.get("status") or "")
            if current_status not in ROLLBACK_SAFE_PLAN_STATUSES:
                raise PlanValidationError(
                    f"计划 {plan_id} 当前状态为 {current_status!r}，"
                    "只能在 pending/paused/failed 状态回滚"
                )
            if expected_revision is not None:
                if (
                    isinstance(expected_revision, bool)
                    or not isinstance(expected_revision, int)
                    or expected_revision < 1
                ):
                    raise PlanValidationError("current_revision 必须是正整数")
                if int(current.get("revision", 0)) != expected_revision:
                    raise PlanConflictError("计划版本已变化，请重新读取后再回滚")
            restored = json.loads(_json_text(snapshot))
            restored["plan_id"] = current["plan_id"]
            restored["user"] = current["user"]
            restored["status"] = current_status

            restored_steps = {
                str(step.get("step_id") or ""): step
                for step in restored.get("steps") or []
                if isinstance(step, dict)
            }
            for current_step in current.get("steps") or []:
                if not isinstance(current_step, dict) or current_step.get("status") != "completed":
                    continue
                step_id = str(current_step.get("step_id") or "")
                if step_id not in restored_steps:
                    raise PlanValidationError(
                        f"不能回滚到缺少已完成步骤 {step_id!r} 的 revision"
                    )
                restored_steps[step_id].clear()
                restored_steps[step_id].update(json.loads(_json_text(current_step)))

            for step in restored.get("steps") or []:
                if not isinstance(step, dict) or step.get("status") != "running":
                    continue
                step["status"] = "pending"
                step["result"] = None
                step["error"] = None
                step["started_at"] = ""
                step["finished_at"] = ""

            runnable_step_ids = [
                str(step.get("step_id") or "")
                for step in restored.get("steps") or []
                if isinstance(step, dict) and step.get("status") == "pending"
            ]
            if str(restored.get("current_step") or "") not in runnable_step_ids:
                restored["current_step"] = runnable_step_ids[0] if runnable_step_ids else ""
            return restored

        return self.update(
            plan_id,
            restore,
            note=f"回滚到 revision {plan_revision}",
        )

    def recover_interrupted(self) -> list[str]:
        """On startup, find plans with running steps and pause them."""
        with self._lock:
            recovered: list[str] = []
            try:
                with self._connection(write=True) as database:
                    ids = [str(row["plan_id"]) for row in database.execute(
                        "SELECT DISTINCT plan_id FROM task_plan_steps WHERE status='running' ORDER BY plan_id"
                    ).fetchall()]
                    for plan_id in ids:
                        data = self._load(database, plan_id)
                        if data is None:
                            continue
                        changed = False
                        for step in data["steps"]:
                            if step.get("status") == "running":
                                step["status"] = "pending"
                                changed = True
                        if changed and data.get("status") in {"running", "approved"}:
                            data["status"] = "paused"
                            data["revision"] = int(data.get("revision", 1)) + 1
                            data["updated_at"] = _now()
                            _validate_plan(data)
                            self._save(database, data)
                            self._save_revision(
                                database,
                                data,
                                note="启动恢复中断计划",
                            )
                            recovered.append(plan_id)
                return recovered
            except sqlite3.Error as exc:
                raise PlanError(f"任务计划数据库不可用：{exc}") from exc


def _prompt_step(step: dict[str, Any]) -> str:
    description = step.get("description") or step.get("title") or "未命名步骤"
    status = str(step.get("status") or "pending")
    if status == "completed":
        return f"- [x] {description} ✓"
    return f"- [ ] {description}（{status}）"


def select_prompt_plans(
    root: Path,
    user: str,
    *,
    max_chars: int,
    source: str | None = None,
    session_id: str | None = None,
) -> TaskPlanSelection:
    """Read unfinished plans, optionally limited to one conversation space."""

    if max_chars == 0:
        return TaskPlanSelection("", (), 0, 0, 0, 0, False)
    directory = _plan_dir(root, user)
    if not directory.is_dir():
        return TaskPlanSelection("", (), 0, 0, 0, 0, False)
    plans = PlanStore(root, user).list_plans()
    status_map = {
        "pending": "pending",
        "approved": "active",
        "running": "active",
        "paused": "active",
        "completed": "completed",
        "failed": "aborted",
        "cancelled": "aborted",
    }
    pieces: list[str] = []
    selected_paths: list[str] = []
    offsets: list[int] = []
    used = 0
    for data in plans:
        if source is not None and str(data.get("source") or "") != source:
            continue
        if session_id is not None and str(data.get("session_id") or "") != session_id:
            continue
        plan_id = str(data.get("plan_id") or "")
        mapped = status_map.get(str(data.get("status") or ""))
        if mapped is None:
            raise PlanError(f"计划状态无效：{plan_id}")
        if mapped in {"completed", "aborted"}:
            continue
        title = str(data.get("title") or plan_id)
        description = str(data.get("description") or "").strip()
        lines = [f"[plan:{plan_id}]", f"title: {title}", f"status: {mapped}"]
        if description:
            lines.append(f"description: {description}")
        steps = data.get("steps") or []
        if not isinstance(steps, list):
            raise PlanError(f"计划 steps 必须是数组：{plan_id}")
        lines.extend(_prompt_step(step) for step in steps if isinstance(step, dict))
        piece = "\n".join(lines)
        offsets.append(used + (2 if pieces else 0))
        pieces.append(piece)
        selected_paths.append(
            f"users/{user}/task_plan/{TASK_PLAN_DB_FILENAME}#{plan_id}"
        )
        used += len(piece) + (2 if len(pieces) > 1 else 0)
    full_text = "\n\n".join(pieces)
    text, truncated = truncate_chars(full_text, max_chars)
    injected_count = sum(offset < len(text) for offset in offsets)
    return TaskPlanSelection(
        text=text,
        source_files=tuple(selected_paths[:injected_count]),
        original_chars=len(full_text),
        injected_chars=len(text),
        original_items=len(pieces),
        injected_items=injected_count,
        truncated=truncated,
    )
