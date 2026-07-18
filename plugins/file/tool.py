"""文件操作工具 — 读/写/列/删/编辑/搜索/移动。kemo-agent 原生插件。"""

import fnmatch
import os
import re
import shutil
from pathlib import Path
from typing import Any

_READ_LIMIT_BYTES = 0
_SEARCH_FILE_LIMIT_BYTES = 0
_TREE_MAX_ENTRIES = 5000
_SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}
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


def _resolve_path(path: str, root: Path) -> Path:
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path 不能为空")
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.resolve()


def _line_parts(value: str) -> tuple[str, str]:
    for ending in ("\r\n", "\n", "\r"):
        if value.endswith(ending):
            return value[:-len(ending)], ending
    return value, ""


def _column(value: str, column: int) -> int:
    return max(1, min(int(column), len(value) + 1)) - 1


# ── 读取 ──────────────────────────────────────────────────────────

def _run_read(path: str, encoding: str = "", **_kw: Any) -> dict[str, Any]:
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

    if edit_mode == "insert":
        idx = line - 1
        if idx < 0 or idx >= total_lines:
            raise ValueError(f"插入行号 {line} 超出范围 (共 {total_lines} 行)")
        body, ending = _line_parts(original_lines[idx])
        col = _column(body, column)
        original_lines[idx] = body[:col] + content + body[col:] + ending
        new_text = "".join(original_lines)

    elif edit_mode == "replace_line":
        idx = line - 1
        if idx < 0 or idx >= total_lines:
            raise ValueError(f"行号 {line} 超出范围 (共 {total_lines} 行)")
        _, ending = _line_parts(original_lines[idx])
        original_lines[idx] = content + ending
        new_text = "".join(original_lines)

    elif edit_mode == "replace_range":
        s_line = line - 1
        e_line = (end_line or line) - 1
        if s_line < 0 or s_line >= total_lines or e_line < s_line or e_line >= total_lines:
            raise ValueError(f"替换行范围无效: {line}-{end_line or line} (共 {total_lines} 行)")
        first, _ = _line_parts(original_lines[s_line])
        last, last_ending = _line_parts(original_lines[e_line])
        start_col = _column(first, column)
        finish_col = _column(last, end_column or (len(last) + 1))
        if s_line == e_line and finish_col < start_col:
            raise ValueError("结束列不能早于起始列")
        replacement = first[:start_col] + content + last[finish_col:] + last_ending
        original_lines[s_line:e_line + 1] = [replacement]
        new_text = "".join(original_lines)

    elif edit_mode == "replace_text":
        if not old_text:
            raise ValueError("replace_text 模式需要 old_text")
        occurrences = original.count(old_text)
        if expected_count >= 0 and occurrences != expected_count:
            raise ValueError(f"期望匹配 {expected_count} 次，实际匹配 {occurrences} 次")
        new_text = original.replace(old_text, content)

    else:
        raise ValueError(f"未知编辑模式: {edit_mode}")

    if create_backup:
        shutil.copy2(p, p.with_suffix(p.suffix + ".bak"))
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
    max_depth = min(max(0, max_depth), 50)
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
        if rel != Path(".") and count < max_entries:
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
    if not p.exists():
        raise FileNotFoundError(f"搜索路径不存在: {path}")
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
        walk_root = p if p.is_dir() else p.parent
        for root, dirs, files in os.walk(str(walk_root)):
            if not include_hidden:
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for f in files + dirs:
                fp = Path(root) / f
                if p.is_file() and fp != p:
                    continue
                if file_glob and not fnmatch.fnmatch(f, file_glob):
                    continue
                if compiled.search(f):
                    results.append({"path": str(fp.relative_to(base)), "type": "dir" if fp.is_dir() else "file"})
                    if len(results) >= max_results:
                        return _result(True, path=path, query=query, results=results, count=len(results), truncated=True)
        return _result(True, path=path, query=query, results=results, count=len(results))

    if mode in ("text", "content", "code"):
        pattern = re.compile(query if regex else re.escape(query), re.IGNORECASE)
    else:
        raise ValueError(f"未知搜索模式: {mode}")

    results: list[dict[str, Any]] = []
    walk_root = p if p.is_dir() else p.parent
    for root, dirs, files in os.walk(str(walk_root)):
        if not include_hidden:
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for f in sorted(files):
            fp = Path(root) / f
            if p.is_file() and fp != p:
                continue
            if file_glob and not fnmatch.fnmatch(f, file_glob):
                continue
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
                    entry: dict[str, Any] = {"path": str(fp.relative_to(base)), "line": lineno,
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
    if not dst_path:
        raise ValueError("copy 需要 dst_path")
    src = Path(path)
    dst = Path(dst_path)
    if not src.is_file():
        raise FileNotFoundError(f"源文件不存在: {path}")
    if dst.exists() and not overwrite:
        raise FileExistsError(f"目标已存在: {dst_path}")
    if dst.is_dir():
        raise IsADirectoryError(f"目标路径必须是文件: {dst_path}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst, follow_symlinks=False)
    return _result(True, src=path, dst=dst_path)


def _run_move(path: str, dst_path: str = "", overwrite: bool = False, **_kw: Any) -> dict[str, Any]:
    if not dst_path:
        raise ValueError("move 需要 dst_path")
    src = Path(path)
    dst = Path(dst_path)
    if not src.exists():
        raise FileNotFoundError(f"源不存在: {path}")
    if dst.exists() and not overwrite:
        raise FileExistsError(f"目标已存在: {dst_path}")
    if dst.is_dir():
        raise IsADirectoryError(f"目标路径必须是文件: {dst_path}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
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
    root = Path(context.get("root") or Path.cwd()).resolve()
    requested_path = path
    try:
        resolved_path = _resolve_path(path, root)
        if action in {"copy", "move"} and kwargs.get("dst_path"):
            kwargs["dst_path"] = str(_resolve_path(str(kwargs["dst_path"]), root))
        result = handler(path=str(resolved_path), **kwargs)
        result.setdefault("requested_path", requested_path)
        return result
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError, FileExistsError) as e:
        return _result(False, path=path, error=str(e), action=action)
