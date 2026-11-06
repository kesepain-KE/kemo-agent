"""Small, public delivery contract; tool history belongs to the workbench."""
from __future__ import annotations

from typing import Any

from run.agents.public_trace import public_tool_data


def numeric_usage(value):
    return {k: v for k, v in (value or {}).items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)}


def conclusion_data(value: Any) -> Any:
    """Project business data after the caller removes the runtime envelope."""
    return public_tool_data(value, hide_private_fields=False)


def delivery(snapshot: dict[str, Any]) -> dict[str, Any]:
    result = snapshot.get("result")
    result_usage = result.get("usage") if isinstance(result, dict) else None
    if result is not None and hasattr(result, "data"):
        result = getattr(result, "data")
    elif (
        "execution_live" not in snapshot
        and isinstance(result, dict) and "data" in result and "agent" in result
    ):
        # Durable task records already contain business data. Only scheduler
        # snapshots still carry the runtime result envelope.
        result = result["data"]
    response = {
        "ok": snapshot.get("status") in {"queued", "running", "completed", "cancelling"},
        "task_id": snapshot.get("id", snapshot.get("task_id", "")),
        "agent": snapshot.get("agent", ""),
        "status": snapshot.get("status", ""),
        "run_count": snapshot.get("run_count", 1),
        "resumable": bool(snapshot.get("resumable")) and snapshot.get("status") in {"timed_out", "tool_limit"},
        "data": conclusion_data(result),
    }
    for key in ("submission_order", "usage", "error", "budget", "progress", "stop_reason", "background_jobs", "origin_task_id"):
        if snapshot.get(key) is not None:
            response[key] = numeric_usage(snapshot[key]) if key == "usage" else conclusion_data(snapshot[key])
    if "usage" not in response and result_usage is not None:
        response["usage"] = numeric_usage(result_usage)
    if not response.get("stop_reason"):
        reason = (snapshot.get("error") or {}).get("stop_reason") or {
            "tool_limit": "tool_budget_exhausted", "timed_out": "timeout",
            "timed_out_running": "timeout", "failed": "execution_error",
            "cancelled": "cancelled", "interrupted": "interrupted",
        }.get(str(snapshot.get("status") or ""))
        if reason:
            response["stop_reason"] = reason
    from run.agents.budgets import legacy_tool_stop
    if legacy_tool_stop(snapshot):
        response["resume_candidate"] = True
        response["message"] = "旧版工具额度失败记录；可显式 resume，仍须通过最新安全点、定义、权限及退出核验。"
    if snapshot.get("status") in {"failed", "interrupted"}:
        response["ok"] = False
    return response


def notification(snapshot: dict[str, Any]) -> dict[str, Any]:
    response = delivery(snapshot)
    response["result"] = response.pop("data")
    return response
