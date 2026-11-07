"""Active-run, compression, and memory controls for WebRunService."""

from __future__ import annotations

import threading
from typing import Callable, Iterator
from events import RunEvent
from run.infra import BoundedReadCache
from web.services.artifact_resolver import DownloadArtifactResolver
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from web.service import ActiveRun

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from web.service import WebRunService

from pathlib import Path

import queue
from typing import Any

from run.config import load_config
from run.conversation import GuidanceInput, session_lock
from run.extensions import history_attachment_descriptors
from run.cards import CardStore
from run.history import other_presence
from run.history import (
    close_session as close_index_session,
    find_record as find_index_record,
    queue_summary as queue_history_summary,
)
from run.memory import extract_card_memory
from web.errors import (
    ConflictError,
    InvalidRequestError,
    NotFoundError,
    WebServiceError,
)


class RunControlServiceMixin:
    if TYPE_CHECKING:
        _active_runs: dict[str, ActiveRun]
        _active_runs_lock: threading.RLock
        _index_session_payload = WebRunService._index_session_payload
        _new_agent_runner = WebRunService._new_agent_runner
        _other_durable_session_clients_locked = WebRunService._other_durable_session_clients_locked
        _prune_session_leases_locked = WebRunService._prune_session_leases_locked
        _queue_memory_extraction = WebRunService._queue_memory_extraction
        _release_durable_session_lease_locked = WebRunService._release_durable_session_lease_locked
        _release_session_lease_locked = WebRunService._release_session_lease_locked
        _session_leases: dict[tuple[str, str, str], dict[str, float]]
        context_compressor: Callable[..., dict[str, Any]]
        require_client_id = WebRunService.require_client_id
        require_run_id = WebRunService.require_run_id
        require_session_id = WebRunService.require_session_id
        require_source = WebRunService.require_source
        require_uploaded_files = WebRunService.require_uploaded_files
        require_user = WebRunService.require_user
        root: Path
        summary_waker: Callable[[], None] | None
        memory_waker: Callable[[], None] | None

    def submit_guidance(
        self,
        user: Any,
        run_id: Any,
        guidance: Any,
        *,
        source: Any = "web",
        session_id: Any = "",
        guidance_id: Any = "",
        uploaded_files: Any = None,
    ) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        normalized_session = self.require_session_id(session_id)
        normalized_run_id = self.require_run_id(run_id)
        text = guidance.strip() if isinstance(guidance, str) else ""
        normalized_id = str(guidance_id or "").strip()
        normalized_files = self.require_uploaded_files(name, uploaded_files)
        if not text and not normalized_files:
            raise InvalidRequestError("guidance 和 uploaded_files 不能同时为空")
        # A bare text call remains a string for compatibility with integrations
        # that consume GuidanceMailbox directly.  New web calls use the
        # structured envelope so attachment-only and duplicate-text guidance
        # can be acknowledged by id.
        value: str | GuidanceInput = (
            text
            if not normalized_files and not normalized_id
            else GuidanceInput(
                id=normalized_id,
                text=text,
                uploaded_files=normalized_files,
            )
        )
        with self._active_runs_lock:
            active = self._active_runs.get(normalized_run_id)
            if active is None:
                raise NotFoundError(f"运行不存在或已结束：{normalized_run_id}")
            if (
                active.user != name
                or active.source != normalized_source
                or active.session_id != normalized_session
            ):
                raise NotFoundError(f"运行不存在或已结束：{normalized_run_id}")
            try:
                accepted_current_run, queued = active.guidance.offer(value)
            except queue.Full as exc:
                raise ConflictError("运行中引导队列已满，请等待当前引导被处理") from exc
        return {
            "ok": accepted_current_run,
            "run_id": normalized_run_id,
            "user": name,
            "session_id": active.session_id,
            "status": (
                "accepted_current_run"
                if accepted_current_run
                else "not_applied"
            ),
            "queued": queued,
            "guidance_id": normalized_id,
            "uploaded_files": history_attachment_descriptors(normalized_files),
            **({} if accepted_current_run else {
                "error": {
                    "code": "guidance_closed",
                    "message": "当前运行已结束，无法投递引导；消息未排队，请下一轮重新发送。",
                },
            }),
        }

    def cancel_run(
        self,
        user: Any,
        run_id: Any,
        *,
        source: Any = "web",
        session_id: Any = "",
        cancel_subagents: bool = False,
    ) -> dict[str, Any]:
        """Request an idempotent emergency stop for one active run owned by the user."""

        name = self.require_user(user)
        normalized_source = self.require_source(source)
        normalized_session = self.require_session_id(session_id)
        normalized_run_id = self.require_run_id(run_id)
        with self._active_runs_lock:
            active = self._active_runs.get(normalized_run_id)
            if (
                active is None
                or active.user != name
                or active.source != normalized_source
                or active.session_id != normalized_session
            ):
                raise NotFoundError(f"运行不存在或已结束：{normalized_run_id}")
            if cancel_subagents:
                active.cancel_subagents_event.set()
            active.cancel_event.set()
            active.guidance.close()
        return {
            "run_id": normalized_run_id,
            "user": name,
            "session_id": active.session_id,
            "status": "stopping",
        }




    def close_session(
        self,
        user: Any,
        session_id: Any,
        *,
        source: Any = "web",
        client_id: Any = "",
    ) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        normalized_session = self.require_session_id(session_id)
        normalized_client = self.require_client_id(client_id)
        with self._active_runs_lock:
            self._prune_session_leases_locked()
            if normalized_client:
                remaining_clients = self._release_session_lease_locked(
                    name, normalized_source, normalized_session, normalized_client
                )
            else:
                remaining_clients = len(self._session_leases.get(
                    (name, normalized_source, normalized_session), {}
                ))
            if normalized_client:
                self._release_durable_session_lease_locked(
                    name, normalized_source, normalized_session, normalized_client
                )
            durable_clients = self._other_durable_session_clients_locked(
                name, normalized_source, normalized_session, normalized_client
            )
            # Web/App presence is cross-process. Another worker may already
            # have released a client still present in this process's cache.
            remaining_clients = (
                len(durable_clients) if normalized_source in {"web", "app"}
                else max(remaining_clients, len(durable_clients))
            )
            if remaining_clients:
                record = find_index_record(
                    self.root, name, normalized_source, normalized_session
                )
                if record is None:
                    raise NotFoundError(f"会话不存在：{normalized_session}")
                return {
                    "user": name,
                    "source": normalized_source,
                    "session_id": normalized_session,
                    "closed": False,
                    "deferred": True,
                    "active_clients": remaining_clients,
                    "retry_after_seconds": other_presence(
                        self.root, name, normalized_source, normalized_session, normalized_client,
                    )["retry_after_seconds"],
                    "memory": {
                        "status": "skipped",
                        "reason": "session_in_use_by_other_clients",
                        "target_seq": record.get("last_seq", 0),
                        "processed_seq": record.get("memory_processed_seq", 0),
                    },
                    "summary": {
                        "status": "skipped",
                        "reason": "session_in_use_by_other_clients",
                        "target_seq": record.get("last_seq", 0),
                    },
                    "session": self._index_session_payload(record),
                }
            if any(
                active.user == name
                and active.source == normalized_source
                and active.session_id == normalized_session
                for active in self._active_runs.values()
            ):
                raise ConflictError("会话正在运行，结束当前响应后再关闭")
            memory = self._queue_memory_extraction(
                self.root,
                name,
                normalized_source,
                normalized_session,
            )
            record = close_index_session(
                self.root,
                name,
                normalized_source,
                normalized_session,
            )
        if record is None:
            raise NotFoundError(f"会话不存在：{normalized_session}")
        summary = queue_history_summary(
            self.root,
            name,
            normalized_source,
            normalized_session,
        )
        if summary is not None and summary.get("status") == "queued" and self.summary_waker is not None:
            self.summary_waker()
        if memory.get("status") == "queued" and self.memory_waker is not None:
            self.memory_waker()
        record = find_index_record(
            self.root, name, normalized_source, normalized_session
        ) or record
        return {
            "user": name,
            "source": normalized_source,
            "session_id": normalized_session,
            "closed": True,
            "deferred": False,
            "active_clients": 0,
            "memory": memory,
            "summary": summary,
            "session": self._index_session_payload(record),
        }

    def compress_session(self, user, session_id, *, source="web"):
        name, source = self.require_user(user), self.require_source(source)
        identity = self.require_session_id(session_id)
        record = find_index_record(self.root, name, source, identity)
        if record is None:
            raise NotFoundError(f"会话不存在：{identity}")
        if record["run_state"] != "idle":
            raise ConflictError("整合或工具仍在运行，不能并发压缩")
        try:
            result = self.context_compressor({"user": name, "source": source,
                                              "session_id": identity}, root=self.root)
            store = CardStore(self.root, name)
            snapshot = store.snapshot(source, identity)
            compressed = result.get("compressed") is True
            verified = snapshot.view_epoch > 0 and any(card.view_kind == "summary" for card in snapshot.cards)
            if compressed and not verified:
                raise WebServiceError("摘要 VIEW 提交校验未通过")
            try:
                memory = self._queue_memory_extraction(self.root, name, source, identity,
                    target_seq=snapshot.last_seq, reason="manual_compression")
            except Exception as error:
                raise WebServiceError("CardStream 压缩已提交，但后台记忆任务登记失败") from error
            if memory.get("status") == "queued" and self.memory_waker is not None:
                self.memory_waker()
            return {"user": name, "source": source, "session_id": identity, "requested": True,
                    "compressed": compressed, "compression_verified": verified,
                    "absorbed_cards": int((result.get("context") or {}).get("absorbed_cards") or 0),
                    "summary_exists": verified,
                    "context": result.get("context", {}), "memory": memory,
                    "history_tokens": snapshot.history_tokens, "view_epoch": snapshot.view_epoch}
        except WebServiceError:
            raise
        except Exception as error:
            # The cause carries the only actionable detail (empty body, fenced
            # JSON, oversized candidate). Swallowing it makes every compaction
            # failure look identical to the caller.
            cause = error.__cause__ or error
            detail = str(cause).strip() or "no detail"
            raise WebServiceError(
                f"CardStream 压缩失败：{type(cause).__name__}: {detail[:400]}；"
                "原 RAW/VIEW 未被丢弃") from error

    def extract_session_memory(self, user, session_id, *, source="web"):
        name, source = self.require_user(user), self.require_source(source)
        identity = self.require_session_id(session_id)
        record = find_index_record(self.root, name, source, identity)
        if record is None:
            raise NotFoundError(f"会话不存在：{identity}")
        if record["run_state"] != "idle":
            raise ConflictError("整合仍在运行，待工具真正退出后再提取记忆")
        config = load_config(name, self.root)
        return extract_card_memory(root=self.root, user=name, source=source, session_id=identity,
            config=config, agent_runner=self._new_agent_runner(name, config), cancel_event=None)
