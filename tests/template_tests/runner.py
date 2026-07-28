"""Command-line entrypoint for Kemo module contract checks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


if __package__ in {None, ""}:
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from tests.template_tests.common import PROJECT_ROOT
from tests.template_tests.validators import SUPPORTED_KINDS, validate_template


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "验证智能体创建的 Kemo 子代理、拓展、外部消息、感知、技能或用户包；"
            "只约束框架入口/出口，不约束内部工程结构。"
        )
    )
    parser.add_argument("--target", required=True, type=Path, help="候选模块目录")
    parser.add_argument(
        "--kind",
        default="auto",
        choices=("auto", *SUPPORTED_KINDS),
        help="合同类型；默认根据根目录标记自动识别",
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=PROJECT_ROOT,
        help="kemo-agent 根目录，用于加载真实框架合同",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="单个子进程超时秒数")
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="标准输出格式",
    )
    parser.add_argument("--report", type=Path, help="可选：同时保存报告文件")
    parser.add_argument(
        "--template-mode",
        action="store_true",
        help="允许仓库参考模板的占位符，并将缺少的可选外部 SDK 标记为跳过",
    )
    parser.add_argument(
        "--static-only",
        action="store_true",
        help="只导入/检查入口，不调用候选的采集、操控或 executor",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = validate_template(
        args.kind,
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


if __name__ == "__main__":
    raise SystemExit(main())

