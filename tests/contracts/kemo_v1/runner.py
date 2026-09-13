"""One stable entrypoint for local Kemo 1.0 compatibility checks."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from tests.contracts.kemo_v1.fixture_loader import FIXTURE_ROOT, load_bundle


def _peer_fixtures(root: Path) -> Path:
    return root.resolve() / "tests" / "contracts" / "kemo_v1" / "fixtures"


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(
        description="运行本仓库 Kemo 1.0 契约测试，并可核对另一仓库的 Fixture 镜像。",
    )
    parser.add_argument("--peer-root", type=Path, help="另一仓库根目录；提供时先比较共享 Fixture")
    options, pytest_args = parser.parse_known_args(argv)
    load_bundle()
    if options.peer_root is not None:
        peer = _peer_fixtures(options.peer_root)
        load_bundle(peer)
        for name in ("manifest.json", "wire.json"):
            if (FIXTURE_ROOT / name).read_bytes() != (peer / name).read_bytes():
                print(f"Kemo Fixture 镜像不一致：{name}", file=sys.stderr)
                return 2
        print(f"[KEMO] Fixture 镜像一致：{peer}", flush=True)
    command = [sys.executable, "-m", "pytest", str(Path(__file__).resolve().parent), *pytest_args]
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        command,
        cwd=Path(__file__).resolve().parents[3],
        env=environment,
        check=False,
    ).returncode
