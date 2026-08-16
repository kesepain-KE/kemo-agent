from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _load_template(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("relative", "manifest_name", "health_field"),
    [
        ("template/sense/data_update.py", "sense.json", "health"),
        ("template/expand/data_update.py", "expand.json", "input_health"),
    ],
)
def test_manifest_recent_update_never_moves_backwards(
    relative: str,
    manifest_name: str,
    health_field: str,
) -> None:
    module = _load_template(ROOT / relative, f"checkpoint_{manifest_name}")
    with tempfile.TemporaryDirectory() as directory:
        manifest = Path(directory) / manifest_name
        manifest.write_text(
            json.dumps(
                {health_field: "正常", "recent_update": "2026-08-16 12:03:00"},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        module.MANIFEST_PATH = manifest

        module.write_manifest_health(
            healthy=True,
            update_time="2026-08-16 12:00:00",
        )

        stored = json.loads(manifest.read_text("utf-8"))
        assert stored["recent_update"] == "2026-08-16 12:03:00"
