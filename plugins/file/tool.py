"""文件操作工具 — 读/写/列/删/编辑/搜索/移动。kemo-agent 原生插件。"""

from collections import deque
from datetime import datetime
import fnmatch
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

_READ_LIMIT_BYTES = 0
_SEARCH_FILE_LIMIT_BYTES = 0
_TREE_MAX_ENTRIES = 5000
_SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_TEXT_ENCODINGS = ("utf-8", "utf-8-sig", "gbk")


# ── 工具函数 ─────────────────────────────────────────────────────

def _read(path: Path, encoding: str) -> str:
    for enc in (encoding, *_TEXT_ENCODINGS) if encoding != "gbk" else (encoding,):
        try:
            return path.read_text(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ValueError(f"无法解码文件: {path}")


def _truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars] + "\n…(截断)", True


def _result(ok: bool, **fields: Any) -> dict[str, Any]:
    return {"ok": ok, **fields}


# ── 读取 ──────────────────────────────────────────────────────────

def _run_read(path: str, encoding: str, **_kw: Any) -> dict[str, Any]:
    content = _read(Path(path), encoding or "utf-8")
    return _result(True, path=path, content=content, size=len(content.encode("utf-8")))


def _run_read_range(path: str, start_line: int = 0, end_line: int = 0, tail: int = 0,
                    max_lines: int = 500, encoding: str = "", **_kw: Any) -> dict[str, Any]:
    content = _read(Path(path), encoding or "utf-8")
    lines = content.splitlines()
    total = len(lines)
    max_lines = min(max(1, max_lines), 50000)
    if tail and tail > 0:
        selected = lines[-min(tail, total):]
    else:
        start = max(1, start_line or 1) - 1
        end = min(total, end_line) if end_line and end_line > 0 else min(total, start + max_lines)
        selected = lines[start:end]
    return _result(True, path=path, lines=selected, total_lines=total, shown=len(selected))


# ── 写入 ──────────────────────────────────────────────────────────

def _run_write(path: str, content: str = "", encoding: str = "", **_kw: Any) -> dict[str, Any]:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding=encoding or "utf-8")
    return _result(True, path=path, size=len(content.encode("utf-8")))


def _run_append(path: str, content: str = "", encoding: str = "", **_kw: Any) -> dict[str, Any]:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding=encoding or "utf-8") as f:
        f.write(content)
    return _result(True, path=path, appended=len(content.encode("utf-8")))


# ── 编辑 ──────────────────────────────────────────────────────────

def _run_edit(path: str, content: str = "", edit_mode: str = "replace_text",
              old_text: str = "", expected_count: int = 1, line: int = 1, column: int = 1,
              end_line: int = 0, end_column: int = 0, create_backup: bool = True,
              encoding: str = "", **_kw: Any) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")
    original = _read(p, encoding or "utf-8")
    original_lines = original.splitlines(keepends=True)
    total_lines = len(original_lines)

    if create_backup:
        backup_path = p.with_suffix(p.suffix + ".bak")
        backup_path.write_text(original, encoding or "utf-8")

    if edit_mode == "insert":
        idx = max(1, line) - 1
        if idx > total_lines:
            raise ValueError(f"插入行号 {line} 超出范围 (共 {total_lines} 行)")
        col = max(1, column) - 1
        target = original_lines[idx]
        new_line = target[:col] + content + target[col:]
        original_lines[idx] = new_line
        new_text = "".join(original_lines)

    elif edit_mode == "replace_line":
        idx = max(1, line) - 1
        if idx >= total_lines:
            raise ValueError(f"行号 {line} 超出范围 (共 {total_lines} 行)")
        original_lines[idx] = content + ("\n" if original_lines[idx].endswith("\n") else "")
        new_text = "".join(original_lines)

    elif edit_mode == "replace_range":
        s_line = max(1, line) - 1
        e_line = max(s_line, (end_line or total_lines) - 1)
        if s_line >= total_lines:
            raise ValueError(f"起始行号 {line} 超出范围")
        before = original_lines[:s_line]
        after = original_lines[e_line + 1:]
        new_text = "".join(before + [content + ("\n" if after and after[0].endswith("\n") else "")] + after)

    elif edit_mode == "replace_text":
        count = expected_count if expected_count >= 1 else -1
        if count == 0:
            count = 1
        occurrences = original.count(old_text)
        if occurrences == 0:
            raise ValueError(f"未找到匹配文本")
        if count > 0 and occurrences != count:
            raise ValueError(f"期望匹配 {count} 次，实际匹配 {occurrences} 次")
        new_text = original.replace(old_text, content, count if count > 0 else -1)

    else:
        raise ValueError(f"未知编辑模式: {edit_mode}")

    p.write_text(new_text, encoding or "utf-8")
    return _result(True, path=path, original_chars=len(original), new_chars=len(new_text),
                   mode=edit_mode, backup_created=create_backup)


# ── 目录操作 ──────────────────────────────────────────────────────

def _run_list_dir(path: str, **_kw: Any) -> dict[str, Any]:
    p = Path(path)
    if not p.is_dir():
        raise NotADirectoryError(f"不是目录: {path}")
    entries = []
    for item in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), str(x).casefold())):
        entries.append({"name": item.name, "type": "dir" if item.is_dir() else "file",
                         "size": item.stat().st_size if item.is_file() else 0})
    return _result(True, path=path, entries=entries, count=len(entries))


def _run_tree_dir(path: str, max_depth: int = 2, max_entries: int = 200,
                  include_hidden: bool = False, **_kw: Any) -> dict[str, Any]:
    p = Path(path)
    if not p.is_dir():
        raise NotADirectoryError(f"不是目录: {path}")
    max_entries = min(max(1, max_entries), 1000)
    max_depth = min(max(1, max_depth), 50)
    lines: list[str] = []
    count = 0

    for root, dirs, files in os.walk(str(p)):
        rel = Path(root).relative_to(p)
        depth = len(rel.parts) if rel != Path(".") else 0
        if depth > max_depth:
            dirs.clear()
            continue
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") or include_hidden)
        if not include_hidden:
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        prefix = "  " * depth + ("└─ " if depth > 0 else "")
        if rel != Path("."):
            lines.append(f"{prefix}{rel.name}/")
            count += 1
        for f in sorted(f for f in files if not f.startswith(".") or include_hidden):
            if count >= max_entries:
                lines.append(f"{'  ' * (depth + 1)}…({count} 项)")
                return _result(True, path=path, tree="\n".join(lines), entries=count, truncated=True)
            lines.append(f"{'  ' * (depth + 1)}{f}")
            count += 1

    return _result(True, path=path, tree="\n".join(lines), entries=count, truncated=count >= max_entries)


def _run_stat(path: str, **_kw: Any) -> dict[str, Any]:
    p = Path(path)
    st = p.stat()
    return _result(True, path=path, type="dir" if p.is_dir() else "file",
                   size=st.st_size, created=st.st_ctime, modified=st.st_mtime)


# ── 搜索 ──────────────────────────────────────────────────────────

def _run_search(path: str, query: str = "", mode: str = "text", file_glob: str = "",
                max_results: int = 50, context_lines: int = 0, regex: bool = False,
                include_hidden: bool = False, **_kw: Any) -> dict[str, Any]:
    p = Path(path)
    base = p if p.is_dir() else p.parent
    max_results = min(max(1, max_results), 5000)
    context_lines = min(max(0, context_lines), 100)

    if mode in ("file", "name"):
        pattern = query.replace("*", ".*").replace("?", ".") if not regex else query
        flags = re.IGNORECASE
        try:
            compiled = re.compile(pattern, flags)
        except re.error:
            compiled = re.compile(re.escape(query), flags)
        results: list[dict[str, Any]] = []
        for root, dirs, files in os.walk(str(p)):
            if not include_hidden:
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for f in files + dirs:
                if file_glob and not fnmatch.fnmatch(f, file_glob):
                    continue
                if compiled.search(f):
                    fp = Path(root) / f
                    results.append({"path": str(fp.relative_to(p)), "type": "dir" if fp.is_dir() else "file"})
                    if len(results) >= max_results:
                        return _result(True, path=path, query=query, results=results, count=len(results), truncated=True)
        return _result(True, path=path, query=query, results=results, count=len(results))

    if mode in ("text", "content", "code"):
        pattern = re.compile(query if regex else re.escape(query), re.IGNORECASE)
    else:
        raise ValueError(f"未知搜索模式: {mode}")

    results: list[dict[str, Any]] = []
    for root, dirs, files in os.walk(str(p)):
        if not include_hidden:
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for f in sorted(files):
            if file_glob and not fnmatch.fnmatch(f, file_glob):
                continue
            fp = Path(root) / f
            suffix = fp.suffix.casefold()
            if suffix not in {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml",
                              ".cfg", ".ini", ".html", ".css", ".js", ".ts", ".tsx", ".jsx", ".env.example"}:
                continue
            try:
                lines = fp.read_text("utf-8").splitlines()
            except (OSError, UnicodeError):
                continue
            for lineno, line_text in enumerate(lines, 1):
                if pattern.search(line_text):
                    entry: dict[str, Any] = {"path": str(fp.relative_to(p)), "line": lineno,
                                               "text": line_text.strip()}
                    if context_lines:
                        ctx_start = max(0, lineno - 1 - context_lines)
                        ctx_end = min(len(lines), lineno + context_lines)
                        entry["context"] = lines[ctx_start:ctx_end]
                    results.append(entry)
                    if len(results) >= max_results:
                        return _result(True, path=path, query=query, results=results, count=len(results), truncated=True)
    return _result(True, path=path, query=query, results=results, count=len(results))


# ── 复制/移动 ─────────────────────────────────────────────────────

def _run_copy(path: str, dst_path: str = "", overwrite: bool = False, **_kw: Any) -> dict[str, Any]:
    src = Path(path)
    dst = Path(dst_path)
    if not src.is_file():
        raise FileNotFoundError(f"源文件不存在: {path}")
    if dst.exists() and not overwrite:
        raise FileExistsError(f"目标已存在: {dst_path}")
    shutil.copy2(src, dst, follow_symlinks=False)
    return _result(True, src=path, dst=dst_path)


def _run_move(path: str, dst_path: str = "", overwrite: bool = False, **_kw: Any) -> dict[str, Any]:
    src = Path(path)
    dst = Path(dst_path)
    if not src.exists():
        raise FileNotFoundError(f"源不存在: {path}")
    if dst.exists() and not overwrite:
        raise FileExistsError(f"目标已存在: {dst_path}")
    shutil.move(str(src), str(dst))
    return _result(True, src=path, dst=dst_path)


# ── 创建/删除 ─────────────────────────────────────────────────────

def _run_make_dir(path: str, parents: bool = True, **_kw: Any) -> dict[str, Any]:
    p = Path(path)
    p.mkdir(parents=parents, exist_ok=True)
    return _result(True, path=path, created=True)


def _run_delete(path: str, **_kw: Any) -> dict[str, Any]:
    p = Path(path)
    if p.is_dir():
        raise IsADirectoryError(f"delete 只能删除文件，不可删除目录: {path}")
    if not p.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")
    p.unlink()
    return _result(True, path=path, deleted=True)


# ── 分发 ──────────────────────────────────────────────────────────

_ACTIONS = {
    "read": _run_read,
    "read_range": _run_read_range,
    "write": _run_write,
    "append": _run_append,
    "edit": _run_edit,
    "list_dir": _run_list_dir,
    "tree_dir": _run_tree_dir,
    "stat": _run_stat,
    "search": _run_search,
    "copy": _run_copy,
    "move": _run_move,
    "make_dir": _run_make_dir,
    "delete": _run_delete,
}


def run(action: str, path: str, *, context: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    handler = _ACTIONS.get(action)
    if handler is None:
        raise ValueError(f"未知 action: {action}，可选: {', '.join(sorted(_ACTIONS))}")
    try:
        return handler(path=path, **kwargs)
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError, FileExistsError) as e:
        return _result(False, path=path, error=str(e), action=action)
