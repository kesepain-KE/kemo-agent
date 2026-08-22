from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from run.extensions import record_module_health


def test_healthy_module_manifest_is_not_rewritten_before_checkpoint(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "expand.json"
    manifest.write_text(
        json.dumps(
            {
                "name": "demo",
                "input_health": "正常",
                "recent_update": "2026-08-16 12:00:00",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        "utf-8",
    )
    before = (manifest.stat().st_mtime_ns, manifest.read_bytes())
    with patch("run.extensions.module_runtime.datetime") as clock:
        clock.now.return_value = __import__("datetime").datetime(
            2026,
            8,
            16,
            12,
            4,
            59,
            tzinfo=__import__("zoneinfo").ZoneInfo("Asia/Shanghai"),
        )
        clock.strptime.side_effect = __import__("datetime").datetime.strptime
        record_module_health(manifest, "expand", healthy=True)
    assert (manifest.stat().st_mtime_ns, manifest.read_bytes()) == before


def test_module_health_transition_is_persisted_immediately(tmp_path: Path) -> None:
    manifest = tmp_path / "sense.json"
    manifest.write_text(
        json.dumps(
            {"name": "demo", "health": "正常", "recent_update": "2026-08-16 12:00:00"},
            ensure_ascii=False,
        ),
        "utf-8",
    )
    record_module_health(manifest, "sense", healthy=False)
    assert json.loads(manifest.read_text("utf-8"))["health"] == "异常"
