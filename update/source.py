"""Remote source acquisition and identity checks."""

from __future__ import annotations

from pathlib import Path

from ._utils import UpdateError, redact_text, run


def clone_latest(repo_url: str, branch: str, work_dir: Path) -> Path:
    target = work_dir / "source"
    # Capture Git diagnostics so a credential embedded in a custom remote URL
    # cannot be echoed directly by Git before the CLI has a chance to redact it.
    run(
        ["git", "clone", "--depth", "1", "--branch", branch, repo_url, str(target)],
        capture=True,
    )
    return target


def source_revision(source_root: Path) -> str:
    try:
        result = run(
            ["git", "rev-parse", "HEAD"],
            cwd=source_root,
            capture=True,
        )
    except Exception as exc:
        raise UpdateError(f"无法读取克隆源码提交号: {redact_text(exc)}") from exc
    revision = str(result.stdout or "").strip()
    if not revision:
        raise UpdateError("克隆源码没有可识别的 Git 提交号")
    return revision
