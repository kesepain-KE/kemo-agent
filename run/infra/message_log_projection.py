"""Owner-scoped message exchanges for the bounded runtime log view."""

from __future__ import annotations

from collections import defaultdict
import hashlib
from typing import Any, Iterable

from run.infra.runtime_diagnostics import diagnostic_identifier, diagnostic_text


def message_log_exchanges(rows: Iterable[Any]) -> list[dict[str, Any]]:
    """Group before pagination; routing identities never leave this projection.

    New records carry an exchange ID. Legacy file-plugin records share their
    routing identity and exact timestamp. Ambiguous legacy buckets stay
    separate rather than pairing unrelated messages by arrival order.
    """

    buckets: dict[tuple[str, ...], list[Any]] = defaultdict(list)
    for row in rows:
        route = tuple(str(row[key]) for key in ("user", "machine_id", "platform", "chat_type", "chat_id"))
        identity = ("exchange", row["exchange_id"]) if row["exchange_id"] else ("legacy", row["occurred_at"])
        buckets[(*route, *identity)].append(row)

    result: list[dict[str, Any]] = []
    for key, bucket in buckets.items():
        ambiguous = key[-2] == "legacy" and any(
            sum(row["direction"] == direction and row["kind"] != "file" for row in bucket) > 1
            for direction in ("receive", "send")
        )
        groups = [[row] for row in bucket] if ambiguous else [bucket]
        for group in groups:
            # SQL arrives newest first; display each direction in write order,
            # including text and attachment names without exporting file paths.
            ordered = sorted(group, key=lambda row: row["id"])
            inputs = [_body(row) for row in ordered if row["direction"] == "receive"]
            outputs = [_body(row) for row in ordered if row["direction"] == "send"]
            latest = max(group, key=lambda row: (row["occurred_at_ms"], row["id"]))
            identity = (*key, ordered[0]["event_key"]) if ambiguous else key
            digest = hashlib.sha256("\x1f".join(identity).encode("utf-8")).hexdigest()
            result.append({
                "id": "message-exchange:" + digest,
                "category": "message",
                "title": diagnostic_identifier(latest["platform"]) + " 消息",
                "occurred_at": latest["occurred_at"],
                "status": "success" if all(row["success"] for row in group) else "failed",
                "duration_ms": None,
                "detail": "输入 / 输出",
                "direction": "exchange",
                "inputs": inputs,
                "outputs": outputs,
                "source": "sqlite",
            })
    return result


def _body(row: Any) -> dict[str, Any]:
    return {
        "id": "message:" + row["event_key"],
        "content": diagnostic_text(row["content"]),
        "kind": diagnostic_identifier(row["kind"]),
        "status": "success" if row["success"] else "failed",
        "occurred_at": row["occurred_at"],
    }
