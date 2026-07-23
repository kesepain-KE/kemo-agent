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


ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default="", help="可选发布标签，例如 v0.1.0")
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
