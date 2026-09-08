"""Encoding-safe, conflict-aware text editing primitives for the file plugin."""

from __future__ import annotations

import locale
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any


DEFAULT_READ_MAX_BYTES = 52_428_800
MAX_READ_BYTES_LIMIT = 536_870_912
WRITE_SNAPSHOT_MAX_LINES = 200
WRITE_SNAPSHOT_MAX_CHARS = 20_000


def encoding_candidates(encoding: str) -> tuple[str, ...]:
    encodings = ["utf-8", "utf-8-sig"]
    try:
        system_encoding = locale.getpreferredencoding(False)
    except Exception:
        system_encoding = ""
    if system_encoding and system_encoding.casefold().replace("-", "") != "utf8":
        encodings.append(system_encoding)
    return tuple(dict.fromkeys(value for value in (encoding, *encodings) if value))


def read_with_encoding_bytes(
    data: bytes,
    encoding: str,
    *,
    replace: bool = True,
    allow_incomplete_tail: bool = False,
) -> tuple[str, str]:
    for candidate in encoding_candidates(encoding):
        try:
            return data.decode(candidate), candidate
        except UnicodeDecodeError as error:
            tail_error = error.end == len(data) and error.start >= max(0, len(data) - 4)
            incomplete = (
                "end" in error.reason.casefold()
                or "incomplete" in error.reason.casefold()
            )
            if allow_incomplete_tail and tail_error and incomplete:
                try:
                    return data[: error.start].decode(candidate), candidate
                except (LookupError, UnicodeError):
                    pass
            continue
        except (LookupError, UnicodeError):
            continue
    if replace:
        return data.decode("utf-8", errors="replace"), "utf-8"
    raise ValueError("无法使用指定编码、UTF-8 或系统编码解码内容")


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_with_encoding(path: Path, encoding: str) -> tuple[str, str]:
    try:
        data = path.read_bytes()
    except IsADirectoryError:
        raise IsADirectoryError(f"目标是目录，不能作为文件读取: {path}") from None
    text, used_encoding = read_with_encoding_bytes(data, encoding, replace=False)
    return normalize_newlines(text), used_encoding


def read_preserving_format(path: Path, encoding: str) -> tuple[str, str, bytes]:
    try:
        data = path.read_bytes()
    except IsADirectoryError:
        raise IsADirectoryError(f"目标是目录，不能作为文件读取: {path}") from None
    text, used_encoding = read_with_encoding_bytes(data, encoding, replace=False)
    return text, used_encoding, data


def read_text(path: Path, encoding: str) -> str:
    return read_with_encoding(path, encoding)[0]


def byte_limit(value: int, default: int = DEFAULT_READ_MAX_BYTES) -> int:
    return max(1, min(int(value or default), MAX_READ_BYTES_LIMIT))


def count_lines_fast(path: Path, sample_size: int = 65_536) -> tuple[int, bool]:
    size = path.stat().st_size
    if size == 0:
        return 0, False
    with path.open("rb") as handle:
        sample = handle.read(min(size, sample_size))
    newline_count = len(re.findall(rb"\r\n|\r|\n", sample))
    if size <= sample_size:
        return newline_count + (0 if sample.endswith((b"\n", b"\r")) else 1), False
    if newline_count == 0:
        return 1, True
    return max(1, round(size * newline_count / len(sample))), True


def result(ok: bool, **fields: Any) -> dict[str, Any]:
    return {"ok": ok, **fields}


def resolve_path(path: str, root: Path) -> Path:
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path 不能为空")
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.resolve()


def is_same_or_child(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def line_parts(value: str) -> tuple[str, str]:
    for ending in ("\r\n", "\n", "\r"):
        if value.endswith(ending):
            return value[: -len(ending)], ending
    return value, ""


def column(value: str, column_number: int, *, label: str = "列号") -> int:
    requested = int(column_number)
    maximum = len(value) + 1
    if requested < 1 or requested > maximum:
        raise ValueError(f"{label} {requested} 超出范围 (有效范围 1-{maximum})")
    return requested - 1


def newline_tokens(text: str) -> list[str]:
    return re.findall(r"\r\n|\r|\n", text)


def newline_style(text: str) -> str:
    endings = newline_tokens(text)
    if not endings:
        return "none"
    unique = set(endings)
    if len(unique) > 1:
        return "mixed"
    return {"\r\n": "CRLF", "\n": "LF", "\r": "CR"}[endings[0]]


def dominant_newline(text: str) -> str:
    endings = newline_tokens(text)
    if not endings:
        return "\n"
    counts = {ending: endings.count(ending) for ending in ("\r\n", "\n", "\r")}
    return max(counts, key=counts.get)


def newline_near(text: str, start: int, end: int, fallback: str) -> str:
    within = newline_tokens(text[start:end])
    if within:
        return within[0]
    after = re.search(r"\r\n|\r|\n", text[end:])
    if after:
        return after.group(0)
    before = list(re.finditer(r"\r\n|\r|\n", text[:start]))
    return before[-1].group(0) if before else fallback


def convert_newlines(text: str, ending: str) -> str:
    return normalize_newlines(text).replace("\n", ending)


def strip_trailing_newlines(text: str) -> str:
    return re.sub(r"(?:\r\n|\r|\n)+\Z", "", text)


def normalized_with_boundaries(text: str) -> tuple[str, list[int]]:
    normalized: list[str] = []
    boundaries = [0]
    index = 0
    while index < len(text):
        if text.startswith("\r\n", index):
            normalized.append("\n")
            index += 2
        elif text[index] == "\r":
            normalized.append("\n")
            index += 1
        else:
            normalized.append(text[index])
            index += 1
        boundaries.append(index)
    return "".join(normalized), boundaries


def atomic_write_bytes(path: Path, data: bytes) -> None:
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_bytes(data)
        shutil.copymode(path, temp)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def next_backup_path(path: Path) -> Path:
    first = path.with_suffix(path.suffix + ".bak")
    if not first.exists():
        return first
    index = 1
    while True:
        candidate = path.with_suffix(path.suffix + f".bak.{index}")
        if not candidate.exists():
            return candidate
        index += 1


def line_entries(lines: list[str], start_index: int) -> list[dict[str, Any]]:
    return [
        {"line": start_index + offset + 1, "text": value}
        for offset, value in enumerate(lines)
    ]


def preview_lines(text: str, line: int, radius: int = 2) -> list[dict[str, Any]]:
    lines = text.splitlines()
    if not lines:
        return []
    start = max(0, min(len(lines) - 1, int(line) - 1) - max(0, radius))
    end = min(len(lines), max(start + 1, int(line) + max(0, radius)))
    return line_entries(lines[start:end], start)


def bounded_line_snapshot(text: str) -> tuple[list[dict[str, Any]], int, bool]:
    lines = text.splitlines()
    selected: list[str] = []
    used_chars = 0
    for value in lines[:WRITE_SNAPSHOT_MAX_LINES]:
        added = len(value) + (1 if selected else 0)
        if selected and used_chars + added > WRITE_SNAPSHOT_MAX_CHARS:
            break
        if not selected and len(value) > WRITE_SNAPSHOT_MAX_CHARS:
            selected.append(value[:WRITE_SNAPSHOT_MAX_CHARS])
            used_chars = WRITE_SNAPSHOT_MAX_CHARS
            break
        selected.append(value)
        used_chars += added
    return line_entries(selected, 0), len(lines), len(selected) < len(lines)


def validate_expected_hash(expected_hash: str, actual_hash: str) -> None:
    expected = str(expected_hash or "").strip().casefold()
    if not expected:
        return
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("expected_hash 必须是 64 位 SHA256 十六进制字符串")
    if expected != actual_hash:
        raise ValueError(
            "文件已在读取后发生变化，expected_hash 不匹配，拒绝写入；请重新读取"
        )


def validate_expected_text(expected: str | None, actual: str, *, label: str) -> None:
    if expected is None:
        raise ValueError(f"{label} 必须提供 expected_old_text，防止行号误判覆盖错误内容")
    normalized_expected = normalize_newlines(expected)
    normalized_actual = normalize_newlines(actual)
    if normalized_expected != normalized_actual:
        raise ValueError(
            f"{label} 的 expected_old_text 与当前目标区域不一致，拒绝写入；"
            f"预期 {len(normalized_expected)} 字符，实际 {len(normalized_actual)} 字符。"
            "若文件刚由 write 创建，可依据 write 返回的 lines/sha256 核对范围；"
            "否则请重新使用 read_range 获取带行号内容"
        )


def range_text(
    lines: list[str],
    start_index: int,
    end_index: int,
    start_column: int,
    finish_column: int,
) -> str:
    first, _ = line_parts(lines[start_index])
    last, _ = line_parts(lines[end_index])
    if start_index == end_index:
        return first[start_column:finish_column]
    parts = [first[start_column:]]
    parts.extend(
        line_parts(lines[index])[0] for index in range(start_index + 1, end_index)
    )
    parts.append(last[:finish_column])
    return "\n".join(parts)
