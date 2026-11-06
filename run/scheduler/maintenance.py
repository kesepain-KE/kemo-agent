"""Background RAW memory and display-title work, never automatic compaction."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json
import threading

from provider.factory import ProviderCongestionError, create_provider
from run.agents import AgentRunner
from run.cards import CardStore
from run.config import cron_history_retention_days, list_users, load_config, read_yaml_object
from run.context import context_status, estimate_text_tokens
from run.history import (
    claim_pending_summary, cleanup_cron_history, defer_summary_claim, finish_summary_claim,
    list_records,
)
from run.scheduler.memory_recovery import recover_pending_memory
from run.tools import discover_tools
from run.infra import diagnostic_text

CONTEXT_REVIEW_INTERVAL = timedelta(hours=1)
CRON_HISTORY_CLEANUP_INTERVAL = timedelta(minutes=5)
HISTORY_SUMMARY_MAX_OUTPUT_TOKENS = 4096
HISTORY_SUMMARY_INPUT_TOKEN_BUDGET = 24_000


class MaintenanceError(RuntimeError):
    pass


def _summary_input_tokens(records, *, session_id, target_seq):
    payload = {
        "trigger": "session_closed",
        "session_id": session_id,
        "target_seq": target_seq,
        "records": records,
    }
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return estimate_text_tokens(serialized)


def _append_latest_summary_record(records, record, *, session_id, target_seq):
    """Keep a bounded chronological suffix, preferring the newest RAW text."""
    selected = [*records, record]
    while len(selected) > 1 and _summary_input_tokens(
        selected, session_id=session_id, target_seq=target_seq
    ) > HISTORY_SUMMARY_INPUT_TOKEN_BUDGET:
        selected.pop(0)

    if _summary_input_tokens(selected, session_id=session_id, target_seq=target_seq) <= (
        HISTORY_SUMMARY_INPUT_TOKEN_BUDGET
    ):
        return selected

    latest = selected[-1]
    text = latest["text"]
    low, high = 0, len(text)
    while low < high:
        middle = (low + high) // 2
        candidate = [{**latest, "text": text[middle:]}]
        if _summary_input_tokens(
            candidate, session_id=session_id, target_seq=target_seq
        ) <= HISTORY_SUMMARY_INPUT_TOKEN_BUDGET:
            high = middle
        else:
            low = middle + 1
    latest = {**latest, "text": text[low:]}
    selected = [latest]
    if not latest["text"] or _summary_input_tokens(
        selected, session_id=session_id, target_seq=target_seq
    ) > HISTORY_SUMMARY_INPUT_TOKEN_BUDGET:
        raise MaintenanceError("历史简介 Token 预算不足以容纳单条正文")
    return selected


class MaintenanceScheduler:
    def __init__(self, root, *, poll_interval=30.0, provider_factory=create_provider,
                 tool_registry_factory=discover_tools, on_error=None,
                 history_summary_enabled=True, summary_max_attempts=5,
                 summary_retry_delays=(30, 120, 600, 1800)):
        self.root = root.resolve()
        self.poll_interval = max(1.0, float(poll_interval))
        self.provider_factory, self.tool_registry_factory = provider_factory, tool_registry_factory
        self.on_error, self.history_summary_enabled = on_error, bool(history_summary_enabled)
        self.summary_max_attempts = max(1, min(5, int(summary_max_attempts)))
        self.summary_retry_delays = summary_retry_delays
        self._stop_event, self._wake_event = threading.Event(), threading.Event()
        self._thread, self._lock = None, threading.RLock()
        self._last_results, self._next_context_review, self._next_cron_history_cleanup = {}, {}, {}

    @property
    def running(self):
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self):
        with self._lock:
            if self.running:
                return
            self._stop_event.clear()
            self._wake_event.clear()
            self._thread = threading.Thread(target=self._run_loop, name="system-maintenance", daemon=True)
            self._thread.start()

    def stop(self, *, timeout=10.0):
        self._stop_event.set()
        self._wake_event.set()
        with self._lock:
            thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)
        with self._lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None

    def status(self):
        with self._lock:
            return {"running": self.running, "last_results": copy.deepcopy(self._last_results)}

    def wake(self):
        self._wake_event.set()

    def scan_once(self, *, now=None, force=False):
        current, results = now or datetime.now(timezone.utc), {}
        for user in list_users(self.root):
            if self._stop_event.is_set():
                break
            try:
                results[user] = self._scan_user(user, current, force=force)
            except Exception as error:
                self._report_error(f"maintenance:{user}", error)
                results[user] = {"error": {"message": str(error), "exception_type": type(error).__name__}}
        with self._lock:
            self._last_results = copy.deepcopy(results)
        return results

    def _scan_user(self, user, current, *, force):
        result = {}
        if force or current >= self._next_cron_history_cleanup.get(user, current):
            config = read_yaml_object(self.root / "config" / "global_config.yaml")
            result["cron_history_cleanup"] = cleanup_cron_history(
                self.root, user, retention_days=cron_history_retention_days(config), now=current)
            self._next_cron_history_cleanup[user] = current + CRON_HISTORY_CLEANUP_INTERVAL
        result["memory_recovery"] = self._recover_pending_memory(user)
        if self.history_summary_enabled:
            result["history_summary"] = self.process_next_summary(user)
        if force or current >= self._next_context_review.setdefault(user, current + CONTEXT_REVIEW_INTERVAL):
            result["context"] = self._review_contexts(user)
            self._next_context_review[user] = current + CONTEXT_REVIEW_INTERVAL
        return result

    def process_next_summary(self, user):
        """The session-list synopsis is metadata, not the rolling VIEW summary."""
        claim = claim_pending_summary(self.root, user, max_attempts=self.summary_max_attempts)
        if claim is None:
            return {"claimed": 0, "processed": [], "failed": []}
        source, identity, claim_id = claim["source"], claim["session_id"], claim["summary_claim_id"]
        target = claim["summary_claim_end_seq"]
        store = CardStore(self.root, user)
        config = load_config(user, self.root)
        runner = AgentRunner(self.root, user, config=config, provider_factory=self.provider_factory)
        try:
            records = []
            through = 0
            while through < target:
                if self._stop_event.is_set():
                    raise MaintenanceError("历史简介任务已取消")
                page = store.raw_page(source, identity, after_seq=through, through_seq=target, limit=50)
                if not page:
                    raise MaintenanceError("RAW 范围缺失，未标记摘要完成")
                for card in page:
                    if card.seq != through + 1:
                        raise MaintenanceError("RAW 序号不连续，未标记摘要完成")
                    is_summary_text = (
                        card.raw_kind == "user_text" and card.raw_level == "initial"
                    ) or (
                        card.raw_kind == "agent_text" and card.raw_level == "final"
                    )
                    if is_summary_text:
                        text = str(card.raw_payload.get("text") or "")
                        if text.strip():
                            records = _append_latest_summary_record(records, {
                                "seq": card.seq,
                                "role": "user" if card.raw_kind == "user_text" else "assistant",
                                "text": text,
                            }, session_id=identity, target_seq=target)
                    through = card.seq
            if records:
                if self._stop_event.is_set():
                    raise MaintenanceError("历史简介任务已取消")
                summary_input = {
                    "trigger": "session_closed",
                    "session_id": identity,
                    "target_seq": target,
                    "records": records,
                }
                input_tokens = _summary_input_tokens(
                    records, session_id=identity, target_seq=target
                )
                result = runner.run(
                    "history_summary", summary_input, cancel_event=self._stop_event,
                    max_tokens=HISTORY_SUMMARY_MAX_OUTPUT_TOKENS,
                    structured_output_tool=True, source=source, session_id=identity,
                )
                summary = {
                    key: str(result.data.get(key) or "").strip()
                    for key in ("title", "summary")
                }
                if not all(summary.values()):
                    raise MaintenanceError("历史简介候选缺少 title 或 summary")
            else:
                input_tokens = 0
                summary = {
                    "title": "工具执行历史记录",
                    "summary": "本次历史记录未包含可用于摘要的用户正文或智能体最终正文；"
                    "工具与运行事件仍完整保存在 RAW 中。",
                }
            saved = finish_summary_claim(
                self.root, user, source, identity, claim_id=claim_id,
                summary_checkpoint=None, **summary,
            )
            if saved is None:
                raise MaintenanceError("历史简介认领已失效")
            return {"claimed": 1, "processed": [{"source": source, "session_id": identity,
                                                "status": "completed", "through_seq": target,
                                                "included_records": len(records),
                                                "input_tokens": input_tokens, **summary}], "failed": []}
        except Exception as error:
            from run.memory import contains_sensitive_credential
            detail = {"message": diagnostic_text(str(error)), "exception_type": type(error).__name__}
            raw_output = getattr(error, "raw_text", "")
            if raw_output:
                detail["raw_output_preview"] = (
                    "[输出包含疑似敏感内容，已隐藏]" if contains_sensitive_credential(raw_output)
                    else diagnostic_text(" ".join(raw_output.split()))[:500])
            deferred = self._stop_event.is_set() or isinstance(error, ProviderCongestionError)
            if deferred:
                defer_summary_claim(self.root, user, source, identity, claim_id=claim_id, error=detail)
            else:
                delays = self.summary_retry_delays or (60,)
                delay = delays[min(max(0, claim.get("summary_attempt_count", 1) - 1), len(delays) - 1)]
                finish_summary_claim(self.root, user, source, identity, claim_id=claim_id,
                                     error=detail, retry_delay=delay)
            self._report_error(f"history_summary:{user}", error)
            return {"claimed": 1, "processed": [], "failed": [
                {"session_id": identity, "error": detail, "deferred": deferred}]}

    def _recover_pending_memory(self, user):
        return recover_pending_memory(self, user)

    def _review_contexts(self, user):
        reviewed, over_budget = 0, []
        for record in list_records(self.root, user, limit=100):
            if self._stop_event.is_set():
                break
            try:
                value = context_status({"user": user, "source": record["source"],
                                        "session_id": record["session_id"]}, root=self.root,
                                       tool_registry_factory=self.tool_registry_factory)
                reviewed += 1
                if value["context"]["compression_needed"]:
                    over_budget.append(record["session_id"])
            except Exception as error:
                self._report_error(f"maintenance:{user}:context", error)
        return {"reviewed": reviewed, "over_budget": over_budget,
                "automatic_compaction": "main_request_barrier_only"}

    def _report_error(self, component, error):
        if self.on_error:
            self.on_error(component, error)

    def _run_loop(self):
        while not self._stop_event.is_set():
            try:
                self.scan_once()
            except Exception as error:
                self._report_error("maintenance", error)
            self._wake_event.wait(self.poll_interval)
            self._wake_event.clear()
