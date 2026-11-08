"""Bounded RAW card history. UI groups are integrations, never context windows."""
from __future__ import annotations

import json
from pathlib import Path
from run.cards import CardStore, session_key
from run.history import find_record
from run.extensions import public_media_assets
from web.errors import NotFoundError, WebServiceError
from web.services._paths import _reject_link_path, _safe_relative_target
from web.services.native_media import media_inputs


def _tool_text_preview(value):
    return (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)), False


def _attachments(service, user, values):
    result, seen = [], set()
    root = (service.root / "users" / user / "file_upload").resolve()
    for item in values if isinstance(values, list) else []:
        if not isinstance(item, dict):
            continue
        asset = str(item.get("asset_id") or "")
        name = Path(str(item.get("name") or "attachment")).name[:255]
        path = str(item.get("relative_path") or "").replace("\\", "/").strip("/")
        scope, available = str(item.get("scope") or "external"), False
        try:
            size = max(0, int(item.get("size") or 0))
        except (TypeError, ValueError):
            size = 0
        if scope == "file_upload" and path:
            try:
                _, target = _safe_relative_target(root, path)
                _reject_link_path(root, target)
                available = target.is_file() and not target.is_symlink() and (
                    not size or target.stat().st_size == size)
            except (OSError, WebServiceError):
                available = False
        else:
            scope, path = "external", ""
        key = asset or f"{scope}\0{path}\0{name}"
        if key in seen:
            continue
        seen.add(key)
        result.append({"asset_id": asset, "name": name, "scope": scope, "relative_path": path,
                       "available": available, "media_kind": item.get("media_kind", "file"),
                       "mime_type": item.get("mime_type", "application/octet-stream"),
                       "size": size, "checksum_sha256": item.get("checksum_sha256", "")})
    return result


def _artifacts(service, user, values):
    fields = ("asset_id", "type", "name", "scope", "path", "mime_type", "size",
              "checksum_sha256", "duration_ms")
    result = []
    for value in values if isinstance(values, list) else []:
        if not isinstance(value, dict) or value.get("scope") != "download":
            continue
        safe = public_media_assets(
            [{**value, "relative_path": value.get("path", ""), "path": ""}],
            root=service.root, user=user)
        if safe and safe[0]["scope"] == "download":
            result.append({**{key: value[key] for key in fields if key in value},
                           "path": safe[0]["relative_path"]})
    return result


def _user_message(service, user, row, number, payload):
    metadata = payload.get("metadata") or {}
    return {
        "integration_id": row["integration_id"], "seq": row["seq"], "card_id": row["card_id"],
        "level": row["raw_level"], "integration_number": number, "role": "user",
        "content": str(payload.get("text") or ""),
        "attachments": _attachments(service, user, payload.get("attachments")),
        "metadata": {key: metadata[key] for key in ("origin",)
                     if key in metadata and isinstance(metadata[key], (str, int, float, bool, type(None)))},
    }


def history(service, user, session_id, *, source="web", limit=None, before=None):
    name = service.require_user(user)
    source = service.require_history_source(source)
    identity = service.require_session_id(session_id)
    record = find_record(service.root, name, source, identity)
    if record is None:
        raise NotFoundError(f"会话不存在：{identity}")
    store, key = CardStore(service.root, name), session_key(source, identity)
    size = max(1, min(500, int(limit or 100)))
    before_seq = max(1, int(before)) if before is not None else record["last_seq"] + 1
    with store.db() as db:
        rows = db.execute("SELECT * FROM context_cards WHERE session_key=? AND seq<? "
                          "ORDER BY seq DESC LIMIT ?", (key, before_seq, size)).fetchall()[::-1]
        integrations = {row["integration_id"]: dict(row) for row in db.execute(
            "SELECT i.*,row_number() OVER(ORDER BY created_at,integration_id) AS ordinal,"
            "(SELECT max(seq) FROM context_cards c WHERE c.integration_id=i.integration_id) AS last_seq "
            "FROM card_integrations i WHERE session_key=?", (key,))}
        user_messages = []
        if before is None:
            inputs = db.execute(
                "SELECT * FROM context_cards WHERE session_key=? "
                "AND raw_kind='user_text' AND raw_level='initial' ORDER BY seq", (key,))
            for row in inputs:
                integration = integrations.get(row["integration_id"], {})
                payload = json.loads(row["raw_payload"])
                if (json.loads(integration.get("record_json", "{}")).get("withdrawn")
                        or str(payload.get("text") or "").startswith("【任务计划连续执行】")):
                    continue
                user_messages.append(_user_message(
                    service, name, row, integration.get("ordinal", 0), payload))
        # A RAW page can start hundreds of events after its user's input.
        # Include one real input anchor per represented integration without
        # expanding the page or changing its seq cursor.
        page_ids = {row["integration_id"] for row in rows}
        initial_cards = {}
        if page_ids:
            placeholders = ",".join("?" for _ in page_ids)
            initial_cards = {row["integration_id"]: dict(row) for row in db.execute(
                "SELECT * FROM context_cards WHERE session_key=? AND seq IN ("
                "SELECT min(seq) FROM context_cards WHERE session_key=? "
                f"AND integration_id IN ({placeholders}) AND raw_kind='user_text' "
                "AND raw_level='initial' GROUP BY integration_id)", (key, key, *page_ids))}
        executions = {}
        ids = {row["tool_call_id"] for row in rows if row["tool_call_id"]}
        if ids:
            placeholders = ",".join("?" for _ in ids)
            executions = {row["execution_id"]: dict(row) for row in db.execute(
                "SELECT t.* FROM card_tool_executions t JOIN card_integrations i USING(integration_id) "
                f"WHERE i.session_key=? AND t.tool_call_id IN ({placeholders})", (key, *ids))}
    messages, metrics, traces, cards = [], {}, {}, []
    for row in rows:
        payload, integration = json.loads(row["raw_payload"]), integrations.get(row["integration_id"])
        if integration is None:
            # Direct storage fixtures may not have runtime metadata; RAW remains
            # inspectable without inventing a completed integration.
            integration = {"ordinal": 0, "status": "unknown", "record_json": "{}"}
        number, iid = integration["ordinal"], row["integration_id"]
        saved = json.loads(integration["record_json"])
        card = {key: row[key] for key in ("card_id", "integration_id", "seq", "raw_kind",
                                         "raw_level", "created_at")}
        cards.append(card)
        metric = metrics.setdefault(iid, {
            "integration_number": number, "integration_id": iid, "usage": saved.get("usage", {}),
            "elapsed_ms": saved.get("elapsed_ms", 0), "tool_calls": saved.get("tool_calls", 0),
            "guidance": [], "guidance_details": saved.get("guidance_details", []),
            "status": integration["status"], "cancelled": integration["status"] == "cancelled",
            "artifacts": [], "media_inputs": media_inputs(service.root, name, saved.get("media_inputs"), final=True),
            "withdrawn": bool(saved.get("withdrawn")),
            "response_closed": bool(integration.get("response_closed")),
            "last_seq": integration.get("last_seq") or 0,
            "acknowledged_seq": integration.get("acknowledged_seq") or 0,
        })
        if iid in initial_cards and "initial_card" not in metric:
            initial = initial_cards[iid]
            metric["initial_card"] = {
                field: initial[field] for field in (
                    "card_id", "integration_id", "seq", "raw_kind", "raw_level", "created_at")}
            metric["initial_card"]["message"] = _user_message(
                service, name, initial, number, json.loads(initial["raw_payload"]))
        trace = traces.setdefault(iid, {"integration_number": number, "integration_id": iid, "reasoning": "", "tools": {}})
        common = {"integration_id": iid, "seq": row["seq"], "card_id": row["card_id"],
                  "level": row["raw_level"], "integration_number": number}
        if row["raw_kind"] in {"user_text", "agent_text"}:
            message = {**common, "role": "user" if row["raw_kind"] == "user_text" else "assistant",
                       "content": str(payload.get("text") or "")}
            if row["raw_kind"] == "user_text":
                message = _user_message(service, name, row, number, payload)
                if row["raw_level"] == "guidance":
                    message["guidance_id"] = payload.get("guidance_id", "")
                    metric["guidance"].append(message["content"])
            else:
                message["reasoning"] = str(payload.get("reasoning") or "")
                trace["reasoning"] += str(payload.get("reasoning") or "")
                for output in payload.get("provider_output") or []:
                    if isinstance(output, dict):
                        for block in output.get("content") or []:
                            if isinstance(block, dict) and block.get("artifact"):
                                metric["artifacts"].extend(_artifacts(service, name, [block["artifact"]]))
            card["message"] = message
            messages.append(message)
        elif row["raw_kind"] in {"tool_call", "tool_result"}:
            original = payload if row["raw_kind"] == "tool_call" else payload["original_call"]
            eid, call_id = payload["execution_id"], row["tool_call_id"]
            # Older service exits remain RAW for pagination, without a tool card.
            if (row["event_key"] == f"shell-result:{eid}"
                    and original.get("arguments", {}).get("completion_mode") == "detached"):
                continue
            execution = executions.get(eid, {})
            result = payload.get("result")
            # Latest durable execution truth supersedes an older timeout notice.
            # The historical receipt remains RAW, including its original status.
            state = execution or payload.get("execution") or {}
            live = bool(state.get("execution_live", execution.get("execution_live", False)))
            status = "timed_out_running" if live and state.get("status") in {
                "timed_out_running", "interrupted_unknown"} else (
                "running" if live else "success" if state.get("status") in {"completed", "duplicate_reused"}
                and isinstance(result, dict) and result.get("ok") is True else "error")
            previous = trace["tools"].get(eid)
            if row["raw_kind"] == "tool_call" and previous:
                continue
            body = (result.get("result") if isinstance(result, dict) and "result" in result
                    else result if isinstance(result, dict) else {})
            attached = []
            if original["name"] == "attach_media":
                attached = media_inputs(
                    service.root, name, body.get("assets") if isinstance(body, dict) else [],
                    paths=original.get("arguments", {}).get("paths"), call_id=call_id,
                    final=bool(integration.get("response_closed")))
                # Accepted model-request delivery evidence wins over the queued
                # receipt; no argument or tool success can fabricate inclusion.
                saved_media = {item["asset_id"]: item for item in metric["media_inputs"]
                               if item.get("tool_call_id") == call_id}
                attached = [saved_media.get(item["asset_id"], item) for item in attached]
            tool = {
                "call_id": call_id, "execution_id": eid, "name": original["name"], "status": status,
                "seq": row["seq"], "elapsed_ms": state.get(
                    "duration_ms", (payload.get("execution") or {}).get("duration_ms", 0)),
                "arguments_text": _tool_text_preview(original.get("arguments", {}))[0],
                "arguments_truncated": False, "result_text": _tool_text_preview(result)[0] if result is not None else "",
                "result_truncated": False, "original_call": original,
                "artifacts": _artifacts(service, name, body.get("artifacts") if isinstance(body, dict) else None),
                "media_inputs": attached,
            }
            card["tool"] = tool
            trace["tools"][eid] = tool
    for trace in traces.values():
        trace["tools"] = list(trace["tools"].values())
    first, last = (rows[0]["seq"], rows[-1]["seq"]) if rows else (0, 0)
    return {
        "user": name, "source": source, "session_id": identity, "engine": "cardstream",
        "messages": messages, "cards": cards, "integrations": list(metrics.values()),
        "integration_traces": list(traces.values()),
        **({"user_messages": user_messages} if before is None else {}),
        "pagination": {"limit": size, "total_cards": record["last_seq"], "first_seq": first, "last_seq": last,
                       "integration_count": record["integration_count"],
                       "first_integration_number": min((m["integration_number"] for m in metrics.values()), default=0),
                       "last_integration_number": max((m["integration_number"] for m in metrics.values()), default=0),
                       "has_more_before": first > 1, "next_before": first if first > 1 else None,
                       "cursor_unit": "raw_seq"},
    }
