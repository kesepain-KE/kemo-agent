"""Immutable dependency environments and supervised existing entrypoints."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import uuid
from pathlib import Path

from .common import DeployError, atomic, digest, json_bytes, read_json, target


def child_env(root: Path) -> dict:
    env = dict(os.environ)
    cache = target(root, '.kemo-deploy/cache')
    cache.mkdir(parents=True, exist_ok=True)
    env.update({'PIP_CACHE_DIR': str(cache / 'pip'), 'TMP': str(cache), 'TEMP': str(cache), 'TMPDIR': str(cache), 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'})
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    return env


def run_checked(command: list[str], root: Path) -> None:
    result = subprocess.run(command, cwd=root, env=child_env(root))
    if result.returncode:
        raise DeployError(f'Runtime preparation failed (exit {result.returncode})')


def prepare(root: Path, requirements: bytes, external_python: str | None, skip: bool) -> str | None:
    if skip:
        return None
    if external_python:
        # Resolving a venv's python symlink would bypass that environment.
        python = str(Path(external_python).absolute())
    else:
        key = digest(requirements + sys.version.encode() + sys.platform.encode())[:20]
        base = target(root, '.kemo-deploy/envs/' + key)
        ready = base / 'ready.json'
        if ready.is_file():
            python = read_json(ready)['python']
        else:
            # Build at its final path: venv script shebangs cannot be relocated.
            environment = base / uuid.uuid4().hex
            environment.mkdir(parents=True)
            run_checked([sys.executable, '-m', 'venv', str(environment)], root)
            python = str(environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python'))
            req = environment / 'requirements.txt'
            atomic(req, requirements)
            run_checked([python, '-m', 'pip', 'install', '--disable-pip-version-check', '-r', str(req)], root)
            probe(python, root)
            atomic(ready, json_bytes({'python': python}))
    probe(python, root)
    return python


def probe(python: str, root: Path) -> None:
    run_checked([python, '-c', 'import fastapi, pydantic, uvicorn, PIL, yaml, multipart, itsdangerous, tavily; import sys; assert sys.version_info >= (3, 10)'], root)


def initialize(root: Path, python: str, yes: bool) -> None:
    marker = target(root, '.kemo-deploy/initialized.json')
    if marker.exists():
        return
    command = [python, str(root / 'setup.py'), '--skip-deps', '--skip-web']
    if yes:
        command.append('--yes')
    run_checked(command, root)
    atomic(marker, json_bytes({'initialized': True}))


def supervise(root: Path, python: str, arguments: list[str]) -> int:
    child = subprocess.Popen([python, str(root / 'start_web.py'), *arguments], cwd=root, env=child_env(root))
    old = {}
    def forward(signum, frame):
        if child.poll() is None:
            if os.name == 'nt':
                # Windows terminate() only kills the supervisor's immediate child.
                # Stop this application's tree so workers cannot survive the lock.
                subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                child.send_signal(signum)
    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            old[signum] = signal.signal(signum, forward)
        return child.wait()
    finally:
        for signum, handler in old.items():
            signal.signal(signum, handler)
        if child.poll() is None:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                child.terminate()
            try:
                child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
