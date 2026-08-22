"""上下文窗口预算和全面选择。

该模块仅构建面向提供者的视图。  它永远不会改变源
历史窗口。  调用者可以为省略的回合预先添加缓存的摘要
从这个角度来看。"""

from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from run.tools import MAX_TOOL_RESULT_CHARS, ToolResultTooLargeError


DEFAULT_OLDER_TOOL_RESULT_CHARS = 200
SMALL_FILE_DIRECTORY_RESULT_CHARS = 4_000


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    recent_tool_rounds: int = 3
    recent_full_rounds: int = 3
    max_rounds: int = 30
    rounds_after_compression: int = 10
    token_limit: int = 120000
    compression_ratio: float = 0.6
    older_tool_result_chars: int = DEFAULT_OLDER_TOOL_RESULT_CHARS

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "ContextPolicy":
        agents = config.get("agents") or {}
        history = config.get("history") or {}
        policy = cls(
            recent_tool_rounds=max(0, int(agents.get("conserved_rounds", 3))),
            recent_full_rounds=max(0, int(history.get("recent_full_rounds", 3))),
            max_rounds=max(1, int(agents.get("max_rounds", 80))),
            rounds_after_compression=max(
                1, int(agents.get("rounds_after_compression", 20))
            ),
            token_limit=max(1, int(agents.get("token_limit", 1000000))),
            compression_ratio=float(agents.get("token_compression_ratio", 0.3)),
        )
        if not 0 < policy.compression_ratio < 1:
            raise ValueError("agents.token_compression_ratio 必须在 0 和 1 之间")
        if policy.rounds_after_compression > policy.max_rounds:
            raise ValueError(
                "agents.rounds_after_compression 不能大于 agents.max_rounds"
            )
        return policy

    @property
    def input_budget(self) -> int:
        return max(1, math.floor(self.token_limit * self.compression_ratio))

    @property
    def output_reserve(self) -> int:
        return self.token_limit - self.input_budget


@dataclass(slots=True)
class RoundGroup:
    number: int
    messages: list[dict[str, Any]]
    raw_text_messages: list[dict[str, Any]] = field(default_factory=list)
    think: dict[str, Any] | None = None
    tool: dict[str, Any] | None = None


@dataclass(slots=True)
class ContextSelection:
    messages: list[dict[str, Any]]
    all_rounds: list[RoundGroup]
    kept_rounds: list[RoundGroup]
    removed_rounds: list[RoundGroup]
    estimated_tokens_before: int
    estimated_tokens_after: int
    tool_schema_tokens: int
    input_budget: int
    output_reserve: int
    round_limit_triggered: bool
    token_limit_triggered: bool
    fixed_content_over_budget: bool
    recent_content_over_budget: bool

    def stats(self) -> dict[str, Any]:
        return {
            "rounds_before": len(self.all_rounds),
            "rounds_kept": len(self.kept_rounds),
            "rounds_removed": len(self.removed_rounds),
            "kept_round_numbers": [item.number for item in self.kept_rounds],
            "removed_round_numbers": [item.number for item in self.removed_rounds],
            "estimated_tokens_before": self.estimated_tokens_before,
            "estimated_tokens_after": self.estimated_tokens_after,
            "tool_schema_tokens": self.tool_schema_tokens,
            "input_budget": self.input_budget,
            "output_reserve": self.output_reserve,
            "round_limit_triggered": self.round_limit_triggered,
            "token_limit_triggered": self.token_limit_triggered,
            "fixed_content_over_budget": self.fixed_content_over_budget,
            "recent_content_over_budget": self.recent_content_over_budget,
        }


def build_context_snapshot(
    selection: ContextSelection,
    *,
    system_prompt: str,
    summary_message: dict[str, Any] | None = None,
    capacity_tokens: int,
    source: str = "runtime_recalculated",
) -> dict[str, Any]:
    """Build a self-consistent display snapshot for one Provider input window."""

    total_tokens = max(0, int(selection.estimated_tokens_after))
    remaining = total_tokens
    system_prompt_tokens = min(remaining, estimate_text_tokens(system_prompt))
    remaining -= system_prompt_tokens
    tool_schema_tokens = min(remaining, max(0, int(selection.tool_schema_tokens)))
    remaining -= tool_schema_tokens
    summary_tokens = min(
        remaining,
        (
            estimate_messages_tokens([summary_message])
            if summary_message is not None
            else 0
        ),
    )
    remaining -= summary_tokens
    conversation_tokens = remaining
    capacity = max(0, int(capacity_tokens))
    return {
        "available": True,
        "source": source,
        "measurement": "estimated",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "system_prompt_tokens": system_prompt_tokens,
        "tool_schema_tokens": tool_schema_tokens,
        "conversation_tokens": conversation_tokens,
        "summary_tokens": summary_tokens,
        "other_tokens": 0,
        "total_tokens": total_tokens,
        "capacity_tokens": capacity,
        "percent": round(total_tokens * 100 / capacity, 4) if capacity else 0.0,
        "foreground_rounds": len(selection.kept_rounds),
        "workspace_rounds": len(selection.all_rounds),
        "removed_rounds": len(selection.removed_rounds),
        "kept_round_numbers": [item.number for item in selection.kept_rounds],
        "removed_round_numbers": [item.number for item in selection.removed_rounds],
    }


def estimate_text_tokens(text: str) -> int:
    """Conservative dependency-free estimate shared by all Provider modes."""
    if not text:
        return 0
    cjk = sum(1 for char in text if "\u3400" <= char <= "\u9fff")
    return cjk + math.ceil((len(text) - cjk) / 4)


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _media_estimation_projection(value: Any) -> tuple[Any, int]:
    """Remove binary payload text while retaining a conservative media budget."""

    if isinstance(value, list):
        projected: list[Any] = []
        media_tokens = 0
        for item in value:
            clean, tokens = _media_estimation_projection(item)
            projected.append(clean)
            media_tokens += tokens
        return projected, media_tokens
    if not isinstance(value, dict):
        return value, 0
    if value.get("kind") == "inline_base64" and isinstance(value.get("data"), str):
        clean = dict(value)
        clean["data"] = "[inline media payload]"
        return clean, 1024
    if isinstance(value.get("uri"), str) and value["uri"].startswith("data:image/"):
        clean = dict(value)
        clean["uri"] = "data:image/[inline media payload]"
        return clean, 1024
    projected: dict[str, Any] = {}
    media_tokens = 0
    for key, item in value.items():
        clean, tokens = _media_estimation_projection(item)
        projected[key] = clean
        media_tokens += tokens
    return projected, media_tokens


def estimate_messages_tokens(messages: Iterable[dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        # Binary media is billed provider-specifically; never count Base64 as text.
        projected, media_tokens = _media_estimation_projection(message)
        total += 4 + estimate_text_tokens(_stable_json(projected)) + media_tokens
    return total


def estimate_tools_tokens(tools: list[dict[str, Any]] | None) -> int:
    if not tools:
        return 0
    return 8 + estimate_text_tokens(_stable_json(tools))


def _round_lookup(rounds: Any) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for item in rounds if isinstance(rounds, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            number = int(item.get("round"))
        except (TypeError, ValueError):
            continue
        result[number] = copy.deepcopy(item)
    return result


def _text_rounds(messages: Any) -> list[list[dict[str, Any]]]:
    """Group persisted text without ever splitting a user-led conversation turn."""
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for raw in messages if isinstance(messages, list) else []:
        if not isinstance(raw, dict):
            continue
        message = copy.deepcopy(raw)
        role = message.get("role")
        if role == "user":
            if current:
                groups.append(current)
            current = [message]
        elif current:
            current.append(message)
        else:
            # 畸形的领导历史被作为一个不可分割的整体保留下来。
            current = [message]
    if current:
        groups.append(current)
    return groups


_LEGACY_EMPTY_MESSAGE = (
    "[历史兼容：该轮消息内容未被旧版本保存；如果该轮仅包含附件，"
    "附件详情已无法从这条旧记录恢复]"
)


def _content_value(content: Any) -> str | list[dict[str, Any]]:
    blocks = content if isinstance(content, list) else []
    if not blocks:
        return _LEGACY_EMPTY_MESSAGE
    if blocks and all(
        isinstance(block, dict) and block.get("type") == "text" for block in blocks
    ):
        return "".join(str(block.get("text") or "") for block in blocks)
    return copy.deepcopy(blocks)


def _item_round_messages(window: dict[str, Any]) -> list[list[dict[str, Any]]] | None:
    container = window.get("items")
    raw_items = container.get("items") if isinstance(container, dict) else None
    if not isinstance(raw_items, list) or not raw_items:
        return None
    by_round: dict[int, list[dict[str, Any]]] = {}
    inferred_round = 0
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        try:
            round_number = int(metadata.get("round"))
        except (TypeError, ValueError):
            if raw.get("type") == "message" and raw.get("role") == "user":
                inferred_round += 1
            round_number = max(1, inferred_round)
        inferred_round = max(inferred_round, round_number)
        by_round.setdefault(round_number, []).append(copy.deepcopy(raw))

    result: list[list[dict[str, Any]]] = []
    for round_number in sorted(by_round):
        items = by_round[round_number]
        messages: list[dict[str, Any]] = []
        pending_reasoning: dict[str, Any] | None = None
        index = 0
        while index < len(items):
            item = items[index]
            kind = item.get("type")
            if kind == "reasoning":
                pending_reasoning = item
                index += 1
                continue
            if kind == "message" and item.get("role") in {"user", "assistant"}:
                message: dict[str, Any] = {
                    "role": item["role"],
                    "content": _content_value(item.get("content")),
                }
                # Old attachment-only rounds were persisted with content=[]. Do not
                # forward that invalid native item to MessageItem validation; the
                # compatibility text above preserves the round boundary instead.
                if isinstance(item.get("content"), list) and item["content"]:
                    message["_kemo_message"] = item
                if pending_reasoning is not None and item["role"] == "assistant":
                    message["_kemo_reasoning"] = pending_reasoning
                    pending_reasoning = None
                messages.append(message)
                index += 1
                continue
            if kind == "tool_call":
                calls: list[dict[str, Any]] = []
                while index < len(items) and items[index].get("type") == "tool_call":
                    call = items[index]
                    calls.append(
                        {
                            "id": str(call.get("call_id") or ""),
                            "type": "function",
                            "function": {
                                "name": str(call.get("name") or "unknown_tool"),
                                "arguments": _stable_json(call.get("arguments") or {}),
                            },
                        }
                    )
                    index += 1
                message = {"role": "assistant", "content": None, "tool_calls": calls}
                if pending_reasoning is not None:
                    message["_kemo_reasoning"] = pending_reasoning
                    pending_reasoning = None
                messages.append(message)
                continue
            if kind == "tool_result":
                content = item.get("content")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(item.get("call_id") or ""),
                        "name": str(item.get("name") or "unknown_tool"),
                        "content": _stable_json(content),
                    }
                )
                index += 1
                continue
            index += 1
        if pending_reasoning is not None:
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "_kemo_reasoning": pending_reasoning,
                }
            )
        if messages:
            result.append(messages)
    return result or None


def _context_tool_result(
    value: Any,
    *,
    tool_name: str,
    compact_limit: int | None,
) -> str:
    """Bound every historical tool result, including protected recent rounds."""

    rendered = value if isinstance(value, str) else _stable_json(value)
    if len(rendered) > MAX_TOOL_RESULT_CHARS:
        error = ToolResultTooLargeError(
            tool_name,
            {},
            result_chars=len(rendered),
            limit_chars=MAX_TOOL_RESULT_CHARS,
        )
        return _stable_json({"ok": False, "error": error.error_payload()})
    file_directory_result = (
        tool_name == "file"
        and isinstance(value, dict)
        and value.get("action") in {"list_dir", "tree_dir"}
    )
    preserve_small_directory_result = (
        file_directory_result and len(rendered) <= SMALL_FILE_DIRECTORY_RESULT_CHARS
    )
    if (
        compact_limit is None
        or len(rendered) <= compact_limit
        or preserve_small_directory_result
    ):
        return rendered
    if file_directory_result:
        return _stable_json(
            {
                "ok": bool(value.get("ok", True)),
                "action": value.get("action"),
                "path": value.get("path"),
                "total": value.get("total"),
                "returned": value.get("returned"),
                "offset": value.get("offset"),
                "limit": value.get("limit"),
                "next_offset": value.get("next_offset"),
                "has_more": value.get("has_more"),
                "compressed": True,
                "original_chars": len(rendered),
                "message": "目录条目已从旧上下文省略；需要时使用 next_offset 重新读取对应页。",
            }
        )
    return _stable_json(
        {
            "compressed": True,
            "preview": rendered[: max(1, compact_limit - 40)],
            "original_chars": len(rendered),
        }
    )


def _tool_iteration_messages(
    records: list[dict[str, Any]], *, compact_limit: int | None
) -> list[dict[str, Any]]:
    by_iteration: dict[int, list[dict[str, Any]]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        try:
            iteration = int(record.get("iteration", 1))
        except (TypeError, ValueError):
            iteration = 1
        by_iteration.setdefault(iteration, []).append(record)

    messages: list[dict[str, Any]] = []
    for iteration in sorted(by_iteration):
        calls = by_iteration[iteration]
        tool_calls: list[dict[str, Any]] = []
        for position, call in enumerate(calls):
            call_id = str(call.get("id") or f"history-{iteration}-{position}")
            tool_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": str(call.get("name") or "unknown_tool"),
                        "arguments": _stable_json(call.get("arguments") or {}),
                    },
                }
            )
        messages.append(
            {"role": "assistant", "content": None, "tool_calls": tool_calls}
        )
        for position, call in enumerate(calls):
            result = call.get("result")
            content = _context_tool_result(
                result,
                tool_name=str(call.get("name") or "unknown_tool"),
                compact_limit=compact_limit,
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(
                        call.get("id") or f"history-{iteration}-{position}"
                    ),
                    "name": str(call.get("name") or "unknown_tool"),
                    "content": content,
                }
            )
    return messages


def build_round_groups(
    window: dict[str, Any], policy: ContextPolicy
) -> list[RoundGroup]:
    item_groups = _item_round_messages(window)
    text_groups = item_groups or _text_rounds(
        (window.get("text") or {}).get("messages")
    )
    think_by_round = _round_lookup((window.get("think") or {}).get("rounds"))
    tool_by_round = _round_lookup((window.get("tool") or {}).get("rounds"))
    total = len(text_groups)
    groups: list[RoundGroup] = []
    for index, raw_group in enumerate(text_groups, start=1):
        tool = tool_by_round.get(index)
        records = tool.get("calls", []) if isinstance(tool, dict) else []
        is_recent = index > total - policy.recent_tool_rounds
        provider_messages: list[dict[str, Any]] = []
        if item_groups is not None:
            provider_messages = copy.deepcopy(raw_group)
            for message in provider_messages:
                if message.get("role") == "tool":
                    message["content"] = _context_tool_result(
                        message.get("content"),
                        tool_name=str(message.get("name") or "unknown_tool"),
                        compact_limit=(
                            None if is_recent else policy.older_tool_result_chars
                        ),
                    )
            raw_text_messages = [
                {
                    key: copy.deepcopy(value)
                    for key, value in message.items()
                    if not key.startswith("_")
                }
                for message in raw_group
                if message.get("role") in {"user", "assistant"}
            ]
            groups.append(
                RoundGroup(
                    number=index,
                    messages=provider_messages,
                    raw_text_messages=raw_text_messages,
                    think=think_by_round.get(index),
                    tool=tool,
                )
            )
            continue
        user_messages = [item for item in raw_group if item.get("role") == "user"]
        assistant_messages = [
            item for item in raw_group if item.get("role") == "assistant"
        ]
        other_messages = [
            item for item in raw_group if item.get("role") not in {"user", "assistant"}
        ]
        provider_messages.extend(user_messages or raw_group[:1])
        if records:
            provider_messages.extend(
                _tool_iteration_messages(
                    records,
                    compact_limit=None if is_recent else policy.older_tool_result_chars,
                )
            )
        provider_messages.extend(assistant_messages)
        provider_messages.extend(other_messages)
        groups.append(
            RoundGroup(
                number=index,
                messages=provider_messages,
                raw_text_messages=raw_group,
                think=think_by_round.get(index),
                tool=tool,
            )
        )
    return groups


def select_context(
    *,
    window: dict[str, Any],
    policy: ContextPolicy,
    system_message: dict[str, Any] | None,
    current_user_message: dict[str, Any] | None,
    tools: list[dict[str, Any]] | None = None,
    summary_message: dict[str, Any] | None = None,
    force_compress: bool = False,
) -> ContextSelection:
    """Select whole historical rounds under configured round and token budgets."""
    rounds = build_round_groups(window, policy)
    fixed_prefix = [
        item for item in (system_message, summary_message) if item is not None
    ]
    fixed_suffix = [current_user_message] if current_user_message is not None else []
    tool_tokens = estimate_tools_tokens(tools)
    all_messages = [*fixed_prefix]
    for group in rounds:
        all_messages.extend(group.messages)
    all_messages.extend(fixed_suffix)
    before = estimate_messages_tokens(all_messages) + tool_tokens

    projected_rounds = len(rounds) + (1 if current_user_message is not None else 0)
    round_trigger = projected_rounds >= policy.max_rounds
    current_round_slots = 1 if current_user_message is not None else 0
    minimum_history_rounds = max(0, policy.recent_full_rounds - current_round_slots)
    if round_trigger or force_compress:
        keep_count = min(
            max(
                0,
                policy.rounds_after_compression - current_round_slots,
                minimum_history_rounds,
            ),
            len(rounds),
        )
        kept = rounds[-keep_count:] if keep_count else []
    else:
        kept = list(rounds)

    fixed_tokens = (
        estimate_messages_tokens([*fixed_prefix, *fixed_suffix]) + tool_tokens
    )
    token_trigger = before > policy.token_limit
    while len(kept) > minimum_history_rounds and token_trigger:
        candidate_messages = [*fixed_prefix]
        for group in kept:
            candidate_messages.extend(group.messages)
        candidate_messages.extend(fixed_suffix)
        if (
            estimate_messages_tokens(candidate_messages) + tool_tokens
            <= policy.input_budget
        ):
            break
        kept.pop(0)

    selected_messages = [*fixed_prefix]
    for group in kept:
        selected_messages.extend(group.messages)
    selected_messages.extend(fixed_suffix)
    after = estimate_messages_tokens(selected_messages) + tool_tokens
    recent_content_over_budget = bool(
        token_trigger
        and len(kept) <= minimum_history_rounds
        and after > policy.input_budget
    )
    kept_numbers = {item.number for item in kept}
    removed = [item for item in rounds if item.number not in kept_numbers]
    return ContextSelection(
        messages=selected_messages,
        all_rounds=rounds,
        kept_rounds=kept,
        removed_rounds=removed,
        estimated_tokens_before=before,
        estimated_tokens_after=after,
        tool_schema_tokens=tool_tokens,
        input_budget=policy.input_budget,
        output_reserve=policy.output_reserve,
        round_limit_triggered=round_trigger or force_compress,
        token_limit_triggered=token_trigger,
        fixed_content_over_budget=fixed_tokens > policy.input_budget,
        recent_content_over_budget=recent_content_over_budget,
    )
