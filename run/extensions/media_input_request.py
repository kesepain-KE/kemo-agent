"""Validated intents and path authorization for native media attachment."""

from __future__ import annotations

from pathlib import Path
import os
import re
import json
from typing import Any, Iterable

from run.extensions.attachments import (
    AttachmentError,
    RunAssetResolver,
    describe_local_asset,
    _is_link_or_junction,
    _MEDIA_LIMITS,
    _media_kind_and_mime,
)


MAX_ATTACH_ITEMS = 8
MAX_RUN_MEDIA_ITEMS = 32
MAX_RUN_MEDIA_BYTES = 1024 * 1024 * 1024


def request_media_attachment(
    *,
    asset_ids: list[str] | None = None,
    paths: list[str] | None = None,
    note: str = "",
    detail: str = "auto",
) -> dict[str, Any]:
    """Return an intent only; a plugin cannot enqueue into a host by itself."""
    checked: dict[str, list[str]] = {}
    for name, values in (("asset_ids", asset_ids), ("paths", paths)):
        if values is None:
            values = []
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value.strip() or len(value) > 4096
            for value in values
        ):
            raise AttachmentError(f"{name} 必须是非空字符串数组")
        checked[name] = list(dict.fromkeys(value.strip() for value in values))
    count = sum(len(values) for values in checked.values())
    if not 1 <= count <= MAX_ATTACH_ITEMS:
        raise AttachmentError(f"每次必须装载 1..{MAX_ATTACH_ITEMS} 个资产或路径")
    if not isinstance(note, str) or len(note) > 4000:
        raise AttachmentError("note 必须是不超过 4000 字的字符串")
    if not isinstance(detail, str) or detail not in {"auto", "low", "high"}:
        raise AttachmentError("detail 必须是 auto、low 或 high")
    return {"media_input_request": {**checked, "note": note, "detail": detail}}


def checked_media_path(root: Path, user: str, raw: str) -> Path:
    """Reject aliases into private users and linked ancestors before reading bytes."""
    if raw.startswith(("\\\\", "//")) or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw):
        raise AttachmentError("只允许本地媒体路径，不接受远程 URL")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = Path(os.path.abspath(candidate))
    try:
        for part in (candidate, *candidate.parents):
            if _is_link_or_junction(part):
                raise AttachmentError("媒体路径不得包含符号链接或目录联接")
        path = candidate.resolve(strict=True)
        users_root = (root / "users").resolve()
        own_root = users_root / user
        if path.is_relative_to(users_root) and not path.is_relative_to(own_root):
            raise AttachmentError("不能装载其他用户的文件")
        if not path.is_file():
            raise AttachmentError("媒体路径不是文件")
        kind, _ = _media_kind_and_mime(path)
        if path.stat().st_size > _MEDIA_LIMITS[kind]:
            raise AttachmentError(f"{kind} 附件超过请求上限")
        return path
    except OSError as exc:
        raise AttachmentError(
            f"本地媒体不可用：{candidate.name}（{type(exc).__name__}）"
        ) from exc


def _referenced_paths(value: Any) -> Iterable[str]:
    """Only successful tool observations, never arbitrary tool arguments."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"path", "local_path", "file_path", "output_path", "saved_to"}:
                if isinstance(item, str):
                    yield item
            elif key in {"paths", "files", "artifacts", "result", "data"}:
                yield from _referenced_paths(item)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                yield item
            else:
                yield from _referenced_paths(item)


def resolve_media_attachment(
    root: Path,
    user: str,
    intent: dict[str, Any],
    known: Iterable[dict[str, Any]],
    *,
    user_text: str,
    tool_records: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    descriptors = [dict(item) for item in known]
    resolver = RunAssetResolver(root, user, descriptors)
    resolved: list[dict[str, Any]] = []
    for asset_id in intent["asset_ids"]:
        registered = next(
            (item for item in descriptors if item.get("asset_id") == asset_id), None
        )
        if registered is not None:
            checked_media_path(root, user, str(registered["path"]))
        _, descriptor = resolver.local_asset(asset_id)
        resolved.append(descriptor)
    observed_paths = [
        value
        for record in tool_records
        if isinstance(record.get("result"), dict)
        and record["result"].get("ok") is True
        for value in _referenced_paths(record["result"].get("result"))
    ]
    users_root = (root / "users").resolve()
    own_root = (users_root / user).resolve()
    for raw in intent["paths"]:
        path = checked_media_path(root, user, raw)
        managed = any(
            path.is_relative_to((own_root / directory).resolve())
            for directory in ("download", "file_upload")
        )
        observed = False
        for value in observed_paths:
            observed_path = Path(value).expanduser()
            if not observed_path.is_absolute():
                observed_path = root / observed_path
            if observed_path.resolve() == path:
                observed = True
                break
        mentioned = raw in user_text or json.dumps(raw, ensure_ascii=False) in user_text
        if not managed and not observed and not mentioned:
            raise AttachmentError(
                "本地路径必须由用户明确提供、由本轮成功工具结果返回，"
                "或位于当前用户的 download/file_upload"
            )
        descriptor = describe_local_asset(root, user, {"path": raw})
        # Preserve the original authorization rules of Web/message descriptors.
        matched = False
        for value in descriptors:
            value_path = Path(str(value.get("path") or ""))
            if not value_path.is_absolute():
                value_path = root / value_path
            if value_path.resolve() == path:
                _, verified = resolver.local_asset(str(value["asset_id"]))
                resolved.append(verified)
                matched = True
                break
        if not matched:
            checked = RunAssetResolver(root, user, [descriptor])
            _, verified = checked.local_asset(str(descriptor["asset_id"]))
            resolved.append(verified)
    return list({str(item["asset_id"]): item for item in resolved}.values())
