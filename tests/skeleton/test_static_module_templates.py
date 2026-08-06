from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from cron.executor import _run_module_updater


ROOT = Path(__file__).resolve().parents[2]


class StaticModuleTemplateTests(unittest.TestCase):
    def _run_template(self, category: str) -> tuple[dict, dict]:
        with tempfile.TemporaryDirectory() as temporary:
            module = Path(temporary) / category
            shutil.copytree(ROOT / "template" / category, module)
            result = _run_module_updater(
                module / "data_update.py",
                module,
                timeout=5,
            )
            manifest_name = "sense.json" if category == "sense" else "expand.json"
            manifest = json.loads((module / manifest_name).read_text("utf-8"))
            return result, manifest

    def test_sense_template_has_a_runnable_zero_argument_entry(self) -> None:
        result, manifest = self._run_template("sense")
        self.assertTrue(result["ok"], result)
        self.assertEqual(manifest["health"], "正常")
        self.assertNotEqual(manifest["recent_update"], "2000-01-01 00:00:00")

    def test_expand_template_has_a_runnable_zero_argument_entry(self) -> None:
        result, manifest = self._run_template("expand")
        self.assertTrue(result["ok"], result)
        self.assertEqual(manifest["input_health"], "正常")
        self.assertTrue(manifest["recent_update"])


if __name__ == "__main__":
    unittest.main()
