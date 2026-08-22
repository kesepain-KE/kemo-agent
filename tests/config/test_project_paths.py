from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from run.config import ensure_user, project_root, user_template_dir
import start_web


def test_project_root_points_to_repository_root() -> None:
    root = project_root()
    assert root == Path(__file__).resolve().parents[2]
    assert (root / "version.json").is_file()
    assert (root / "config" / "global_config.json").is_file()
    assert user_template_dir(root) == root / "template" / "user"


def test_ensure_user_uses_bundled_template_user(tmp_path: Path) -> None:
    root = tmp_path
    template = root / "template" / "user"
    template.mkdir(parents=True)
    (template / "user_config.json").write_text("{}", encoding="utf-8")

    created = ensure_user("alice", root)

    assert created == root / "users" / "alice"
    assert (created / "user_config.json").read_text("utf-8") == "{}"


def test_start_web_bootstraps_from_bundled_user_template(tmp_path: Path) -> None:
    template = tmp_path / "template" / "user"
    template.mkdir(parents=True)
    (template / "user_config.json").write_text("{}", encoding="utf-8")

    with patch("builtins.input", return_value="alice"):
        assert start_web._check_users(tmp_path) is True

    assert (tmp_path / "users" / "alice" / "user_config.json").is_file()
