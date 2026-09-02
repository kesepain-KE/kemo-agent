"""会话目录、会话 CRUD 与历史读取领域服务。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from run.history import (
    HistoryError,
    delete_all_sessions as delete_all_history_sessions,
    delete_session as delete_history_session,
    find_window,
    list_sessions,
    list_sessions_page,
    load_window,
    rename_session as rename_history_session,
    undo_last_round as undo_history_last_round,
)
from run.history import (
    retry_summary as retry_history_summary,
    find_record as find_index_record,
    get_or_reserve_active as get_or_reserve_index_session,
    new_conversation_id,
    reserve_session,
)
from run.history import session_page_cursor
from web.constants import _TOOL_TEXT_LIMIT
from web.errors import (
    ConflictError,
    InvalidRequestError,
    NotFoundError,
    WebServiceError,
)
from web.services._paths import _reject_link_path, _safe_relative_target
from web.services.history import history as _history_impl


def _tool_text_preview(value: Any) -> tuple[str, bool]:
    if isinstance(value, str):
        rendered = value
    else:
        try:
            rendered = json.dumps(value, ensure_ascii=False, indent=2, default=str)
        except (TypeError, ValueError):
            rendered = str(value)
    truncated = len(rendered) > _TOOL_TEXT_LIMIT
    return rendered[:_TOOL_TEXT_LIMIT], truncated


class SessionServiceMixin:
    def sessions(
        self,
        user: Any,
        *,
        source: Any = "web",
        query: Any = "",
        limit: Any = 50,
        before: Any = "",
    ) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_history_source(source, allow_all=True)
        if not isinstance(query, str):
            raise InvalidRequestError("query 必须是字符串")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise InvalidRequestError("limit 必须是 1 到 100 的整数")
        if not isinstance(before, str):
            raise InvalidRequestError("before 必须是字符串")
        sessions, has_more = list_sessions_page(
            self.root,
            name,
            normalized_source,
            query=query.strip(),
            limit=limit,
            before_updated_at=before.strip(),
        )
        next_cursor = session_page_cursor(sessions[-1]) if has_more and sessions else ""
        return {
            "user": name,
            "source": normalized_source or "all",
            "query": query.strip(),
            "sessions": sessions,
            "has_more": has_more,
            "next_cursor": next_cursor,
        }

    @staticmethod
    def _interactive_active_key(
        user: str,
        client_id: str = "",
        *,
        source: str = "web",
    ) -> str:
        prefix = "interactive" if source == "web" else source
        return f"{prefix}:{user}:{client_id}" if client_id else f"{prefix}:{user}"

    @staticmethod
    def _index_session_payload(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "source": str(record.get("source") or ""),
            "bound_platform": str(record.get("bound_platform") or ""),
            "session_id": str(record.get("session_id") or ""),
            "conversation_id": str(record.get("conversation_id") or ""),
            "window": str(record.get("archive_window") or ""),
            "title": str(record.get("title") or ""),
            "summary": str(record.get("summary") or ""),
            "summary_status": str(record.get("summary_status") or "none"),
            "summary_target_round": int(record.get("summary_target_round") or 0),
            "summary_completed_round": int(record.get("summary_completed_round") or 0),
            "summary_retry_at": str(record.get("summary_retry_at") or ""),
            "summary_retry_count": max(0, int(record.get("summary_retry_count") or 0)),
            "summary_attempt_count": max(
                0, int(record.get("summary_attempt_count") or 0)
            ),
            "summary_consecutive_failures": max(
                0, int(record.get("summary_consecutive_failures") or 0)
            ),
            "summary_max_attempts": max(
                1, int(record.get("summary_max_attempts") or 5)
            ),
            "summary_last_attempt_at": str(record.get("summary_last_attempt_at") or ""),
            "summary_recovered_at": str(record.get("summary_recovered_at") or ""),
            "summary_last_error": (
                dict(record["summary_last_error"])
                if isinstance(record.get("summary_last_error"), dict)
                else None
            ),
            "summary_checkpoint_next_chunk": max(
                0, int(record.get("summary_checkpoint_next_chunk") or 0)
            ),
            "summary_checkpoint_total_chunks": max(
                0, int(record.get("summary_checkpoint_total_chunks") or 0)
            ),
            "state": str(record.get("lifecycle") or "open"),
            "run_state": str(record.get("run_state") or "idle"),
            "chain": str(record.get("chain") or "interactive"),
            "memory_status": str(record.get("memory_status") or "unknown"),
            "memory_processed_round": max(
                0, int(record.get("memory_processed_round") or 0)
            ),
            "memory_target_round": max(
                0, int(record.get("memory_target_round") or 0)
            ),
            "memory_queue_reason": str(record.get("memory_queue_reason") or ""),
            "memory_queued_at": str(record.get("memory_queued_at") or ""),
            "memory_last_error": (
                dict(record["memory_last_error"])
                if isinstance(record.get("memory_last_error"), dict)
                else None
            ),
            "rounds": max(0, int(record.get("rounds") or 0)),
            "updated_at": str(record.get("updated_at") or ""),
        }

    def active_session(
        self,
        user: Any,
        client_id: Any = "",
        *,
        source: Any = "web",
    ) -> dict[str, Any]:
        """Return or reserve the user's durable interactive session."""

        name = self.require_user(user)
        normalized_client = self.require_client_id(client_id)
        normalized_source = self.require_source(source)
        active_key = self._interactive_active_key(
            name,
            normalized_client,
            source=normalized_source,
        )
        app_session_id = f"app-{uuid4()}" if normalized_source == "app" else None
        record, created = get_or_reserve_index_session(
            self.root,
            name,
            normalized_source,
            active_key,
            preferred_session_id=app_session_id,
            new_session_id=app_session_id,
            reuse_latest=True,
        )
        if normalized_source == "web":
            with self._active_runs_lock:
                active_clients = self._touch_session_lease_locked(
                    name,
                    normalized_source,
                    str(record.get("session_id") or ""),
                    normalized_client,
                )
        else:
            # APP callers only need the durable active binding. Mobile screens
            # do not own browser-style leases, otherwise a restore request
            # would prevent the same device from closing its conversation.
            active_clients = 0
        return {
            "user": name,
            "active_key": active_key,
            "created": created,
            "client_id": normalized_client,
            "active_clients": active_clients,
            "session": self._index_session_payload(record),
        }

    def create_session(self, user: Any, client_id: Any = "") -> dict[str, Any]:
        name = self.require_user(user)
        normalized_client = self.require_client_id(client_id)
        with self._active_runs_lock:
            session_id = new_conversation_id()
            active_key = self._interactive_active_key(name, normalized_client)
            record = reserve_session(
                self.root,
                name,
                "web",
                session_id,
                active_key=active_key,
            )
            active_clients = self._touch_session_lease_locked(
                name, "web", session_id, normalized_client
            )
        return {
            "user": name,
            "active_key": active_key,
            "created": True,
            "client_id": normalized_client,
            "active_clients": active_clients,
            "session": self._index_session_payload(record),
        }

    def retry_session_summary(
        self,
        user: Any,
        session_id: Any,
        *,
        source: Any = "web",
    ) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        normalized_session = self.require_session_id(session_id)
        existing = find_index_record(
            self.root, name, normalized_source, normalized_session
        )
        if existing is None:
            raise NotFoundError(f"会话不存在：{normalized_session}")
        record = retry_history_summary(
            self.root,
            name,
            normalized_source,
            normalized_session,
        )
        if record is None:
            raise ConflictError("当前会话没有可重新生成的历史摘要任务")
        if self.summary_waker is not None:
            self.summary_waker()
        return {
            "user": name,
            "source": normalized_source,
            "session_id": normalized_session,
            "queued": True,
            "session": self._index_session_payload(record),
        }

    def rename_session(
        self,
        user: Any,
        session_id: Any,
        title: Any,
        *,
        source: Any = "web",
    ) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        normalized_session = self.require_session_id(session_id)
        normalized_title = self.require_session_title(title)
        changed = rename_history_session(
            self.root,
            name,
            normalized_source,
            normalized_session,
            normalized_title,
        )
        if changed == 0:
            raise NotFoundError(f"会话不存在：{normalized_session}")
        session = next(
            (
                item
                for item in list_sessions(self.root, name, normalized_source)
                if item.get("session_id") == normalized_session
            ),
            None,
        )
        return {
            "user": name,
            "source": normalized_source,
            "session": session,
        }

    def delete_session(
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
            lease_clients = self._session_leases.get(
                (name, normalized_source, normalized_session), {}
            )
            other_clients = [
                value for value in lease_clients if value != normalized_client
            ]
            if other_clients:
                raise ConflictError(
                    f"该对话正在其他 {len(other_clients)} 个页面中使用，暂时不能删除"
                )
            if any(
                active.user == name
                and active.source == normalized_source
                and active.session_id == normalized_session
                for active in self._active_runs.values()
            ):
                raise ConflictError("会话正在运行，结束当前响应后再删除")
            deleted = delete_history_session(
                self.root,
                name,
                normalized_source,
                normalized_session,
            )
            self._session_leases.pop(
                (name, normalized_source, normalized_session), None
            )
        if deleted == 0:
            raise NotFoundError(f"会话不存在：{normalized_session}")
        return {
            "user": name,
            "source": normalized_source,
            "session_id": normalized_session,
            "deleted": True,
        }

    def undo_last_round(
        self,
        user: Any,
        session_id: Any,
        expected_round: Any,
        prompt: Any,
        *,
        source: Any = "web",
    ) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        normalized_session = self.require_session_id(session_id)
        if isinstance(expected_round, bool) or not isinstance(expected_round, int):
            raise InvalidRequestError("expected_round 必须是正整数")
        if expected_round < 1:
            raise InvalidRequestError("expected_round 必须是正整数")
        if not isinstance(prompt, str) or not prompt.strip():
            raise InvalidRequestError("prompt 不能为空")
        with self._active_runs_lock:
            if any(
                active.user == name
                and active.source == normalized_source
                and active.session_id == normalized_session
                for active in self._active_runs.values()
            ):
                raise ConflictError("会话仍在运行，确认上一轮结束后再重新发送")
            try:
                result = undo_history_last_round(
                    self.root,
                    name,
                    normalized_source,
                    normalized_session,
                    expected_round=expected_round,
                    expected_prompt=prompt,
                )
            except HistoryError as exc:
                raise ConflictError(str(exc)) from exc
        return {
            "user": name,
            "source": normalized_source,
            "session_id": normalized_session,
            **result,
        }

    def delete_all_sessions(
        self,
        user: Any,
        *,
        source: Any = "web",
    ) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        with self._active_runs_lock:
            if any(
                active.user == name and active.source == normalized_source
                for active in self._active_runs.values()
            ):
                raise ConflictError("存在正在运行的会话，结束当前响应后再全部删除")
            deleted_sessions, deleted_windows = delete_all_history_sessions(
                self.root,
                name,
                normalized_source,
            )
        return {
            "user": name,
            "source": normalized_source,
            "deleted": True,
            "deleted_sessions": deleted_sessions,
            "deleted_windows": deleted_windows,
        }

    def history(
        self,
        user: Any,
        session_id: Any,
        *,
        source: Any = "web",
        limit: int | None = None,
        before: int | None = None,
    ) -> dict[str, Any]:
        """Compatibility entry point delegated to history aggregation."""

        return _history_impl(
            self,
            user,
            session_id,
            source=source,
            limit=limit,
            before=before,
        )
