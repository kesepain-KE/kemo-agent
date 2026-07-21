"""获取当前 UTC、北京时间及可选 IANA 时区时间。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


BEIJING_TIMEZONE_NAME = "Asia/Shanghai"
BEIJING = ZoneInfo(BEIJING_TIMEZONE_NAME)
SUPPORTED_FORMATS = ("iso", "unix", "date", "time")


def _format_time(value: datetime, output_format: str) -> str:
    if output_format == "unix":
        return str(int(value.timestamp()))
    if output_format == "date":
        return value.strftime("%Y-%m-%d")
    if output_format == "time":
        return value.strftime("%H:%M:%S")
    return value.isoformat()


def _target_zone(value: str) -> tuple[str, ZoneInfo] | None:
    if not isinstance(value, str):
        raise ValueError("target_timezone 必须是 IANA 时区名字符串")
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return normalized, ZoneInfo(normalized)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(
            f"无效的 IANA 时区名: {normalized}。"
            "示例: Asia/Tokyo, America/New_York, Europe/London"
        ) from exc


def run(target_timezone: str = "", format: str = "iso") -> dict[str, Any]:
    if format not in SUPPORTED_FORMATS:
        raise ValueError(
            f"不支持的 format: {format}，可选: {', '.join(SUPPORTED_FORMATS)}"
        )
    target_zone = _target_zone(target_timezone)
    utc_now = datetime.now(timezone.utc)
    beijing_now = utc_now.astimezone(BEIJING)
    result: dict[str, Any] = {
        "utc": _format_time(utc_now, format),
        "local": _format_time(beijing_now, format),
        "iana_timezone": BEIJING_TIMEZONE_NAME,
        "utc_offset": beijing_now.strftime("%z"),
        "format": format,
    }
    if target_zone is not None:
        normalized_name, zone = target_zone
        target_now = utc_now.astimezone(zone)
        result.update(
            {
                "target": _format_time(target_now, format),
                "target_timezone": normalized_name,
                "target_offset": target_now.strftime("%z"),
            }
        )
    return result
