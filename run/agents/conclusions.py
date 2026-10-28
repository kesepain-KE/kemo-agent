"""Small, public delivery contract; tool history belongs to the workbench."""
from __future__ import annotations

import json
import re
from typing import Any

from run.agents.public_trace import public_tool_data

_HIDDEN = {
    "metadata", "tool_calls", "tool_records", "trace", "events", "messages",
    "raw_text", "reasoning", "thinking", "system_prompt", "checkpoint", "input",
}


def numeric_usage(value):
    return {k: v for k, v in (value or {}).items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)}


def conclusion_data(value: Any) -> Any:
    """Filter legacy result envelopes too; never project raw runner metadata."""
    budget = [512]
    def visit(item, depth=0):
        budget[0] -= 1
        if depth > 8 or budget[0] < 0:
            return "[truncated]"
        if isinstance(item, dict):
            result = {}
            for k, v in list(item.items())[:80]:
                normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(k)).lower().replace("-", "_")
                if normalized not in _HIDDEN:
                    result[k] = visit(v, depth + 1)
                if budget[0] <= 0:
                    result["truncated"] = True
                    break
            if len(item) > 80:
                result["truncated"] = True
            return result
        if isinstance(item, (list, tuple)):
            result = []
            for v in item[:80]:
                if budget[0] <= 0:
                    break
                result.append(visit(v, depth + 1))
            if len(result) < len(item):
                result.append("[truncated]")
            return result
        if isinstance(item, str) and len(item) <= 16000 and item.lstrip().startswith(("{", "[")):
            try:
                parsed = json.loads(item)
            except (ValueError, RecursionError):
                pass
            else:
                if isinstance(parsed, (dict, list)):
                    return json.dumps(visit(parsed, depth + 1), ensure_ascii=False)
        return item
    return public_tool_data(visit(value))


def delivery(snapshot: dict[str, Any]) -> dict[str, Any]:
    result = snapshot.get("result")
    result_usage = result.get("usage") if isinstance(result, dict) else None
    if result is not None and hasattr(result, "data"):
        result = getattr(result, "data")
    elif isinstance(result, dict) and "data" in result and "agent" in result:
        result = result["data"]
    response = {
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
