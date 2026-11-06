"""Shared SQLite simple extension loading; runtime access is entirely offline."""
from __future__ import annotations

import ctypes
from functools import lru_cache
from pathlib import Path
import platform
import sqlite3
import struct
import threading


NATIVE_ROOT = Path(__file__).with_name("sqlite_simple")
_LOAD_LOCK = threading.Lock()
_DICTIONARY_CONFIGURED = False


@lru_cache(maxsize=1)
def _library() -> ctypes.CDLL:
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64", "i386": "x86", "i686": "x86"}.get(machine, machine)
    if system == "windows" and struct.calcsize("P") == 4:
        arch = "x86"
    suffix = {"windows": "dll", "linux": "so", "darwin": "dylib"}.get(system)
    path = NATIVE_ROOT / f"{system}-{arch}" / f"libsimple.{suffix}"
    if not path.is_file():
        raise RuntimeError(f"缺少 simple 扩展或平台不受支持：{system}-{arch}；请运行 python deploy/vendor_simple.py")
    for name in ("jieba.dict", "hmm_model", "user.dict", "idf", "stop_words"):
        if not (NATIVE_ROOT / "dict" / f"{name}.utf8").is_file():
            raise RuntimeError("simple 分词词典不完整；请运行 python deploy/vendor_simple.py")
    # Retain the module so its dictionary cache survives short SQLite connections.
    try:
        return ctypes.CDLL(str(path))
    except OSError as exc:
        raise RuntimeError(
            "simple 动态库加载失败；Windows 需要匹配的 VC++ 运行库，Linux 需要兼容的 glibc/libstdc++6。"
            f"详见 run/infra/sqlite_simple/README.md：{exc}"
        ) from exc


def _dictionary_path() -> str | bytes:
    path = str(NATIVE_ROOT / "dict")
    if platform.system() != "Windows":
        return path
    # Upstream cppjieba opens narrow paths with the Windows ANSI code page.
    # A SQLite BLOB preserves those bytes instead of re-encoding them as UTF-8.
    short_path = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
    short_path.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    short_path.restype = ctypes.c_uint32
    buffer = ctypes.create_unicode_buffer(32768)
    size = short_path(path, buffer, len(buffer))
    candidates = (buffer.value, path) if 0 < size < len(buffer) else (path,)
    for candidate in candidates:
        try:
            encoded = candidate.encode("mbcs")
        except UnicodeEncodeError:
            continue
        if encoded.decode("mbcs") == candidate:
            return encoded
    raise RuntimeError(
        "simple 词典路径无法表示为 Windows 本地文件路径，且无可用短路径；"
        "请将安装目录移至当前系统代码页支持的路径。已阻止调用原生分词器，避免进程崩溃。"
    )


def load_simple(database: sqlite3.Connection) -> None:
    global _DICTIONARY_CONFIGURED
    with _LOAD_LOCK:
        library = _library()
        database.enable_load_extension(True)
        try:
            database.load_extension(str(library._name))
        finally:
            database.enable_load_extension(False)
        # The upstream dictionary path is process-global, not connection-local.
        if not _DICTIONARY_CONFIGURED:
            database.execute("SELECT jieba_dict(?)", (_dictionary_path(),))
            _DICTIONARY_CONFIGURED = True


def compile_fulltext(database: sqlite3.Connection, query: str) -> str:
    if "\0" in query or len(query) > 1000:
        raise ValueError("fulltext 查询不能包含 NUL，且最多 1000 个字符")
    # Keep English keywords literal instead of expanding them into Chinese pinyin.
    return database.execute("SELECT jieba_query(?,0)", (query,)).fetchone()[0]


def match_positions(content: str, positions: str | None) -> list[tuple[int, int]]:
    """Translate simple's UTF-8 byte ranges into Python character spans."""
    raw = content.encode("utf-8")
    spans = []
    for pair in (positions or "").split(";"):
        if pair:
            start, end = map(int, pair.split(","))
            spans.append((len(raw[:start].decode("utf-8")), len(raw[:end].decode("utf-8"))))
    return spans
