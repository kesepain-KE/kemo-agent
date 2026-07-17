"""批量翻译 kemo-agent Python 源码注释 → 中文。使用 tokenize 精确提取注释和 docstring。"""

from __future__ import annotations

import io
import re
import sys
import time
import tokenize
from pathlib import Path
from typing import Any

from deep_translator import GoogleTranslator

PROJECT_ROOT = Path(__file__).resolve().parent
EXCLUDE_DIRS = {"__pycache__", ".git", "node_modules", "开发临时目录", "tests"}
EXCLUDE_FILES = {"translate_comments.py", "update.py"}

# 短注释跳过（无需翻译的常见短模式）
SKIP_PATTERNS = re.compile(
    r"^(#\s*(TODO|FIXME|HACK|NOTE|XXX|noqa|no cover|type:\s*ignore)|"
    r"#\s*$|"
    r"#!/usr|# -\*-)"
)
SKIP_SHORT_LEN = 3  # ≤3 字符的注释跳过

translator = GoogleTranslator(source="auto", target="zh-CN")
total_translated = 0
total_skipped = 0


def _is_skip(comment: str) -> bool:
    if SKIP_PATTERNS.match(comment):
        return True
    stripped = comment.lstrip("#").strip()
    if len(stripped) <= SKIP_SHORT_LEN:
        return True
    # 已经是中文的跳过
    if re.search(r"[\u4e00-\u9fff]", stripped):
        return True
    return False


def translate_text(text: str) -> str:
    """翻译一段文本，失败返回原文。"""
    global total_translated, total_skipped
    if not text.strip():
        return text
    if _is_skip(text if text.startswith("#") else f"# {text}"):
        total_skipped += 1
        return text
    for attempt in range(3):
        try:
            result = translator.translate(text)
            if result and result != text:
                total_translated += 1
                return result
            total_skipped += 1
            return text
        except Exception as exc:
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
            else:
                print(f"  翻译失败 ({exc})，保留原文", file=sys.stderr)
                total_skipped += 1
                return text
    return text


def translate_docstring(indent: str, body: str) -> str:
    """翻译 docstring 主体内容，保留缩进和引号。"""
    stripped = body.strip()
    if not stripped:
        return body
    if re.search(r"[\u4e00-\u9fff]", stripped):
        return body  # 已是中文
    # 去掉首尾三引号
    if stripped.startswith('"""') or stripped.startswith("'''"):
        quote = stripped[:3]
        inner = stripped[3:]
        if inner.endswith(quote):
            inner = inner[:-3]
        translated = translate_text(inner.strip())
        return f'{indent}{quote}{translated}{quote}'
    translated = translate_text(stripped)
    return f"{indent}{translated}"


def process_file(filepath: Path) -> bool:
    """翻译单个 Python 文件的注释和 docstring。返回是否成功。"""
    try:
        source = filepath.read_text("utf-8")
    except Exception as exc:
        print(f"  读取失败: {exc}", file=sys.stderr)
        return False

    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except tokenize.TokenError as exc:
        print(f"  tokenize 失败: {exc}", file=sys.stderr)
        return False

    # 收集需要替换的注释和 docstring，按 (start, end) 排序
    replacements: list[tuple[int, int, str]] = []

    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            comment = tok.string
            if _is_skip(comment):
                continue
            translated = translate_text(comment.lstrip("#").strip())
            new_comment = f"# {translated}"
            # 保留原缩进
            col = tok.start[1]
            prefix = " " * col if col > 0 else ""
            replacements.append((tok.start, tok.end, f"{prefix}{new_comment}"))

        elif tok.type == tokenize.STRING and tok.start[0] > 0:
            # 只处理模块级/函数级 docstring（行首的字符串）
            line = tok.start[0]
            col = tok.start[1]
            # 检查是否是 docstring：行首，且上一行是 def/class/模块开头
            if col == 0:
                # 模块级 docstring 或类/函数后第一个字符串
                stripped = tok.string.strip()
                if (stripped.startswith('"""') or stripped.startswith("'''")) and not re.search(r"[\u4e00-\u9fff]", stripped):
                    # 翻译 docstring 内部内容
                    quote = stripped[:3]
                    inner = stripped[3:-3] if stripped.endswith(quote) else stripped[3:]
                    if inner.strip():
                        translated_inner = translate_text(inner.strip())
                        new_string = f'{quote}{translated_inner}{quote}'
                        replacements.append((tok.start, tok.end, new_string))

    if not replacements:
        return True  # 无变更

    # 应用替换（从后往前，避免偏移问题）
    lines = source.splitlines(keepends=True)
    for (start, end, new_text) in reversed(replacements):
        sl, sc = start  # (行号, 列号) 1-based
        el, ec = end
        sl_idx = sl - 1
        el_idx = el - 1

        if sl_idx == el_idx:
            # 同行替换
            line = lines[sl_idx]
            lines[sl_idx] = line[:sc] + new_text + line[ec:]
        else:
            # 跨行（docstring）
            lines[sl_idx] = lines[sl_idx][:sc] + new_text
            for i in range(sl_idx + 1, el_idx):
                lines[i] = ""
            if el_idx < len(lines):
                lines[el_idx] = lines[el_idx][ec:]

    new_source = "".join(lines)

    # 写回
    try:
        filepath.write_text(new_source, "utf-8")
        return True
    except Exception as exc:
        print(f"  写入失败: {exc}", file=sys.stderr)
        return False


def main() -> int:
    global total_translated, total_skipped

    py_files = sorted(
        p for p in PROJECT_ROOT.rglob("*.py")
        if not any(excl in p.parts for excl in EXCLUDE_DIRS)
        and p.name not in EXCLUDE_FILES
    )

    total = len(py_files)
    print(f"找到 {total} 个 Python 文件\n")

    failed: list[str] = []
    for idx, filepath in enumerate(py_files, 1):
        rel = filepath.relative_to(PROJECT_ROOT)
        print(f"[{idx}/{total}] {rel} ...", end=" ", flush=True)
        ok = process_file(filepath)
        if ok:
            print("✓")
        else:
            print("✗")
            failed.append(str(rel))
        time.sleep(0.3)  # 避免翻译服务限流

    print(f"\n完成：{total} 个文件")
    print(f"  翻译: {total_translated} 条")
    print(f"  跳过: {total_skipped} 条")
    if failed:
        print(f"  失败: {len(failed)} 个文件:")
        for f in failed:
            print(f"    - {f}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
