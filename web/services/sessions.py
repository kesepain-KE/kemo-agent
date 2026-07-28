"""会话目录、会话 CRUD 与历史读取领域服务。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from run.history import (
    HistoryError,
    delete_all_sessions as delete_all_history_sessions,
    delete_session as delete_history_session,
    find_window,
    list_sessions,
    load_window,
    rename_session as rename_history_session,
    session_messages,
    undo_last_round as undo_history_last_round,
)
from run.history_index import (
    retry_summary as retry_history_summary,
    find_record as find_index_record,
    get_or_reserve_active as get_or_reserve_index_session,
    new_conversation_id,
    reserve_session,
)
from web.constants import _TOOL_TEXT_LIMIT
from web.errors import (
    ConflictError,
    InvalidRequestError,
    NotFoundError,
    WebServiceError,
)
from web.services._paths import _reject_link_path, _safe_relative_target


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
    ) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        if not isinstance(query, str):
            raise InvalidRequestError("query 必须是字符串")
        normalized_query = query.strip().casefold()
        sessions = list_sessions(self.root, name, normalized_source)
        if normalized_query:
            matched = []
            for item in sessions:
                searchable = " ".join(
                    str(item.get(key) or "") for key in ("session_id", "title", "window")
                ).casefold()
                if normalized_query in searchable:
                    matched.append(item)
                    continue
                directory = find_window(
                    self.root,
                    name,
                    normalized_source,
                    str(item.get("session_id") or ""),
                )
                if directory is None:
                    continue
                try:
                    messages = session_messages(load_window(directory))
                except (OSError, ValueError, TypeError):
                    continue
                if any(
                    normalized_query in str(message.get("content") or "").casefold()
                    for message in messages
                    if isinstance(message, dict)
                ):
                    matched.append(item)
            sessions = matched
        return {
            "user": name,
            "source": normalized_source,
            "query": query.strip(),
            "sessions": sessions,
        }

    @staticmethod
    def _interactive_active_key(user: str, client_id: str = "") -> str:
        return f"interactive:{user}:{client_id}" if client_id else f"interactive:{user}"

    @staticmethod
    def _index_session_payload(record: dict[str, Any]) -> dict[str, Any]:
        return {
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
            "summary_attempt_count": max(0, int(record.get("summary_attempt_count") or 0)),
            "summary_consecutive_failures": max(
                0, int(record.get("summary_consecutive_failures") or 0)
            ),
            "summary_max_attempts": max(1, int(record.get("summary_max_attempts") or 5)),
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
            "rounds": max(0, int(record.get("rounds") or 0)),
            "updated_at": str(record.get("updated_at") or ""),
        }

    def active_session(self, user: Any, client_id: Any = "") -> dict[str, Any]:
        """Return or reserve the user's durable interactive session."""

        name = self.require_user(user)
        normalized_client = self.require_client_id(client_id)
        active_key = self._interactive_active_key(name, normalized_client)
        record, created = get_or_reserve_index_session(
            self.root,
            name,
            "web",
            active_key,
            reuse_latest=True,
        )
        with self._active_runs_lock:
            active_clients = self._touch_session_lease_locked(
                name, "web", str(record.get("session_id") or ""), normalized_client
            )
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
                active.user == name and active.session_id == normalized_session
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
                active.user == name and active.session_id == normalized_session
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
            if any(active.user == name for active in self._active_runs.values()):
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
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        normalized_session = self.require_session_id(session_id)
        directory = find_window(self.root, name, normalized_source, normalized_session)
        if directory is None:
            reserved = find_index_record(
                self.root,
                name,
                normalized_source,
                normalized_session,
            )
            if isinstance(reserved, dict) and not reserved.get("archive_window"):
                return {
                    "user": name,
                    "source": normalized_source,
                    "session_id": normalized_session,
                    "messages": [],
                    "round_metrics": [],
                    "round_traces": [],
                    "pagination": {
                        "limit": limit,
                        "total_rounds": 0,
                        "first_round": 0,
                        "last_round": 0,
                        "has_more_before": False,
                        "next_before": None,
                    },
                }
            raise NotFoundError(f"会话不存在：{normalized_session}")
        window = load_window(directory)
        raw_messages = (window.get("text") or {}).get("messages") or []
        message_rounds: list[list[dict[str, Any]]] = []
        current_round: list[dict[str, Any]] = []
        for raw_message in raw_messages if isinstance(raw_messages, list) else []:
            if not isinstance(raw_message, dict):
                continue
            if raw_message.get("role") == "user" and current_round:
                message_rounds.append(current_round)
                current_round = []
            current_round.append(dict(raw_message))
        if current_round:
            message_rounds.append(current_round)

        total_rounds = len(message_rounds)
        end_round = total_rounds
        if before is not None:
            end_round = min(end_round, max(0, int(before) - 1))
        start_round = 1 if end_round > 0 else 0
        if limit is not None and end_round > 0:
            start_round = max(1, end_round - max(1, int(limit)) + 1)
        selected_messages = (
            [
                message
                for group in message_rounds[start_round - 1 : end_round]
                for message in group
            ]
            if start_round > 0
            else []
        )

        def in_selected_page(round_number: int) -> bool:
            return start_round > 0 and start_round <= round_number <= end_round

        def media_artifacts(value: Any) -> list[dict[str, Any]]:
            if not isinstance(value, list):
                return []
            artifacts: list[dict[str, Any]] = []
            for item in value:
                if not isinstance(item, dict):
                    continue
                path = str(item.get("path") or "")
                if not path or str(item.get("scope") or "") != "download":
                    continue
                artifacts.append(
                    {
                        key: item[key]
                        for key in (
                            "asset_id",
                            "type",
                            "name",
                            "scope",
                            "path",
                            "mime_type",
                            "size",
                            "checksum_sha256",
                            "duration_ms",
                        )
                        if key in item
                    }
                )
            return artifacts

        raw_metrics = (window.get("data") or {}).get("round_metrics") or []
        input_attachments_by_round: dict[int, list[dict[str, Any]]] = {}

        def input_attachments(value: Any) -> list[dict[str, Any]]:
            if not isinstance(value, list):
                return []
            attachments: list[dict[str, Any]] = []
            seen: set[str] = set()
            upload_root = (self.root / "users" / name / "file_upload").resolve()
            for item in value:
                if not isinstance(item, dict):
                    continue
                asset_id = str(item.get("asset_id") or "")
                attachment_name = Path(str(item.get("name") or "attachment")).name[:255]
                media_kind = str(item.get("media_kind") or "file").lower()
                if media_kind not in {"image", "audio", "video", "file"}:
                    media_kind = "file"
                scope = str(item.get("scope") or "external")
                relative_path = str(item.get("relative_path") or "").replace("\\", "/").strip("/")
                available = False
                if scope == "file_upload" and relative_path:
                    try:
                        _, target = _safe_relative_target(upload_root, relative_path)
                        _reject_link_path(upload_root, target)
                        expected_size = max(0, int(item.get("size") or 0))
                        available = (
                            not target.is_symlink()
                            and target.is_file()
                            and (not expected_size or target.stat().st_size == expected_size)
                        )
                    except (OSError, WebServiceError):
                        available = False
                else:
                    scope = "external"
                    relative_path = ""
                key = asset_id or f"{scope}\0{relative_path}\0{attachment_name}"
                if key in seen:
                    continue
                seen.add(key)
                attachments.append(
                    {
                        "asset_id": asset_id,
                        "name": attachment_name,
                        "media_kind": media_kind,
                        "mime_type": str(
                            item.get("mime_type") or "application/octet-stream"
                        ),
                        "size": max(0, int(item.get("size") or 0)),
                        "checksum_sha256": str(item.get("checksum_sha256") or ""),
                        "scope": scope,
                        "relative_path": relative_path,
                        "available": available,
                    }
                )
            return attachments

        if isinstance(raw_metrics, list):
            for metric in raw_metrics:
                if not isinstance(metric, dict):
                    continue
                round_number = int(metric.get("round") or 0)
                values = input_attachments(metric.get("input_attachments"))
                if round_number > 0 and values:
                    input_attachments_by_round[round_number] = values
        raw_items = (window.get("items") or {}).get("items") or []
        if isinstance(raw_items, list):
            for raw_item in raw_items:
                if not isinstance(raw_item, dict) or raw_item.get("role") != "user":
                    continue
                metadata = raw_item.get("metadata")
                if not isinstance(metadata, dict):
                    continue
                round_number = int(metadata.get("round") or 0)
                values = input_attachments(metadata.get("input_attachments"))
                if round_number > 0 and values:
                    input_attachments_by_round.setdefault(round_number, values)

        decorated_messages: list[dict[str, Any]] = []
        selected_round = max(0, start_round - 1)
        for raw_message in selected_messages:
            message = dict(raw_message)
            if message.get("role") == "user":
                selected_round += 1
                values = input_attachments(message.get("attachments"))
                if not values:
                    values = input_attachments_by_round.get(selected_round, [])
                if values:
                    message["attachments"] = values
                else:
                    message.pop("attachments", None)
            decorated_messages.append(message)
        selected_messages = decorated_messages

        round_metrics = []
        if isinstance(raw_metrics, list):
            for item in raw_metrics:
                if not isinstance(item, dict):
                    continue
                round_number = int(item.get("round") or 0)
                if not in_selected_page(round_number):
                    continue
                usage = item.get("usage") if isinstance(item.get("usage"), dict) else {}
                artifacts: list[dict[str, Any]] = []
                responses = item.get("provider_responses") or []
                if isinstance(responses, list):
                    for response in responses:
                        metadata = response.get("metadata") if isinstance(response, dict) else None
                        if isinstance(metadata, dict):
                            artifacts.extend(media_artifacts(metadata.get("artifacts")))
                round_metrics.append(
                    {
                        "round": round_number,
                        "usage": dict(usage),
                        "elapsed_ms": max(0, int(item.get("elapsed_ms") or 0)),
                        "tool_calls": max(0, int(item.get("tool_calls") or 0)),
                        "guidance": [
                            str(value)
                            for value in item.get("guidance", [])
                            if isinstance(value, str)
                        ] if isinstance(item.get("guidance"), list) else [],
                        "status": str(item.get("status") or "completed"),
                        "cancelled": bool(item.get("cancelled", False)),
                        "cancel_reason": str(item.get("cancel_reason") or ""),
                        "artifacts": artifacts,
                    }
                )
        reasoning_by_round: dict[int, str] = {}
        raw_reasoning = (window.get("think") or {}).get("rounds") or []
        if isinstance(raw_reasoning, list):
            for item in raw_reasoning:
                if not isinstance(item, dict):
                    continue
                round_number = int(item.get("round") or 0)
                if in_selected_page(round_number):
                    reasoning_by_round[round_number] = str(item.get("content") or "")

        tools_by_round: dict[int, list[dict[str, Any]]] = {}
        raw_tools = (window.get("tool") or {}).get("rounds") or []
        if isinstance(raw_tools, list):
            for item in raw_tools:
                if not isinstance(item, dict):
                    continue
                round_number = int(item.get("round") or 0)
                if not in_selected_page(round_number) or not isinstance(
                    item.get("calls"), list
                ):
                    continue
                calls = []
                for call in item["calls"]:
                    if not isinstance(call, dict):
                        continue
                    arguments_text, arguments_truncated = _tool_text_preview(call.get("arguments") or {})
                    result_text, result_truncated = _tool_text_preview(call.get("result"))
                    raw_result = call.get("result")
                    tool_value = (
                        raw_result.get("result")
                        if isinstance(raw_result, dict)
                        else None
                    )
                    artifacts = media_artifacts(
                        tool_value.get("artifacts")
                        if isinstance(tool_value, dict)
                        else None
                    )
                    raw_status = str(call.get("status") or "completed").casefold()
                    status = (
                        "running"
                        if raw_status in {"running", "started", "pending", "deferred"}
                        else "error"
                        if raw_status
                        in {"failed", "error", "temporarily_unavailable", "cancelled"}
                        else "success"
                    )
                    calls.append(
                        {
                            "call_id": str(call.get("id") or ""),
                            "name": str(call.get("name") or "未知工具"),
                            "status": status,
                            "elapsed_ms": max(0, int(call.get("elapsed_ms") or 0)),
                            "arguments_text": arguments_text,
                            "arguments_truncated": arguments_truncated,
                            "result_text": result_text,
                            "result_truncated": result_truncated,
                            "artifacts": artifacts,
                        }
                    )
                tools_by_round[round_number] = calls

        round_traces = [
            {
                "round": round_number,
                "reasoning": reasoning_by_round.get(round_number, ""),
                "tools": tools_by_round.get(round_number, []),
            }
            for round_number in sorted(reasoning_by_round.keys() | tools_by_round.keys())
        ]
        return {
            "user": name,
            "source": normalized_source,
            "session_id": normalized_session,
            "messages": selected_messages,
            "round_metrics": round_metrics,
            "round_traces": round_traces,
            "pagination": {
                "limit": limit,
                "total_rounds": total_rounds,
                "first_round": start_round,
                "last_round": end_round,
                "has_more_before": start_round > 1,
                "next_before": start_round if start_round > 1 else None,
            },
        }

