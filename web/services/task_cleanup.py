"""Explicit, previewed cleanup of completed task-hub items only.

Execution cleanup is a durable view dismissal: immutable plan results and
diagnostic logs remain intact, and a later execution remains visible.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from web.service import WebRunService

from pathlib import Path

import hashlib
import json
import sqlite3
from typing import Any

from run.infra import LogStore
from run.scheduler import CronStore
from run.tasks import PlanStore
from web.errors import InvalidRequestError


def _version(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     default=str).encode("utf-8")).hexdigest()


def execution_key(record: dict) -> str:
    return f"{record['kind']}:{record['record_id']}"


def visible_executions(root, user, records: list[dict]) -> list[dict]:
    with LogStore(root)._connection() as db:
        hidden = {tuple(row) for row in db.execute(
            "SELECT kind, record_id, updated_at FROM task_hub_cleaned_records WHERE user=?",
            (user,)).fetchall()}
    return [r for r in records if (r["kind"], r["record_id"], r["updated_at"]) not in hidden]


class TaskCleanupServiceMixin:
    if TYPE_CHECKING:
        require_session_id = WebRunService.require_session_id
        require_source = WebRunService.require_source
        require_user = WebRunService.require_user
        root: Path

    def _cleanup_scope(self, source: Any, session_id: Any) -> tuple[str, str]:
        if not isinstance(source, str):
            raise InvalidRequestError("source 必须是字符串")
        if not isinstance(session_id, str):
            raise InvalidRequestError("session_id 必须是字符串")
        return self.require_source(source), self.require_session_id(session_id) if session_id else ""

    def _completed_task_items(self, user: str, category: str, source: str,
                              session_id: str) -> list[dict]:
        from web.services.tasks import _redact_plan_revision
        if not isinstance(category, str) or category not in {"plans", "crons", "executions"}:
            raise InvalidRequestError("清理类别只允许 plans、crons 或 executions")
        plans = [p for p in PlanStore(self.root, user).list_plans()
                 if p["source"] == source and (not session_id or p["session_id"] == session_id)]
        if category == "plans":
            return [{"id": p["plan_id"], "title": _redact_plan_revision(p["title"]),
                     "version": _version(p), "_plan": p}
                    for p in plans if p["status"] == "completed"]
        crons = CronStore(self.root, user).list_tasks()
        if category == "crons":
            return [{"id": c["task_id"], "title": _redact_plan_revision(c["title"]),
                     "version": _version(c), "_cron": c}
                    for c in crons if c["status"] == "completed" and c.get("exec_mode") != "system"]
        records = []
        for plan in plans:
            for step in plan["steps"]:
                if step["status"] == "completed" and step.get("finished_at"):
                    records.append({"kind": "plan_step", "record_id": f"{plan['plan_id']}:{step['step_id']}",
                                    "updated_at": step["finished_at"], "title": step["title"]})
        system_ids = {t["task_id"] for t in CronStore(self.root, "__system__", system=True).list_tasks()}
        titles = {t["task_id"]: t["title"] for t in crons}
        # Select every completed record, not only the latest 100 or current page.
        with LogStore(self.root)._connection() as db:
            for row in db.execute(
                    """SELECT task_id, event_key, occurred_at FROM cron_execution_logs
                       WHERE user=? AND status='completed' ORDER BY id DESC""", (user,)).fetchall():
                if row["task_id"] in system_ids:
                    continue
                records.append({"kind": "cron", "record_id": f"{row['task_id']}:{row['event_key']}",
                                "updated_at": row["occurred_at"],
                                "title": titles.get(row["task_id"], row["task_id"])})
        return [{"id": execution_key(r), "title": _redact_plan_revision(r["title"]),
                 "version": _version(r), "_record": r}
                for r in visible_executions(self.root, user, records)]

    def preview_task_cleanup(self, user: Any, category: str, *, source: Any = "web",
                             session_id: Any = "") -> dict:
        name = self.require_user(user)
        channel, session = self._cleanup_scope(source, session_id)
        items = self._completed_task_items(name, category, channel, session)
        return {"user": name, "category": category, "count": len(items),
                "items": [{k: item[k] for k in ("id", "title", "version")} for item in items]}

    def cleanup_completed_tasks(self, user: Any, payload: Any) -> dict:
        name = self.require_user(user)
        if not isinstance(payload, dict):
            raise InvalidRequestError("清理请求必须是对象")
        if set(payload) - {"category", "source", "session_id", "items"}:
            raise InvalidRequestError("清理请求包含未知字段")
        category = payload.get("category")
        if not isinstance(category, str):
            raise InvalidRequestError("清理请求缺少 category")
        source, session = self._cleanup_scope(
            payload.get("source", "web"), payload.get("session_id", ""))
        selected = payload.get("items")
        if not isinstance(selected, list) or any(
                not isinstance(item, dict) or set(item) != {"id", "version"}
                or not isinstance(item["id"], str) or not isinstance(item["version"], str)
                for item in selected):
            raise InvalidRequestError("清理必须提交预览中的 id 与 version 清单")
        current = {item["id"]: item for item in self._completed_task_items(name, category, source, session)}
        removed, skipped, errors = [], [], []
        for chosen in {item["id"]: item for item in selected}.values():
            item = current.get(chosen["id"])
            if item is None or item["version"] != chosen["version"]:
                skipped.append(chosen["id"])
                continue
            try:
                if category == "plans":
                    p = item["_plan"]
                    applied = PlanStore(self.root, name).delete(
                        p["plan_id"], completed_revision=p["revision"], source=source,
                        session_id=session or None)
                elif category == "crons":
                    applied = CronStore(self.root, name).delete(
                        item["_cron"]["task_id"], completed_snapshot=item["_cron"])
                else:
                    r = item["_record"]
                    with LogStore(self.root)._connection() as db:
                        applied = db.execute(
                            "INSERT OR IGNORE INTO task_hub_cleaned_records VALUES(?,?,?,?)",
                            (name, r["kind"], r["record_id"], r["updated_at"])).rowcount > 0
                (removed if applied else skipped).append(chosen["id"])
            except (OSError, RuntimeError, sqlite3.Error) as exc:
                errors.append({"id": chosen["id"], "message": str(exc)})
        return {"user": name, "category": category, "removed_count": len(removed),
                "removed": removed, "skipped": skipped, "errors": errors}
