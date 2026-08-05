"""用户隔离的任务计划存储，具有原子写入和版本检查。"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from run.prompt_sources import truncate_chars

SCHEMA_VERSION = 1
TASK_PLAN_DB_FILENAME = "task_plans.sqlite3"
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
                """
                )
                database.execute(
                    "INSERT INTO task_plan_meta(key, value) VALUES('schema_version', '1') "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
                )
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
                plan["plan_id"], int(plan["schema_version"]), plan["title"],
                plan["description"], plan["user"], str(plan.get("source") or "cli"),
                str(plan.get("session_id") or "default"), plan["status"],
                1 if plan["auto_accept"] else 0, str(plan.get("reminder") or ""),
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
                    plan["plan_id"], position, step["step_id"], step["title"],
                    step["description"], step.get("status", "pending"),
                    step.get("tool_name"), _json_text(step.get("tool_arguments") or {}),
                    1 if step.get("critical", True) else 0,
                    _json_text(step.get("result")) if step.get("result") is not None else None,
                    _json_text(step.get("error")) if step.get("error") is not None else None,
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
            if plan.get("user") != self.user:
                raise PlanValidationError(
                    f"计划用户与存储用户不一致：{plan.get('user')!r} != {self.user!r}"
                )
            try:
                with self._connection(write=True) as database:
                    if self._load(database, plan["plan_id"]) is not None:
                        raise PlanConflictError(f"计划已存在：{plan['plan_id']}")
                    self._save(database, plan)
            except sqlite3.Error as exc:
                raise PlanError(f"任务计划数据库不可用：{exc}") from exc
            return dict(plan)

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

    def update(self, plan_id: str, mutator: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
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
                    actual = database.execute(
                        "SELECT revision FROM task_plans WHERE plan_id=?", (plan_id,)
                    ).fetchone()
                    if actual is None or int(actual["revision"]) != expected_revision:
                        raise PlanConflictError(f"计划版本冲突：{plan_id}")
                    self._save(database, updated)
                    return updated
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


def select_prompt_plans(root: Path, user: str, *, max_chars: int) -> TaskPlanSelection:
    """Read unfinished plans without changing the persisted plan state machine."""

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
