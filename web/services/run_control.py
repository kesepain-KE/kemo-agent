"""Active-run, long-task, compression, and memory controls for WebRunService."""

from __future__ import annotations

import queue
from typing import Any

from run.config import load_config
from run.conversation import GuidanceInput, session_lock
from run.extensions import history_attachment_descriptors
from run.history import find_window, load_window
from run.history import (
    close_session as close_index_session,
    find_record as find_index_record,
    queue_summary as queue_history_summary,
)
from run.long_task import (
    LONG_TASK_ACTIVE_STATUSES,
    get_long_task_state,
    reconcile_orphaned_long_task,
    request_long_task_cancel,
    set_long_task_enabled as update_long_task_enabled,
)
from run.memory import extract_memory_backlog
from web.errors import (
    ConflictError,
    InvalidRequestError,
    NotFoundError,
    WebServiceError,
)


class RunControlServiceMixin:
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
            "run_id": normalized_run_id,
            "user": name,
            "session_id": active.session_id,
            "status": (
                "accepted_current_run"
                if accepted_current_run
                else "queued_next_turn"
            ),
            "queued": queued,
            "guidance_id": normalized_id,
            "uploaded_files": history_attachment_descriptors(normalized_files),
        }

    def cancel_run(
        self,
        user: Any,
        run_id: Any,
        *,
        source: Any = "web",
        session_id: Any = "",
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
            active.cancel_event.set()
            active.guidance.close()
        return {
            "run_id": normalized_run_id,
            "user": name,
            "session_id": active.session_id,
            "status": "stopping",
        }

    def long_task_state(
        self,
        user: Any,
        session_id: Any,
        *,
        source: Any = "web",
    ) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        normalized_session = self.require_session_id(session_id)
        if find_index_record(
            self.root, name, normalized_source, normalized_session
        ) is None:
            raise NotFoundError(f"会话不存在：{normalized_session}")
        persisted_state = get_long_task_state(
            self.root, name, normalized_source, normalized_session
        )
        current_run_id = str(persisted_state.get("current_run_id") or "")
        with self._active_runs_lock:
            if current_run_id:
                active = self._active_runs.get(current_run_id)
                has_live_run = bool(
                    active is not None
                    and active.user == name
                    and active.source == normalized_source
                    and active.session_id == normalized_session
                )
            else:
                # Activation briefly precedes assigning the first persisted
                # Run id.  During only that narrow window, session ownership
                # is the strongest available liveness signal.
                has_live_run = any(
                    active.user == name
                    and active.source == normalized_source
                    and active.session_id == normalized_session
                    for active in self._active_runs.values()
                )
        return {
            "user": name,
            "source": normalized_source,
            "session_id": normalized_session,
            "long_task": (
                persisted_state
                if has_live_run
                else reconcile_orphaned_long_task(
                    self.root,
                    name,
                    normalized_source,
                    normalized_session,
                    has_live_run=False,
                )
            ),
        }

    def set_long_task_enabled(
        self,
        user: Any,
        session_id: Any,
        enabled: Any,
        *,
        source: Any = "web",
    ) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        normalized_session = self.require_session_id(session_id)
        if not isinstance(enabled, bool):
            raise InvalidRequestError("enabled 必须是布尔值")
        try:
            state = update_long_task_enabled(
                self.root,
                name,
                normalized_source,
                normalized_session,
                enabled,
            )
        except KeyError as exc:
            raise NotFoundError(str(exc)) from None
        return {
            "user": name,
            "source": normalized_source,
            "session_id": normalized_session,
            "long_task": state,
        }

    def cancel_long_task(
        self,
        user: Any,
        session_id: Any,
        *,
        source: Any = "web",
    ) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        normalized_session = self.require_session_id(session_id)
        try:
            state = request_long_task_cancel(
                self.root, name, normalized_source, normalized_session
            )
        except KeyError as exc:
            raise NotFoundError(str(exc)) from None
        matched_run = False
        with self._active_runs_lock:
            for active in self._active_runs.values():
                if (
                    active.user == name
                    and active.source == normalized_source
                    and active.session_id == normalized_session
                ):
                    matched_run = True
                    active.cancel_event.set()
                    active.guidance.close()
        if not matched_run and state.get("status") in LONG_TASK_ACTIVE_STATUSES:
            state = reconcile_orphaned_long_task(
                self.root,
                name,
                normalized_source,
                normalized_session,
                has_live_run=False,
                grace_seconds=0,
                stop_reason="orphaned_user_cancel",
            )
        return {
            "user": name,
            "source": normalized_source,
            "session_id": normalized_session,
            "long_task": state,
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
            remaining_clients = self._release_session_lease_locked(
                name, normalized_source, normalized_session, normalized_client
            ) if normalized_client else 0
            if normalized_client and remaining_clients:
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
                    "memory": {
                        "status": "skipped",
                        "reason": "session_in_use_by_other_clients",
                        "rounds": 0,
                        "processed_round": 0,
                    },
                    "summary": {
                        "status": "skipped",
                        "reason": "session_in_use_by_other_clients",
                        "rounds": max(0, int(record.get("rounds") or 0)),
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
        if summary.get("status") == "queued" and self.summary_waker is not None:
            self.summary_waker()
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

    def compress_session(
        self,
        user: Any,
        session_id: Any,
        *,
        source: Any = "web",
    ) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        normalized_session = self.require_session_id(session_id)
        directory = find_window(
            self.root,
            name,
            normalized_source,
            normalized_session,
        )
        if directory is None:
            raise NotFoundError(f"会话不存在：{normalized_session}")
        with self._active_runs_lock:
            if any(
                active.user == name
                and active.source == normalized_source
                and active.session_id == normalized_session
                for active in self._active_runs.values()
            ):
                raise ConflictError("会话正在运行，结束当前响应后再压缩")
        try:
            result = self.context_compressor(
                {
                    "user": name,
                    "source": normalized_source,
                    "session_id": normalized_session,
                    "memory_extraction_policy": "queue",
                },
                root=self.root,
            )
        except WebServiceError:
            raise
        except Exception as exc:
            raise WebServiceError("手动上下文压缩失败") from exc
        context = result.get("context") if isinstance(result.get("context"), dict) else {}
        rounds_removed = max(0, int(context.get("rounds_removed") or 0))
        summary_cache = str(result.get("summary_cache") or "")
        compressed = result.get("compressed") is True
        compression_verified = result.get("compression_verified") is True
        summary = context.get("summary")
        if isinstance(summary, dict) and summary.get("failed") is True:
            detail = str(summary.get("error") or "").strip()
            message = "手动上下文压缩失败"
            if detail:
                message = f"{message}：{detail}"
            raise WebServiceError(message)
        if rounds_removed and not (compressed and compression_verified):
            raise WebServiceError("手动上下文压缩失败：运行窗口落盘校验未通过")
        raw_memory = result.get("memory")
        if isinstance(raw_memory, dict):
            memory = dict(raw_memory)
        else:
            try:
                latest_window = load_window(directory)
                memory = self._queue_memory_extraction(
                    self.root,
                    name,
                    normalized_source,
                    normalized_session,
                    target_round=int(
                        latest_window.get("data", {}).get("rounds") or 0
                    ),
                    reason="manual_compression",
                )
            except Exception as exc:
                raise WebServiceError(
                    "上下文压缩成功，但后台记忆任务登记失败"
                ) from exc
        memory.setdefault("user", name)
        memory.setdefault("source", normalized_source)
        memory.setdefault("session_id", normalized_session)
        memory.setdefault(
            "round",
            int(memory.get("target_round") or memory.get("rounds") or 0),
        )
        memory.setdefault("candidates", 0)
        memory.setdefault("extraction", None)
        memory["retry_pending"] = memory.get("status") == "failed"
        return {
            "user": name,
            "source": normalized_source,
            "session_id": normalized_session,
            "requested": True,
            "compressed": compressed,
            "compression_verified": compression_verified,
            "rounds_removed": rounds_removed,
            "summary_cache_exists": bool(summary_cache),
            "context": dict(context),
            "memory": memory,
        }

    def extract_session_memory(
        self,
        user: Any,
        session_id: Any,
        *,
        source: Any = "web",
    ) -> dict[str, Any]:
        """Extract every unprocessed archived round through the durable cursor."""

        name = self.require_user(user)
        normalized_source = self.require_source(source)
        normalized_session = self.require_session_id(session_id)
        directory = find_window(
            self.root,
            name,
            normalized_source,
            normalized_session,
        )
        if directory is None:
            raise NotFoundError(f"会话不存在：{normalized_session}")
        with self._active_runs_lock:
            if any(
                active.user == name
                and active.source == normalized_source
                and active.session_id == normalized_session
                for active in self._active_runs.values()
            ):
                raise ConflictError("会话正在运行，结束当前响应后再提取记忆")

        with session_lock(self.root, name, normalized_source, normalized_session):
            window = load_window(directory)
            if max(0, int((window.get("data") or {}).get("rounds") or 0)) < 1:
                return extract_memory_backlog(
                    root=self.root,
                    user=name,
                    source=normalized_source,
                    session_id=normalized_session,
                    directory=directory,
                    window=window,
                    config={},
                    agent_runner=None,
                    cancel_event=None,
                )
            config = load_config(name, self.root)
            runner = self._new_agent_runner(name, config)
            return extract_memory_backlog(
                root=self.root,
                user=name,
                source=normalized_source,
                session_id=normalized_session,
                directory=directory,
                window=window,
                config=config,
                agent_runner=runner,
                cancel_event=None,
            )
