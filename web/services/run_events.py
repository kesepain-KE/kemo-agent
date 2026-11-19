"""Bounded replay for an existing Run; reconnecting never starts execution."""
from __future__ import annotations

from collections import deque
import copy
import json
import threading
import time

from events import RunEvent


def _event_bytes(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


class RunEventJournal:
    def __init__(self, *, max_bytes=2 * 1024 * 1024, snapshot_bytes=4 * 1024 * 1024):
        self.max_bytes = max_bytes
        self.snapshot_bytes = snapshot_bytes
        self.condition = threading.Condition()
        self.events = deque()
        self.bytes = 0
        self.cursor = 0
        self.history_seq = 0
        self.snapshot_events = []
        self.snapshot_size = 0
        self.snapshot_incomplete = False
        self.closed = False
        self.terminal = None
        self.finished_at = 0.0

    def publish(self, event: RunEvent, *, history_seq=0) -> RunEvent:
        with self.condition:
            self.cursor += 1
            event.metadata = {**event.metadata, "web_event_seq": self.cursor}
            value = event.to_dict()
            # Detailed request fingerprints stay in durable diagnostics, not
            # in the reconnect buffer or its lightweight terminal snapshot.
            if "provider_request_diagnostics" in value.get("metadata", {}):
                value = {**value, "metadata": {
                    key: data for key, data in value["metadata"].items()
                    if key != "provider_request_diagnostics"
                }}
            size = _event_bytes(value)
            self.events.append((self.cursor, value, size))
            self.bytes += size
            while self.events and (self.bytes > self.max_bytes or len(self.events) > 2048):
                self.bytes -= self.events.popleft()[2]

            if history_seq > self.history_seq:
                self.history_seq = history_seq
                self.snapshot_events = []
                self.snapshot_size = 0
                self.snapshot_incomplete = False
            # Tool cards are already durable before their events are published.
            # The snapshot only adds progress since the durable card watermark.
            durable_tool = history_seq and event.type in {"tool_call_start", "tool_call_result"}
            if not durable_tool and event.type not in {"done", "error"}:
                last = self.snapshot_events[-1] if self.snapshot_events else None
                if (last and event.type in {"text_delta", "reasoning_delta"}
                        and all(last.get(key) == value.get(key)
                                for key in ("type", "item_id", "response_id", "content_index"))):
                    self.snapshot_size += (
                        _event_bytes(event.content) - 2
                        + _event_bytes(value.get("metadata", {})) - _event_bytes(last.get("metadata", {}))
                    )
                    last["content"] = last.get("content", "") + event.content
                    last["metadata"] = value.get("metadata", {})
                else:
                    self.snapshot_events.append(copy.deepcopy(value))
                    self.snapshot_size += size
                while self.snapshot_events and self.snapshot_size > self.snapshot_bytes:
                    removed = self.snapshot_events.pop(0)
                    self.snapshot_size -= _event_bytes(removed)
                    self.snapshot_incomplete = True
            if event.type in {"done", "error"} and not (
                event.metadata.get("retryable") and event.metadata.get("committed") is False
            ):
                self.closed = True
                self.terminal = {**value, "metadata": {
                    key: data for key, data in value.get("metadata", {}).items()
                    if key in {"run_id", "integration_id", "source", "session_id", "user", "status", "cancelled",
                               "committed", "stop_reason", "web_event_seq", "failure"}
                }}
                self.finished_at = time.monotonic()
            self.condition.notify_all()
            return event

    def finish(self):
        with self.condition:
            if not self.closed:
                self.publish(RunEvent(type="error", error={
                    "code": "RUN_MISSING_TERMINAL", "message": "运行结束但未返回终态，请核对已保存记录",
                }, metadata={"status": "failed", "committed": False}))
            self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            return {
                "cursor": self.cursor, "history_seq": self.history_seq,
                "events": copy.deepcopy(self.snapshot_events),
                "snapshot_incomplete": self.snapshot_incomplete, "finished": self.closed,
                "terminal": copy.deepcopy(self.terminal),
            }

    def read(self, after, *, history_seq, wait=5):
        with self.condition:
            if after >= self.cursor and not self.closed:
                self.condition.wait_for(
                    lambda: self.cursor > after or self.closed, timeout=min(5, max(0, wait)),
                )
            first = self.events[0][0] if self.events else self.cursor + 1
            if after > self.cursor or after < first - 1 or history_seq == 0 and self.history_seq > 0:
                return {"reset": True, "snapshot": self.snapshot()}
            events = [copy.deepcopy(value) for seq, value, _ in self.events if seq > after][:256]
            cursor = events[-1]["metadata"]["web_event_seq"] if events else after
            return {"reset": False, "events": events,
                    "finished": self.closed and cursor >= self.cursor, "cursor": cursor}


def publish_run_event(service, active, event):
    event = copy.copy(event)
    event.metadata = {**dict(event.metadata or {}), "user": active.user, "source": active.source,
                      "session_id": active.session_id, "run_id": active.run_id}
    history_seq = 0
    if event.type == "context_window":
        history_seq = int((event.metadata.get("context_snapshot") or {}).get("last_seq") or 0)
    elif event.type in {"tool_call_start", "tool_call_result", "done", "error"}:
        from run.cards import CardStore, card_read_connection, session_key
        if CardStore(service.root, active.user).path.is_file():
            with card_read_connection(service.root, active.user) as db:
                row = db.execute("SELECT last_seq FROM card_sessions WHERE session_key=?",
                                 (session_key(active.source, active.session_id),)).fetchone() if db else None
                history_seq = row["last_seq"] if row else 0
    return active.journal.publish(event, history_seq=history_seq)


def retire_run(service, active):
    active.journal.finish()
    with service._active_runs_lock:
        active.guidance.close()
        service._active_runs.pop(active.run_id, None)
        service._finished_runs[active.run_id] = active
        now = time.monotonic()
        total = sum(run.journal.bytes + run.journal.snapshot_size
                    for run in service._finished_runs.values())
        for key, run in list(service._finished_runs.items()):
            weight = run.journal.bytes + run.journal.snapshot_size
            if (now - run.journal.finished_at > 120 or len(service._finished_runs) > 16
                    or total > 16 * 1024 * 1024):
                service._finished_runs.pop(key)
                total -= weight
