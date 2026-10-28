"""Exercise public version guards using disposable, current-source snapshots."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil

import pytest


ROOT = Path(__file__).resolve().parents[2]
SURFACES = (
    "version.json", "cli.py", "web/frontend/package.json",
    "web/frontend/package-lock.json", "readme.md", "README_EN.md", "agents.md",
    "config/global_soul.md", "global_knowledge/project-introduction.md",
    "global_knowledge/version-and-update-modules.md",
    "global_knowledge/provider-reliability.md", "global_knowledge/builtin-expansions.md",
)


@pytest.fixture
def version_guard(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "release_version_guard", ROOT / ".github/scripts/check_versions.py",
    )
    assert spec is not None and spec.loader is not None
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    for relative in SURFACES:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    monkeypatch.delenv("GITHUB_REF_TYPE", raising=False)
    monkeypatch.delenv("GITHUB_REF_NAME", raising=False)
    version = json.loads((tmp_path / "version.json").read_text("utf-8"))["version"]
    return checker, tmp_path, version


def test_current_source_versions_and_matching_tag_pass(version_guard):
    checker, _, version = version_guard
    assert checker.main([]) == 0
    assert checker.main(["--tag", f"v{version}"]) == 0


def test_old_release_tag_cannot_validate_new_source(version_guard, capsys):
    checker, _, _ = version_guard
    assert checker.main(["--tag", "v0.0.0"]) == 1
    assert "发布标签" in capsys.readouterr().err


@pytest.mark.parametrize("relative, marker", [
    ("web/frontend/package.json", '"version": "{version}"'),
    ("web/frontend/package-lock.json", '"version": "{version}"'),
    ("cli.py", 'VERSION = "{version}"'),
    ("readme.md", "version-{version}-blue"),
    ("README_EN.md", "Current version: `{version}`"),
    ("agents.md", "当前稳定版本：`kemo-agent {version}`"),
    ("config/global_soul.md", "kemo-agent {version}"),
    ("global_knowledge/project-introduction.md", "当前稳定版本为 `{version}`"),
    ("global_knowledge/version-and-update-modules.md", '"version": "{version}"'),
])
def test_stale_current_version_surface_is_rejected(version_guard, capsys, relative, marker):
    checker, root, version = version_guard
    path = root / relative
    text = path.read_text("utf-8")
    current = marker.format(version=version)
    assert current in text
    path.write_text(text.replace(current, marker.format(version="0.0.0"), 1), "utf-8")
    assert checker.main([]) == 1
    assert "版本一致性检查失败" in capsys.readouterr().err


def test_lockfile_root_package_cannot_hide_behind_correct_top_level_version(version_guard):
    checker, root, _ = version_guard
    path = root / "web/frontend/package-lock.json"
    document = json.loads(path.read_text("utf-8"))
    document["packages"][""]["version"] = "0.0.0"
    path.write_text(json.dumps(document, ensure_ascii=False), "utf-8")
    assert checker.main([]) == 1
