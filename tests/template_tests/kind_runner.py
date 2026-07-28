"""Reusable CLI shell for one independently callable kind package."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable

from tests.template_tests.common import PROJECT_ROOT
from tests.template_tests.contracts import ContractReport


Validator = Callable[..., ContractReport]


def main_for_kind(
    kind: str,
    validator: Validator,
    argv: list[str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description=f"验证一个 {kind} 候选包的框架入口/出口合同。"
    )
    parser.add_argument("--target", required=True, type=Path, help="候选模块目录")
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=PROJECT_ROOT,
        help="kemo-agent 根目录",
    )
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--template-mode", action="store_true")
    parser.add_argument("--static-only", action="store_true")
    args = parser.parse_args(argv)
    report = validator(
        args.target,
        repository_root=args.repository_root,
        timeout=args.timeout,
        template_mode=args.template_mode,
        runtime_probe=not args.static_only,
    )
    rendered = (
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
        if args.format == "json"
        else report.render_text()
    )
    print(rendered)
    if args.report is not None:
        destination = args.report.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered + "\n", "utf-8")
    return 0 if report.ok else 1

