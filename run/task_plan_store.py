"""用户隔离的任务计划存储，具有原子写入和版本检查。"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SCHEMA_VERSION = 1
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

_STORE_LOCKS: dict[tuple[str, str], threading.RLock] = {}
_STORE_LOCKS_GUARD = threading.Lock()


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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_plan_id() -> str:
    return f"plan_{uuid.uuid4().hex[:8]}"


def _plan_dir(root: Path, user: str) -> Path:
    return root / "users" / user / "task_plan"


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        os.replace(tmp, path)
    except OSError:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


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
        for prefix in _BLOCKED_TOOL_PREFIXES:
            if tool_name.startswith(prefix):
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
        "status": "pending",
        "auto_accept": auto_accept,
        "revision": 1,
        "created_at": now,
        "updated_at": now,
        "current_step": normalized_steps[0]["step_id"] if normalized_steps else "",
        "steps": normalized_steps,
    }
    _validate_plan(plan, tool_names=tool_names)
    return plan


class PlanStore:
    """Disk-authoritative plan storage with per-user locking."""

    def __init__(self, root: Path, user: str) -> None:
        self.root = root.resolve()
        self.user = user
        self._dir = _plan_dir(self.root, self.user)
        self._lock = _store_lock(self.root, self.user)

    def _path(self, plan_id: str) -> Path:
        return self._dir / f"{plan_id}.json"

    def create(self, plan: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._dir.mkdir(parents=True, exist_ok=True)
            plan_id = plan["plan_id"]
            path = self._path(plan_id)
            if path.exists():
                raise PlanConflictError(f"计划已存在：{plan_id}")
            _atomic_write(path, plan)
            return plan

    def read(self, plan_id: str) -> dict[str, Any]:
        with self._lock:
            path = self._path(plan_id)
            if not path.exists():
                raise PlanNotFoundError(f"计划不存在：{plan_id}")
            try:
                data = json.loads(path.read_text("utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise PlanError(f"计划文件损坏：{plan_id}（{exc}）") from exc
            return data

    def list_plans(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self._dir.is_dir():
                return []
            plans: list[dict[str, Any]] = []
            for path in sorted(self._dir.glob("plan_*.json"), key=lambda p: p.name):
                try:
                    data = json.loads(path.read_text("utf-8"))
                    if isinstance(data, dict) and "plan_id" in data:
                        plans.append(data)
                except (OSError, json.JSONDecodeError):
                    continue
            return plans

    def update(self, plan_id: str, mutator: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        """Atomically read, mutate, validate and persist a plan."""
        with self._lock:
            path = self._path(plan_id)
            if not path.exists():
                raise PlanNotFoundError(f"计划不存在：{plan_id}")
            try:
                current = json.loads(path.read_text("utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise PlanError(f"计划文件损坏：{plan_id}（{exc}）") from exc
            updated = mutator(current)
            if not isinstance(updated, dict):
                raise PlanError("mutator 必须返回 dict")
            updated["revision"] = int(current.get("revision", 1)) + 1
            updated["updated_at"] = _now()
            _validate_plan(updated)
            _atomic_write(path, updated)
            return updated

    def delete(self, plan_id: str) -> bool:
        with self._lock:
            path = self._path(plan_id)
            if not path.exists():
                return False
            path.unlink()
            return True

    def recover_interrupted(self) -> list[str]:
        """On startup, find plans with running steps and pause them."""
        recovered: list[str] = []
        with self._lock:
            if not self._dir.is_dir():
                return recovered
            for path in sorted(self._dir.glob("plan_*.json"), key=lambda p: p.name):
                try:
                    data = json.loads(path.read_text("utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(data, dict):
                    continue
                changed = False
                for step in data.get("steps", []):
                    if step.get("status") == "running":
                        step["status"] = "pending"
                        changed = True
                if changed and data.get("status") in ("running", "approved"):
                    data["status"] = "paused"
                    data["revision"] = int(data.get("revision", 1)) + 1
                    data["updated_at"] = _now()
                    _atomic_write(path, data)
                    recovered.append(data.get("plan_id", path.stem))
        return recovered
