"""User-owned Web UI locale; never changes agent prompts or reply language."""

from __future__ import annotations

from typing import Any
import math

DEFAULT_WEB_LANGUAGE = "zh-CN"
WEB_LANGUAGES = frozenset(
    {"zh-CN", "en", "es", "fr", "de", "pt-BR", "ru", "ja", "ko"}
)
DEFAULT_WEB_BACKGROUND = {
    "enabled": False, "overlay_opacity": 0.65, "blur": 0,
    "ui_opacity": 0.94, "ui_blur": 0,
}


def web_language(
    config: dict[str, Any], *, strict: bool = False, allow_retired: bool = False
) -> str:
    section = config.get("web_ui", {})
    if not isinstance(section, dict):
        if strict:
            raise ValueError("web_ui 必须是对象")
        return DEFAULT_WEB_LANGUAGE
    value = section.get("language", DEFAULT_WEB_LANGUAGE)
    # Existing Arabic selections remain readable, but new writes must reject them.
    if allow_retired and value == "ar":
        return DEFAULT_WEB_LANGUAGE
    if not isinstance(value, str) or value not in WEB_LANGUAGES:
        if strict:
            raise ValueError("web_ui.language 必须是支持的语言代码：" + ", ".join(sorted(WEB_LANGUAGES)))
        return DEFAULT_WEB_LANGUAGE
    return value


def web_background(config: dict[str, Any], *, strict: bool = False) -> dict[str, Any]:
    """Validate presentation-only settings; never accept a path or remote URL."""
    section = config.get("web_ui", {})
    value = section.get("background", {}) if isinstance(section, dict) else None
    valid = isinstance(value, dict) and not (value.keys() - DEFAULT_WEB_BACKGROUND.keys())
    result = {**DEFAULT_WEB_BACKGROUND, **value} if isinstance(value, dict) else dict(DEFAULT_WEB_BACKGROUND)
    opacity, blur = result["overlay_opacity"], result["blur"]
    valid = valid and type(result["enabled"]) is bool
    valid = valid and type(opacity) in (int, float) and 0 <= opacity <= 1 and math.isfinite(opacity)
    valid = valid and type(blur) is int and 0 <= blur <= 20
    ui_opacity, ui_blur = result["ui_opacity"], result["ui_blur"]
    valid = valid and type(ui_opacity) in (int, float) and 0 <= ui_opacity <= 1 and math.isfinite(ui_opacity)
    valid = valid and type(ui_blur) is int and 0 <= ui_blur <= 30
    if not valid:
        if strict:
            raise ValueError("web_ui.background 仅允许 enabled 布尔值、overlay_opacity/ui_opacity 0～1、blur 整数 0～20、ui_blur 整数 0～30")
        return dict(DEFAULT_WEB_BACKGROUND)
    return result
