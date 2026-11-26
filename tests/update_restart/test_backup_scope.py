from __future__ import annotations

from pathlib import Path

from update._utils import _is_excluded, _parse_excludes
from update.backup import make_backup, restore_backup
from update.constants import BACKUP_EXCLUDES, RUNTIME_EXCLUDES


RUNTIME_CASES = (
    ("expand/global/dsh-model-system/data/", "x.json"),
    ("expand/global/dsh-model-system/assets/", "x.bin"),
    ("expand/share/browser_control/_chrome_profile/", "Preferences"),
    ("deploy/artifacts/", "1.5.0/x.zip"),
)
SOURCE_FILES = (
    "run/conversation/main_orchestration.py",
    "provider/protocol/validation.py",
    "cron/executor.py",
    "update/constants.py",
    "web/frontend/src/components/AppShell.tsx",
    "expand/global/kemo_bridge/app.py",
    "expand/global/dsh-model-system/backend/app.py",
    "expand/global/dsh-model-system/module.yaml",
)


def test_backup_exclusions_match_local_data_but_keep_source() -> None:
    patterns = _parse_excludes(BACKUP_EXCLUDES)
    for directory, name in RUNTIME_CASES:
        assert directory in RUNTIME_EXCLUDES
        assert _is_excluded(directory + name, *patterns)
    for relative in SOURCE_FILES:
        assert not _is_excluded(relative, *patterns), relative


def write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_backup_and_restore_skip_local_runtime_directories(tmp_path: Path) -> None:
    for relative in SOURCE_FILES:
        write(tmp_path / relative, b"original source")
    for directory, name in RUNTIME_CASES:
        write(tmp_path / directory / name, b"local data")

    backup = make_backup(root=tmp_path)
    assert backup is not None

    for relative in SOURCE_FILES:
        assert (backup / relative).read_bytes() == b"original source"
        (tmp_path / relative).write_bytes(b"updated source")
    for directory, name in RUNTIME_CASES:
        assert not (backup / directory).exists(), directory
        # Older snapshots may contain these files, but must not overwrite live data.
        write(backup / directory / name, b"stale data")
        write(tmp_path / directory / "new-local", b"local data since backup")

    restore_backup(backup_dir=backup, root=tmp_path)

    for relative in SOURCE_FILES:
        assert (tmp_path / relative).read_bytes() == b"original source"
    for directory, name in RUNTIME_CASES:
        assert (tmp_path / directory / name).read_bytes() == b"local data"
        assert (tmp_path / directory / "new-local").read_bytes() == b"local data since backup"
