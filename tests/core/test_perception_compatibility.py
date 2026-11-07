from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from run.config import PromptSourceRegistry
from run.modules import read_manifest, ModuleSpecError


class PerceptionCompatibilityTests(unittest.TestCase):
    def make_module(
        self,
        root: Path,
        *,
        name: str = "sensor",
        create_start_update: bool = True,
    ) -> Path:
        module = root / "sense" / name
        module.mkdir(parents=True)
        (module / "sense.md").write_text("SENSOR_DATA", "utf-8")
        if create_start_update:
            (module / "data_update.py").write_text("def main():\n    return None\n", "utf-8")
        (module / "module.yaml").write_text(
            json.dumps(
                {
                    'name': name,
                    'input_file': 'sense.md',
                    'recent_update': '2026-08-06 12:00:00',
                    'health': '正常',
                    'update_script': 'data_update.py',
                    'kind': 'sense',
                    'explain': 'fixture perception',
                    'input_open': True,
                    'control_open': False,
                },
                ensure_ascii=False,
            ),
            "utf-8",
        )
        return module

    def test_perception_module_dir_unreadable_skips_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            perception_root = root / "sense"
            perception_root.mkdir()
            registry = PromptSourceRegistry(root, "alice")
            registry.add_perception(perception_root)

            with patch.object(Path, "iterdir", side_effect=PermissionError("denied")):
                selection = registry.select_perception(max_chars=1000)

            self.assertEqual(selection.text, "")
            diagnostics = registry.selection_diagnostics()["perception"]["global"]
            self.assertEqual(diagnostics["discovered"], [])
            self.assertEqual(diagnostics["scan_errors"], ["sense 目录不可读"])

    def test_sense_meta_start_update_missing_marks_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            module = self.make_module(
                Path(temporary),
                create_start_update=False,
            )

            with self.assertRaises(ModuleSpecError) as failure:
                read_manifest(module, kind_hint="sense", scope="global")
            self.assertEqual(failure.exception.error.reason, "file_missing")
            self.assertEqual(failure.exception.error.field, "update_script")

    def test_sense_meta_data_md_resolve_oserror_marks_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            module = self.make_module(Path(temporary))

            with patch.object(Path, "resolve", side_effect=OSError("denied")):
                with self.assertRaises(ModuleSpecError) as failure:
                    read_manifest(module, kind_hint="sense", scope="global")
            self.assertEqual(failure.exception.error.reason, "path_escape")

    def test_normal_sense_module_remains_valid_and_injectable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            module = self.make_module(root)
            meta = read_manifest(module, kind_hint="sense", scope="global")
            self.assertEqual(meta.kind, "sense")

            registry = PromptSourceRegistry(root, "alice")
            registry.add_perception(root / "sense")
            selection = registry.select_perception(max_chars=1000)

            self.assertTrue(selection.text.startswith("[sensor]\n"))
            self.assertIn("SENSOR_DATA", selection.text)
            self.assertIn("sense/sensor/sense.md", selection.text)
            diagnostics = registry.selection_diagnostics()["perception"]["global"]
            self.assertEqual(diagnostics["scan_errors"], [])


if __name__ == "__main__":
    unittest.main()
