"""Ordered browser presence adaptation; durable state is authoritative."""
from __future__ import annotations

import time
from typing import Any

from run.history import other_presence, update_presence, valid_sequence
from web.errors import InvalidRequestError, NotFoundError


def ordered_presence(service: Any, user: str, source: str, session_id: str,
                     client_id: str, sequence: int, *, release: bool = False) -> dict:
    if source not in {"web", "app"} or not valid_sequence(sequence):
        raise InvalidRequestError("lease_sequence 仅支持 Web/App，必须为安全范围内的正整数")
    with service._active_runs_lock:
        state = update_presence(service.root, user, source, session_id, client_id,
                                sequence, release=release)
        if not state["exists"] and not release:
            raise NotFoundError("对话不存在、已经结束或已离线清理，请新建对话")
        if state["accepted"]:
            service._prune_session_leases_locked()
            key = (user, source, session_id)
            if release:
                service._release_session_lease_locked(user, source, session_id, client_id)
            else:
                service._session_leases.setdefault(key, {})[client_id] = time.monotonic()
        count = other_presence(service.root, user, source, session_id)["active_clients"]
    return {
        "user": user, "source": source, "session_id": session_id, "client_id": client_id,
        "active_clients": count, "lease_sequence": sequence, "ignored": not state["accepted"],
        **({"released": not state["active"]} if release else {"leased": state["active"]}),
    }
