"""Authenticated, bounded outbound peer actions. No raw HTTP or SSE escapes."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from concurrent.futures import Future, TimeoutError as WaitTimeout, wait
from contextlib import closing, contextmanager
from pathlib import Path
from uuid import UUID, uuid4

import httpx

from bridge_config import (
    MAX_EVENT_ID, PEER_ACK_HEADER, PEER_CHAT_PATH, PEER_PAGE_BYTES, PEER_RETENTION_DAYS, PEER_RUN_HEADER,
    PEER_RUN_PATTERN,
)
from outbound_credentials import VAULT_NAME, OutboundTarget, allowed_targets
from peer_store import BridgeError
from run_broker import TERMINAL_STATUSES, TerminalStatus, _failure, _terminal_status
from upstream import stream_transport_failure

COMMANDS = frozenset({"peer_list", "peer_status", "peer_chat_submit", "peer_chat_query", "peer_chat"})
_CHAT_SECONDS = 8.0  # The host holds the panel's module lock for the whole action.
_HTTP_SECONDS = 3.0
_STATUS_SECONDS = 4.0
_POLL_SECONDS = 0.4
_MAX_BODY_BYTES = 4 * PEER_PAGE_BYTES
_TERMINAL_RETENTION_SECONDS = PEER_RETENTION_DAYS * 24 * 60 * 60
_IDENTITY_TTL_SECONDS = 24 * 60 * 60
_PRUNE_BATCH = 128
_UNKNOWN_MESSAGE = "对端接单状态未知。请用同一 run_id 查询，不要换新编号重提。"


def _error(code: str) -> dict:
    return {"ok": False, "error": code}


def _result(state: str, *, cursor: int = 0, frames: int = 0, text: str = "",
            outcome: TerminalStatus | None = None) -> dict:
    result = {"ok": True, "state": state, "frames": frames, "text": text, "after_event": cursor}
    if state == "unknown":
        result["message"] = _UNKNOWN_MESSAGE
    if outcome is not None and outcome.status != "completed":
        result["failure"] = {"code": outcome.code, "transient": outcome.transient, "phase": outcome.phase}
    return result


def _failed(code: str, phase: str, *, frames: int = 0) -> dict:
    return _result("failed", outcome=_failure(code, phase), frames=frames)


def _worker(function, *args, **kwargs) -> Future:
    future = Future()

    def run():
        try:
            future.set_result(function(*args, **kwargs))
        except Exception as exc:
            future.set_exception(exc)

    # DNS and slow readers may outlast a socket timeout. They cannot hold the
    # short-lived control process open.
    threading.Thread(target=run, daemon=True).start()
    return future


def _chunks(response: httpx.Response, deadline: float):
    count = 0
    for chunk in response.iter_bytes():
        if time.monotonic() >= deadline:
            raise WaitTimeout
        count += len(chunk)
        if count > _MAX_BODY_BYTES:
            raise ValueError("peer response too large")
        yield chunk


def _sse(response: httpx.Response, deadline: float):
    pending = b""
    lines = []
    event_id = None
    for chunk in _chunks(response, deadline):
        full = (pending + chunk).split(b"\n")
        pending = full.pop()
        for line in full:
            line = line.rstrip(b"\r")
            if not line:
                if lines:
                    value = json.loads(b"\n".join(lines))
                    if not isinstance(value, dict):
                        raise ValueError("peer frame must be an object")
                    yield event_id, value
                lines, event_id = [], None
                continue
            field, _, value = line.partition(b":")
            if value.startswith(b" "):
                value = value[1:]
            if field == b"data":
                lines.append(value)
            elif field == b"id":
                event_id = int(value)
                if not 0 < event_id <= MAX_EVENT_ID:
                    raise ValueError("invalid peer event id")


def _peer_terminal(value: object) -> TerminalStatus:
    if not isinstance(value, dict) or value.get("type") != "done":
        raise ValueError("invalid peer terminal frame")
    state = value.get("status")
    if not isinstance(state, str) or state not in TERMINAL_STATUSES:
        raise ValueError("invalid peer terminal status")
    # Adapt the peer's public projection back to the shared terminal parser.
    outcome = _terminal_status({"type": "done", "status": state, "error": {
        "message": "", **{key: value.get(key) for key in ("code", "transient", "phase")},
    }}, upstream=False)
    if outcome is None:
        raise ValueError("missing peer terminal status")
    return outcome


def _read_chat(response: httpx.Response, deadline: float, *, submit: bool, after: int) -> dict:
    cursor, frames, parts = after, 0, []

    def finish(outcome: TerminalStatus | None = None, *, peer_finished: bool = False) -> dict:
        result = _result(outcome.status if outcome else "running", cursor=cursor, frames=frames,
                         text="".join(parts), outcome=outcome)
        if peer_finished:
            result["_peer_finished"] = True
        return result

    def invalid_stream(code: str, phase: str = "stream") -> dict:
        # A malformed or interrupted reply is not proof the POST was rejected.
        return _result("unknown") if submit else finish(_failure(code, phase))

    try:
        for event_id, value in _sse(response, deadline):
            kind = value.get("type")
            if kind == "accepted" and submit:
                return finish()
            if kind == "page" and not submit:
                next_cursor = value.get("after_event")
                if type(next_cursor) is not int or not cursor <= next_cursor <= MAX_EVENT_ID:
                    raise ValueError("invalid peer page cursor")
                cursor = next_cursor
                if "terminal" in value:
                    return finish(_peer_terminal(value["terminal"]), peer_finished=True)
                return finish()
            if kind not in ("text_delta", "done"):
                continue
            if event_id is None:
                raise ValueError("peer event id required")
            if event_id <= cursor:
                continue
            if kind == "text_delta":
                content = value.get("content")
                if not isinstance(content, str):
                    raise ValueError("invalid peer text delta")
                if submit:
                    # Older peers may ignore the ACK header. Replay the first delta on query.
                    return finish()
                parts.append(content)
            cursor = event_id
            frames += 1
            if kind == "done":
                return finish(_peer_terminal(value), peer_finished=True)
    except (ValueError, RecursionError):
        return invalid_stream("upstream_invalid_payload")
    except WaitTimeout:
        return _result("unknown") if submit else _error("upstream_timeout")
    except httpx.HTTPError as exc:
        failure = stream_transport_failure(exc, phase="stream", attempt=1)
        if failure is None:
            return invalid_stream("bridge_internal", "bridge")
        if failure.code == "upstream_timeout":
            return _result("unknown") if submit else _error(failure.code)
        return invalid_stream(failure.code, failure.phase)
    return invalid_stream("upstream_stream_incomplete")


def _http(target: OutboundTarget, deadline: float, *, submit: dict | None = None, run_id: str = "",
          session_id: str = "", after: int = 0, version: bool = False, send: _SendAttempt | None = None) -> dict:
    chat = bool(run_id)
    if not target.secret:
        return _error("upstream_auth_missing")
    headers = {
        "Authorization": f"Bearer {target.secret}",
        "X-Kemo-Peer-Id": target.peer_id,
        "Accept-Encoding": "identity",
    }
    params = None
    if chat:
        if submit is not None:
            headers[PEER_ACK_HEADER] = "1"
        else:
            headers[PEER_RUN_HEADER] = run_id
            params = {"session_id": session_id, "after_event": after}
    path = PEER_CHAT_PATH if chat else ("/peer/v1/version" if version else "/peer/v1/status")
    extensions = None
    if send is not None:
        def trace(name, _):
            if name == "http11.send_request_headers.started":
                send.claim(deadline)

        extensions = {"trace": trace}
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return send.timeout() if send is not None else _error("upstream_timeout")
    try:
        with httpx.Client(timeout=remaining, follow_redirects=False, trust_env=False) as client:
            with client.stream("POST" if submit is not None else "GET",
                                target.url + path, headers=headers, json=submit, params=params,
                                extensions=extensions) as response:
                status = response.status_code
                if status == 401:
                    return (_failed("upstream_auth_rejected", "auth", frames=1) if chat
                            else _error("upstream_auth_rejected"))
                if chat and submit is None and status == 404 and response.headers.get(PEER_RUN_HEADER) == run_id:
                    return _error("run_not_found")
                if chat and submit is None and status == 413 and response.headers.get(PEER_RUN_HEADER) == run_id:
                    return _error("peer_event_too_large")
                if not response.is_success:
                    return _error("upstream_http_error")
                if chat:
                    if (response.headers.get(PEER_RUN_HEADER) != run_id
                            or response.headers.get("content-type", "").split(";", 1)[0] != "text/event-stream"):
                        return (_result("unknown") if submit is not None
                                else _failed("upstream_invalid_payload", "stream"))
                    return _read_chat(response, deadline, submit=submit is not None, after=after)
                value = json.loads(b"".join(_chunks(response, deadline)))
                if version:
                    if not isinstance(value, dict) or not isinstance(value.get("instance_uuid"), str):
                        return _error("upstream_invalid_payload")
                    return {"ok": True, "instance_uuid": str(UUID(value["instance_uuid"]))}
                if not isinstance(value, dict) or not (
                    type(value.get("online")) is bool and type(value.get("busy")) is bool
                    and isinstance(value.get("version"), str) and len(value["version"]) <= 64
                ):
                    return _error("upstream_invalid_payload")
                return {"ok": True, **{key: value[key] for key in ("online", "busy", "version")}}
    except WaitTimeout:
        return send.timeout() if send is not None else _error("upstream_timeout")
    except httpx.HTTPError as exc:
        if send is not None and (send.claimed or time.monotonic() >= deadline):
            return send.timeout()
        failure = stream_transport_failure(exc, phase="request", attempt=1)
        return _error(failure.code if failure else "bridge_internal")
    except (ValueError, RecursionError, httpx.InvalidURL):
        return _error("upstream_invalid_payload")


def _request(target: OutboundTarget, deadline: float, *, send: _SendAttempt | None = None, **kwargs) -> dict:
    deadline = min(deadline, time.monotonic() + _HTTP_SECONDS)
    if time.monotonic() >= deadline:
        return send.timeout() if send is not None else _error("upstream_timeout")
    future = _worker(_http, target, deadline, send=send, **kwargs)
    try:
        return future.result(timeout=max(0, deadline - time.monotonic()))
    except WaitTimeout:
        return send.timeout() if send is not None else _error("upstream_timeout")


def _identifier(value: object) -> str:
    if not isinstance(value, str) or len(value) > 128 or not re.fullmatch(PEER_RUN_PATTERN, value):
        raise BridgeError(400, "invalid_run_id")
    return value


def _session(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise BridgeError(400, "invalid_session_id")
    return value.strip()


@contextmanager
def _ledger(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
    with closing(sqlite3.connect(path, timeout=0.25)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("""CREATE TABLE IF NOT EXISTS submissions (
            run_id TEXT PRIMARY KEY, username TEXT NOT NULL, target_id TEXT NOT NULL,
            session_id TEXT NOT NULL, prompt_sha256 TEXT NOT NULL, receipt TEXT NOT NULL,
            send_state TEXT NOT NULL
        )""")
        if not any(row["name"] == "send_state" for row in db.execute("PRAGMA table_info(submissions)")):
            db.execute("BEGIN IMMEDIATE")
            if not any(row["name"] == "send_state" for row in db.execute("PRAGMA table_info(submissions)")):
                # Old reservations may already have been sent. Never make them retryable.
                db.execute("ALTER TABLE submissions ADD COLUMN send_state TEXT NOT NULL DEFAULT 'sent_unknown'")
                db.execute("UPDATE submissions SET send_state='accepted' WHERE json_extract(receipt, '$.state') IN "
                           "('running','completed','failed','cancelled','interrupted')")
            db.commit()
        db.execute("CREATE INDEX IF NOT EXISTS submissions_by_session ON submissions(session_id)")
        db.execute("""CREATE TABLE IF NOT EXISTS submission_terminals (
            run_id TEXT PRIMARY KEY REFERENCES submissions(run_id) ON DELETE CASCADE, observed_at INTEGER NOT NULL
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS terminal_expiry ON submission_terminals(observed_at)")
        db.execute("""CREATE TABLE IF NOT EXISTS archived_session_owners (
            target_id TEXT NOT NULL, session_id TEXT NOT NULL, username TEXT NOT NULL,
            PRIMARY KEY (target_id, session_id)
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS archived_owners_by_session ON archived_session_owners(session_id)")
        db.execute("""CREATE TABLE IF NOT EXISTS remote_targets (
            target_id TEXT PRIMARY KEY, instance_uuid TEXT NOT NULL, peer_id TEXT NOT NULL, checked_at INTEGER NOT NULL
        )""")
        yield db


class _SendAttempt:
    def __init__(self, path: Path, run_id: str):
        self.path, self.run_id = path, run_id
        self.lock = threading.Lock()
        self.claimed = self.closed = False
        self.repeat = None

    def claim(self, deadline: float) -> None:
        with self.lock:
            if self.closed or time.monotonic() >= deadline:
                raise WaitTimeout
            with _ledger(self.path) as db:
                db.execute("BEGIN IMMEDIATE")
                if time.monotonic() >= deadline:
                    raise WaitTimeout
                # HTTP/1.1 calls this after connecting, just before writing headers.
                # Commit first: once bytes can leave, a worker/process loss is ambiguous.
                changed = db.execute("UPDATE submissions SET send_state='sent_unknown' "
                                     "WHERE run_id=? AND send_state='reserved'", (self.run_id,)).rowcount
                if not changed:
                    row = db.execute("SELECT receipt FROM submissions WHERE run_id=?", (self.run_id,)).fetchone()
                    self.repeat = json.loads(row["receipt"]) if row else _error("run_not_found")
                    raise WaitTimeout
                if time.monotonic() >= deadline:
                    raise WaitTimeout
            self.claimed = True
            if time.monotonic() < deadline:
                return
            # The commit crossed the deadline, but the pre-write hook hasn't returned.
            try:
                with _ledger(self.path) as db:
                    released = db.execute("UPDATE submissions SET send_state='reserved' "
                                          "WHERE run_id=? AND send_state='sent_unknown'", (self.run_id,)).rowcount
            except (OSError, sqlite3.Error):
                # Don't promise a retry if the durable claim may still stand.
                raise WaitTimeout from None
            self.claimed = not released
            raise WaitTimeout

    def timeout(self) -> dict:
        with self.lock:
            self.closed = True
            if self.repeat is not None:
                return self.repeat
            if self.claimed:
                return _result("unknown")
            return _error("budget_exhausted_before_sent")


def _remote_instance(path: Path, target: OutboundTarget, deadline: float) -> str | dict:
    if not target.secret:
        return _error("upstream_auth_missing")
    with _ledger(path) as db:
        cached = db.execute("SELECT instance_uuid,checked_at FROM remote_targets WHERE target_id=?",
                            (target.target_id,)).fetchone()
    if cached and 0 <= time.time() - cached["checked_at"] < _IDENTITY_TTL_SECONDS:
        return cached["instance_uuid"]
    result = _request(target, deadline, version=True)
    if result.get("error") == "upstream_auth_rejected":
        return _failed("upstream_auth_rejected", "auth", frames=1)
    if not result["ok"]:
        return _error("remote_identity_unavailable")
    identity = result["instance_uuid"]
    with _ledger(path) as db:
        # Keep the old binding even if the address later points somewhere else.
        updated = db.execute("""INSERT INTO remote_targets VALUES (?, ?, ?, ?)
            ON CONFLICT(target_id) DO UPDATE SET checked_at=excluded.checked_at
            WHERE remote_targets.instance_uuid=excluded.instance_uuid
        """, (target.target_id, identity, target.peer_id, int(time.time()))).rowcount
    return identity if updated else _error("remote_identity_changed")


def _remember_terminal(db: sqlite3.Connection, run_id: str) -> None:
    db.execute("INSERT OR IGNORE INTO submission_terminals VALUES (?, ?)", (run_id, int(time.time())))


def _prune_expired(db: sqlite3.Connection) -> None:
    cutoff = int(time.time()) - _TERMINAL_RETENTION_SECONDS
    rows = db.execute("""
        SELECT s.run_id, s.target_id, s.session_id, s.username
        FROM submission_terminals AS t JOIN submissions AS s ON s.run_id = t.run_id
        WHERE t.observed_at <= ? ORDER BY t.observed_at LIMIT ?
    """, (cutoff, _PRUNE_BATCH)).fetchall()
    # The round may expire before the peer's conversation history does.
    db.executemany("INSERT OR IGNORE INTO archived_session_owners VALUES (?, ?, ?)",
                   ((row["target_id"], row["session_id"], row["username"]) for row in rows))
    db.executemany("DELETE FROM submissions WHERE run_id=?", ((row["run_id"],) for row in rows))


def _replay(row: sqlite3.Row, target: OutboundTarget, username: str, session_id: str, digest: str) -> dict | None:
    if row["username"] != username:
        raise BridgeError(404, "run_not_found")
    if row["prompt_sha256"] != digest:
        raise BridgeError(409, "run_prompt_conflict")
    if (row["target_id"], row["session_id"]) != (target.target_id, session_id):
        raise BridgeError(409, "run_scope_conflict")
    return None if row["send_state"] == "reserved" else json.loads(row["receipt"])


def _reserve(path: Path, target: OutboundTarget, username: str, request: dict, deadline: float) -> dict | None:
    run_id, session_id = request["run_id"], request["session_id"]
    digest = hashlib.sha256(request["prompt"].encode()).hexdigest()
    with _ledger(path) as db:
        row = db.execute("SELECT * FROM submissions WHERE run_id=?", (run_id,)).fetchone()
    if row is not None and (receipt := _replay(row, target, username, session_id, digest)) is not None:
        return receipt
    identity = _remote_instance(path, target, deadline)
    if isinstance(identity, dict):
        if identity.get("error") == "remote_identity_unavailable" and time.monotonic() >= deadline:
            return _error("budget_exhausted_before_sent")
        return identity
    with _ledger(path) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM submissions WHERE run_id=?", (run_id,)).fetchone()
        if row is not None:
            return _replay(row, target, username, session_id, digest)
        if db.execute("""
            SELECT 1 FROM (
                SELECT target_id,username FROM archived_session_owners WHERE session_id=?
                UNION ALL SELECT target_id,username FROM submissions WHERE session_id=?
            ) AS owners LEFT JOIN remote_targets AS remote USING (target_id)
            WHERE owners.username<>? AND (remote.target_id IS NULL OR (instance_uuid=? AND peer_id=?))
            LIMIT 1
        """, (session_id, session_id, username, identity, target.peer_id)).fetchone() is not None:
            # Legacy owners without a verified target are ambiguous, not free to reuse.
            raise BridgeError(409, "session_scope_conflict")
        _prune_expired(db)
        db.execute("INSERT INTO submissions VALUES (?, ?, ?, ?, ?, ?, 'reserved')",
                   (run_id, username, target.target_id, session_id, digest, json.dumps(_result("unknown"))))
        return None


def _submit(path: Path, target: OutboundTarget, username: str, args: dict, deadline: float) -> dict:
    request = {"run_id": _identifier(args.get("run_id", uuid4().hex)),
               "session_id": _session(args.get("session_id")), "prompt": args.get("prompt")}
    prompt = request["prompt"]
    if not isinstance(prompt, str) or not prompt.strip():
        return _error("invalid_prompt")
    run_id = request["run_id"]
    receipt = _reserve(path, target, username, request, deadline)
    if receipt is None:
        attempt = _SendAttempt(path, run_id)
        receipt = _request(target, deadline, submit=request, run_id=run_id, send=attempt)
        peer_finished = receipt.pop("_peer_finished", False)
        if attempt.claimed and receipt.get("state") != "unknown":
            try:
                with _ledger(path) as db:
                    confirmed = peer_finished or receipt.get("state") == "running" or (
                        receipt.get("failure", {}).get("code") == "upstream_auth_rejected"
                    )
                    db.execute("UPDATE submissions SET receipt=?, "
                               "send_state=CASE WHEN ? THEN 'accepted' ELSE send_state END WHERE run_id=?",
                               (json.dumps(receipt), confirmed, run_id))
                    if peer_finished:
                        _remember_terminal(db, run_id)
            except sqlite3.Error:
                # The reservation still stands. Give the caller the handle even
                # if the receipt could not be committed after the POST.
                receipt = _result("unknown")
    if receipt.get("error") == "upstream_auth_missing":
        return receipt
    return {**receipt, "run_id": run_id}


def _query(path: Path, target: OutboundTarget, username: str, args: dict, deadline: float) -> dict:
    run_id, after = _identifier(args.get("run_id")), args.get("after_event", 0)
    if type(after) is not int or not 0 <= after <= MAX_EVENT_ID:
        return _error("invalid_after_event")
    with _ledger(path) as db:
        row = db.execute(
            """SELECT s.session_id, s.send_state, t.observed_at
               FROM submissions AS s LEFT JOIN submission_terminals AS t ON t.run_id = s.run_id
               WHERE s.run_id=? AND s.username=? AND s.target_id=?""",
            (run_id, username, target.target_id),
        ).fetchone()
    if row is None or ("session_id" in args and _session(args["session_id"]) != row["session_id"]):
        return _error("run_not_found")
    if row["send_state"] == "reserved":
        return {**_error("run_not_submitted"), "run_id": run_id, "after_event": after}
    identity = _remote_instance(path, target, deadline)
    if isinstance(identity, dict):
        return {**identity, "run_id": run_id, "after_event": after}
    result = _request(target, deadline, run_id=run_id, session_id=row["session_id"], after=after)
    peer_finished = result.pop("_peer_finished", False)
    if ((result.get("state") == "running" and row["send_state"] == "sent_unknown")
            or (peer_finished and row["observed_at"] is None)):
        with _ledger(path) as db:
            if row["send_state"] == "sent_unknown":
                db.execute("UPDATE submissions SET send_state='accepted' WHERE run_id=?", (run_id,))
            if peer_finished:
                _remember_terminal(db, run_id)
    elif result.get("error") == "run_not_found" and row["send_state"] == "sent_unknown":
        result = _result("unknown", cursor=after)
    return {"after_event": after, **result, "run_id": run_id}


def _status(targets: list[OutboundTarget], deadline: float) -> list[dict]:
    futures = [_worker(_http, target, min(deadline, time.monotonic() + _HTTP_SECONDS)) for target in targets]
    wait(futures, timeout=max(0, deadline - time.monotonic()))
    return [future.result() if future.done() else _error("upstream_timeout") for future in futures]


def _chat(path: Path, target: OutboundTarget, username: str, args: dict, deadline: float) -> dict:
    result = _submit(path, target, username, args, deadline)
    delivered_after = result.get("after_event", 0)
    while result.get("state") == "running" and time.monotonic() < deadline:
        try:
            page = _query(path, target, username, result, deadline)
        except (OSError, sqlite3.Error):
            page = _error("outbound_state_unavailable")
        if not page["ok"]:
            if page["error"] != "upstream_timeout":
                run_id = result["run_id"]
                message = f"请使用 run_id={run_id}、after_event={delivered_after} 续查已提交的运行。"
                return {**page, "after_event": delivered_after, "message": message,
                        "failures": [{"ok": False, "name": "peer_chat_query", "error": message}]}
        else:
            result = {**page, "frames": result["frames"] + page["frames"], "text": result["text"] + page["text"]}
            if page["frames"] or result["state"] != "running":
                continue
        time.sleep(max(0, min(_POLL_SECONDS, deadline - time.monotonic())))
    return result


def _chat_wait_receipt(result: dict, target: OutboundTarget, started: float, requested: float) -> dict:
    if not result["ok"]:
        return result
    elapsed = time.monotonic() - started
    next_action = None
    if result["state"] in {"running", "unknown"}:
        next_action = {"command": "peer_chat_query", "params": {
            "target_id": target.target_id, "run_id": result["run_id"], "after_event": result["after_event"],
        }}
    return {
        **result, "waited_seconds": round(elapsed, 3),
        "budget_remaining_seconds": round(max(0, requested - elapsed), 3), "next_action": next_action,
    }


def execute(command: str, args: dict, *, username: str, base: Path) -> dict:
    started = time.monotonic()
    try:
        if not username:
            return _error("missing_user")
        targets = allowed_targets(base / VAULT_NAME, username)
        if command == "peer_list":
            return {"ok": True, "targets": [target.public() for target in targets]}
        target_id = args.get("target_id")
        if command == "peer_status" and target_id is None:
            return {"ok": True, "targets": [
                {**target.public(), **status} for target, status in zip(targets, _status(targets, started + _STATUS_SECONDS))
            ]}
        target = next((target for target in targets if target.target_id == target_id), None)
        if target is None:
            return _error("target_not_found")
        if command == "peer_status":
            return {**target.public(), **_status([target], started + _STATUS_SECONDS)[0]}
        path = base.parents[2] / "runtime" / "kemo_bridge_outbound.sqlite3"
        deadline = started + _CHAT_SECONDS
        if command == "peer_chat_submit":
            return _submit(path, target, username, args, deadline)
        if command == "peer_chat_query":
            return _query(path, target, username, args, deadline)
        duration = args.get("max_wait_seconds", 60)
        if type(duration) not in (int, float) or not 0 < duration <= 300:
            return _error("invalid_max_wait_seconds")
        result = _chat(path, target, username, args, min(deadline, started + duration))
        return _chat_wait_receipt(result, target, started, duration)
    except BridgeError as exc:
        return _error(exc.code)
    except (OSError, sqlite3.Error):
        return _error("outbound_state_unavailable")
    except UnicodeError:
        return _error("invalid_peer_parameters")
