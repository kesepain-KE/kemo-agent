"""Restore native-media references, including receipts written before UI linkage."""
from pathlib import Path
from typing import Any

from run.extensions import public_media_assets

_STATES = {"queued", "prepared", "included", "failed", "not_sent", "unconfirmed"}


def media_inputs(root: Path, user: str, values: Any, *, paths: Any = None,
                 call_id: str = "", final: bool = False) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    result = []
    candidates = [value for value in paths[:8] if isinstance(value, str)] if isinstance(paths, list) else []
    for raw in values[:32]:
        if not isinstance(raw, dict):
            continue
        raw = dict(raw)
        # Only the current user's managed areas are projected by public_media_assets.
        # The saved receipt must supply the file's metadata; arguments alone add nothing.
        if not raw.get("relative_path") and not raw.get("path"):
            matches = [path for path in candidates if Path(path).name == raw.get("name")]
            if len(matches) == 1:
                raw["path"] = matches[0]
        public = public_media_assets([raw], root=root, user=user)
        if not public:
            continue
        raw_state = raw.get("status")
        state = raw_state if isinstance(raw_state, str) and raw_state in _STATES else "queued"
        result.append({
            **public[0],
            "tool_call_id": call_id or str(raw.get("tool_call_id") or ""),
            "attachment_id": str(raw.get("attachment_id") or ""),
            "status": "not_sent" if final and state in {"queued", "prepared"} else state,
            **({"error": str(raw["error"])[:1000]} if raw.get("error") else {}),
        })
    return result
