"""Small Markdown structure scanner shared by skill readers and validators."""

from __future__ import annotations

import re
from dataclasses import dataclass


_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})([^\r\n]*)$")
_ATX_HEADING_RE = re.compile(
    r"^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$"
)


@dataclass(frozen=True, slots=True)
class MarkdownHeading:
    line: int
    level: int
    text: str


@dataclass(frozen=True, slots=True)
class MarkdownFence:
    start_line: int
    end_line: int
    info: str
    content: str
    closed: bool


@dataclass(frozen=True, slots=True)
class MarkdownStructure:
    headings: tuple[MarkdownHeading, ...]
    fences: tuple[MarkdownFence, ...]
    code_lines: frozenset[int]


def scan_markdown_structure(text: str) -> MarkdownStructure:
    """Return real headings/fences while ignoring fenced and indented code.

    Line numbers are zero-based so callers can slice ``str.splitlines()``
    without conversions.  This is intentionally a bounded structural scanner,
    not a renderer; it implements only the Markdown pieces used by SKILL.md.
    """

    lines = text.splitlines()
    headings: list[MarkdownHeading] = []
    fences: list[MarkdownFence] = []
    code_lines: set[int] = set()
    fence_marker = ""
    fence_length = 0
    fence_info = ""
    fence_start = -1
    fence_content: list[str] = []
    html_comment = False

    for index, line in enumerate(lines):
        if fence_marker:
            code_lines.add(index)
            stripped = line.lstrip(" ")
            indent = len(line) - len(stripped)
            if (
                indent <= 3
                and stripped.startswith(fence_marker * fence_length)
                and not stripped.rstrip().strip(fence_marker)
            ):
                fences.append(
                    MarkdownFence(
                        start_line=fence_start,
                        end_line=index,
                        info=fence_info,
                        content="\n".join(fence_content),
                        closed=True,
                    )
                )
                fence_marker = ""
                fence_content = []
            else:
                fence_content.append(line)
            continue

        if html_comment:
            code_lines.add(index)
            if "-->" in line:
                html_comment = False
            continue

        if line.startswith("\t") or line.startswith("    "):
            code_lines.add(index)
            continue

        stripped = line.lstrip(" ")
        if len(line) - len(stripped) <= 3 and stripped.startswith("<!--"):
            code_lines.add(index)
            if "-->" not in stripped[4:]:
                html_comment = True
            continue

        opened = _FENCE_OPEN_RE.fullmatch(line)
        if opened:
            marker = opened.group(1)
            if marker[0] == "`" and "`" in opened.group(2):
                continue
            fence_marker = marker[0]
            fence_length = len(marker)
            fence_info = opened.group(2).strip()
            fence_start = index
            fence_content = []
            code_lines.add(index)
            continue

        matched = _ATX_HEADING_RE.fullmatch(line)
        if matched:
            title = matched.group(2).strip()
            if title:
                headings.append(
                    MarkdownHeading(index, len(matched.group(1)), title)
                )

    if fence_marker:
        fences.append(
            MarkdownFence(
                start_line=fence_start,
                end_line=len(lines),
                info=fence_info,
                content="\n".join(fence_content),
                closed=False,
            )
        )

    return MarkdownStructure(
        headings=tuple(headings),
        fences=tuple(fences),
        code_lines=frozenset(code_lines),
    )
