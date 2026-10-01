from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from plugins._creator_security import is_link, is_within, reject_link_components


def _windows_short_path(path: Path) -> Path | None:
    """Return the 8.3 short-path spelling of *path*, or ``None`` when absent."""

    if not sys.platform.startswith("win"):
        return None
    import ctypes

    get_short_path_name = ctypes.windll.kernel32.GetShortPathNameW
    buffer = ctypes.create_unicode_buffer(1024)
    length = get_short_path_name(str(path), buffer, len(buffer))
    if not length:
        return None
    return Path(buffer.value)


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

    @unittest.skipUnless(
        sys.platform.startswith("win"), "Windows 8.3 short-name alias spelling"
    )
    def test_alias_spelled_absolute_root_is_allowed(self) -> None:
        # Regression: CI temp roots resolve through 8.3 short names
        # (``RUNNER~1``), so an absolute target may carry a short-name alias
        # spelling of the root itself.  Lexical containment then fails while
        # the path is legitimately inside the root.  Short-name generation is
        # volume controlled; skip when this volume has none.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "modules" / "item"
            target.parent.mkdir()
            alias_root = _windows_short_path(root)
            if alias_root is None or str(alias_root) == str(root):
                self.skipTest("no 8.3 short-name alias on this volume")
            reject_link_components(root, alias_root / "modules" / "item", label="拓展")

    @unittest.skipUnless(
        sys.platform.startswith("win"), "Windows 8.3 short-name alias spelling"
    )
    def test_escape_is_rejected_through_short_name_spelling(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root.parent / f"{root.name}-outside"
            outside.mkdir()
            outside_target = outside / "modules" / "item"
            outside_target.parent.mkdir()
            alias_outside = _windows_short_path(outside) or outside
            try:
                with self.assertRaisesRegex(ValueError, "越出项目根目录"):
                    reject_link_components(root, alias_outside / "modules" / "item", label="拓展")
            finally:
                shutil.rmtree(outside, ignore_errors=True)

    @unittest.skipUnless(
        sys.platform.startswith("win"), "Windows directory junctions"
    )
    def test_junction_escape_is_rejected_even_through_alias_fallback(self) -> None:
        base = Path(tempfile.mkdtemp())
        root = base / "ksr"
        escape_target_parent = base / "outside-real" / "modules"
        escape_target_parent.mkdir(parents=True)
        junction = base / "ksr-mirror"
        try:
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(junction), str(escape_target_parent.parent)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            with self.assertRaisesRegex(ValueError, "越出项目根目录"):
                reject_link_components(root, junction / "modules" / "item", label="拓展")
        finally:
            if junction.exists():
                subprocess.run(
                    ["cmd", "/c", "rmdir", str(junction)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            shutil.rmtree(junction.resolve(), ignore_errors=True)
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
