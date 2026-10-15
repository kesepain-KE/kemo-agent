"""Write-once Markdown expiration archive; never part of Prompt sources."""
from __future__ import annotations

import json
import os
from pathlib import Path
import uuid

from run.memory.store import connection
from run.memory.work import WorkLedger, digest


def _safe_path(root: Path, target: Path) -> None:
    from plugins._creator_security import reject_link_components
    reject_link_components(root.resolve(), target, label="过期记忆归档")
    target.resolve().relative_to(root.resolve())


def _publish(path: Path, body: str) -> None:
    """Hard-link a complete fsynced temporary file, without overwriting a peer."""
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_text("utf-8") != body:
                raise FileExistsError("归档目标存在不同内容，保留活跃记忆")
    finally:
        temporary.unlink(missing_ok=True)


def archive_expired(store, tier: str, filename: str, current) -> dict:
    from run.memory import contains_sensitive_credential, iso

    with store._lock:
        with connection(store.root, store.user) as db:
            row = store._row_by_filename(db, filename, tier=tier)
            if row is None:
                return {"archived": False, "reason": "missing"}
            snapshot = dict(row)
        if tier == "permanent" or not snapshot["expires_at"] or snapshot["expires_at"] > iso(current):
            return {"archived": False, "reason": "not_expired"}
        if snapshot["weight"] >= int(store.rules[tier].upgrade_threshold or 0):
            return {"archived": False, "reason": "promotion_due"}
        if contains_sensitive_credential(snapshot["content"]) or contains_sensitive_credential(filename):
            raise ValueError("过期记忆包含疑似敏感凭据，归档和删除均已阻止")
        # Stable identity includes content/revision/lifecycle, not this retry's time.
        key = "archive:" + digest({k: snapshot[k] for k in
                                  ("id", "tier", "content_hash", "revision", "expires_at")})
        ledger = WorkLedger(store.root, store.user)
        job = ledger.enqueue("archive", key, {"fragment": snapshot, "archived_at": iso(current)})
        payload = job["payload"]
        stamp = payload["archived_at"]
        default_relative = Path(stamp[:7]) / (
            f"{stamp[:19].replace('-', '').replace(':', '').replace('T', '-')}"
            f"-{tier}-{Path(filename).stem}-{key[-12:]}.md")
        directory = store.root / "users" / store.user / "improve" / "expired_memory"
        relative = Path(payload.get("archive_relative") or default_relative)
        path = directory / relative
        _safe_path(store.root, path)
        path.resolve().relative_to(directory.resolve())
        path.parent.mkdir(parents=True, exist_ok=True)
        metadata = {k: snapshot[k] for k in
                    ("id", "filename", "tier", "weight", "created_at", "expires_at", "content_hash")}
        metadata.update(archived_at=stamp, archive_id=key)
        body = "---\n" + "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}"
                                  for k, v in metadata.items()) + "\n---\n\n" + snapshot["content"] + "\n"
        try:
            collision = 0
            while True:
                payload["archive_relative"] = relative.as_posix()
                ledger.save(key, payload)
                try:
                    _safe_path(store.root, path)
                    _publish(path, body)
                    break
                except FileExistsError:
                    collision += 1
                    relative = default_relative.with_stem(f"{default_relative.stem}-{collision}")
                    path = directory / relative
            with connection(store.root, store.user, write=True) as db:
                # Concurrent edits/promotions must not be erased after writing a snapshot.
                removed = db.execute(
                    """DELETE FROM memory_fragments
                       WHERE id=? AND revision=? AND content_hash=? AND tier=?""",
                    (snapshot["id"], snapshot["revision"], snapshot["content_hash"], tier)).rowcount
                db.execute(
                    """UPDATE memory_work_items SET status='completed', updated_at=?, last_error=NULL
                       WHERE work_key=?""", (iso(current), key))
            return {"archived": bool(removed), "path": path.relative_to(store.root).as_posix()}
        except Exception as exc:
            ledger.save(key, payload, error=type(exc).__name__)
            raise
