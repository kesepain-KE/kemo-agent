"""Session-scoped synchronization and archive commit helpers."""

from __future__ import annotations

import copy
import threading
from pathlib import Path
from typing import Any


_SESSION_LOCKS: dict[tuple[str, str, str, str], threading.RLock] = {}
_SESSION_LOCKS_GUARD = threading.Lock()


def session_lock(
    root: Path,
    user: str,
    source: str,
    session_id: str,
) -> threading.RLock:
    key = (str(root.resolve()), user, source, session_id)
    with _SESSION_LOCKS_GUARD:
        return _SESSION_LOCKS.setdefault(key, threading.RLock())


def copy_committed_round_to_archive(
    archive_window: dict[str, Any],
    runtime_window: dict[str, Any],
    runtime_round_number: int,
    archive_round_number: int,
) -> None:
    """Append only the new raw round to archive, preserving older uncompressed data."""

    archive_window["text"]["messages"].extend(
        copy.deepcopy(runtime_window["text"]["messages"][-2:])
    )
    for section in ("think", "tool"):
        source_rounds = (runtime_window.get(section) or {}).get("rounds", [])
        target_rounds = archive_window.setdefault(section, {}).setdefault("rounds", [])
        for raw in source_rounds:
            if not isinstance(raw, dict) or raw.get("round") != runtime_round_number:
                continue
            item = copy.deepcopy(raw)
            item["round"] = archive_round_number
            target_rounds.append(item)
    source_items = (runtime_window.get("items") or {}).get("items", [])
    target_items = archive_window.setdefault("items", {}).setdefault("items", [])
    for raw in source_items:
        if (
            not isinstance(raw, dict)
            or not isinstance(raw.get("metadata"), dict)
            or raw["metadata"].get("round") != runtime_round_number
        ):
            continue
        item = copy.deepcopy(raw)
        item["metadata"] = {
            **item["metadata"],
            "round": archive_round_number,
        }
        target_items.append(item)
    runtime_data = runtime_window["data"]
    archive_data = archive_window["data"]
    archive_data["rounds"] = archive_round_number
    archive_metrics = archive_data.setdefault("round_metrics", [])
    if not isinstance(archive_metrics, list):
        archive_metrics = []
        archive_data["round_metrics"] = archive_metrics
    runtime_metric = next(
        (
            item
            for item in reversed(runtime_data.get("round_metrics", []))
            if isinstance(item, dict) and item.get("round") == runtime_round_number
        ),
        None,
    )
    if runtime_metric is not None:
        metric = copy.deepcopy(runtime_metric)
        metric["round"] = archive_round_number
        archive_metrics.append(metric)
    archive_data["token_usage"] = copy.deepcopy(runtime_data.get("token_usage", {}))
    archive_data.pop("context", None)
