#!/usr/bin/env python3
"""Ensure every public project version agrees with version.json."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default="", help="可选发布标签，例如 v0.2.0")
    args = parser.parse_args(argv)

    errors: list[str] = []
    version_document = read_json(ROOT / "version.json")
    version = str(version_document.get("version") or "")
    if not SEMVER.fullmatch(version):
        errors.append(f"version.json 的根版本不是合法 SemVer：{version!r}")

    components = version_document.get("components")
    if not isinstance(components, dict) or not components:
        errors.append("version.json 缺少 components")
    else:
        for name, payload in components.items():
            component_version = str(payload.get("version") or "") if isinstance(payload, dict) else ""
            if not SEMVER.fullmatch(component_version):
                errors.append(f"组件 {name} 的版本不是合法 SemVer：{component_version!r}")

    compatibility = version_document.get("compatibility")
    gateway_version = ""
    version_match = SEMVER.fullmatch(version)
    requires_gateway_baseline = bool(
        version_match
        and tuple(int(value) for value in version_match.groups()[:3]) >= (1, 3, 0)
    )
    if compatibility is None:
        if requires_gateway_baseline:
            errors.append("version.json 缺少 compatibility.kemo-adapter-api")
    elif not isinstance(compatibility, dict):
        errors.append("version.json 的 compatibility 必须是对象")
    else:
        gateway_version = str(compatibility.get("kemo-adapter-api") or "").strip()
        if not gateway_version:
            errors.append("version.json compatibility 缺少 kemo-adapter-api")
        elif not SEMVER.fullmatch(gateway_version):
            errors.append(
                "version.json 的 kemo-adapter-api 兼容版本不是合法 SemVer："
                f"{gateway_version!r}"
            )

    frontend = read_json(ROOT / "web" / "frontend" / "package.json")
    if frontend.get("version") != version:
        errors.append(
            "web/frontend/package.json 版本不一致："
            f"{frontend.get('version')!r} != {version!r}"
        )

    lock = read_json(ROOT / "web" / "frontend" / "package-lock.json")
    lock_versions = {
        str(lock.get("version") or ""),
        str((lock.get("packages") or {}).get("", {}).get("version") or ""),
    }
    if lock_versions != {version}:
        errors.append(
            "web/frontend/package-lock.json 版本不一致："
            f"{sorted(lock_versions)!r} != {version!r}"
        )

    readme = (ROOT / "readme.md").read_text(encoding="utf-8")
    badge_version = version.replace("-", "--")
    if f"img.shields.io/badge/version-{badge_version}-" not in readme:
        errors.append(f"README 版本徽章未指向 {version}")
    if f"当前版本：`{version}`" not in readme:
        errors.append(f"README 当前版本文本未指向 {version}")

    readme_en = (ROOT / "README_EN.md").read_text(encoding="utf-8")
    if f"img.shields.io/badge/version-{badge_version}-" not in readme_en:
        errors.append(f"README_EN 版本徽章未指向 {version}")
    if f"Current version: `{version}`" not in readme_en:
        errors.append(f"README_EN 当前版本文本未指向 {version}")

    cli_text = (ROOT / "cli.py").read_text(encoding="utf-8")
    if f'VERSION = "{version}"' not in cli_text:
        errors.append(f"CLI 版本未指向 {version}")

    project_introduction = (
        ROOT / "global_knowledge" / "project-introduction.md"
    ).read_text(encoding="utf-8")
    if not any(
        f"当前{status}版本为 `{version}`" in project_introduction
        for status in ("稳定", "暂定")
    ):
        errors.append(f"项目介绍中的稳定或暂定版本未指向 {version}")

    version_guide = (
        ROOT / "global_knowledge" / "version-and-update-modules.md"
    ).read_text(encoding="utf-8")
    if f'"version": "{version}"' not in version_guide:
        errors.append(f"版本与更新模块文档未展示根版本 {version}")

    agents_manual = (ROOT / "agents.md").read_text(encoding="utf-8")
    manual_version = re.search(r"当前(?:稳定|暂定)版本：`kemo-agent ([0-9][^`]+)`", agents_manual)
    if not manual_version:
        errors.append("agents.md 运行手册缺少「当前稳定版本」或「当前暂定版本」标注")
    elif manual_version.group(1) != version:
        errors.append(
            "agents.md 运行手册稳定版本不一致："
            f"{manual_version.group(1)!r} != {version!r}"
        )
    else:
        summary_span = agents_manual[manual_version.end():]
        next_heading = summary_span.find("\n## ")
        summary = summary_span[: next_heading if next_heading >= 0 else len(summary_span)]
        highlights = {
            "1.3.1": "四渠道独立部署",
            "1.3.0": "长期智能、会话生命周期、模块面板与 Web 交互收敛",
            "1.2.8": "多用户 Web 工作区与运行可靠性",
            "1.2.7": "Chat 兼容传输链路",
        }
        marker = highlights.get(version)
        if marker and marker not in summary:
            errors.append(f"agents.md 运行手册版本摘要未包含 {version} 的重点「{marker}」")

    global_soul_path = ROOT / "config" / "global_soul.md"
    global_soul = (
        global_soul_path.read_text(encoding="utf-8")
        if global_soul_path.is_file()
        else ""
    )
    if global_soul and f"kemo-agent {version}" not in global_soul:
        errors.append(f"全局人格能力基线未指向 kemo-agent {version}")

    if gateway_version:
        gateway_markers = {
            "agents.md": agents_manual,
            "config/global_soul.md": global_soul,
            "global_knowledge/project-introduction.md": project_introduction,
            "global_knowledge/version-and-update-modules.md": version_guide,
            "readme.md": readme,
            "README_EN.md": readme_en,
        }
        optional_gateway_docs = (
            "global_knowledge/provider-reliability.md",
            "global_knowledge/builtin-expansions.md",
        )
        for relative in optional_gateway_docs:
            path = ROOT / relative
            if path.is_file():
                gateway_markers[relative] = path.read_text(encoding="utf-8")
        for label, text in gateway_markers.items():
            if gateway_version not in text:
                errors.append(
                    f"{label} 未声明配套 kemo-adapter-api {gateway_version}"
                )

    tag = args.tag.strip()
    if not tag and os.getenv("GITHUB_REF_TYPE") == "tag":
        tag = os.getenv("GITHUB_REF_NAME", "").strip()
    if tag and tag != f"v{version}":
        errors.append(f"发布标签 {tag!r} 与版本 v{version} 不一致")

    if errors:
        print("版本一致性检查失败：", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(f"版本一致性检查通过：{version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
