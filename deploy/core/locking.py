"""OS locks compatible with the existing updater's .update.lock contract."""
from __future__ import annotations

import os
from pathlib import Path
from .common import DeployError, no_links


class Lock:
    def __init__(self, path: Path):
        self.path = path
        self.fd = None

    def __enter__(self):
        no_links(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.name == 'nt':
                import msvcrt
                if os.fstat(self.fd).st_size < 1:
                    os.write(self.fd, b'0')
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self.fd)
            self.fd = None
            raise DeployError(f'Installation is running or another updater holds {self.path.name}; stop it first') from exc
        return self

    def __exit__(self, *args):
        if self.fd is None:
            return
        try:
            if os.name == 'nt':
                import msvcrt
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)
            self.fd = None

