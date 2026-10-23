from __future__ import annotations

from pathlib import Path

import pytest

from run.config import ensure_user
from run.config.user_skeleton import (
    REQUIRED_USER_DIRS,
    ensure_user_skeleton,
    inspect_user_skeleton,
)
from update.migrations import migrate_user_skeletons


def test_existing_user_skeleton_is_repaired_idempotently(tmp_path: Path) -> None:
    user_dir = tmp_path / "users" / "alice"
    user_dir.mkdir(parents=True)
    (user_dir / "user_config.json").write_text("{}\n", encoding="utf-8")
    (user_dir / "knowledge").mkdir()

    created = ensure_user_skeleton(user_dir)

    assert created == [
        relative
        for relative in REQUIRED_USER_DIRS
        if relative != "knowledge"
    ]
    assert all((user_dir / relative).is_dir() for relative in REQUIRED_USER_DIRS)
    assert ensure_user_skeleton(user_dir) == []
    assert inspect_user_skeleton(user_dir) == ([], [])


def test_skeleton_repair_skips_symlinked_directory(tmp_path: Path) -> None:
    user_dir = tmp_path / "users" / "alice"
    outside = tmp_path / "outside"
    user_dir.mkdir(parents=True)
    outside.mkdir()
    linked = user_dir / "runtime"
    try:
        linked.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"当前环境不允许创建目录符号链接：{exc}")

    issues: list[str] = []
    created = ensure_user_skeleton(user_dir, issues=issues)

    assert "runtime" in issues
    assert "runtime" not in created
    assert linked.is_symlink()
    assert not (outside / "background_jobs").exists()


def test_ensure_user_repairs_an_existing_user(tmp_path: Path) -> None:
    root = tmp_path
    user_dir = root / "users" / "alice"
    user_dir.mkdir(parents=True)
    (user_dir / "user_config.json").write_text("{}\n", encoding="utf-8")
    template = root / "template" / "user"
    template.mkdir(parents=True)
    (template / "user_config.json").write_text("{}\n", encoding="utf-8")

    assert ensure_user("alice", root) == user_dir
    assert all((user_dir / relative).is_dir() for relative in REQUIRED_USER_DIRS)


def test_skeleton_repair_never_mutates_template_user(tmp_path: Path) -> None:
    template = tmp_path / "template" / "user"
    template.mkdir(parents=True)

    issues: list[str] = []
    assert ensure_user_skeleton(template, issues=issues) == []

    assert issues == ["."]
    assert not (template / "runtime").exists()
    assert not (template / "tools").exists()


def test_update_migration_repairs_missing_directories(tmp_path: Path, capsys) -> None:
    user_dir = tmp_path / "users" / "alice"
    user_dir.mkdir(parents=True)
    (user_dir / "user_config.json").write_text("{}\n", encoding="utf-8")

    migrate_user_skeletons(root=tmp_path, dry_run=False)

    output = capsys.readouterr().out
    assert "新增 16 项目录" in output
    assert all((user_dir / relative).is_dir() for relative in REQUIRED_USER_DIRS)


def test_update_migration_dry_run_reports_missing_without_mutating(
    tmp_path: Path, capsys
) -> None:
    user_dir = tmp_path / "users" / "alice"
    user_dir.mkdir(parents=True)
    (user_dir / "user_config.json").write_text("{}\n", encoding="utf-8")

    migrate_user_skeletons(root=tmp_path, dry_run=True)

    output = capsys.readouterr().out
    assert "补齐 16 项目录骨架" in output
    assert not (user_dir / "runtime").exists()
    assert not (user_dir / "tools").exists()
