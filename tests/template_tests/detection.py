"""Marker-only kind detection, intentionally independent from validators."""

from __future__ import annotations

from pathlib import Path

from tests.template_tests.common import ContractValidationError, ensure_plain_directory


SUPPORTED_KINDS = ("agent", "expand", "message", "sense", "skills", "user")


def detect_kind(target: Path) -> str:
    directory = ensure_plain_directory(target)
    markers: list[str] = []
    if (directory / "agent.json").is_file():
        markers.append("agent")
    if (directory / "expand.json").is_file():
        markers.append("expand")
    if (directory / "message.json").is_file():
        markers.append("message")
    if (directory / "sense.json").is_file():
        markers.append("sense")
    if (directory / "user_config.json").is_file() and (
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
            "无法识别模块类型；没有找到 agent.json、expand.json、message.json、"
            "sense.json、SKILL.md 或用户模板标记"
        )
    if len(markers) != 1:
        raise ContractValidationError(
            "目录同时命中多个模块合同，请明确指定类型：" + ", ".join(markers)
        )
    return markers[0]

