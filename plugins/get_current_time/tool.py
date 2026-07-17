from __future__ import annotations

from datetime import datetime, timezone


def run() -> dict[str, str]:
    utc = datetime.now(timezone.utc)
    local = utc.astimezone()
    return {
        "utc": utc.isoformat(),
        "local": local.isoformat(),
        "timezone": str(local.tzinfo),
        "utc_offset": local.strftime("%z"),
    }
