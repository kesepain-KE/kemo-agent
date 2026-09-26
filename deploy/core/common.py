from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path, PurePosixPath


class DeployError(RuntimeError):
    """An actionable deployment failure."""


SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?$")


def version(value: str) -> str:
    if not isinstance(value, str):
        raise DeployError('SemVer must be a string')
    match = SEMVER.fullmatch(value)
    if not match:
        raise DeployError(f"Invalid SemVer: {value!r}")
    if match[4]:
        for part in match[4].split('.'):
            if not part or (part.isdigit() and len(part) > 1 and part[0] == '0'):
                raise DeployError(f"Invalid prerelease: {value!r}")
    if match[5] and any(not part for part in match[5].split('.')):
        raise DeployError(f"Invalid build metadata: {value!r}")
    return value


def version_key(value: str) -> tuple:
    match = SEMVER.fullmatch(version(value))
    assert match is not None
    pre = match[4]
    parts = tuple((0, int(p)) if p.isdigit() else (1, p) for p in pre.split('.')) if pre else ()
    return (*(int(match[i]) for i in (1, 2, 3)), 0 if pre else 1, parts)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as exc:
        raise DeployError(f"Cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DeployError(f"Expected JSON object: {path}")
    return value


def relative(value: str) -> str:
    """Portable paths: reject escapes, ADS, Windows device names and aliases."""
    if not isinstance(value, str) or not value or '\\' in value or any(ord(c) < 32 or c in '<>"|?*' for c in value):
        raise DeployError(f"Unsafe relative path: {value!r}")
    parts = value.split('/')
    if any(p in ('', '.', '..') or ':' in p or p.endswith((' ', '.')) for p in parts):
        raise DeployError(f"Unsafe relative path: {value!r}")
    for part in parts:
        if re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', part, re.I):
            raise DeployError(f"Reserved path: {value!r}")
    if PurePosixPath(value).is_absolute():
        raise DeployError(f"Absolute path not allowed: {value!r}")
    return value


def no_links(path: Path) -> None:
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise DeployError(f"Symlink/junction not allowed: {item}")


def target(root: Path, name: str) -> Path:
    path = root.joinpath(*relative(name).split('/'))
    no_links(path)
    if not path.resolve().is_relative_to(root.resolve()):
        raise DeployError(f"Path escapes install root: {name}")
    return path


def atomic(path: Path, data: bytes, mode: int = 0o600) -> None:
    no_links(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.deploy-write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
