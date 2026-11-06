"""Attempt-local duplicate protection without replaying prior tool results."""
from __future__ import annotations

import copy
from typing import Any

from run.retry.recovery import _FILE_LIVE_READ_ACTIONS, _LIVE_READ_ACTIONS


REEXECUTE_ACTIONS = {
    "file": _FILE_LIVE_READ_ACTIONS, **_LIVE_READ_ACTIONS,
    "memory_manage": frozenset({"list", "get", "search_by_title", "search_by_content", "search_many"}),
    "task_time": frozenset({"list", "get", "history"}),
    "task_plan": frozenset({"view", "list"}),
    "expand_creater": frozenset({"list", "validate"}),
    "sense_creater": frozenset({"list", "validate"}),
    "skill_creater": frozenset({"list", "get", "validate"}),
    "multimodal": frozenset({"analyze_image", "transcribe_audio", "analyze_video"}),
}
REEXECUTE_TOOLS = frozenset({
    "web_search", "history_search", "get_current_time",
    "attach_media", "wait_for_condition", "kemo_graph_read",
})
BLOCK_TOOLS = frozenset({"external_message"})
BLOCK_ACTIONS = {
    "file": frozenset({"write", "append", "edit", "copy", "move", "delete", "make_dir"}),
    "shell": frozenset({"run", "cancel"}),
    "network": frozenset({"post", "put", "patch", "delete"}),
    "memory_manage": frozenset({"add", "edit", "delete"}),
    "task_time": frozenset({"create", "update", "delete"}),
    "task_plan": frozenset({
        "edit", "revise_remaining", "retry_step", "reset_step", "skip_step",
        "step_done", "step_fail", "abort", "approve", "pause", "resume",
    }),
    "subagent_dispatch": frozenset({"create", "call", "resume", "split", "cancel", "guide"}),
    "multimodal": frozenset({"generate_image", "generate_speech", "generate_video",
                             "edit_image", "convert_speech"}),
    "expand_creater": frozenset({"create"}),
    "sense_creater": frozenset({"create"}),
    "skill_creater": frozenset({"create", "update", "delete"}),
}


def attempt_dedup_policy(name: str, arguments: dict[str, Any]) -> str:
    if name == "expand_call":
        return "reexecute"
    action = str(arguments.get("action") or "").strip().casefold()
    if name in REEXECUTE_TOOLS or action in REEXECUTE_ACTIONS.get(name, ()):
        return "reexecute"
    if name in BLOCK_TOOLS or action in BLOCK_ACTIONS.get(name, ()):
        return "block"
    return "block"


class AttemptToolDedup:
    """Track duplicate signatures without returning prior result payloads."""

    def __init__(self, recovery: dict[str, Any] | None = None):
        self.entries = {
            signature: {
                key: copy.deepcopy(value[key])
                for key in ("name", "arguments", "iteration", "dedup_policy", "retry_failure")
                if key in value
            }
            for signature, value in (recovery or {}).items()
            if isinstance(value, dict) and value.get("dedup_policy") == "block"
        }
        self.recovered = set(self.entries)
        self.policies: dict[str, str] = {}

    def check(self, name: str, arguments: dict[str, Any], tracker: Any, *,
              retry_recovery: bool = False
              ) -> tuple[dict[str, Any], str, bool, int] | None:
        from run.tools import tool_call_signature
        signature = tool_call_signature(name, arguments)
        policy = attempt_dedup_policy(name, arguments)
        self.policies[signature] = policy
        count = tracker.count if signature == tracker.signature else 0
        entry = self.entries.get(signature)
        if entry and policy == "block":
            if signature in self.recovered:
                self.recovered.discard(signature)
                return _duplicate_blocked_payload(retry=True), "duplicate_blocked", True, count
            if entry.get("retry_failure") and not retry_recovery:
                self.entries.pop(signature, None)
            else:
                return (
                    _duplicate_blocked_payload(retry=retry_recovery),
                    "duplicate_blocked", True, count,
                )
        if policy == "reexecute":
            self.entries.pop(signature, None)
            self.recovered.discard(signature)
        if tracker.is_blocked(tracker.next_count(name, arguments)):
            return {
                "ok": False,
                "error": {
                    "code": "identical_call_blocked",
                    "exception_type": "ConsecutiveIdenticalToolCallLimitExceeded",
                    "message": f"工具 {name} 相同参数连续真实执行已达到上限 {tracker.limit} 次",
                    "limit": tracker.limit, "consecutive_identical_calls": count,
                    "instruction": "请根据已有结果继续任务，或调整方案；本次没有执行。",
                },
            }, "identical_call_blocked", False, count
        return None

    def remember(self, name: str, arguments: dict[str, Any], payload: dict[str, Any],
                 iteration: int, *, retain_failure_for_retry: bool = False) -> None:
        from run.tools import tool_call_signature
        signature = tool_call_signature(name, arguments)
        policy = self.policies[signature]
        if payload.get("ok") is not True:
            if policy == "block" and retain_failure_for_retry:
                self.entries[signature] = {
                    "name": name, "arguments": copy.deepcopy(arguments),
                    "iteration": iteration,
                    "dedup_policy": "block", "retry_failure": True,
                }
            else:
                self.entries.pop(signature, None)
            return
        if policy == "block":
            self.entries[signature] = {
                "name": name, "arguments": copy.deepcopy(arguments),
                "iteration": iteration, "dedup_policy": "block",
            }
        else:
            self.entries.pop(signature, None)


def _duplicate_blocked_payload(*, retry: bool = False) -> dict[str, Any]:
    reason = "此前尝试中已有相同调用记录" if retry else "本次尝试中已有相同调用记录"
    return {
        "ok": False,
        "error": {
            "code": "duplicate_blocked",
            "exception_type": "DuplicateToolCallBlocked",
            "message": f"{reason}；为避免重复副作用，本次未再次执行，也未返回此前结果。",
            "instruction": "需要最新状态时重新调用查询工具；需要继续操作时调整任务步骤，不要重复提交同一副作用。",
        },
        "execution": {"executed": False, "deduplicated": True},
    }
