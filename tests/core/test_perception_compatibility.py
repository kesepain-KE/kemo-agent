from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from run.prompt_sources import PromptSourceRegistry, _read_sense_meta


class PerceptionCompatibilityTests(unittest.TestCase):
    def make_module(
        self,
        root: Path,
        *,
        name: str = "sensor",
        create_start_update: bool = True,
    ) -> Path:
        module = root / "global_sense" / name
        module.mkdir(parents=True)
        (module / "sense.md").write_text("SENSOR_DATA", "utf-8")
        if create_start_update:
            (module / "data_update.py").write_text("def main():\n    return None\n", "utf-8")
        (module / "sense.json").write_text(
            json.dumps(
                {
                    "name": name,
                    "data_md": "sense.md",
                    "recent_update": "2026-08-06 12:00:00",
                    "health": "正常",
                    "start_update": "data_update.py",
                },
                ensure_ascii=False,
            ),
            "utf-8",
        )
        return module

    def test_perception_module_dir_unreadable_skips_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            perception_root = root / "global_sense"
            perception_root.mkdir()
            registry = PromptSourceRegistry(root, "alice")
            registry.add_perception(perception_root)

            with patch.object(Path, "iterdir", side_effect=PermissionError("denied")):
                selection = registry.select_perception(max_chars=1000)

            self.assertEqual(selection.text, "")
            diagnostics = registry.selection_diagnostics()["perception"]["global"]
            self.assertEqual(diagnostics["discovered"], [])
            self.assertEqual(diagnostics["scan_errors"], ["global_sense: 目录不可读"])

    def test_sense_meta_start_update_missing_marks_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            module = self.make_module(
                Path(temporary),
                create_start_update=False,
            )

            meta = _read_sense_meta(module)

            self.assertFalse(meta.valid)
            self.assertIn("start_update 文件不存在", meta.error)

    def test_sense_meta_data_md_resolve_oserror_marks_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            module = self.make_module(Path(temporary))

            with patch.object(Path, "resolve", side_effect=OSError("denied")):
                meta = _read_sense_meta(module)

            self.assertFalse(meta.valid)
            self.assertEqual(meta.error, "data_md 路径解析失败")

    def test_normal_sense_module_remains_valid_and_injectable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            module = self.make_module(root)
            meta = _read_sense_meta(module)
            self.assertTrue(meta.valid)

            registry = PromptSourceRegistry(root, "alice")
            registry.add_perception(root / "global_sense")
            selection = registry.select_perception(max_chars=1000)

            self.assertEqual(selection.text, "[sensor]\nSENSOR_DATA")
            diagnostics = registry.selection_diagnostics()["perception"]["global"]
            self.assertEqual(diagnostics["scan_errors"], [])


if __name__ == "__main__":
    unittest.main()
