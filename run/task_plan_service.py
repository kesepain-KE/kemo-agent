"""Task-plan generation service backed by the task_plan subagent."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable

from provider.factory import create_provider
from run.agent_runner import AgentRunError, AgentRunner
from run.config import load_config
from run.memory import MemoryStore
from run.task_plan_store import PlanStore, PlanValidationError, normalize_plan
from run.tools import ToolRegistry, apply_runtime_tool_policy, discover_tools


EDITABLE_PLAN_STATUSES = frozenset({"pending", "approved", "paused"})
_PROTECTED_COMPLETED_FIELDS = (
    "step_id",
    "title",
    "description",
    "status",
    "depends_on",
    "tool_name",
    "tool_arguments",
    "critical",
)


class PlanGenerationError(RuntimeError):
    pass


class PlanSkipped(PlanGenerationError):
    """The subagent decided a plan is unnecessary."""


def _tool_summary(registry: ToolRegistry, max_tools: int = 50) -> list[dict[str, str]]:
    return [
        {"name": tool.name, "description": tool.description}
        for tool in registry.enabled_tools()[:max_tools]
    ]


def _safe_markdown_files(base: Path, *, recursive: bool) -> list[Path]:
    if not base.is_dir() or base.is_symlink():
        return []
    candidates = base.rglob("SKILL.md") if recursive else base.glob("*/SKILL.md")
    files: list[Path] = []
    resolved_base = base.resolve()
    for path in candidates:
        if not path.is_file() or path.is_symlink():
            continue
        try:
            path.resolve().relative_to(resolved_base)
        except (OSError, ValueError):
            continue
        relative = path.relative_to(base)
        current = base
        unsafe = False
        for part in relative.parts[:-1]:
            current = current / part
            if current.is_symlink():
                unsafe = True
                break
        if not unsafe:
            files.append(path)
    return sorted(files, key=lambda item: item.relative_to(base).as_posix().casefold())


def _render_markdown_files(root: Path, paths: list[Path]) -> str:
    parts: list[str] = []
    for path in paths:
        try:
            content = path.read_text("utf-8").strip()
        except (OSError, UnicodeError) as exc:
            raise PlanGenerationError(f"技能文件不可读：{path}（{exc}）") from exc
        relative = path.relative_to(root).as_posix()
        parts.append(f"## {relative}\n\n{content}")
    return "\n\n---\n\n".join(parts)


def _collect_skills(root: Path, user: str) -> dict[str, str]:
    """Collect complete plugin, shared, and current-user SKILL.md files."""
    plugin_files = _safe_markdown_files(root / "plugins", recursive=False)
    shared_files = _safe_markdown_files(root / "shared_skills", recursive=True)
    user_files = _safe_markdown_files(
        root / "users" / user / "user_skills",
        recursive=True,
    )
    return {
        "plugin_skills": _render_markdown_files(root, plugin_files),
        "shared_skills": _render_markdown_files(root, shared_files),
        "user_skills": _render_markdown_files(root, user_files),
    }


def _read_index(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        return ""
    try:
        return path.read_text("utf-8")
    except (OSError, UnicodeError) as exc:
        raise PlanGenerationError(f"知识库索引不可读：{path}（{exc}）") from exc


def _collect_knowledge_indexes(root: Path, user: str) -> dict[str, str]:
    """Read all three data_structure.md knowledge indexes without truncation."""
    return {
        "global_knowledge_index": _read_index(
            root / "global_knowledge" / "data_structure.md"
        ),
        "shared_knowledge_index": _read_index(
            root / "shared_knowledge" / "data_structure.md"
        ),
        "user_knowledge_index": _read_index(
            root / "users" / user / "knowledge" / "data_structure.md"
        ),
    }


def _planning_injections(root: Path, user: str) -> dict[str, str]:
    skills = _collect_skills(root, user)
    knowledge = _collect_knowledge_indexes(root, user)
    return {
        "plugin_skills": skills["plugin_skills"],
        "shared_skills_text": skills["shared_skills"],
        "user_skills_text": skills["user_skills"],
        "global_knowledge_index": knowledge["global_knowledge_index"],
        "shared_knowledge_index": knowledge["shared_knowledge_index"],
        "user_knowledge_index": knowledge["user_knowledge_index"],
    }


def _relevant_memory(root: Path, user: str, config: dict[str, Any], goal: str) -> str:
    store = MemoryStore(root, user, config)
    lines: list[str] = []
    used = 0
    for item in store.search(goal, limit=5):
        line = (
            f"- [{item['filename']}] ({item['tier']}, weight={item['weight']}) "
            f"{item['content']}"
        )
        extra = len(line) + (1 if lines else 0)
        if used + extra > 1000:
            continue
        lines.append(line)
        used += extra
    if not lines:
        return ""
    return "以下是按文件名匹配的相关用户记忆，当前计划目标优先：\n" + "\n".join(lines)


def _editable_plan(plan: dict[str, Any]) -> None:
    status = plan.get("status")
    if status not in EDITABLE_PLAN_STATUSES:
        raise PlanGenerationError(
            f"计划 {plan.get('plan_id')} 当前状态为 {status!r}，"
            "只能编辑 pending/approved/paused 状态的计划"
        )


def _completed_steps(plan: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"step_id": str(step.get("step_id") or ""), "title": str(step.get("title") or "")}
        for step in plan.get("steps", [])
        if isinstance(step, dict) and step.get("status") == "completed"
    ]


def _protect_completed_steps(
    existing_plan: dict[str, Any],
    proposed_steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    protected = {
        step["step_id"]: step
        for step in existing_plan.get("steps", [])
        if isinstance(step, dict) and step.get("status") == "completed"
    }
    if not protected:
        return proposed_steps
    output = copy.deepcopy(proposed_steps)
    positions = {
        step.get("step_id"): index
        for index, step in enumerate(output)
        if isinstance(step, dict)
    }
    for step_id, original in protected.items():
        if step_id not in positions:
            raise PlanGenerationError(f"已完成步骤 {step_id} 不得删除")
        proposed = output[positions[step_id]]
        for field in _PROTECTED_COMPLETED_FIELDS:
            if field in proposed and proposed[field] != original.get(field):
                raise PlanGenerationError(f"已完成步骤 {step_id} 的 {field} 不得修改")
        output[positions[step_id]] = copy.deepcopy(original)
    return output


def _reminder(auto_accept: bool, action: str) -> str:
    if auto_accept:
        return ""
    if action == "edit":
        return "当前任务计划已修改，请让用户点击批准后执行"
    return "当前任务计划已创建，请让用户点击批准后执行"


def _runtime_tools(
    root: Path,
    user: str,
    config: dict[str, Any],
    registry: ToolRegistry | None,
) -> ToolRegistry:
    if registry is not None:
        return registry
    tool_config = config.get("tools") or {}
    if not bool(tool_config.get("enabled", True)):
        return ToolRegistry({})
    return apply_runtime_tool_policy(discover_tools(root, user), config)


def prepare_task_plan_input(
    *,
    root: Path,
    user: str,
    input_data: dict[str, Any],
    config: dict[str, Any] | None = None,
    tool_registry: ToolRegistry | None = None,
) -> dict[str, Any]:
    """Build the authoritative task-plan payload for every invocation path.

    Tool names, planning sources, and limits are runtime authority.  They must
    not depend on a main-agent model remembering to copy them into a generic
    subagent call, and caller-supplied values must not widen that authority.
    """

    if not isinstance(input_data, dict):
        raise PlanGenerationError("task_plan 输入必须是对象")
    cfg = config if config is not None else load_config(user, root)
    registry = _runtime_tools(root, user, cfg, tool_registry)
    task_plan_config = cfg.get("task_plan") or {}
    existing_plan = input_data.get("existing_plan")
    existing = existing_plan if isinstance(existing_plan, dict) else None
    auto_accept = (
        bool(existing.get("auto_accept", False))
        if existing is not None
        else bool(task_plan_config.get("auto_accept", False))
    )
    max_steps = int(task_plan_config.get("max_steps", 20))
    goal = str(input_data.get("goal") or "")
    edit_request = input_data.get("edit_request")
    memory_query = str(edit_request or goal)
    injections = _planning_injections(root, user)

    payload: dict[str, Any] = {
        "action": input_data.get("action"),
        "goal": goal,
        "available_tools": _tool_summary(registry),
        "plugin_skills": injections["plugin_skills"],
        "shared_skills_text": injections["shared_skills_text"],
        "user_skills_text": injections["user_skills_text"],
        "global_knowledge_index": injections["global_knowledge_index"],
        "shared_knowledge_index": injections["shared_knowledge_index"],
        "user_knowledge_index": injections["user_knowledge_index"],
        "max_steps": max_steps,
        "auto_accept": auto_accept,
        "relevant_memory": _relevant_memory(root, user, cfg, memory_query),
    }
    protected_fields = set(payload) | {"completed_steps"}
    for key, value in input_data.items():
        if key not in protected_fields:
            payload[key] = value
    if existing is not None:
        payload["existing_plan"] = existing
        payload["completed_steps"] = _completed_steps(existing)
    return payload


def _normalize_result(
    *,
    data: dict[str, Any],
    goal: str,
    user: str,
    source: str,
    session_id: str,
    auto_accept: bool,
    max_steps: int,
    tool_names: set[str],
    existing_plan: dict[str, Any] | None,
) -> dict[str, Any]:
    expected_action = "edit" if existing_plan is not None else "create"
    action = data.get("action")
    if action == "skip" and existing_plan is None:
        raise PlanSkipped(str(data.get("message") or "问题过于简单，可以直接执行"))
    if action != expected_action:
        raise PlanGenerationError(
            f"task_plan 子代理应返回 action={expected_action!r}，实际为 {action!r}"
        )
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        raise PlanGenerationError("task_plan 子代理未返回步骤列表")
    if len(steps) > max_steps:
        raise PlanGenerationError(f"步骤数量 {len(steps)} 超过最大限制 {max_steps}")
    if existing_plan is not None:
        steps = _protect_completed_steps(existing_plan, steps)

    try:
        return normalize_plan(
            plan_id=existing_plan.get("plan_id") if existing_plan else None,
            title=(
                data.get("title")
                or (existing_plan or {}).get("title")
                or goal[:100]
            ),
            description=(
                data.get("description")
                or (existing_plan or {}).get("description")
                or goal
            ),
            user=user,
            source=(existing_plan or {}).get("source", source),
            session_id=(existing_plan or {}).get("session_id", session_id),
            steps=steps,
            auto_accept=auto_accept,
            reminder=_reminder(auto_accept, expected_action),
            status=(existing_plan or {}).get("status", "pending"),
            revision=int((existing_plan or {}).get("revision", 1)),
            created_at=(existing_plan or {}).get("created_at"),
            current_step=(existing_plan or {}).get("current_step"),
            tool_names=tool_names,
        )
    except PlanValidationError as exc:
        raise PlanGenerationError(f"计划校验失败：{exc}") from exc


def generate_plan(
    *,
    root: Path,
    user: str,
    goal: str,
    source: str = "cli",
    session_id: str = "default",
    config: dict[str, Any] | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
    tool_registry: ToolRegistry | None = None,
    existing_plan: dict[str, Any] | None = None,
    edit_request: str | None = None,
) -> dict[str, Any]:
    """Generate a normalized plan draft; persistence remains caller-owned."""
    if existing_plan is not None:
        _editable_plan(existing_plan)
    cfg = config if config is not None else load_config(user, root)
    registry = _runtime_tools(root, user, cfg, tool_registry)
    tool_names = set(registry.tools)
    task_plan_config = cfg.get("task_plan") or {}
    max_steps = int(task_plan_config.get("max_steps", 20))
    auto_accept = (
        bool(existing_plan.get("auto_accept", False))
        if existing_plan is not None
        else bool(task_plan_config.get("auto_accept", False))
    )
    action = "edit" if existing_plan is not None else "create"
    raw_input: dict[str, Any] = {
        "action": action,
        "goal": goal,
    }
    if existing_plan is not None:
        raw_input["existing_plan"] = existing_plan
    if edit_request is not None:
        raw_input["edit_request"] = edit_request
    input_data = prepare_task_plan_input(
        root=root,
        user=user,
        input_data=raw_input,
        config=cfg,
        tool_registry=registry,
    )

    try:
        result = AgentRunner(
            root,
            user,
            config=cfg,
            provider_factory=provider_factory,
        ).run("task_plan", input_data)
    except AgentRunError as exc:
        raise PlanGenerationError(f"task_plan 子代理执行失败：{exc}") from exc

    return _normalize_result(
        data=result.data,
        goal=goal,
        user=user,
        source=source,
        session_id=session_id,
        auto_accept=auto_accept,
        max_steps=max_steps,
        tool_names=tool_names,
        existing_plan=existing_plan,
    )


def edit_plan(
    *,
    root: Path,
    user: str,
    plan: dict[str, Any],
    edit_request: str,
    config: dict[str, Any] | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
    tool_registry: ToolRegistry | None = None,
) -> dict[str, Any]:
    """Edit an eligible plan while preserving completed steps."""
    _editable_plan(plan)
    return generate_plan(
        root=root,
        user=user,
        goal=str(plan.get("description") or plan.get("title") or ""),
        source=str(plan.get("source") or "cli"),
        session_id=str(plan.get("session_id") or "default"),
        config=config,
        provider_factory=provider_factory,
        tool_registry=tool_registry,
        existing_plan=plan,
        edit_request=edit_request,
    )


def persist_agent_result(
    *,
    root: Path,
    user: str,
    input_data: dict[str, Any],
    result_data: dict[str, Any],
    source: str = "web",
    session_id: str = "web",
    config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Normalize and persist a task_plan agent response."""

    action = result_data.get("action")
    if action == "skip":
        return None
    if action not in {"create", "edit"}:
        raise PlanGenerationError(f"task_plan 返回未知 action：{action!r}")
    existing = input_data.get("existing_plan")
    if action == "edit" and not isinstance(existing, dict):
        raise PlanGenerationError("task_plan 编辑结果缺少 existing_plan")
    if action == "create":
        existing = None
    cfg = config if config is not None else load_config(user, root)
    registry = _runtime_tools(root, user, cfg, None)
    task_plan_config = cfg.get("task_plan") or {}
    auto_accept = bool(input_data.get("auto_accept", task_plan_config.get("auto_accept", False)))
    max_steps = int(input_data.get("max_steps", task_plan_config.get("max_steps", 20)))
    normalized = _normalize_result(
        data=result_data,
        goal=str(input_data.get("goal") or result_data.get("description") or ""),
        user=user,
        source=source,
        session_id=session_id,
        auto_accept=auto_accept,
        max_steps=max_steps,
        tool_names=set(registry.tools),
        existing_plan=existing,
    )
    store = PlanStore(root, user)
    if action == "create":
        return store.create(normalized)
    plan_id = str(existing.get("plan_id") or normalized.get("plan_id") or "")
    return store.update(plan_id, lambda current: {**normalized, "plan_id": current["plan_id"]})
