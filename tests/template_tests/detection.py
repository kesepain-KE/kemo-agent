"""Marker-only kind detection, intentionally independent from validators."""

from __future__ import annotations

from pathlib import Path
import yaml

from tests.template_tests.common import ContractValidationError, ensure_plain_directory


SUPPORTED_KINDS = ("agent", "expand", "message", "sense", "skills", "user")


def detect_kind(target: Path) -> str:
    directory = ensure_plain_directory(target)
    markers: list[str] = []
    if (directory / "agent.json").is_file():
        markers.append("agent")
    manifest = directory / "module.yaml"
    if manifest.is_file():
        try:
            data = yaml.safe_load(manifest.read_text("utf-8-sig"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ContractValidationError("module.yaml 不可读或 YAML 无效") from exc
        if not isinstance(data, dict) or data.get("kind") not in {"sense", "expand"}:
            raise ContractValidationError("module.yaml 必须声明 kind: sense 或 expand")
        markers.append(data["kind"])
    if (directory / "message.json").is_file():
        markers.append("message")
    if (directory / "user_config.yaml").is_file() and (
        directory / "user_soul.md"
    ).is_file():
        markers.append("user")
    if not markers and any(
        path.is_file() and path.name.casefold() == "skill.md"
        for path in directory.rglob("*")
    ):
        markers.append("skills")
    if not markers:
        raise ContractValidationError(
            "无法识别模块类型；没有找到 agent.json、module.yaml、message.json、"
            "SKILL.md 或用户模板标记"
        )
    if len(markers) != 1:
        raise ContractValidationError(
            "目录同时命中多个模块合同，请明确指定类型：" + ", ".join(markers)
        )
    return markers[0]

