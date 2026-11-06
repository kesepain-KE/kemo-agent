"""Explicit business-success envelopes shared by main and child tool loops."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from run.infra import diagnostic_text, record_runtime_event


def tool_result_payload(
    name: str, result: Any, *, root: Path, user: str, session_id: str = "",
) -> dict[str, Any]:
    """Do not infer success for legacy/unknown results or normalize them to ok."""
    succeeded = isinstance(result, dict) and result.get("ok") is True
    payload = {"ok": succeeded, "result": result}
    if not isinstance(result, dict) or "ok" not in result:
        record_runtime_event(
            root, user, category="backend", name="tool_result_missing_ok",
            status="failed", tool_name=name, session_id=session_id,
        )
    if not succeeded:
        detail = (
            result.get("error") or result.get("message")
            if isinstance(result, dict) else None
        )
        category = (
            detail.get("category")
            if isinstance(detail, dict) and isinstance(detail.get("category"), str)
            else "tool_result_error"
        )
        payload["error"] = {
            "message": diagnostic_text(str(detail or "工具未明确返回 ok:true，不能确认执行成功")),
            "category": category,
        }
        if isinstance(detail, dict) and isinstance(detail.get("exception_type"), str):
            payload["error"]["exception_type"] = detail["exception_type"]
    if isinstance(result, dict):
        flags = [flag for flag in ("duplicate", "cached") if result.get(flag)]
        if flags:
            payload["execution"] = {
                "note": (
                    "工具报告 " + "/".join(flags)
                    + " 标记；该结果可能来自缓存或重复调用，不应视为本次重新执行的证据。"
                ),
            }
    return payload
