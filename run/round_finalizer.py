"""Durable finalization of cancelled and otherwise controlled-stop rounds."""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from events import RunEvent
from provider.protocol.models import ContentBlock
from run.context import (
    ContextPolicy,
    ContextSelection,
    build_context_snapshot,
    select_context,
)
from run.context_summary import build_summary_message
from run.history import (
    _trim_to_max_rounds,
    append_round_items,
    commit_window,
    queue_memory_extraction,
)
from run.history_index import set_active as set_active_history_session
from run.history_index import update_run_state
from run.prompt import PromptBundle
from run.run_state import RoundState, RunDependencies, RunIdentity
from run.session_runtime import copy_committed_round_to_archive
from run.usage import merge_usage, usage_from_dict


def queue_summary_memory_extraction(
    *,
    root: Path,
    user: str,
    source: str,
    session_id: str,
    summary_cache: dict[str, Any],
    archive_round_number: int,
    reason: str,
) -> dict[str, Any] | None:
    """Queue only the absolute rounds already covered by the rolling summary."""

    raw_covered = summary_cache.get("covered_through_round")
    if raw_covered is None:
        covered_rounds = summary_cache.get("covered_rounds")
        raw_covered = (
            max(
                (
                    int(value)
                    for value in covered_rounds
                    if isinstance(value, int) or str(value).isdigit()
                ),
                default=0,
            )
            if isinstance(covered_rounds, list)
            else 0
        )
    try:
        target_round = min(
            max(0, int(archive_round_number)),
            max(0, int(raw_covered or 0)),
        )
    except (TypeError, ValueError):
        target_round = 0
    if target_round < 1:
        return None
    try:
        return queue_memory_extraction(
            root,
            user,
            source,
            session_id,
            target_round=target_round,
            reason=reason,
        )
    except Exception as exc:
        return {
            "status": "failed",
            "reason": "memory_queue_registration_failed",
            "round": target_round,
            "candidates": 0,
            "error": {
                "message": str(exc),
                "exception_type": type(exc).__name__,
            },
        }


@dataclass(frozen=True, slots=True)
class TerminalRoundContext:
    identity: RunIdentity
    dependencies: RunDependencies
    state: RoundState
    request: dict[str, Any]
    content_blocks: list[ContentBlock]
    prompt: str
    window: dict[str, Any]
    archive_window: dict[str, Any]
    window_path: Path
    runtime_path: Path
    context_selection: ContextSelection
    context_policy: ContextPolicy
    summary_cache: dict[str, Any] | None
    system_message: dict[str, Any] | None
    tool_schemas: list[dict[str, Any]] | None
    prompt_bundle: PromptBundle
    runtime_provider: dict[str, Any]
    queue_compression_memory: bool


@dataclass(slots=True)
class TerminalRoundCommitter:
    context: TerminalRoundContext

    def commit_terminal_round(
        self,
        *,
        status: str,
        reason: str,
        marker: str,
        pending_message: str,
        pending_exception_type: str,
        pending_status: str = "not_executed",
    ) -> RunEvent:
        """Persist a controlled stop as a real, terminal conversation round."""

        context = self.context
        identity = context.identity
        base = identity.root
        user = identity.user
        source = identity.source
        session_id = identity.session_id
        run_id = identity.run_id
        request = context.request
        content_blocks = context.content_blocks
        prompt = context.prompt
        window = context.window
        archive_window = context.archive_window
        window_path = context.window_path
        runtime_path = context.runtime_path
        context_selection = context.context_selection
        context_policy = context.context_policy
        summary_cache = context.summary_cache
        system_message = context.system_message
        tool_schemas = context.tool_schemas
        prompt_bundle = context.prompt_bundle
        runtime_provider = context.runtime_provider
        queue_compression_memory = context.queue_compression_memory
    
        cancelled = status == "cancelled"
        if context.state.finalized:
            return RunEvent(
                type="done",
                usage=dict(context.state.usage_total),
                metadata={
                    "committed": True,
                    "status": status,
                    "stop_reason": reason,
                    **(
                        {"cancelled": True, "cancel_reason": reason}
                        if cancelled
                        else {}
                    ),
                    "run_id": run_id,
                },
            )
    
        cancelled_records = copy.deepcopy(context.state.tool_records)
        recorded_ids = {
            str(record.get("id") or "")
            for record in cancelled_records
            if isinstance(record, dict)
        }
        for call_id, pending in context.state.pending_tool_calls.items():
            if call_id in recorded_ids:
                continue
            cancelled_records.append(
                {
                    "id": call_id,
                    "name": str(pending.get("name") or "unknown_tool"),
                    "arguments": copy.deepcopy(pending.get("arguments") or {}),
                    "status": pending_status,
                    "duplicate": False,
                    "result": {
                        "ok": False,
                        "error": {
                            "message": pending_message,
                            "exception_type": pending_exception_type,
                            **({"cancelled": True} if cancelled else {}),
                        },
                    },
                    "iteration": int(pending.get("iteration") or 1),
                    "elapsed_ms": 0,
                }
            )
    
        cancelled_window = copy.deepcopy(window)
        cancelled_archive = copy.deepcopy(archive_window)
        round_number = int(cancelled_window["data"].get("rounds", 0)) + 1
        archive_round_number = int(
            cancelled_archive["data"].get("rounds", 0)
        ) + 1
        elapsed_ms = max(0, round((time.monotonic() - context.state.run_started) * 1000))
        partial_text = "".join(context.state.observed_text).rstrip()
        terminal_text = f"{partial_text}\n\n{marker}" if partial_text else marker
        reasoning = "".join(context.state.observed_reasoning)
        cancelled_window["text"]["messages"].extend(
            [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": terminal_text},
            ]
        )
        cancelled_window["think"]["rounds"].append(
            {"round": round_number, "content": reasoning}
        )
        cancelled_window["tool"]["rounds"].append(
            {"round": round_number, "calls": cancelled_records}
        )
        append_round_items(
            cancelled_window,
            round_number=round_number,
            user_content=[
                block.model_dump(mode="json", exclude_none=True)
                for block in content_blocks
            ],
            reasoning=reasoning,
            text=terminal_text,
            tool_records=cancelled_records,
            # A controlled stop may happen before the Provider exposes a
            # complete native output item list. Synthesize every call/result
            # pair from records so the durable item protocol has no orphan.
            provider_responses=[],
        )
        cancelled_window["data"]["rounds"] = round_number
        metrics = cancelled_window["data"].setdefault("round_metrics", [])
        if not isinstance(metrics, list):
            metrics = []
            cancelled_window["data"]["round_metrics"] = metrics
        terminal_metric = {
            "round": round_number,
            "usage": dict(context.state.usage_total),
            "elapsed_ms": elapsed_ms,
            "tool_calls": len(cancelled_records),
            "guidance": list(context.state.consumed_guidance),
            "context.state.provider_responses": copy.deepcopy(context.state.provider_responses),
            "status": status,
            "stop_reason": reason,
        }
        if cancelled:
            terminal_metric.update(
                {"cancelled": True, "cancel_reason": reason}
            )
        metrics.append(terminal_metric)
        merge_usage(
            cancelled_window["data"]["token_usage"],
            usage_from_dict(context.state.usage_total),
        )
        copy_committed_round_to_archive(
            cancelled_archive,
            cancelled_window,
            round_number,
            archive_round_number,
        )
        cancelled_runtime_limit = (
            max(1, len(context_selection.kept_rounds) + 1)
            if summary_cache is not None and context_selection.removed_rounds
            else context_policy.max_rounds
        )
        runtime_window = _trim_to_max_rounds(
            cancelled_window, cancelled_runtime_limit
        )
        try:
            next_summary_message = build_summary_message(summary_cache)
            next_selection = select_context(
                window=runtime_window,
                policy=context_policy,
                system_message=system_message,
                summary_message=next_summary_message,
                current_user_message=None,
                tools=tool_schemas,
            )
            runtime_window["data"]["context"] = {
                **next_selection.stats(),
                "round_offset": max(
                    0,
                    archive_round_number
                    - int(runtime_window["data"].get("rounds", 0)),
                ),
                "workspace_rounds": int(
                    runtime_window["data"].get("rounds", 0)
                ),
                "summary_cache": (
                    "context_summary.json" if summary_cache is not None else None
                ),
            }
            runtime_window["data"]["context_snapshot"] = build_context_snapshot(
                next_selection,
                system_prompt=prompt_bundle.text,
                summary_message=next_summary_message,
                capacity_tokens=context_policy.token_limit,
            )
        except Exception as exc:
            runtime_window["data"]["context"] = {
                **context.state.context_stats,
                "snapshot_error": {
                    "message": str(exc),
                    "exception_type": type(exc).__name__,
                },
            }
    
        commit_window(window_path, cancelled_archive)
        commit_window(runtime_path, runtime_window)
        context.state.finalized = True
        if (
            queue_compression_memory
            and summary_cache is not None
            and context_selection.removed_rounds
        ):
            queue_summary_memory_extraction(
                root=base,
                user=user,
                source=source,
                session_id=session_id,
                summary_cache=summary_cache,
                archive_round_number=archive_round_number,
                reason=f"automatic_compression_{status}_round",
            )
        try:
            update_run_state(
                base,
                user,
                source,
                session_id,
                run_state="idle",
                run_id=run_id or None,
                directory=window_path,
            )
            context.state.history_run_registered = False
        except Exception:
            pass
        active_key = request.get("_history_active_key")
        if isinstance(active_key, str) and active_key.strip():
            try:
                set_active_history_session(
                    base,
                    user,
                    active_key.strip(),
                    session_id,
                    source=source,
                )
            except Exception:
                pass
        terminal_metadata = {
            "text": terminal_text,
            "reasoning": reasoning,
            "usage": dict(context.state.usage_total),
            "model": runtime_provider["model"],
            "user": user,
            "source": source,
            "session_id": session_id,
            "window": window_path.name,
            "tool_calls": len(cancelled_records),
            "elapsed_ms": elapsed_ms,
            "run_id": run_id,
            "guidance_count": len(context.state.consumed_guidance),
            "committed": True,
            "status": status,
            "stop_reason": reason,
        }
        if cancelled:
            terminal_metadata.update(
                {"cancelled": True, "cancel_reason": reason}
            )
        return RunEvent(
            type="done",
            usage=dict(context.state.usage_total),
            metadata=terminal_metadata,
        )
    
    def commit_cancelled_round(self) -> RunEvent:
        return self.commit_terminal_round(
            status="cancelled",
            reason="user_emergency_stop",
            marker="[本轮已由用户紧急停止]",
            pending_message="工具调用因用户紧急停止而取消",
            pending_exception_type="ToolCancelledError",
            pending_status="cancelled",
        )
