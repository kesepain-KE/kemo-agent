"""Private, bounded JSON checkpoints and cooperative timeout state.

No pickle, model-supplied file names, public metadata or Provider system prompts.
Only builtin LLM business agents are resumable, not arbitrary Python executors.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import uuid
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter
from provider.protocol.diagnostics import redact_diagnostic_text
from provider.protocol.enums import MessageRole
from provider.protocol.models import Item, JsonContent, MessageItem, ToolCallItem, ToolResultItem
from run.config import user_dir, _contains_reparse_point

MAX_CHECKPOINT_BYTES = 32 * 1024 * 1024
MAX_RESUME_ROUNDS = 32
_TASK = re.compile(r"agent-task-[a-f0-9]{32}")
_SECRETS = re.compile(r"(?:^|_)(?:api_key|api_token|password|passwd|secret|credential|credentials|authorization|cookie|cookies|private_key|access_token|refresh_token|session_token|auth_token|token)$")


def definition_digest(definition) -> str:
    digest = hashlib.sha256()
    for name in ("agent.json", "agent-config.json", "schema.json", "AGENT.md", "trigger.md"):
        path = definition.directory / name
        digest.update(name.encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _private_data(value):
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).lower().replace("-", "_")
            result[key] = "[redacted]" if _SECRETS.search(normalized) else _private_data(child)
        return result
    if isinstance(value, list):
        return [_private_data(v) for v in value]
    if isinstance(value, str):
        if value.lstrip().startswith(("{", "[")):
            try:
                parsed = json.loads(value)
            except (ValueError, RecursionError):
                pass
            else:
                if isinstance(parsed, (dict, list)):
                    return json.dumps(_private_data(parsed), ensure_ascii=False)
        return redact_diagnostic_text(value)
    return value


class CheckpointStore:
    def __init__(self, root, user, task_id):
        if not _TASK.fullmatch(task_id):
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
        raw = json.dumps(_private_data(payload), ensure_ascii=False).encode("utf-8")
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


def restore_items(data):
    items = TypeAdapter(list[Item]).validate_python(data["items"])
    items = [item for item in items if not item.id.startswith("msg_wrap_")]
    return pair_pending(items)


def pair_pending(items):
    # A response can contain multiple calls. Never replay a call whose result
    # is present; mark unstarted calls explicitly rather than emitting orphans.
    paired = {item.call_id for item in items if isinstance(item, ToolResultItem)}
    for item in list(items):
        if isinstance(item, ToolCallItem) and item.call_id not in paired:
            items.append(ToolResultItem(
                id=f"result_{uuid.uuid4().hex}", call_id=item.call_id, name=item.name,
                is_error=True, content=[JsonContent(data={
                    "ok": False, "not_executed": True,
                    "error": {"message": "上一轮在执行此调用前停止。未执行此操作；请根据已有结果重新决定。"},
                })],
            ))
    return items


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
                        and bool(source and session_id) and bool(_TASK.fullmatch(task_id)))
        self.store = CheckpointStore(runner.root, runner.user, task_id) if self.enabled else None
        self.payload = {
            "version": 1, "available": False, "task_id": task_id,
            "source": source, "session_id": session_id, "agent": definition.name,
            "definition_digest": definition_digest(definition),
            "input_data": copy.deepcopy(input_data), "run_count": run_count,
            "items": [], "usage": {}, "response_ids": [], "conclusion": None,
        }
        if resume_state is not None:
            self.payload["conclusion"] = copy.deepcopy(resume_state.get("conclusion"))
        self.available = False
        self.error = ""
        if self.enabled:
            seed = restore_items(resume_state) if resume_state is not None else [
                MessageItem.text(
                    MessageRole.USER, json.dumps(input_data, ensure_ascii=False),
                    item_id=f"msg_{uuid.uuid4().hex}",
                )
            ]
            self.capture(seed, (resume_state or {}).get("usage", {}),
                         (resume_state or {}).get("response_ids", []))

    def capture(self, items, usage, response_ids, *, conclusion=None):
        if not self.enabled:
            return
        with self.lock:
            self.payload.update(
                items=[i.model_dump(mode="json") for i in items
                       if getattr(i, "type", "") != "reasoning"],
                usage=copy.deepcopy(usage), response_ids=list(response_ids)[-160:],
            )
            if conclusion is not None:
                self.payload["conclusion"] = copy.deepcopy(conclusion)
            self.payload["available"] = True
            self._persist_locked()

    def _persist_locked(self):
        try:
            assert self.store is not None
            self.store.write(self.payload)
            self.available, self.error = True, ""
        except (OSError, ValueError, RecursionError) as exc:
            self.available, self.error = False, type(exc).__name__
            # Revoke any older safe point, which might omit newer side effects.
            try:
                assert self.store is not None
                self.store.write({**self.payload, "items": [], "available": False})
            except (OSError, ValueError, RecursionError):
                pass

    def partial(self, agent, model=""):
        from run.agents.contracts import AgentRunResult
        from run.agents.conclusions import conclusion_data
        with self.lock:
            if self.enabled and self.available:
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
