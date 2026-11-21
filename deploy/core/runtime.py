"""Immutable dependency environments and supervised existing entrypoints."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import BinaryIO

from .common import DeployError, atomic, digest, json_bytes, read_json, setup_subprocess_kwargs, target


_WEB_LOG_NAME = '.kemo-deploy/web.log'
_WEB_LOG_MAX_BYTES = 10 * 1024 * 1024


def child_env(root: Path) -> dict:
    env = dict(os.environ)
    cache = target(root, '.kemo-deploy/cache')
    cache.mkdir(parents=True, exist_ok=True)
    env.update({'PIP_CACHE_DIR': str(cache / 'pip'), 'TMP': str(cache), 'TEMP': str(cache), 'TMPDIR': str(cache), 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'})
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    return env


def run_checked(command: list[str], root: Path) -> None:
    result = subprocess.run(command, cwd=root, env=child_env(root), **setup_subprocess_kwargs())
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


def _stop_windows_tree(child: subprocess.Popen) -> None:
    subprocess.run(
        ['taskkill', '/PID', str(child.pid), '/T', '/F'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **setup_subprocess_kwargs(),
    )


def _cancel_windows_launch(child: subprocess.Popen) -> None:
    if child.poll() is None:
        _stop_windows_tree(child)
        try:
            child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def start_windows(root: Path, python: str, arguments: list[str]) -> int:
    """Launch Web independently of the installer's console and wait until it listens."""
    env = child_env(root)
    if '-h' in arguments or '--help' in arguments:
        return subprocess.run(
            [python, str(root / 'start_web.py'), *arguments],
            cwd=root, env=env, **setup_subprocess_kwargs(),
        ).returncode

    log_path = target(root, _WEB_LOG_NAME)
    with tempfile.TemporaryDirectory(prefix='web-start-', dir=env['TMP']) as directory:
        receipt = Path(directory) / 'ready.json'
        deadline = time.monotonic() + 60
        # Keep the pipe reader alive after this foreground command returns.
        child = subprocess.Popen(
            [sys.executable, str(root / 'deploy' / 'deploy.py'), '_serve_windows',
             receipt.parent.name, python, *arguments],
            cwd=root, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        try:
            while not receipt.exists():
                code = child.poll()
                if code is not None:
                    # The supervisor may have published its error between exists() and poll().
                    if receipt.exists():
                        break
                    raise DeployError(f'Web launch failed (exit {code}); see {log_path}')
                if time.monotonic() >= deadline:
                    raise DeployError(f'Web did not start listening within 60 seconds; see {log_path}')
                time.sleep(0.05)
            ready = read_json(receipt)
        except BaseException:
            _cancel_windows_launch(child)
            raise
        if 'error' in ready:
            child.wait()
            raise DeployError(ready['error'])
    print(f'Web is listening at {ready["url"]} (PID {ready["pid"]}); output: {log_path}')
    return 0


def _copy_web_log(source: BinaryIO, log: BinaryIO) -> None:
    """Keep the recent output in place so an open log need not be renamed on Windows."""
    log.seek(0, os.SEEK_END)
    chunk = b''
    while True:
        if log.tell() + len(chunk) > _WEB_LOG_MAX_BYTES:
            log.seek(-(_WEB_LOG_MAX_BYTES // 2), os.SEEK_END)
            tail = log.read().lstrip(bytes(range(0x80, 0xC0)))  # Skip a cut UTF-8 prefix.
            log.seek(0)
            log.write(tail)
            log.truncate()
        log.write(chunk)
        log.flush()
        chunk = source.read(64 * 1024)
        if not chunk:
            break


def _windows_receipt(root: Path, directory: str) -> Path:
    return target(root, f'.kemo-deploy/cache/{directory}/ready.json')


def serve_windows(receipt_directory: str, expected_python: str, arguments: list[str]) -> int:
    from .locking import Lock

    root = Path.cwd()
    web = None
    try:
        with Lock(target(root, '.update.lock')):
            # An update or another start may win the lock during the handoff.
            if read_json(target(root, '.kemo-install.json')).get('python') != expected_python:
                raise DeployError('The deployment runtime changed during startup; run deploy start again')

            gui_python = Path(expected_python).with_name('pythonw.exe')
            use_gui_python = Path(expected_python).name.lower() == 'python.exe' and gui_python.is_file()
            executable = str(gui_python) if use_gui_python else expected_python
            # A venv's console python.exe redirector can reopen a detached console.
            flags = subprocess.DETACHED_PROCESS if use_gui_python else subprocess.CREATE_NO_WINDOW
            log_path = target(root, _WEB_LOG_NAME)
            log_path.touch(exist_ok=True)
            with log_path.open('r+b') as log:
                web = subprocess.Popen(
                    [executable, '-u', str(root / 'deploy' / 'deploy.py'), '_windows_web',
                     receipt_directory, *arguments],
                    cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    creationflags=flags | subprocess.CREATE_NEW_PROCESS_GROUP, bufsize=0,
                )
                try:
                    assert web.stdout is not None
                    with web.stdout:
                        _copy_web_log(web.stdout, log)
                    return web.wait()
                except BaseException:
                    # Never release the deployment lock while an unmonitored Web is still running.
                    _cancel_windows_launch(web)
                    raise
    except (DeployError, OSError) as exc:
        if web is not None:
            raise
        # A failed handoff must not write into the active instance's log.
        atomic(_windows_receipt(root, receipt_directory), json_bytes({'error': str(exc)}))
        return 1


def run_windows_web(receipt_directory: str, arguments: list[str]) -> int:
    root = Path.cwd()
    sys.path.insert(0, str(root))
    from start_web import main

    def ready(url: str) -> None:
        atomic(_windows_receipt(root, receipt_directory), json_bytes({'pid': os.getpid(), 'url': url}))

    return main(arguments, on_ready=ready)


def supervise(root: Path, python: str, arguments: list[str]) -> int:
    child = subprocess.Popen(
        [python, str(root / 'start_web.py'), *arguments],
        cwd=root, env=child_env(root), **setup_subprocess_kwargs(),
    )
    old = {}
    def forward(signum, frame):
        if child.poll() is None:
            if os.name == 'nt':
                # Windows terminate() only kills the supervisor's immediate child.
                # Stop this application's tree so workers cannot survive the lock.
                _stop_windows_tree(child)
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
                _stop_windows_tree(child)
            else:
                child.terminate()
            try:
                child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
