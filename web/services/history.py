"""History page aggregation extracted from :mod:`web.services.sessions`."""

from __future__ import annotations

def history(
    service,
    user: Any,
    session_id: Any,
    *,
    source: Any = "web",
    limit: int | None = None,
    before: int | None = None,
) -> dict[str, Any]:
    import importlib
    _sessions = importlib.import_module("web.services.sessions")
    Any = _sessions.Any
    NotFoundError = _sessions.NotFoundError
    Path = _sessions.Path
    WebServiceError = _sessions.WebServiceError
    _reject_link_path = _sessions._reject_link_path
    _safe_relative_target = _sessions._safe_relative_target
    _tool_text_preview = _sessions._tool_text_preview
    find_index_record = _sessions.find_index_record
    find_window = _sessions.find_window
    load_window = _sessions.load_window
    name = service.require_user(user)
    normalized_source = service.require_history_source(source)
    assert normalized_source is not None
    normalized_session = service.require_session_id(session_id)
    directory = find_window(service.root, name, normalized_source, normalized_session)
    if directory is None:
        reserved = find_index_record(
            service.root,
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
    user_metadata_by_round: dict[int, dict[str, Any]] = {}

    def public_user_metadata(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        allowed = {
            "synthetic",
            "origin",
            "long_task_id",
            "continuation",
            "long_task_original_prompt",
        }
        return {
            key: value[key]
            for key in allowed
            if key in value
            and isinstance(value[key], (str, int, float, bool, type(None)))
        }

    def input_attachments(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        attachments: list[dict[str, Any]] = []
        seen: set[str] = set()
        upload_root = (service.root / "users" / name / "file_upload").resolve()
        for item in value:
            if not isinstance(item, dict):
                continue
            asset_id = str(item.get("asset_id") or "")
            attachment_name = Path(str(item.get("name") or "attachment")).name[:255]
            media_kind = str(item.get("media_kind") or "file").lower()
            if media_kind not in {"image", "audio", "video", "file"}:
                media_kind = "file"
            scope = str(item.get("scope") or "external")
            relative_path = (
                str(item.get("relative_path") or "").replace("\\", "/").strip("/")
            )
            available = False
            if scope == "file_upload" and relative_path:
                try:
                    _, target = _safe_relative_target(upload_root, relative_path)
                    _reject_link_path(upload_root, target)
                    expected_size = max(0, int(item.get("size") or 0))
                    available = (
                        not target.is_symlink()
                        and target.is_file()
                        and (
                            not expected_size
                            or target.stat().st_size == expected_size
                        )
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
            public_metadata = public_user_metadata(metadata)
            if round_number > 0 and public_metadata:
                user_metadata_by_round.setdefault(round_number, public_metadata)

    decorated_messages: list[dict[str, Any]] = []
    selected_round = max(0, start_round - 1)
    for raw_message in selected_messages:
        message = dict(raw_message)
        if message.get("role") == "user":
            selected_round += 1
            public_metadata = public_user_metadata(message.get("metadata"))
            if not public_metadata:
                public_metadata = user_metadata_by_round.get(selected_round, {})
            if public_metadata:
                message["metadata"] = public_metadata
            else:
                message.pop("metadata", None)
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
                    metadata = (
                        response.get("metadata")
                        if isinstance(response, dict)
                        else None
                    )
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
                    ]
                    if isinstance(item.get("guidance"), list)
                    else [],
                    "guidance_details": [
                        {
                            "id": str(value.get("id") or ""),
                            "text": str(value.get("text") or ""),
                            "display_text": str(
                                value.get("display_text")
                                or value.get("text")
                                or "附件引导"
                            ),
                            "uploaded_files": input_attachments(
                                value.get("uploaded_files")
                            ),
                        }
                        for value in item.get("guidance_details", [])
                        if isinstance(value, dict)
                    ]
                    if isinstance(item.get("guidance_details"), list)
                    else [],
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
                arguments_text, arguments_truncated = _tool_text_preview(
                    call.get("arguments") or {}
                )
                result_text, result_truncated = _tool_text_preview(
                    call.get("result")
                )
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
        for round_number in sorted(
            reasoning_by_round.keys() | tools_by_round.keys()
        )
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



