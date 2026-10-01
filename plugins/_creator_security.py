"""Shared filesystem guards for the skill, expand and sense creators.

The three creator plugins all write user-controlled module names below a
project-owned directory.  Keeping the link/junction and containment checks in
one place prevents the creators from slowly acquiring subtly different path
traversal behaviour.  The public helpers intentionally remain small and
side-effect free so they can be used by plugin-level tests without creating a
module.
"""

from __future__ import annotations

from pathlib import Path


def is_link(path: Path) -> bool:
    """Return whether *path* is a symlink or a Windows directory junction."""

    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def is_within(path: Path, parent: Path) -> bool:
    """Return whether *path* is contained by *parent* without resolving either."""

    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def reject_link_components(
    root: Path,
    target: Path,
    *,
    label: str,
) -> None:
    """Reject targets outside *root* or traversing links/junctions.

    ``label`` is retained by the callers so their existing, user-facing error
    messages remain unchanged (for example ``技能路径…`` versus
    ``感知模块路径…``).  The validation order mirrors the historical creator
    implementations: containment is checked first, then each existing path
    component is inspected for a link or junction.
    """

    resolved_root = root.resolve()
    candidate = target if target.is_absolute() else resolved_root / target
    relative: Path | None = None
    try:
        relative = candidate.relative_to(resolved_root)
    except ValueError:
        # Absolute targets may carry a legitimate alias spelling of the root
        # itself (Windows 8.3 short names such as ``RUNNER~1``, case drift).
        # Lexical containment then fails for a path that is actually inside
        # the root.  Fall back to resolved containment once; this follows at
        # most one final alias resolution and never bypasses the junction
        # walk below, which still runs on the component names.
        try:
            relative = candidate.resolve(strict=False).relative_to(resolved_root)
        except (OSError, ValueError):
            raise ValueError(f"{label}路径越出项目根目录") from None
    current = resolved_root
    for part in relative.parts:
        current = current / part
        if current.exists() and is_link(current):
            raise ValueError(f"{label}路径不允许包含符号链接或目录联接")

