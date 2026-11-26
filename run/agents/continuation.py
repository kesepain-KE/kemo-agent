"""CardStream safe points, legacy checkpoint import and cooperative timeout state.

No pickle, model-supplied file names, public metadata or Provider system prompts.
Only builtin LLM business agents are resumable, not arbitrary Python executors.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter
from provider.protocol.diagnostics import redact_diagnostic_text
from provider.protocol.models import (
    Item, MessageItem, ReasoningItem, ToolCallItem, ToolResultItem,
)
from run.config import user_dir, _contains_reparse_point

if TYPE_CHECKING:
    from run.agents.card_stream import AgentCardStream

MAX_CHECKPOINT_BYTES = 32 * 1024 * 1024
MAX_RESUME_ROUNDS = 32
TASK_ID = re.compile(r"agent-task-[a-f0-9]{32}")
_SECRETS = re.compile(r"(?:^|_)(?:api_key|api_token|password|passwd|secret|credential|credentials|authorization|cookie|cookies|private_key|access_token|refresh_token|session_token|auth_token|token)$")


def response_items_for_next_request(output: list[Any]) -> list[Any]:
    """Normalize Provider-scoped IDs before storing output across iterations."""
    call_id_map: dict[str, str] = {}
    normalized_call_ids: dict[int, str] = {}
    for item in output:
        if not isinstance(item, ToolCallItem):
            continue
        call_id = f"callid_{uuid.uuid4().hex}"
        normalized_call_ids[id(item)] = call_id
        call_id_map.setdefault(item.call_id, call_id)

    normalized: list[Any] = []
    for item in output:
        prefix = (
            "rs"
            if isinstance(item, ReasoningItem)
            else (
                "call"
                if isinstance(item, ToolCallItem)
                else (
                    "result"
                    if isinstance(item, ToolResultItem)
                    else "msg" if isinstance(item, MessageItem) else "item"
                )
            )
        )
        updates = {"id": f"{prefix}_{uuid.uuid4().hex}"}
        if isinstance(item, ToolCallItem):
            updates["call_id"] = normalized_call_ids[id(item)]
        elif isinstance(item, ToolResultItem) and item.call_id in call_id_map:
            updates["call_id"] = call_id_map[item.call_id]
        normalized.append(item.model_copy(update=updates))
    return normalized


def definition_digest(definition) -> str:
    digest = hashlib.sha256()
    for name in ("agent.json", "agent-config.json", "schema.json", "AGENT.md", "trigger.md"):
        path = definition.directory / name
        digest.update(name.encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def private_data(value):
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).lower().replace("-", "_")
            result[key] = "[redacted]" if _SECRETS.search(normalized) else private_data(child)
        return result
    if isinstance(value, list):
        return [private_data(v) for v in value]
    if isinstance(value, str):
        if value.lstrip().startswith(("{", "[")):
            try:
                parsed = json.loads(value)
            except (ValueError, RecursionError):
                pass
            else:
                if isinstance(parsed, (dict, list)):
                    safe = private_data(parsed)
                    return json.dumps(safe, ensure_ascii=False) if safe != parsed else value
        return redact_diagnostic_text(value)
    return value


class CheckpointStore:
    def __init__(self, root, user, task_id):
        if not TASK_ID.fullmatch(task_id):
            raise ValueError("续跑任务 ID 无效")
        self.root = Path(root)
        self.base = user_dir(user, self.root)
        self.directory = self.base / "runtime" / "agent_checkpoints"
        self.path = self.directory / f"{task_id}.json"
        self.task_id = task_id

    def checked(self):
        if (_contains_reparse_point(self.path, self.root)
                or (self.path.exists() and self.path.stat().st_nlink != 1)):
            raise ValueError("续跑快照不能是链接")
        self.directory.mkdir(parents=True, exist_ok=True)

    def write(self, payload):
        self.checked()
        raw = json.dumps(private_data(payload), ensure_ascii=False).encode("utf-8")
        if len(raw) > MAX_CHECKPOINT_BYTES:
            raise ValueError("续跑快照超过安全上限")
        temporary = self.directory / f"{self.task_id}.{uuid.uuid4().hex}.tmp"
        # Exclusive creation and replace: never expose a partially written JSON.
        with temporary.open("xb") as file:
            file.write(raw)
        self.checked()
        temporary.replace(self.path)

    def load(self, *, source, session_id, definition=None, run_count=None):
        self.checked()
        if self.path.stat().st_size > MAX_CHECKPOINT_BYTES:
            raise ValueError("续跑快照超过安全上限")
        data = json.loads(self.path.read_text("utf-8"))
        if (data.get("version") != 1 or data.get("task_id") != self.task_id
                or data.get("source") != source or data.get("session_id") != session_id
                or not data.get("available")
                or (run_count is not None and data.get("run_count") != run_count)):
            raise ValueError("续跑快照不可用或身份不匹配")
        if definition is not None and data.get("definition_digest") != definition_digest(definition):
            raise ValueError("子代理定义已改变，拒绝恢复旧快照")
        return data


def load_resume_state(root, user, task_id, *, source, session_id, definition, run_count, version=None):
    from run.agents.card_stream import agent_store, read_checkpoint

    store = agent_store(root, user, task_id)
    if version == 2 or store.path.exists():
        data = read_checkpoint(store, task_id, run_count)
        if (data.get("source") != source or data.get("session_id") != session_id
                or data.get("definition_digest") != definition_digest(definition)
                or not isinstance(data.get("input_data"), dict)):
            raise ValueError("子代理续跑卡片安全点身份或定义不匹配")
        return data
    return CheckpointStore(root, user, task_id).load(
        source=source, session_id=session_id, definition=definition, run_count=run_count,
    )


def decode_legacy_items(data):
    return [item for item in TypeAdapter(list[Item]).validate_python(data["items"])
            if not isinstance(item, ReasoningItem)
            and not item.id.startswith(("msg_wrap_", "msg_budget_", "msg_media_"))]


class ContinuationState:
    def __init__(self, runner, definition, task_id, source, session_id, input_data,
                 *, resume_state=None, run_count=1):
        self.lock = threading.RLock()
        self.wrap_up = threading.Event()
        self.timed_out = threading.Event()
        self.stop_reason = "timeout"
        self.wrap_injected = False
        self.run_count = run_count
        self.initial = resume_state
        self.enabled = (definition.executor == "builtin:llm"
                        and definition.execution != "background_serial"
                        and bool(source and session_id) and bool(TASK_ID.fullmatch(task_id)))
        self.stream: AgentCardStream | None = None
        self.payload = {
            "version": 2, "task_id": task_id,
            "source": source, "session_id": session_id, "agent": definition.name,
            "definition_digest": definition_digest(definition),
            "input_data": copy.deepcopy(input_data), "run_count": run_count,
            "usage": {}, "response_ids": [], "conclusion": None,
        }
        if resume_state is not None:
            self.payload["conclusion"] = copy.deepcopy(resume_state.get("conclusion"))
        self.available = False
        self.error = ""

    def capture(self, usage, response_ids, *, conclusion=None):
        if not self.enabled:
            return
        with self.lock:
            self.payload.update(
                usage=copy.deepcopy(usage), response_ids=list(response_ids)[-160:],
            )
            if conclusion is not None:
                self.payload["conclusion"] = copy.deepcopy(conclusion)
            self._persist_locked()

    def _persist_locked(self):
        if self.stream is None:
            return
        try:
            self.available = self.stream.checkpoint(self.payload)
            self.error = ""
        except (OSError, sqlite3.Error, ValueError, RecursionError) as exc:
            self.available, self.error = False, type(exc).__name__

    def finish(self, stream: AgentCardStream, status: str):
        with self.lock:
            self.available = False
            self.available = stream.finish(status, checkpoint=self.payload if self.enabled else None)
            self.error = ""

    def partial(self, agent, model=""):
        from run.agents.contracts import AgentRunResult
        from run.agents.conclusions import conclusion_data
        with self.lock:
            if self.enabled:
                self._persist_locked()
            data = conclusion_data(self.payload.get("conclusion"))
            if not isinstance(data, dict):
                data = {"status": "partial", "summary": "尚未形成可交付结论。",
                        "remaining": ["本轮停止，任务尚未完成；继续前核对已执行操作和后台作业。"]}
            elif data.get("status") == "completed":
                data["status"] = "partial"
            return AgentRunResult(
                agent=agent, data=data, raw_text="", model=model or self.payload.get("model", ""),
                usage=copy.deepcopy(self.payload["usage"]),
                metadata={"partial": True, "stop_reason": self.stop_reason,
                          "resumable": self.available, "run_count": self.run_count,
                          "budget": copy.deepcopy(self.payload.get("budget", {})),
                          "checkpoint_error": self.error},
            )
