from __future__ import annotations

import importlib
import json
import secrets
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

MODULE = Path(__file__).resolve().parents[1]


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    base = tmp_path / "expand" / "global" / "kemo_bridge"
    base.mkdir(parents=True)
    for source in MODULE.glob("*.py"):
        shutil.copyfile(source, base / source.name)
    shutil.copyfile(MODULE / "config.example.json", base / "config.example.json")
    settings = json.loads((base / "config.example.json").read_text(encoding="utf-8"))
    settings["session_secret"] = secrets.token_urlsafe(48)
    (base / "config.json").write_text(json.dumps(settings), encoding="utf-8")
    shutil.copytree(MODULE / "ui", base / "ui")
    shutil.copytree(MODULE / "module", base / "module")
    for name in ("module.yaml", "input_data.md", "expand_control.md"):
        shutil.copyfile(MODULE / name, base / name)
    for name in ("alice", "bob"):
        (tmp_path / "users" / name).mkdir(parents=True)
    monkeypatch.syspath_prepend(str(base))
    names = {source.stem for source in base.glob("*.py")}
    saved = {name: sys.modules.pop(name) for name in names if name in sys.modules}
    try:
        module = importlib.import_module("app")
        client = TestClient(module.app, client=("127.0.0.1", 60000))
        try:
            yield module, client
        finally:
            client.close()
    finally:
        for name in names:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


def peer_headers(peer: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {peer['secret']}", "X-Kemo-Peer-Id": peer["peer_id"]}
