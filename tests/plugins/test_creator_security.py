from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from plugins._creator_security import is_link, is_within, reject_link_components


class CreatorSecurityTests(unittest.TestCase):
    def test_containment_check_does_not_accept_siblings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / "modules"
            child = parent / "item"
            sibling = root / "modules-other"

            self.assertTrue(is_within(child, parent))
            self.assertFalse(is_within(sibling, parent))

    def test_reject_link_components_preserves_creator_label(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root.parent / f"{root.name}-outside"
            try:
                with self.assertRaisesRegex(ValueError, "感知模块路径越出项目根目录"):
                    reject_link_components(root, outside, label="感知模块")
            finally:
                outside.rmdir() if outside.is_dir() else None

    def test_regular_components_are_allowed_and_not_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "modules" / "item"
            target.parent.mkdir()
            reject_link_components(root, target, label="拓展")
            self.assertFalse(is_link(target))


if __name__ == "__main__":
    unittest.main()

