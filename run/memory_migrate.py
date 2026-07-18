"""One-time migration from array-based memory storage to file-backed schema v2."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from run.memory import (
    MEMORY_SCHEMA_VERSION,
    TEMPORARY_TIERS,
    TIERS,
    contains_sensitive_credential,
    iso,
    normalize_memory_filename,
    parse_time,
    utc_now,
)


class MemoryMigrationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MigrationReport:
    user: str
    migrated: bool
    already_v2: bool
    files: int
    rejected_sensitive: int
    backup: str
    conflicts: tuple[str, ...]


def _read_legacy_items(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text("utf-8")
    except FileNotFoundError:
        return []
    if not text.strip():
        return []
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MemoryMigrationError(f"旧记忆文件不是有效 JSON：{path}（{exc}）") from exc
    if isinstance(raw, dict) and raw.get("schema_version") == MEMORY_SCHEMA_VERSION:
        raise MemoryMigrationError(f"检测到部分 v2 索引但根版本标记缺失：{path}")
    if isinstance(raw, dict):
        raw = raw.get("items", [])
    if not isinstance(raw, list):
        raise MemoryMigrationError(f"旧记忆文件根节点必须是数组：{path}")
    return [item if isinstance(item, dict) else {"content": item} for item in raw]


def _is_v2(base: Path) -> bool:
    marker = base / "storage.json"
    try:
        raw = json.loads(marker.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    return isinstance(raw, dict) and raw.get("schema_version") == MEMORY_SCHEMA_VERSION


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(value.rstrip())
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _temporary_meta(item: dict[str, Any], tier: str, now: datetime) -> dict[str, Any]:
    days = {"seven_days": 7, "one_month": 30, "half_year": 180}[tier]
    entered = parse_time(item.get("tier_entered_at")) or parse_time(item.get("created_at")) or now
    updated = parse_time(item.get("updated_at")) or entered
    expires = parse_time(item.get("review_at")) or entered + timedelta(days=days)
    return {
        "weight": max(0, int(item.get("tier_weight", item.get("weight", 0)))),
        "updated_at": iso(updated),
        "last_weight_date": item.get("last_weight_date"),
        "expires_at": iso(expires),
    }


def _validate_stage(stage: Path, expected: int) -> None:
    found = 0
    seen: set[str] = set()
    for tier in TEMPORARY_TIERS:
        index_path = stage / tier / "data.json"
        raw = json.loads(index_path.read_text("utf-8"))
        if raw.get("schema_version") != MEMORY_SCHEMA_VERSION or not isinstance(raw.get("files"), dict):
            raise MemoryMigrationError(f"迁移后索引无效：{index_path}")
        for filename in raw["files"]:
            filename_key = filename.casefold()
            if filename_key in seen:
                raise MemoryMigrationError(f"迁移后出现跨层同名文件：{filename}")
            seen.add(filename_key)
            if not (stage / tier / filename).is_file():
                raise MemoryMigrationError(f"迁移后正文缺失：{tier}/{filename}")
            found += 1
    permanent_dir = stage / "permanent"
    if (permanent_dir / "data.json").exists():
        raise MemoryMigrationError("永久层迁移后不应存在 data.json")
    for path in permanent_dir.glob("*.md"):
        filename_key = path.name.casefold()
        if filename_key in seen:
            raise MemoryMigrationError(f"迁移后出现跨层同名文件：{path.name}")
        seen.add(filename_key)
        found += 1
    if found != expected:
        raise MemoryMigrationError(f"迁移条数校验失败：expected={expected}, actual={found}")


def migrate_user_memory(
    root: Path,
    user: str,
    *,
    dry_run: bool = False,
    backup: bool = True,
    now: datetime | None = None,
) -> MigrationReport:
    root = root.resolve()
    user_dir = (root / "users" / user).resolve()
    if user_dir.parent != (root / "users").resolve():
        raise MemoryMigrationError(f"无效用户目录：{user}")
    base = user_dir / "improve"
    if _is_v2(base):
        return MigrationReport(user, False, True, 0, 0, "", ())

    current = now or utc_now()
    entries: list[tuple[str, str, str, dict[str, Any]]] = []
    rejected_sensitive = 0
    conflicts: list[str] = []
    seen: dict[str, str] = {}
    for tier in TIERS:
        for item in _read_legacy_items(base / tier / "data.json"):
            content = str(item.get("content") or item.get("text") or "").strip()
            if not content:
                continue
            if contains_sensitive_credential(content):
                rejected_sensitive += 1
                continue
            filename = normalize_memory_filename(item.get("filename") or content)
            filename_key = filename.casefold()
            if filename_key in seen:
                conflicts.append(f"{filename}: {seen[filename_key]} <-> {tier}")
                continue
            seen[filename_key] = tier
            meta = {} if tier == "permanent" else _temporary_meta(item, tier, current)
            entries.append((tier, filename, content, meta))
    if conflicts:
        raise MemoryMigrationError("旧数据存在全层级文件名冲突：" + "; ".join(conflicts))

    stage = user_dir / f".improve-v2-{uuid.uuid4().hex}"
    if stage.exists():
        raise MemoryMigrationError(f"迁移临时目录已存在：{stage}")
    try:
        for tier in TIERS:
            (stage / tier).mkdir(parents=True, exist_ok=True)
        indexes = {tier: {} for tier in TEMPORARY_TIERS}
        for tier, filename, content, meta in entries:
            _atomic_text(stage / tier / filename, content)
            if tier in TEMPORARY_TIERS:
                indexes[tier][filename] = meta
        for tier in TEMPORARY_TIERS:
            _atomic_json(
                stage / tier / "data.json",
                {"schema_version": MEMORY_SCHEMA_VERSION, "files": indexes[tier]},
            )
        _atomic_json(stage / "storage.json", {"schema_version": MEMORY_SCHEMA_VERSION})
        _validate_stage(stage, len(entries))
        if dry_run:
            shutil.rmtree(stage)
            return MigrationReport(user, False, False, len(entries), rejected_sensitive, "", ())

        backup_path = Path()
        if backup and base.exists():
            stamp = current.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_path = user_dir / f"improve_backup_v1_{stamp}_{uuid.uuid4().hex[:8]}"
            shutil.copytree(base, backup_path)

        held = user_dir / f".improve-v1-{uuid.uuid4().hex}"
        try:
            if base.exists():
                os.replace(base, held)
            os.replace(stage, base)
        except Exception:
            if not base.exists() and held.exists():
                os.replace(held, base)
            raise
        if held.exists():
            if held.parent != user_dir:
                raise MemoryMigrationError(f"拒绝清理用户目录外路径：{held}")
            shutil.rmtree(held)
        return MigrationReport(
            user=user,
            migrated=True,
            already_v2=False,
            files=len(entries),
            rejected_sensitive=rejected_sensitive,
            backup=str(backup_path) if backup_path else "",
            conflicts=(),
        )
    finally:
        if stage.exists():
            if stage.parent != user_dir:
                raise MemoryMigrationError(f"拒绝清理用户目录外路径：{stage}")
            shutil.rmtree(stage)


def _users(root: Path) -> list[str]:
    directory = root / "users"
    if not directory.is_dir():
        return []
    return sorted(
        path.name
        for path in directory.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="迁移 kemo-agent 文件记忆到 schema v2")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--user", action="append", default=[])
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args(argv)
    users = _users(args.root) if args.all else list(dict.fromkeys(args.user))
    if not users:
        parser.error("请指定 --user <name> 或 --all")
    reports = [
        migrate_user_memory(
            args.root,
            user,
            dry_run=args.dry_run,
            backup=not args.no_backup,
        )
        for user in users
    ]
    print(json.dumps([asdict(report) for report in reports], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
