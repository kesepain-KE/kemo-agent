"""文件操作工具 — 读写、编辑、搜索、校验和目录操作。kemo-agent 原生插件。"""

import fnmatch
import hashlib
import locale
import os
import re
import shutil
from pathlib import Path
from typing import Any

from plugins.file.text_editing import (
    DEFAULT_READ_MAX_BYTES as _DEFAULT_READ_MAX_BYTES,
    MAX_READ_BYTES_LIMIT as _MAX_READ_BYTES_LIMIT,
    atomic_write_bytes as _atomic_write_bytes,
    bounded_line_snapshot as _bounded_line_snapshot,
    byte_limit as _byte_limit,
    column as _column,
    convert_newlines as _convert_newlines,
    count_lines_fast as _count_lines_fast,
    dominant_newline as _dominant_newline,
    is_same_or_child as _is_same_or_child,
    line_entries as _line_entries,
    line_parts as _line_parts,
    newline_near as _newline_near,
    newline_style as _newline_style,
    newline_tokens as _newline_tokens,
    next_backup_path as _next_backup_path,
    normalize_newlines as _normalize_newlines,
    normalized_with_boundaries as _normalized_with_boundaries,
    preview_lines as _preview_lines,
    range_text as _range_text,
    read_preserving_format as _read_preserving_format,
    read_text as _read,
    read_with_encoding as _read_with_encoding,
    read_with_encoding_bytes as _read_with_encoding_bytes,
    resolve_path as _resolve_path,
    result as _result,
    strip_trailing_newlines as _strip_trailing_newlines,
    validate_expected_hash as _validate_expected_hash,
    validate_expected_text as _validate_expected_text,
)


_DEFAULT_SEARCH_FILE_MAX_BYTES = 52_428_800
_DEFAULT_DIR_PAGE_SIZE = 200
_MAX_DIR_PAGE_SIZE = 1000
_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
}
_BINARY_EXTENSIONS = {
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".bin",
    ".dat",
    ".db",
    ".sqlite",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".ico",
    ".webp",
    ".svgz",
    ".mp3",
    ".mp4",
    ".avi",
    ".mkv",
    ".mov",
    ".wmv",
    ".flv",
    ".webm",
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".xz",
    ".7z",
    ".rar",
    ".zst",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".ttf",
    ".otf",
    ".woff",
    ".woff2",
    ".eot",
    ".pyc",
    ".pyo",
    ".class",
    ".o",
    ".obj",
    ".a",
    ".lib",
    ".iso",
    ".img",
    ".dmg",
    ".vmdk",
    ".qcow2",
    ".pkl",
    ".pickle",
    ".npy",
    ".npz",
    ".parquet",
    ".avro",
}


# ── 读取 ──────────────────────────────────────────────────────────


def _run_read(
    path: str, encoding: str = "", max_bytes: int = 0, **_kw: Any
) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        if p.is_dir():
            raise IsADirectoryError(f"目标是目录，不能作为文件读取: {path}")
        raise FileNotFoundError(f"文件不存在: {path}")
    limit = _byte_limit(max_bytes)
    file_size = p.stat().st_size
    read_size = min(file_size, limit)
    with p.open("rb") as handle:
        raw = handle.read(read_size)
    snapshot_hash = hashlib.sha256(raw).hexdigest() if read_size == file_size else ""
    content, used_encoding = _read_with_encoding_bytes(
        raw,
        encoding or "utf-8",
        allow_incomplete_tail=file_size > limit,
    )
    content = _normalize_newlines(content)
    return _result(
        True,
        path=path,
        content=content,
        sha256=snapshot_hash,
        sha256_complete=read_size == file_size,
        size=file_size,
        read_bytes=len(raw),
        truncated=file_size > limit,
        encoding=used_encoding,
    )


def _run_read_range(
    path: str,
    start_line: int = 0,
    end_line: int = 0,
    tail: int = 0,
    max_lines: int = 500,
    encoding: str = "",
    max_bytes: int = 0,
    **_kw: Any,
) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        if p.is_dir():
            raise IsADirectoryError(f"目标是目录，不能作为文件读取: {path}")
        raise FileNotFoundError(f"文件不存在: {path}")
    limit = _byte_limit(max_bytes)
    file_size = p.stat().st_size
    requested_tail = max(0, int(tail))

    if requested_tail:
        read_size = min(file_size, limit)
        start_offset = file_size - read_size
        with p.open("rb") as handle:
            previous = b""
            if start_offset:
                handle.seek(start_offset - 1)
                previous = handle.read(1)
            handle.seek(start_offset)
            raw = handle.read(read_size)
        snapshot_hash = (
            hashlib.sha256(raw).hexdigest() if read_size == file_size else ""
        )
        if start_offset and previous not in (b"\n", b"\r"):
            newline_positions = [
                position
                for marker in (b"\n", b"\r")
                if (position := raw.find(marker)) >= 0
            ]
            raw = raw[min(newline_positions) + 1 :] if newline_positions else b""
        text, used_encoding = _read_with_encoding_bytes(raw, encoding or "utf-8")
        lines = text.splitlines()
        selected = lines[-min(requested_tail, len(lines)) :]
        total_lines, estimated = _count_lines_fast(p)
        selected_start = max(0, total_lines - len(selected))
        return _result(
            True,
            path=path,
            content=selected,
            lines=_line_entries(selected, selected_start),
            start_line=(selected_start + 1 if selected else 0),
            end_line=(selected_start + len(selected) if selected else 0),
            line_numbers_estimated=estimated,
            sha256=snapshot_hash,
            sha256_complete=read_size == file_size,
            total_lines=total_lines,
            total_lines_estimated=estimated,
            shown=len(selected),
            tail_mode=True,
            truncated=start_offset > 0 and len(lines) < requested_tail,
            encoding=used_encoding,
        )

    read_size = min(file_size, limit)
    with p.open("rb") as handle:
        raw = handle.read(read_size)
    snapshot_hash = hashlib.sha256(raw).hexdigest() if read_size == file_size else ""
    text, used_encoding = _read_with_encoding_bytes(
        raw,
        encoding or "utf-8",
        allow_incomplete_tail=file_size > limit,
    )
    lines = text.splitlines()
    truncated = file_size > limit
    total_lines, estimated = _count_lines_fast(p) if truncated else (len(lines), False)
    maximum_lines = min(max(1, int(max_lines)), 50_000)
    start = max(1, int(start_line or 1)) - 1
    end = (
        min(len(lines), int(end_line))
        if end_line and end_line > 0
        else min(len(lines), start + maximum_lines)
    )
    selected = lines[start:end]
    return _result(
        True,
        path=path,
        content=selected,
        lines=_line_entries(selected, start),
        start_line=(start + 1 if selected else 0),
        end_line=(start + len(selected) if selected else 0),
        line_numbers_estimated=False,
        sha256=snapshot_hash,
        sha256_complete=read_size == file_size,
        total_lines=total_lines,
        total_lines_estimated=estimated,
        shown=len(selected),
        tail_mode=False,
        truncated=truncated,
        encoding=used_encoding,
    )


# ── 写入 ──────────────────────────────────────────────────────────


def _run_write(
    path: str, content: str = "", encoding: str = "", **_kw: Any
) -> dict[str, Any]:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    requested_encoding = encoding or "utf-8"
    p.write_text(content, encoding=requested_encoding)
    written = p.read_bytes()
    snapshot_text, used_encoding = _read_with_encoding_bytes(
        written, requested_encoding, replace=False
    )
    lines, total_lines, snapshot_truncated = _bounded_line_snapshot(
        _normalize_newlines(snapshot_text)
    )
    return _result(
        True,
        action="write",
        path=path,
        size=len(written),
        sha256=hashlib.sha256(written).hexdigest(),
        sha256_complete=True,
        encoding=used_encoding,
        total_lines=total_lines,
        shown=len(lines),
        lines=lines,
        snapshot_truncated=snapshot_truncated,
    )


def _run_append(
    path: str, content: str = "", encoding: str = "", **_kw: Any
) -> dict[str, Any]:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding=encoding or "utf-8") as handle:
        handle.write(content)
    return _result(True, path=path, appended=len(content.encode(encoding or "utf-8")))


# ── 编辑 ──────────────────────────────────────────────────────────


def _run_edit(
    path: str,
    content: str | None = None,
    new_text: str | None = None,
    edit_mode: str = "replace_text",
    old_text: str = "",
    expected_old_text: str | None = None,
    expected_hash: str = "",
    expected_count: int = 1,
    line: int = 1,
    column: int = 1,
    end_line: int = 0,
    end_column: int = 0,
    create_backup: bool = True,
    encoding: str = "",
    **_kw: Any,
) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")
    if content is not None and new_text is not None and content != new_text:
        raise ValueError("content 与 new_text 同时提供时必须完全一致")
    delete_mode = edit_mode in {"delete_line", "delete_range"}
    if delete_mode and (content not in (None, "") or new_text not in (None, "")):
        raise ValueError(f"{edit_mode} 不接受 new_text/content；删除范围由行号决定")
    if not delete_mode and content is None and new_text is None:
        raise ValueError("edit 需要提供 new_text（旧版调用可继续使用 content）")
    replacement_text = (
        "" if delete_mode else (new_text if new_text is not None else content)
    )
    assert replacement_text is not None
    if edit_mode == "replace_range" and replacement_text == "":
        raise ValueError("replace_range 不接受空 new_text；删除整行请使用 delete_range")

    original, used_encoding, original_bytes = _read_preserving_format(
        p, encoding or "utf-8"
    )
    before_hash = hashlib.sha256(original_bytes).hexdigest()
    _validate_expected_hash(expected_hash, before_hash)
    original_lines = original.splitlines(keepends=True)
    total_lines = len(original_lines)
    dominant_ending = _dominant_newline(original)
    replacements = 1
    changed_lines = 0

    if edit_mode == "insert":
        idx = line - 1
        if idx < 0 or idx >= total_lines:
            raise ValueError(f"插入行号 {line} 超出范围 (共 {total_lines} 行)")
        body, ending = _line_parts(original_lines[idx])
        col = _column(body, column)
        if not str(expected_hash or "").strip():
            raise ValueError("insert 必须提供 read/read_range 返回的 expected_hash")
        inserted = _convert_newlines(replacement_text, ending or dominant_ending)
        original_lines[idx] = body[:col] + inserted + body[col:] + ending
        updated = "".join(original_lines)
        changed_lines = 1
    elif edit_mode == "replace_line":
        idx = line - 1
        if idx < 0 or idx >= total_lines:
            raise ValueError(f"行号 {line} 超出范围 (共 {total_lines} 行)")
        current_body, ending = _line_parts(original_lines[idx])
        _validate_expected_text(
            expected_old_text,
            current_body,
            label=f"replace_line 第 {line} 行",
        )
        body = _strip_trailing_newlines(replacement_text)
        body = _convert_newlines(body, ending or dominant_ending)
        original_lines[idx] = body + ending
        updated = "".join(original_lines)
        changed_lines = 1
    elif edit_mode == "replace_range":
        start_index = line - 1
        end_index = (end_line or line) - 1
        if (
            start_index < 0
            or start_index >= total_lines
            or end_index < start_index
            or end_index >= total_lines
        ):
            raise ValueError(
                f"替换行范围无效: {line}-{end_line or line} (共 {total_lines} 行)"
            )
        first, _ = _line_parts(original_lines[start_index])
        last, last_ending = _line_parts(original_lines[end_index])
        start_column = _column(first, column, label="起始列号")
        finish_column = _column(last, end_column or (len(last) + 1), label="结束列号")
        if start_index == end_index and finish_column < start_column:
            raise ValueError("结束列不能早于起始列")
        _validate_expected_text(
            expected_old_text,
            _range_text(
                original_lines,
                start_index,
                end_index,
                start_column,
                finish_column,
            ),
            label=f"replace_range 第 {line}-{end_line or line} 行",
        )
        body = _strip_trailing_newlines(replacement_text)
        body = _convert_newlines(body, last_ending or dominant_ending)
        replacement = first[:start_column] + body + last[finish_column:] + last_ending
        original_lines[start_index : end_index + 1] = [replacement]
        updated = "".join(original_lines)
        changed_lines = end_index - start_index + 1
    elif edit_mode in {"delete_line", "delete_range"}:
        start_index = line - 1
        if edit_mode == "delete_line" and end_line not in {0, line}:
            raise ValueError("delete_line 只能删除 line 指定的一行")
        end_index = (
            start_index if edit_mode == "delete_line" else (end_line or line) - 1
        )
        if (
            start_index < 0
            or start_index >= total_lines
            or end_index < start_index
            or end_index >= total_lines
        ):
            raise ValueError(
                f"删除行范围无效: {line}-{end_line or line} (共 {total_lines} 行)"
            )
        if column != 1 or end_column not in {0}:
            raise ValueError(f"{edit_mode} 只删除完整行，不接受 column/end_column")
        current = "\n".join(
            _line_parts(original_lines[index])[0]
            for index in range(start_index, end_index + 1)
        )
        _validate_expected_text(
            expected_old_text,
            current,
            label=f"{edit_mode} 第 {line}-{end_line or line} 行",
        )
        del original_lines[start_index : end_index + 1]
        updated = "".join(original_lines)
        changed_lines = end_index - start_index + 1
    elif edit_mode == "replace_text":
        if not old_text:
            raise ValueError("replace_text 模式需要 old_text")
        normalized, boundaries = _normalized_with_boundaries(original)
        normalized_old = _normalize_newlines(old_text)
        occurrences = normalized.count(normalized_old)
        if expected_count >= 0 and occurrences != expected_count:
            raise ValueError(f"期望匹配 {expected_count} 次，实际匹配 {occurrences} 次")
        matches: list[tuple[int, int]] = []
        cursor = 0
        while (
            normalized_old and (match := normalized.find(normalized_old, cursor)) >= 0
        ):
            matches.append((boundaries[match], boundaries[match + len(normalized_old)]))
            cursor = match + len(normalized_old)
        pieces: list[str] = []
        cursor = 0
        touched_lines: set[int] = set()
        for start, end in matches:
            pieces.append(original[cursor:start])
            ending = _newline_near(original, start, end, dominant_ending)
            pieces.append(_convert_newlines(replacement_text, ending))
            touched_lines.add(
                original.count("\n", 0, start)
                + original.count("\r", 0, start)
                - original.count("\r\n", 0, start)
                + 1
            )
            cursor = end
        pieces.append(original[cursor:])
        updated = "".join(pieces)
        replacements = occurrences
        changed_lines = len(touched_lines)
    else:
        raise ValueError(f"未知编辑模式: {edit_mode}")

    changed = updated != original
    if not changed:
        return _result(
            True,
            path=path,
            original_chars=len(original),
            new_chars=len(updated),
            mode=edit_mode,
            changed=False,
            replacements=0,
            changed_lines=0,
            newline_style=_newline_style(original),
            backup_created=False,
            backup_path="",
            before_hash=before_hash,
            after_hash=before_hash,
            preview=_preview_lines(original, line),
        )

    backup_path: Path | None = None
    if create_backup:
        backup_path = _next_backup_path(p)
        shutil.copy2(p, backup_path)
    updated_bytes = updated.encode(used_encoding)
    after_hash = hashlib.sha256(updated_bytes).hexdigest()
    _atomic_write_bytes(p, updated_bytes)
    return _result(
        True,
        path=path,
        original_chars=len(original),
        new_chars=len(updated),
        mode=edit_mode,
        changed=True,
        replacements=replacements,
        changed_lines=changed_lines,
        newline_style=_newline_style(updated),
        backup_created=create_backup,
        backup_path=str(backup_path) if backup_path is not None else "",
        before_hash=before_hash,
        after_hash=after_hash,
        preview=_preview_lines(updated, line),
    )


# ── 目录与元数据 ──────────────────────────────────────────────────


def _run_list_dir(
    path: str,
    offset: int = 0,
    limit: int = _DEFAULT_DIR_PAGE_SIZE,
    **_kw: Any,
) -> dict[str, Any]:
    p = Path(path)
    if not p.is_dir():
        raise NotADirectoryError(f"不是目录: {path}")
    entries: list[dict[str, Any]] = []
    for item in sorted(
        p.iterdir(), key=lambda value: (not value.is_dir(), str(value).casefold())
    ):
        entries.append(
            {
                "name": item.name,
                "type": "dir" if item.is_dir() else "file",
                "size": item.stat().st_size if item.is_file() else 0,
            }
        )
    page_offset = max(0, int(offset))
    page_limit = min(max(1, int(limit or _DEFAULT_DIR_PAGE_SIZE)), _MAX_DIR_PAGE_SIZE)
    total = len(entries)
    page = entries[page_offset : page_offset + page_limit]
    next_offset = page_offset + len(page)
    has_more = next_offset < total
    return _result(
        True,
        action="list_dir",
        path=path,
        total=total,
        count=total,
        returned=len(page),
        offset=page_offset,
        limit=page_limit,
        next_offset=next_offset if has_more else None,
        has_more=has_more,
        entries=page,
    )


def _run_tree_dir(
    path: str,
    max_depth: int = 2,
    max_entries: int = 200,
    offset: int = 0,
    limit: int = 0,
    include_hidden: bool = False,
    **_kw: Any,
) -> dict[str, Any]:
    p = Path(path)
    if not p.is_dir():
        raise NotADirectoryError(f"不是目录: {path}")
    page_limit = min(
        max(1, int(limit or max_entries or _DEFAULT_DIR_PAGE_SIZE)),
        _MAX_DIR_PAGE_SIZE,
    )
    page_offset = max(0, int(offset))
    max_depth = min(max(0, max_depth), 50)
    all_items: list[dict[str, Any]] = []
    for root, dirs, files in os.walk(str(p)):
        relative = Path(root).relative_to(p)
        depth = len(relative.parts) if relative != Path(".") else 0
        if depth > max_depth:
            dirs.clear()
            continue
        dirs[:] = sorted(
            directory
            for directory in dirs
            if include_hidden or not directory.startswith(".")
        )
        if not include_hidden:
            dirs[:] = [directory for directory in dirs if directory not in _SKIP_DIRS]
        prefix = "  " * depth + ("└─ " if depth > 0 else "")
        if relative != Path("."):
            all_items.append(
                {
                    "path": relative.as_posix(),
                    "name": relative.name,
                    "type": "dir",
                    "depth": depth,
                    "display": f"{prefix}{relative.name}/",
                }
            )
        for filename in sorted(
            filename
            for filename in files
            if include_hidden or not filename.startswith(".")
        ):
            relative_path = (
                (relative / filename) if relative != Path(".") else Path(filename)
            )
            all_items.append(
                {
                    "path": relative_path.as_posix(),
                    "name": filename,
                    "type": "file",
                    "depth": depth + 1,
                    "display": f"{'  ' * (depth + 1)}{filename}",
                }
            )
    total = len(all_items)
    items = all_items[page_offset : page_offset + page_limit]
    next_offset = page_offset + len(items)
    has_more = next_offset < total
    lines = [str(item["display"]) for item in items]
    if has_more:
        lines.append(f"…(还有 {total - next_offset} 项；下一页 offset={next_offset})")
    return _result(
        True,
        action="tree_dir",
        path=path,
        total=total,
        count=len(items),
        entries=len(items),
        returned=len(items),
        offset=page_offset,
        limit=page_limit,
        next_offset=next_offset if has_more else None,
        has_more=has_more,
        truncated=has_more,
        tree="\n".join(lines),
        items=items,
    )


def _run_exists(path: str, **_kw: Any) -> dict[str, Any]:
    p = Path(path)
    if p.exists():
        return _result(
            True, path=path, exists=True, type="dir" if p.is_dir() else "file"
        )
    return _result(True, path=path, exists=False, type=None)


def _run_stat(path: str, **_kw: Any) -> dict[str, Any]:
    p = Path(path)
    stat = p.stat()
    return _result(
        True,
        path=path,
        type="dir" if p.is_dir() else "file",
        size=stat.st_size,
        created=stat.st_ctime,
        modified=stat.st_mtime,
    )


# ── 搜索与校验 ────────────────────────────────────────────────────


def _search_result(
    path: str,
    query: str,
    results: list[dict[str, Any]],
    skipped_large: list[str],
    truncated: bool,
) -> dict[str, Any]:
    return _result(
        True,
        path=path,
        query=query,
        results=results,
        count=len(results),
        skipped_large=skipped_large,
        truncated=truncated,
    )


def _run_search(
    path: str,
    query: str = "",
    mode: str = "text",
    file_glob: str = "",
    max_results: int = 50,
    context_lines: int = 0,
    regex: bool = False,
    include_hidden: bool = False,
    max_file_bytes: int = 0,
    encoding: str = "",
    **_kw: Any,
) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"搜索路径不存在: {path}")
    base = p if p.is_dir() else p.parent
    max_results = min(max(1, max_results), 5000)
    context_lines = min(max(0, context_lines), 100)
    skipped_large: list[str] = []
    results: list[dict[str, Any]] = []
    walk_root = p if p.is_dir() else p.parent

    if mode in ("file", "name"):
        pattern = query.replace("*", ".*").replace("?", ".") if not regex else query
        try:
            compiled = re.compile(pattern, re.IGNORECASE)
        except re.error:
            compiled = re.compile(re.escape(query), re.IGNORECASE)
        for root, dirs, files in os.walk(str(walk_root)):
            if not include_hidden:
                dirs[:] = [
                    directory
                    for directory in dirs
                    if not directory.startswith(".") and directory not in _SKIP_DIRS
                ]
                files = [filename for filename in files if not filename.startswith(".")]
            for filename in files + dirs:
                candidate = Path(root) / filename
                if p.is_file() and candidate != p:
                    continue
                if file_glob and not fnmatch.fnmatch(filename, file_glob):
                    continue
                if compiled.search(filename):
                    results.append(
                        {
                            "path": str(candidate.relative_to(base)),
                            "type": "dir" if candidate.is_dir() else "file",
                        }
                    )
                    if len(results) >= max_results:
                        return _search_result(path, query, results, skipped_large, True)
        return _search_result(path, query, results, skipped_large, False)

    if mode not in ("text", "content", "code"):
        raise ValueError(f"未知搜索模式: {mode}")
    try:
        pattern = re.compile(query if regex else re.escape(query), re.IGNORECASE)
    except re.error as error:
        raise ValueError(f"搜索正则无效: {error}") from error
    search_limit = _byte_limit(max_file_bytes, _DEFAULT_SEARCH_FILE_MAX_BYTES)

    for root, dirs, files in os.walk(str(walk_root)):
        if not include_hidden:
            dirs[:] = [
                directory
                for directory in dirs
                if not directory.startswith(".") and directory not in _SKIP_DIRS
            ]
            files = [filename for filename in files if not filename.startswith(".")]
        for filename in sorted(files):
            candidate = Path(root) / filename
            if p.is_file() and candidate != p:
                continue
            if file_glob and not fnmatch.fnmatch(filename, file_glob):
                continue
            if candidate.suffix.casefold() in _BINARY_EXTENSIONS:
                continue
            relative = str(candidate.relative_to(base))
            try:
                if candidate.stat().st_size > search_limit:
                    skipped_large.append(relative)
                    continue
                raw = candidate.read_bytes()
                text, _ = _read_with_encoding_bytes(
                    raw, encoding or "utf-8", replace=False
                )
            except (OSError, ValueError):
                continue
            lines = text.splitlines()
            for line_number, line_text in enumerate(lines, 1):
                if not pattern.search(line_text):
                    continue
                entry: dict[str, Any] = {
                    "path": relative,
                    "line": line_number,
                    "text": line_text.strip(),
                }
                if context_lines:
                    context_start = max(0, line_number - 1 - context_lines)
                    context_end = min(len(lines), line_number + context_lines)
                    entry["context"] = lines[context_start:context_end]
                results.append(entry)
                if len(results) >= max_results:
                    return _search_result(path, query, results, skipped_large, True)
    return _search_result(path, query, results, skipped_large, False)


def _run_hash(path: str, algorithm: str = "sha256", **_kw: Any) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")
    normalized = algorithm.casefold().replace("-", "")
    if normalized not in {"md5", "sha1", "sha256"}:
        raise ValueError(f"不支持的哈希算法: {algorithm}，可选: md5, sha1, sha256")
    digest = hashlib.new(normalized)
    with p.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return _result(True, path=path, algorithm=normalized, hash=digest.hexdigest())


# ── 复制/移动 ─────────────────────────────────────────────────────


def _run_copy(
    path: str,
    dst_path: str = "",
    overwrite: bool = False,
    recursive: bool = False,
    **_kw: Any,
) -> dict[str, Any]:
    if not dst_path:
        raise ValueError("copy 需要 dst_path")
    source = Path(path)
    destination = Path(dst_path)
    if not source.exists():
        raise FileNotFoundError(f"源不存在: {path}")
    if destination == source:
        raise ValueError("源路径与目标路径不能相同")
    if source.is_dir() and _is_same_or_child(destination, source):
        raise ValueError("不能将目录复制到自身或自身子目录")
    if source.is_dir():
        if not recursive:
            raise IsADirectoryError(
                f"源是目录，如需递归复制请设置 recursive=true: {path}"
            )
        if destination.exists() and not overwrite:
            raise FileExistsError(f"目标已存在: {dst_path}")
        if destination.exists() and not destination.is_dir():
            raise NotADirectoryError(f"目标路径是文件但源是目录: {dst_path}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, dirs_exist_ok=overwrite)
        return _result(True, path=path, dst_path=dst_path, type="dir", recursive=True)
    if destination.exists() and not overwrite:
        raise FileExistsError(f"目标已存在: {dst_path}")
    if destination.is_dir():
        raise IsADirectoryError(f"目标路径是目录但源是文件: {dst_path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination, follow_symlinks=False)
    return _result(True, path=path, dst_path=dst_path, type="file", recursive=False)


def _run_move(
    path: str, dst_path: str = "", overwrite: bool = False, **_kw: Any
) -> dict[str, Any]:
    if not dst_path:
        raise ValueError("move 需要 dst_path")
    source = Path(path)
    destination = Path(dst_path)
    if not source.exists():
        raise FileNotFoundError(f"源不存在: {path}")
    if destination == source:
        raise ValueError("源路径与目标路径不能相同")
    source_is_dir = source.is_dir()
    if source_is_dir and _is_same_or_child(destination, source):
        raise ValueError("不能将目录移动到自身或自身子目录")
    if destination.exists() and not overwrite:
        raise FileExistsError(f"目标已存在: {dst_path}")
    if destination.exists() and destination.is_dir() != source_is_dir:
        source_type = "目录" if source_is_dir else "文件"
        destination_type = "目录" if destination.is_dir() else "文件"
        raise IsADirectoryError(
            f"源是{source_type}但目标是{destination_type}: {dst_path}"
        )
    if (
        source_is_dir
        and destination.exists()
        and _is_same_or_child(source, destination)
    ):
        raise ValueError("不能覆盖源目录的父目录")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.is_dir():
            shutil.rmtree(destination)
        else:
            destination.unlink()
    shutil.move(str(source), str(destination))
    return _result(
        True, path=path, dst_path=dst_path, type="dir" if source_is_dir else "file"
    )


# ── 创建/删除 ─────────────────────────────────────────────────────


def _run_make_dir(path: str, parents: bool = True, **_kw: Any) -> dict[str, Any]:
    p = Path(path)
    p.mkdir(parents=parents, exist_ok=True)
    return _result(True, path=path, created=True)


def _run_delete(path: str, **_kw: Any) -> dict[str, Any]:
    p = Path(path)
    if p.is_dir():
        raise IsADirectoryError(
            f"delete 只能删除文件，不可删除目录: {path}。如需删除目录，请使用 shell 工具执行对应的系统命令"
        )
    if not p.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")
    p.unlink()
    return _result(True, path=path, deleted=True)


# ── 分发 ──────────────────────────────────────────────────────────

_ACTIONS = {
    "exists": _run_exists,
    "read": _run_read,
    "read_range": _run_read_range,
    "write": _run_write,
    "append": _run_append,
    "edit": _run_edit,
    "list_dir": _run_list_dir,
    "tree_dir": _run_tree_dir,
    "stat": _run_stat,
    "search": _run_search,
    "hash": _run_hash,
    "copy": _run_copy,
    "move": _run_move,
    "make_dir": _run_make_dir,
    "delete": _run_delete,
}


def run(
    action: str, path: str, *, context: dict[str, Any], **kwargs: Any
) -> dict[str, Any]:
    handler = _ACTIONS.get(action)
    if handler is None:
        raise ValueError(f"未知 action: {action}，可选: {', '.join(sorted(_ACTIONS))}")
    root = Path(context.get("root") or Path.cwd()).resolve()
    try:
        resolved_path = _resolve_path(path, root)
        if action in {"copy", "move"} and kwargs.get("dst_path"):
            kwargs["dst_path"] = str(_resolve_path(str(kwargs["dst_path"]), root))
        return handler(path=str(resolved_path), **kwargs)
    except OSError as error:
        return _result(False, path=path, error=str(error), action=action)
