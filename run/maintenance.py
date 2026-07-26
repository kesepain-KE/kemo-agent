"""Background maintenance for memory review and context compaction."""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from provider.factory import ProviderCongestionError, create_provider
from run.agent_runner import AgentRunner
from run.config import load_config
from run.context import estimate_text_tokens
from run.context_service import context_status
from run.engine import compress_context
from run.memory_analysis import (
    analyze_memory_batch_resilient,
    analyze_round_memory,
    memory_batch_operation_id,
    memory_round_data,
    persist_round_memory_analysis,
)
from run.history import commit_window, load_window
from run.history_index import (
    claim_pending_memory,
    claim_pending_summary,
    defer_summary_claim,
    find_record,
    finish_memory_claim,
    finish_summary_claim,
    history_directory,
    update_summary_checkpoint,
)
from run.memory import (
    MemoryStore,
    contains_sensitive_credential,
    memory_extraction_batch_rounds,
    memory_extraction_mode,
)
from run.memory_pipeline import memory_round_payload
from run.session_runtime import session_lock
from run.tools import ToolRegistry, discover_tools
from run.users import list_users


BEIJING = ZoneInfo("Asia/Shanghai")
CONTEXT_REVIEW_INTERVAL = timedelta(hours=1)
IMPORTANT_MEMORY_INPUT_LIMIT = 200
MEMORY_RECOVERY_ROUNDS_PER_SCAN = 10
HISTORY_SUMMARY_CHUNK_TOKENS = 24_000
HISTORY_SUMMARY_MAX_OUTPUT_TOKENS = 10_000


class MaintenanceError(RuntimeError):
    pass


def _safe_agent_output_preview(error: BaseException) -> str:
    raw_text = str(getattr(error, "raw_text", "") or "").strip()
    if not raw_text:
        return ""
    if contains_sensitive_credential(raw_text):
        return "[输出包含疑似敏感内容，已隐藏]"
    return " ".join(raw_text.split())[:1000]


def _parse_daily_time(value: Any) -> tuple[int, int]:
    text = str(value or "02:00").strip()
    parts = text.split(":")
    if len(parts) != 2:
        raise MaintenanceError("agents.daily_memory_review_time 必须是 HH:MM")
    try:
        hour, minute = (int(part) for part in parts)
    except ValueError as exc:
        raise MaintenanceError("agents.daily_memory_review_time 必须是 HH:MM") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise MaintenanceError("agents.daily_memory_review_time 必须是有效的北京时间")
    return hour, minute


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content.rstrip())
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _message_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, list):
        return str(value or "").strip()
    parts = [
        str(block.get("text") or "").strip()
        for block in value
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "\n".join(part for part in parts if part)


def _summary_rounds(window: dict[str, Any], target_round: int) -> list[dict[str, Any]]:
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for raw in (window.get("text") or {}).get("messages", []):
        if not isinstance(raw, dict):
            continue
        if raw.get("role") == "user" and current:
            groups.append(current)
            current = []
        current.append(raw)
    if current:
        groups.append(current)
    result: list[dict[str, Any]] = []
    for number, group in enumerate(groups[:target_round], start=1):
        user_text = "\n".join(
            text
            for item in group
            if item.get("role") == "user"
            for text in [_message_text(item.get("content"))]
            if text
        )
        assistant_text = "\n".join(
            text
            for item in group
            if item.get("role") == "assistant"
            for text in [_message_text(item.get("content"))]
            if text
        )
        if user_text or assistant_text:
            result.append({"round": number, "user": user_text, "assistant": assistant_text})
    return result


def _split_summary_text(text: str, token_budget: int) -> list[str]:
    value = str(text or "")
    if not value:
        return []
    budget = max(256, int(token_budget))
    parts: list[str] = []
    remaining = value
    while remaining:
        if estimate_text_tokens(remaining) <= budget:
            parts.append(remaining)
            break
        low = 1
        high = len(remaining)
        while low < high:
            middle = (low + high + 1) // 2
            if estimate_text_tokens(remaining[:middle]) <= budget:
                low = middle
            else:
                high = middle - 1
        cut = max(1, low)
        search_start = max(1, int(cut * 0.75))
        preferred = max(
            remaining.rfind("\n", search_start, cut),
            remaining.rfind("。", search_start, cut),
            remaining.rfind("！", search_start, cut),
            remaining.rfind("？", search_start, cut),
        )
        if preferred >= search_start:
            cut = preferred + 1
        parts.append(remaining[:cut])
        remaining = remaining[cut:]
    return parts


def _summary_round_parts(
    item: dict[str, Any],
    token_budget: int,
) -> list[dict[str, Any]]:
    if estimate_text_tokens(json.dumps(item, ensure_ascii=False, default=str)) <= token_budget:
        return [item]
    round_number = max(0, int(item.get("round") or 0))
    empty_tokens = estimate_text_tokens(
        json.dumps(
            {"round": round_number, "user": "", "assistant": ""},
            ensure_ascii=False,
        )
    )
    text_budget = max(256, token_budget - empty_tokens - 64)
    parts = [
        {"round": round_number, "user": value, "assistant": ""}
        for value in _split_summary_text(str(item.get("user") or ""), text_budget)
    ]
    parts.extend(
        {"round": round_number, "user": "", "assistant": value}
        for value in _split_summary_text(
            str(item.get("assistant") or ""),
            text_budget,
        )
    )
    return parts or [{"round": round_number, "user": "", "assistant": ""}]


def _summary_chunks(rounds: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for raw_item in rounds:
        for item in _summary_round_parts(raw_item, HISTORY_SUMMARY_CHUNK_TOKENS):
            candidate = [*current, item]
            candidate_tokens = estimate_text_tokens(
                json.dumps(candidate, ensure_ascii=False, default=str)
            )
            if current and candidate_tokens > HISTORY_SUMMARY_CHUNK_TOKENS:
                chunks.append(current)
                current = [item]
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def _history_sessions(root: Path, user: str) -> list[dict[str, str]]:
    history = root / "users" / user / "history"
    if not history.is_dir():
        return []
    latest: dict[tuple[str, str], tuple[str, dict[str, str]]] = {}
    for directory in history.iterdir():
        if not directory.is_dir() or directory.is_symlink():
            continue
        try:
            data = load_window(directory).get("data") or {}
        except Exception:
            continue
        source = str(data.get("source") or "")
        session_id = str(data.get("session_id") or "")
        if not source or not session_id:
            continue
        value = {
            "source": source,
            "session_id": session_id,
            "window": directory.name,
        }
        updated = str(data.get("updated_at") or "")
        key = (source, session_id)
        if key not in latest or updated > latest[key][0]:
            latest[key] = (updated, value)
    return [
        value
        for _, value in sorted(
            latest.values(),
            key=lambda item: item[0],
            reverse=True,
        )
    ]


class MaintenanceScheduler:
    """Run system-owned maintenance alongside, but independently from, cron."""

    def __init__(
        self,
        root: Path,
        *,
        poll_interval: float = 30.0,
        provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
        tool_registry_factory: Callable[[Path, str], ToolRegistry] = discover_tools,
        on_error: Callable[[str, Exception], None] | None = None,
        history_summary_enabled: bool = True,
        summary_max_attempts: int = 5,
        summary_retry_delays: tuple[int, ...] = (30, 120, 600, 1800),
    ) -> None:
        self.root = root.resolve()
        self.poll_interval = max(1.0, float(poll_interval))
        self.provider_factory = provider_factory
        self.tool_registry_factory = tool_registry_factory
        self.on_error = on_error
        self.history_summary_enabled = bool(history_summary_enabled)
        self.summary_max_attempts = max(1, int(summary_max_attempts))
        self.summary_retry_delays = tuple(
            max(1, int(value)) for value in summary_retry_delays
        ) or (30,)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._next_context_review: dict[str, datetime] = {}
        self._last_results: dict[str, Any] = {}

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="system-maintenance",
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, timeout: float = 10.0) -> None:
        self._stop_event.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        with self._lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self.running,
                "last_results": json.loads(
                    json.dumps(self._last_results, ensure_ascii=False, default=str)
                ),
            }

    def scan_once(
        self,
        *,
        now: datetime | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        results: dict[str, Any] = {}
        for user in list_users(self.root):
            if self._stop_event.is_set():
                break
            try:
                results[user] = self._scan_user(user, current, force=force)
            except Exception as exc:
                self._report_error(f"maintenance:{user}", exc)
                results[user] = {
                    "error": {
                        "message": str(exc),
                        "exception_type": type(exc).__name__,
                    }
                }
        with self._lock:
            self._last_results = results
        return results

    def _scan_user(
        self,
        user: str,
        current: datetime,
        *,
        force: bool,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "memory_recovery": self._recover_pending_memory(user),
        }
        if self.history_summary_enabled:
            result["history_summary"] = self.process_next_summary(user)

        next_context = self._next_context_review.setdefault(
            user, current + CONTEXT_REVIEW_INTERVAL
        )
        if force or current >= next_context:
            result["context"] = self._review_contexts(user)
            self._next_context_review[user] = current + CONTEXT_REVIEW_INTERVAL
        return result

    def process_next_summary(self, user: str) -> dict[str, Any]:
        claim = claim_pending_summary(self.root, user)
        if claim is None:
            return {"claimed": 0, "processed": [], "failed": []}
        source = str(claim.get("source") or "")
        session_id = str(claim.get("session_id") or "")
        archive_name = str(claim.get("archive_window") or "")
        claim_id = str(claim.get("summary_claim_id") or "")
        target_round = max(0, int(claim.get("summary_target_round") or 0))
        identity = {"source": source, "session_id": session_id, "round": target_round}
        try:
            if (
                not source
                or not session_id
                or not claim_id
                or target_round < 1
                or not archive_name
                or Path(archive_name).name != archive_name
            ):
                raise MaintenanceError("历史摘要领取记录缺少有效会话身份")
            archive_path = history_directory(self.root, user) / archive_name
            with session_lock(self.root, user, source, session_id):
                window = load_window(archive_path)
                data = window.get("data") or {}
                if data.get("source") != source or data.get("session_id") != session_id:
                    raise MaintenanceError("历史摘要领取记录与归档身份不一致")
                archive_rounds = max(0, int(data.get("rounds") or 0))
                if target_round > archive_rounds:
                    raise MaintenanceError(
                        f"历史摘要目标轮次 {target_round} 超过归档轮数 {archive_rounds}"
                    )
                rounds = _summary_rounds(window, target_round)
            if not rounds:
                raise MaintenanceError("历史摘要没有可用的用户或助手正文")
            config = load_config(user, self.root)
            runner = AgentRunner(
                self.root,
                user,
                config=config,
                provider_factory=self.provider_factory,
            )
            chunks = _summary_chunks(rounds)
            checkpoint = claim.get("summary_checkpoint")
            checkpoint_round = (
                max(0, int(checkpoint.get("target_round") or 0))
                if isinstance(checkpoint, dict)
                else 0
            )
            start_chunk = max(0, int(claim.get("summary_checkpoint_next_chunk") or 0))
            checkpoint_total = max(
                0, int(claim.get("summary_checkpoint_total_chunks") or 0)
            )
            if (
                not isinstance(checkpoint, dict)
                or checkpoint_round != target_round
                or checkpoint_total != len(chunks)
                or start_chunk > len(chunks)
            ):
                rolling: dict[str, str] | None = None
                start_chunk = 0
            else:
                rolling = {
                    "title": str(checkpoint.get("title") or "").strip(),
                    "summary": str(checkpoint.get("summary") or "").strip(),
                }
                if not rolling["title"] or not rolling["summary"]:
                    rolling = None
                    start_chunk = 0
            recovery_modes: list[str] = []
            output_transports: list[str] = []
            for chunk_index, chunk in enumerate(
                chunks[start_chunk:], start=start_chunk
            ):
                if self._stop_event.is_set():
                    raise MaintenanceError("历史摘要任务已取消")
                result = runner.run(
                    "history_summary",
                    {
                        "trigger": "session_closed",
                        "session_id": session_id,
                        "target_round": target_round,
                        "previous_summary": rolling,
                        "rounds": chunk,
                    },
                    cancel_event=self._stop_event,
                    max_tokens=HISTORY_SUMMARY_MAX_OUTPUT_TOKENS,
                    structured_output_tool=True,
                )
                result_metadata = getattr(result, "metadata", {})
                if not isinstance(result_metadata, dict):
                    result_metadata = {}
                recovery = str(
                    result_metadata.get("history_summary_recovery") or ""
                ).strip()
                if recovery:
                    recovery_modes.append(recovery)
                transport = str(
                    result_metadata.get("structured_output_transport") or ""
                ).strip()
                if transport:
                    output_transports.append(transport)
                rolling = {
                    "title": str(result.data.get("title") or "").strip(),
                    "summary": str(result.data.get("summary") or "").strip(),
                }
                checkpoint_result = update_summary_checkpoint(
                    self.root,
                    user,
                    source,
                    session_id,
                    claim_id=claim_id,
                    next_chunk=chunk_index + 1,
                    total_chunks=len(chunks),
                    title=rolling["title"],
                    summary=rolling["summary"],
                )
                if checkpoint_result is None:
                    raise MaintenanceError("历史摘要领取已失效")
            if not rolling:
                raise MaintenanceError("历史摘要结果为空")
            finished = finish_summary_claim(
                self.root,
                user,
                source,
                session_id,
                claim_id=claim_id,
                title=rolling["title"],
                summary=rolling["summary"],
                completed_round=target_round,
                max_attempts=self.summary_max_attempts,
                retry_delays=self.summary_retry_delays,
            )
            if finished is None:
                raise MaintenanceError("历史摘要领取已失效")
            return {
                "claimed": 1,
                "processed": [{
                    **identity,
                    "status": "completed",
                    "chunks": len(chunks),
                    "resumed_from_chunk": start_chunk,
                    "recovery_modes": recovery_modes,
                    "output_transports": output_transports,
                }],
                "failed": [],
            }
        except ProviderCongestionError as exc:
            error = {"message": str(exc), "exception_type": type(exc).__name__}
            try:
                defer_summary_claim(
                    self.root,
                    user,
                    source,
                    session_id,
                    claim_id=claim_id,
                    error=error,
                    delay_seconds=30,
                )
            except Exception as index_exc:
                error["index_error"] = {
                    "message": str(index_exc),
                    "exception_type": type(index_exc).__name__,
                }
            self._report_error(f"history_summary:{user}:congestion", exc)
            return {
                "claimed": 1,
                "processed": [],
                "failed": [{**identity, "deferred": True, "error": error}],
            }
        except Exception as exc:
            error = {"message": str(exc), "exception_type": type(exc).__name__}
            raw_output_preview = _safe_agent_output_preview(exc)
            if raw_output_preview:
                error["raw_output_preview"] = raw_output_preview
            if self._stop_event.is_set():
                try:
                    defer_summary_claim(
                        self.root,
                        user,
                        source,
                        session_id,
                        claim_id=claim_id,
                        error=error,
                        delay_seconds=1,
                    )
                except Exception as index_exc:
                    error["index_error"] = {
                        "message": str(index_exc),
                        "exception_type": type(index_exc).__name__,
                    }
                return {
                    "claimed": 1,
                    "processed": [],
                    "failed": [{**identity, "deferred": True, "error": error}],
                }
            try:
                finish_summary_claim(
                    self.root,
                    user,
                    source,
                    session_id,
                    claim_id=claim_id,
                    error=error,
                    max_attempts=self.summary_max_attempts,
                    retry_delays=self.summary_retry_delays,
                )
            except Exception as index_exc:
                error["index_error"] = {
                    "message": str(index_exc),
                    "exception_type": type(index_exc).__name__,
                }
            self._report_error(f"maintenance:{user}:history_summary", exc)
            return {"claimed": 1, "processed": [], "failed": [{**identity, "error": error}]}

    def _recover_pending_summary(self, user: str) -> dict[str, Any]:
        """Compatibility alias for callers predating the dedicated scheduler."""

        return self.process_next_summary(user)

    def _recover_pending_memory(self, user: str) -> dict[str, Any]:
        config = load_config(user, self.root)
        extraction_mode = memory_extraction_mode(config)
        if extraction_mode == "disabled":
            return {
                "mode": extraction_mode,
                "claimed": 0,
                "processed": [],
                "failed": [],
            }
        claimable_statuses = {"failed", "processing", "queued"}
        remaining_status = "deferred"
        if extraction_mode in {"background", "on_commit"}:
            claimable_statuses.add("pending")
            remaining_status = "pending"
        raw_limit = (config.get("memory") or {}).get(
            "recovery_max_rounds_per_scan", MEMORY_RECOVERY_ROUNDS_PER_SCAN
        )
        try:
            limit = max(1, min(20, int(raw_limit)))
        except (TypeError, ValueError):
            limit = MEMORY_RECOVERY_ROUNDS_PER_SCAN
        batch_size = memory_extraction_batch_rounds(config)
        runner: AgentRunner | None = None
        processed: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        claimed = 0
        batches = 0

        while claimed < limit:
            if self._stop_event.is_set():
                break
            claim = claim_pending_memory(
                self.root,
                user,
                statuses=claimable_statuses,
                max_rounds=min(batch_size, limit - claimed),
            )
            if claim is None:
                break
            source = str(claim.get("source") or "")
            session_id = str(claim.get("session_id") or "")
            archive_name = str(claim.get("archive_window") or "")
            claim_id = str(claim.get("memory_claim_id") or "")
            round_start = int(
                claim.get("memory_claim_start_round")
                or claim.get("memory_claim_round")
                or 0
            )
            round_end = int(
                claim.get("memory_claim_end_round")
                or claim.get("memory_claim_round")
                or round_start
            )
            claimed_count = max(1, round_end - round_start + 1)
            claimed += claimed_count
            batches += 1
            target_round = max(0, int(claim.get("memory_target_round") or 0))
            claim_remaining_status = (
                "queued"
                if target_round > 0
                or (
                    extraction_mode == "compression_only"
                    and str(claim.get("lifecycle") or "") == "closed"
                )
                else remaining_status
            )
            archive_path = history_directory(self.root, user) / archive_name
            identity = {
                "source": source,
                "session_id": session_id,
                "round": round_start,
                "round_start": round_start,
                "round_end": round_end,
            }
            extraction: dict[str, Any] | None = None
            extraction_error: dict[str, Any] | None = None
            archive_committed = False
            stale_result = False
            analysis: dict[str, Any] | None = None
            batch_rounds: list[dict[str, Any]] = []
            skipped_rounds: list[int] = []
            try:
                if (
                    not source
                    or not session_id
                    or not claim_id
                    or round_start < 1
                    or round_end < round_start
                    or not archive_name
                    or Path(archive_name).name != archive_name
                ):
                    raise MaintenanceError("记忆恢复领取记录缺少有效会话身份")
                with session_lock(self.root, user, source, session_id):
                    window = load_window(archive_path)
                    data = window.get("data") or {}
                    if data.get("source") != source or data.get("session_id") != session_id:
                        raise MaintenanceError("记忆恢复领取记录与归档身份不一致")
                    archive_rounds = max(0, int(data.get("rounds") or 0))
                    archive_cursor = max(0, int(data.get("memory_processed_round") or 0))
                    if round_end > archive_rounds:
                        raise MaintenanceError(
                            f"待提取轮次 {round_start}-{round_end} 超过归档轮数 {archive_rounds}"
                        )
                    if archive_cursor >= round_end:
                        extraction = {
                            "status": "already_processed",
                            "candidate_count": 0,
                            "round_start": round_start,
                            "round_end": round_end,
                        }
                    else:
                        if archive_cursor != round_start - 1:
                            raise MaintenanceError(
                                f"记忆提取游标已变化：{archive_cursor} != {round_start - 1}"
                            )
                        cancelled_rounds = {
                            int(item.get("round") or 0)
                            for item in data.get("round_metrics", [])
                            if isinstance(item, dict)
                            and item.get("status") == "cancelled"
                        }
                        for round_number in range(round_start, round_end + 1):
                            if round_number in cancelled_rounds:
                                skipped_rounds.append(round_number)
                                continue
                            batch_rounds.append(
                                memory_round_data(
                                    round_number=round_number,
                                    **memory_round_payload(window, round_number),
                                )
                            )
                if batch_rounds:
                    if runner is None:
                        runner = AgentRunner(
                            self.root,
                            user,
                            config=config,
                            provider_factory=self.provider_factory,
                        )
                    if len(batch_rounds) == 1:
                        only_round = batch_rounds[0]
                        messages = only_round.get("messages") or []
                        analysis = analyze_round_memory(
                            round_number=int(only_round["round"]),
                            prompt=str((messages[0] or {}).get("content") or ""),
                            text=str((messages[1] or {}).get("content") or ""),
                            reasoning=str(
                                ((only_round.get("think") or {}).get("content") or "")
                            ),
                            tool_records=list(only_round.get("tools") or []),
                            agent_runner=runner,
                            cancel_event=self._stop_event,
                        )
                    else:
                        analysis = analyze_memory_batch_resilient(
                            rounds=batch_rounds,
                            agent_runner=runner,
                            cancel_event=self._stop_event,
                            source={
                                "source": "round_commit",
                                "channel": source,
                                "session_id": session_id,
                                "round_start": round_start,
                                "round_end": round_end,
                            },
                        )
                    if analysis.get("status") != "completed":
                        raw_error = analysis.get("error")
                        extraction_error = (
                            dict(raw_error)
                            if isinstance(raw_error, dict)
                            else {
                                "message": "记忆提取失败",
                                "exception_type": "MaintenanceError",
                            }
                        )
                        raise MaintenanceError(
                            str(extraction_error.get("message") or "记忆提取失败")
                        )
                elif extraction is None:
                    extraction = {
                        "status": "skipped",
                        "candidate_count": 0,
                        "reason": "cancelled_rounds",
                        "round_start": round_start,
                        "round_end": round_end,
                        "rounds": [],
                        "skipped_rounds": skipped_rounds,
                    }

                with session_lock(self.root, user, source, session_id):
                    window = load_window(archive_path)
                    data = window.setdefault("data", {})
                    archive_cursor = max(
                        0, int(data.get("memory_processed_round") or 0)
                    )
                    current_claim = find_record(
                        self.root,
                        user,
                        source,
                        session_id,
                    )
                    if (
                        not isinstance(current_claim, dict)
                        or current_claim.get("memory_claim_id") != claim_id
                        or int(
                            current_claim.get("memory_claim_start_round")
                            or current_claim.get("memory_claim_round")
                            or 0
                        )
                        != round_start
                        or int(
                            current_claim.get("memory_claim_end_round")
                            or current_claim.get("memory_claim_round")
                            or 0
                        )
                        != round_end
                    ):
                        stale_result = True
                        raise MaintenanceError("记忆提取 claim 已失效，丢弃陈旧结果")
                    if archive_cursor >= round_end:
                        extraction = {
                            "status": "already_processed",
                            "candidate_count": 0,
                            "round_start": round_start,
                            "round_end": round_end,
                        }
                    elif archive_cursor != round_start - 1:
                        raise MaintenanceError(
                            f"记忆提取游标已变化：{archive_cursor} != {round_start - 1}"
                        )
                    elif analysis is not None:
                        extraction = persist_round_memory_analysis(
                            root=self.root,
                            user=user,
                            config=config,
                            analysis=analysis,
                            operation_id=memory_batch_operation_id(
                                user,
                                source,
                                session_id,
                                round_start,
                                round_end,
                                batch_rounds,
                            ),
                        )
                        extraction["skipped_rounds"] = skipped_rounds
                        if extraction.get("status") != "completed":
                            raw_error = extraction.get("error")
                            extraction_error = (
                                dict(raw_error)
                                if isinstance(raw_error, dict)
                                else {
                                    "message": "记忆持久化失败",
                                    "exception_type": "MaintenanceError",
                                }
                            )
                            raise MaintenanceError(
                                str(
                                    extraction_error.get("message")
                                    or "记忆持久化失败"
                                )
                            )
                    finished = finish_memory_claim(
                        self.root,
                        user,
                        source,
                        session_id,
                        claim_id=claim_id,
                        processed_round=round_end,
                        remaining_status=claim_remaining_status,
                    )
                    if finished is None:
                        raise MaintenanceError("记忆提取 claim 在提交时已失效")
                    data["memory_processed_round"] = int(
                        finished.get("memory_processed_round") or round_end
                    )
                    data["memory_status"] = str(
                        finished.get("memory_status") or claim_remaining_status
                    )
                    data.pop("memory_error", None)
                    if isinstance(finished.get("memory_last_error"), dict):
                        data["memory_last_error"] = dict(
                            finished["memory_last_error"]
                        )
                    for field in (
                        "memory_queue_reason",
                        "memory_target_round",
                        "memory_queued_at",
                    ):
                        if finished.get(field) not in {None, "", 0}:
                            data[field] = finished[field]
                        else:
                            data.pop(field, None)
                    commit_window(archive_path, window)
                    archive_committed = True
                processed.append(
                    {
                        **identity,
                        "status": str((extraction or {}).get("status") or "completed"),
                        "candidate_count": int(
                            (extraction or {}).get("candidate_count") or 0
                        ),
                        "claim_applied": finished is not None,
                        "archive_committed": archive_committed,
                    }
                )
            except Exception as exc:
                error = extraction_error or {
                    "message": str(exc),
                    "exception_type": type(exc).__name__,
                }
                if stale_result:
                    failed.append({**identity, "stale": True, "error": error})
                    continue
                try:
                    finished = finish_memory_claim(
                        self.root,
                        user,
                        source,
                        session_id,
                        claim_id=claim_id,
                        error=error,
                    )
                except Exception as index_exc:
                    error["index_error"] = {
                        "message": str(index_exc),
                        "exception_type": type(index_exc).__name__,
                    }
                    finished = None
                try:
                    with session_lock(self.root, user, source, session_id):
                        window = load_window(archive_path)
                        data = window.setdefault("data", {})
                        diagnostic = (
                            finished.get("memory_last_error")
                            if isinstance(finished, dict)
                            and isinstance(finished.get("memory_last_error"), dict)
                            else error
                        )
                        data["memory_status"] = "failed"
                        data["memory_error"] = dict(diagnostic)
                        data["memory_last_error"] = dict(diagnostic)
                        commit_window(archive_path, window)
                except Exception as archive_exc:
                    error["archive_error"] = {
                        "message": str(archive_exc),
                        "exception_type": type(archive_exc).__name__,
                    }
                failed.append({**identity, "error": error})
                self._report_error(f"maintenance:{user}:memory", exc)
        return {
            "mode": extraction_mode,
            "claimed": claimed,
            "batches": batches,
            "processed": processed,
            "failed": failed,
        }

    def _review_important_memory(
        self,
        user: str,
        config: dict[str, Any],
        store: MemoryStore,
    ) -> dict[str, Any]:
        """Deprecated compatibility helper; important memory now runs via cron."""
        temporary = [
            item
            for tier in ("half_year", "one_month", "seven_days")
            for item in store.load_tier(tier)
        ]
        temporary.sort(
            key=lambda item: (-int(item.get("weight", 0)), str(item.get("filename", "")))
        )
        important_path = self.root / "users" / user / "memory_temporary_important.md"
        try:
            existing = important_path.read_text("utf-8").strip()
        except FileNotFoundError:
            existing = ""
        permanent = store.load_tier("permanent")
        if not temporary and not existing:
            return {"status": "skipped", "reason": "no_temporary_memory"}
        result = AgentRunner(
            self.root,
            user,
            config=config,
            provider_factory=self.provider_factory,
        ).run(
            "memory_temporary_important",
            {
                "temporary_memories": temporary[:IMPORTANT_MEMORY_INPUT_LIMIT],
                "existing_important_memory": existing,
                "permanent_memories": permanent,
            },
            cancel_event=self._stop_event,
        )
        content = result.data.get("content")
        if not isinstance(content, str):
            raise MaintenanceError("memory_temporary_important 输出缺少 content 字符串")
        if contains_sensitive_credential(content):
            raise MaintenanceError("重要记忆审阅结果包含疑似敏感凭据，已拒绝持久化")
        if content.strip():
            _atomic_text(important_path, content)
        else:
            important_path.unlink(missing_ok=True)
        return {
            "status": "completed",
            "items_considered": min(len(temporary), IMPORTANT_MEMORY_INPUT_LIMIT),
            "chars": len(content.strip()),
        }

    def _review_contexts(self, user: str) -> dict[str, Any]:
        reviewed = 0
        compressed: list[str] = []
        for session in _history_sessions(self.root, user):
            if self._stop_event.is_set():
                break
            request = {
                "user": user,
                "source": session["source"],
                "session_id": session["session_id"],
            }
            try:
                status = context_status(
                    request,
                    root=self.root,
                    tool_registry_factory=self.tool_registry_factory,
                )
                reviewed += 1
                context = status.get("context") or {}
                if not (
                    context.get("round_limit_triggered")
                    or context.get("token_limit_triggered")
                ):
                    continue
                compress_context(
                    request,
                    root=self.root,
                    provider_factory=self.provider_factory,
                    tool_registry_factory=self.tool_registry_factory,
                    cancel_event=self._stop_event,
                )
                compressed.append(session["window"])
            except Exception as exc:
                self._report_error(f"maintenance:{user}:context", exc)
        return {"reviewed": reviewed, "compressed": compressed}

    def _report_error(self, component: str, exc: Exception) -> None:
        if self.on_error is not None:
            self.on_error(component, exc)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.scan_once()
            except Exception as exc:
                self._report_error("maintenance", exc)
            self._stop_event.wait(self.poll_interval)
