"""任务计划与定时任务领域服务。"""

from __future__ import annotations

from typing import Any

from cron.schedule import compute_next_run
from run.cron_store import (
    CronConflictError,
    CronError,
    CronNotFoundError,
    CronStore,
    normalize_task,
)
from run.task_plan_executor import cancel_plan, pause_plan
from run.task_plan_store import (
    PlanConflictError,
    PlanError,
    PlanNotFoundError,
    PlanStore,
    normalize_plan,
)
from web.errors import ConflictError, InvalidRequestError, NotFoundError


class TaskServiceMixin:
    """Task APIs mixed into the backwards-compatible ``WebRunService`` facade."""

    @staticmethod
    def _plan_summary(plan: dict[str, Any]) -> dict[str, Any]:
        steps = []
        for item in plan.get("steps") or []:
            if not isinstance(item, dict):
                continue
            steps.append(
                {
                    "step_id": str(item.get("step_id") or ""),
                    "title": str(item.get("title") or ""),
                    "description": str(item.get("description") or ""),
                    "status": str(item.get("status") or "pending"),
                    "depends_on": [str(value) for value in (item.get("depends_on") or [])],
                    "critical": bool(item.get("critical", True)),
                    "tool_name": str(item.get("tool_name") or ""),
                    "started_at": str(item.get("started_at") or ""),
                    "finished_at": str(item.get("finished_at") or ""),
                }
            )
        completed = sum(item["status"] in {"completed", "skipped"} for item in steps)
        return {
            "plan_id": str(plan.get("plan_id") or ""),
            "title": str(plan.get("title") or ""),
            "description": str(plan.get("description") or ""),
            "status": str(plan.get("status") or "pending"),
            "auto_accept": bool(plan.get("auto_accept", False)),
            "reminder": str(plan.get("reminder") or ""),
            "source": str(plan.get("source") or ""),
            "session_id": str(plan.get("session_id") or ""),
            "current_step": str(plan.get("current_step") or ""),
            "revision": int(plan.get("revision") or 1),
            "created_at": str(plan.get("created_at") or ""),
            "updated_at": str(plan.get("updated_at") or ""),
            "progress": {
                "completed": completed,
                "total": len(steps),
                "percent": round(completed * 100 / len(steps)) if steps else 0,
            },
            "steps": steps,
        }

    @staticmethod
    def _cron_summary(task: dict[str, Any]) -> dict[str, Any]:
        summary = {
            "task_id": str(task.get("task_id") or ""),
            "title": str(task.get("title") or ""),
            "user_defined": task.get("exec_mode") != "system",
            "status": str(task.get("status") or "enabled"),
            "type": str(task.get("type") or ""),
            "next_run_at": str(task.get("next_run_at") or ""),
            "latest_run_at": str(task.get("latest_run_at") or ""),
            "created_at": str(task.get("created_at") or ""),
        }
        if task.get("type") == "daily":
            summary["time"] = str(task.get("time") or "")
        elif task.get("type") == "recurring":
            summary["interval_seconds"] = int(task.get("interval_seconds") or 0)
        summary["last_state"] = (
            "never" if not task.get("latest_run_at") else str(task.get("status") or "completed")
        )
        return summary

    def tasks(self, user: Any) -> dict[str, Any]:
        name = self.require_user(user)
        plans = [self._plan_summary(item) for item in PlanStore(self.root, name).list_plans()]
        crons = [self._cron_summary(item) for item in CronStore(self.root, name).list_tasks()]
        plans.sort(key=lambda item: item["updated_at"], reverse=True)
        crons.sort(
            key=lambda item: item.get("latest_run_at") or item.get("created_at") or "",
            reverse=True,
        )
        active_statuses = {"approved", "running", "paused"}
        waiting_statuses = {"pending", "approved", "paused"}
        executions: list[dict[str, Any]] = []
        for plan in plans:
            for step in plan.get("steps", []):
                if not isinstance(step, dict) or not step.get("finished_at"):
                    continue
                executions.append(
                    {
                        "kind": "plan_step",
                        "task_id": plan["plan_id"],
                        "title": step.get("title", ""),
                        "status": step.get("status", ""),
                        "updated_at": step.get("finished_at", ""),
                        "result": step.get("result"),
                        "error": step.get("error"),
                    }
                )
        for task in crons:
            if task.get("latest_run_at"):
                executions.append(
                    {
                        "kind": "cron",
                        "task_id": task["task_id"],
                        "title": task.get("title", ""),
                        "status": task.get("last_state", task.get("status", "")),
                        "updated_at": task.get("latest_run_at", ""),
                        "result": None,
                        "error": task.get("last_error"),
                    }
                )
        executions.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return {
            "user": name,
            "summary": {
                "active_plans": sum(item["status"] in active_statuses for item in plans),
                "waiting_plans": sum(item["status"] in waiting_statuses for item in plans),
                "enabled_crons": sum(item["status"] == "enabled" for item in crons),
                "completed_plans": sum(item["status"] == "completed" for item in plans),
            },
            "plans": plans,
            "cron_tasks": crons,
            "executions": executions[:100],
        }

    def create_plan(self, user: Any, payload: Any) -> dict[str, Any]:
        name = self.require_user(user)
        if not isinstance(payload, dict):
            raise InvalidRequestError("计划必须是对象")
        try:
            plan = normalize_plan(
                plan_id=payload.get("plan_id"),
                title=payload.get("title", ""),
                description=payload.get("description", ""),
                user=name,
                source="web",
                session_id=str(payload.get("session_id") or "web"),
                steps=payload.get("steps") or [],
                auto_accept=payload.get("auto_accept", False),
                reminder=payload.get("reminder", ""),
                status=payload.get("status", "pending"),
                current_step=payload.get("current_step"),
            )
            stored = PlanStore(self.root, name).create(plan)
        except (PlanError, KeyError, TypeError, ValueError) as exc:
            raise InvalidRequestError(f"计划校验失败：{exc}") from None
        if stored.get("status") == "approved" and self.plan_waker is not None:
            self.plan_waker()
        return {"user": name, "plan": self._plan_summary(stored), "updated": True}

    def update_plan(self, user: Any, plan_id: Any, payload: Any) -> dict[str, Any]:
        name = self.require_user(user)
        if not isinstance(payload, dict):
            raise InvalidRequestError("计划更新必须是对象")
        expected = payload.get("revision")

        def mutate(current: dict[str, Any]) -> dict[str, Any]:
            if expected is not None and expected != current.get("revision"):
                raise PlanConflictError("计划版本已变化，请重新读取后再保存")
            updated = dict(current)
            for key in (
                "title",
                "description",
                "status",
                "auto_accept",
                "reminder",
                "current_step",
                "steps",
            ):
                if key in payload:
                    updated[key] = payload[key]
            updated["user"] = name
            return updated

        try:
            stored = PlanStore(self.root, name).update(str(plan_id), mutate)
        except PlanNotFoundError as exc:
            raise NotFoundError(str(exc)) from None
        except PlanConflictError as exc:
            raise ConflictError(str(exc)) from None
        except (PlanError, KeyError, TypeError, ValueError) as exc:
            raise InvalidRequestError(f"计划校验失败：{exc}") from None
        if stored.get("status") == "approved" and self.plan_waker is not None:
            self.plan_waker()
        return {"user": name, "plan": self._plan_summary(stored), "updated": True}

    def command_plan(self, user: Any, plan_id: Any, action: Any) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_action = str(action or "").strip().casefold()
        try:
            if normalized_action == "pause":
                stored = pause_plan(self.root, name, str(plan_id))
            elif normalized_action == "cancel":
                stored = cancel_plan(self.root, name, str(plan_id))
            else:
                raise InvalidRequestError("计划状态指令只允许 pause 或 cancel")
        except PlanNotFoundError as exc:
            raise NotFoundError(str(exc)) from None
        except InvalidRequestError:
            raise
        except (PlanError, RuntimeError, ValueError) as exc:
            raise ConflictError(str(exc)) from None
        return {
            "user": name,
            "plan": self._plan_summary(stored),
            "action": normalized_action,
            "updated": True,
        }

    def delete_plan(self, user: Any, plan_id: Any) -> dict[str, Any]:
        name = self.require_user(user)
        if not PlanStore(self.root, name).delete(str(plan_id)):
            raise NotFoundError(f"计划不存在：{plan_id}")
        return {"user": name, "plan_id": str(plan_id), "deleted": True}

    def _cron_payload(
        self,
        user: str,
        payload: dict[str, Any],
        current: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        source = dict(current or {})
        allowed = {
            "title",
            "prompt",
            "type",
            "interval_seconds",
            "time",
            "next_run_at",
            "status",
        }
        source.update({key: value for key, value in payload.items() if key in allowed})
        task_type = source.get("type")
        interval = source.get("interval_seconds")
        if task_type == "recurring" and (
            isinstance(interval, bool) or not isinstance(interval, int) or interval < 60
        ):
            raise InvalidRequestError("recurring interval_seconds 必须是 ≥ 60 的整数")
        try:
            if task_type in {"daily", "recurring"}:
                source["next_run_at"] = compute_next_run(source)
            return normalize_task(
                task_id=source.get("task_id"),
                title=source.get("title", ""),
                prompt=source.get("prompt", ""),
                user=user,
                type=task_type,
                interval_seconds=interval,
                time=source.get("time"),
                next_run_at=source.get("next_run_at", ""),
                latest_run_at=source.get("latest_run_at", ""),
                status=source.get("status", "enabled"),
                created_at=source.get("created_at", ""),
                exec_mode=source.get("exec_mode", "agent"),
            )
        except (CronError, KeyError, TypeError, ValueError) as exc:
            raise InvalidRequestError(f"定时任务校验失败：{exc}") from None

    def create_cron(self, user: Any, payload: Any) -> dict[str, Any]:
        name = self.require_user(user)
        if not isinstance(payload, dict):
            raise InvalidRequestError("定时任务必须是对象")
        task = self._cron_payload(name, payload)
        try:
            stored = CronStore(self.root, name).create(task)
        except CronConflictError as exc:
            raise ConflictError(str(exc)) from None
        except CronError as exc:
            raise InvalidRequestError(str(exc)) from None
        return {"user": name, "cron_task": self._cron_summary(stored), "updated": True}

    def update_cron(self, user: Any, task_id: Any, payload: Any) -> dict[str, Any]:
        name = self.require_user(user)
        if not isinstance(payload, dict):
            raise InvalidRequestError("定时任务更新必须是对象")
        store = CronStore(self.root, name)
        try:
            stored = store.update(
                str(task_id),
                lambda current: self._cron_payload(name, payload, current),
            )
        except CronNotFoundError as exc:
            raise NotFoundError(str(exc)) from None
        except CronError as exc:
            raise InvalidRequestError(str(exc)) from None
        return {"user": name, "cron_task": self._cron_summary(stored), "updated": True}

    def delete_cron(self, user: Any, task_id: Any) -> dict[str, Any]:
        name = self.require_user(user)
        if not CronStore(self.root, name).delete(str(task_id)):
            raise NotFoundError(f"定时任务不存在：{task_id}")
        return {"user": name, "task_id": str(task_id), "deleted": True}
