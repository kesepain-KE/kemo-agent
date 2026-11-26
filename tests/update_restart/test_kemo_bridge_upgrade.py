"""Exercise the updater's real source board and transaction on a stopped Bridge."""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing, nullcontext
from pathlib import Path

import pytest
import yaml

from update.constants import KEMO_BRIDGE_EXPAND
from update.dispatcher import run_modules
from update.transaction import update_transaction


ROOT = Path(__file__).resolve().parents[2]
INSTANCE_UUID = "6bd916e2-01b2-48fd-9a63-ac3f84e3fbee"

_SEED_STORES = """
import sys
from pathlib import Path

from artifact_store import ArtifactStore
from device_commands import DeviceCommandStore
from initialize_config import initialize
from peer_store import PeerStore
from run_broker import RunStore
from start_expand import _write_activation

base = Path.cwd()
users = base.parents[2] / "users"
initialize(base)
_write_activation()
peers = PeerStore(base / "_bridge_peers.sqlite3", users)
with peers.connection(write=True) as db:
    db.execute("UPDATE bridge_identity SET instance_uuid=? WHERE id=1", (sys.argv[1],))
device = peers.create("device", "alice", label="existing phone")
ArtifactStore(peers).register(
    device["peer_id"], str(users / "alice/download/report.txt"), "file",
)
DeviceCommandStore(base / "_device_commands.json").enqueue(
    username="alice", device_id=device["peer_id"], action="timer.start",
    arguments={"duration_seconds": 60},
)
runs = RunStore(base / "_bridge_runs.sqlite3")
runs.create("alice", {"run_id": "upgrade-run", "session_id": "app-session"})
runs.set_status("alice", "upgrade-run", "completed")
runs.close()
"""


@pytest.mark.parametrize("rollback", [False, True], ids=["commit", "rollback"])
def test_core_upgrade_keeps_existing_bridge_runtime(tmp_path: Path, rollback: bool) -> None:
    target = tmp_path / "installed"
    bridge = target / KEMO_BRIDGE_EXPAND
    release = ROOT / KEMO_BRIDGE_EXPAND
    bridge.mkdir(parents=True)
    for name in [*release.glob("*.py"), release / "config.example.json", release / "module.yaml"]:
        shutil.copyfile(name, bridge / name.name)
    original_app = b"# Previous installed application\n"
    app = bridge / "app.py"
    app.write_bytes(original_app)
    artifact = target / "users/alice/download/report.txt"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"local deliverable")
    subprocess.run([sys.executable, "-X", "utf8", "-c", _SEED_STORES, INSTANCE_UUID],
                   cwd=bridge, check=True, timeout=30)
    (bridge / "_workspace_credentials.yaml").write_text(yaml.safe_dump({
        "access_key": "legacy-admin-test-key",
        "outbound": [{"label": "existing remote", "url": "https://peer.example.test", "peer_id": INSTANCE_UUID,
                      "secret": "legacy-peer-test-key"}],
    }), encoding="utf-8")

    state = {
        "_connections.json": {"connected_devices": 1},
        "_runtime.json": {"status": "stopped"},
        "_last_run.json": {"status": "succeeded"},
        "_server.pid": {"pid": 123, "instance_id": "stale"},
        "credential_registry.json": {"default": {"fingerprint": "local"}},
        "module/status.json": {"bridge_state": "last local status"},
        "module/panel.values.json": {"local": True},
    }
    for name, value in state.items():
        path = bridge / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
    for name in ("_lifecycle.lock", ".runtime.lock", ".module.execution.lock", "input_data.md", "logs/bridge.log"):
        path = bridge / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"existing local state")
    local_grant = target / "config/module_ui_grants.json"
    local_grant.parent.mkdir()
    local_grant.write_text(json.dumps({"expand:global:kemo_bridge": {"permissions": ["module.action.invoke"]}}),
                           encoding="utf-8")
    manifest = bridge / "module.yaml"
    local_fields = {"input_open": True, "health": "正常", "recent_update": "2026-11-15 07:00:00"}
    manifest.write_text(yaml.safe_dump({**yaml.safe_load(manifest.read_bytes()), **local_fields},
                                      allow_unicode=True), encoding="utf-8")
    runtime = [bridge / name for name in (*state, "config.json", "users.json", "_workspace_credentials.yaml",
               "_bridge_peers.sqlite3", "_bridge_runs.sqlite3", "_device_commands.json",
               "_device_commands.json.lock", "_activated.json", "_lifecycle.lock", ".runtime.lock",
               ".module.execution.lock", "logs/bridge.log", "input_data.md")]
    tracked_local = {path: path.read_bytes() for path in (*runtime, local_grant, artifact)}

    with pytest.raises(RuntimeError, match="simulated failure after sync") if rollback else nullcontext():
        with update_transaction(root=target, dry_run=False) as backup:
            assert backup is not None
            for path in tracked_local:
                assert not (backup / path.relative_to(target)).exists(), path.name
            [result] = run_modules(["core"], ROOT, target, dry_run=False, assume_yes=True)
            assert result["status"] == "ok", result
            assert app.read_bytes() == (release / "app.py").read_bytes() != original_app
            for name in ("ui/dist/index.html", "ui/dist/admin.js", "ui/dist/admin.css",
                         "ui/admin/index.html", "module/workspace.json", "workspace_actions.py"):
                assert (bridge / name).read_bytes() == (release / name).read_bytes()
            assert yaml.safe_load(manifest.read_bytes())["ui"]["action_entry"] == "workspace_actions.py"
            print("[upgrade] core status=ok; Bridge Web action entry and embedded UI installed")
            if rollback:
                # A backup made by an older updater may include files now classified as private.
                for path in (bridge / "_bridge_peers.sqlite3", bridge / "_activated.json",
                             bridge / "_workspace_credentials.yaml", local_grant):
                    stale = backup / path.relative_to(target)
                    stale.parent.mkdir(parents=True, exist_ok=True)
                    stale.write_bytes(b"stale snapshot; must not overwrite the live copy")
                raise RuntimeError("simulated failure after sync")

    if rollback:
        assert app.read_bytes() == original_app
        assert not (bridge / "ui/admin/index.html").exists()
    else:
        current = yaml.safe_load(manifest.read_bytes())
        assert {name: current[name] for name in local_fields} == local_fields
    for path, original in tracked_local.items():
        assert path.read_bytes() == original, path.name
    with closing(sqlite3.connect(bridge / "_bridge_peers.sqlite3")) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("SELECT instance_uuid FROM bridge_identity WHERE id=1").fetchone() == (INSTANCE_UUID,)
        assert db.execute("SELECT label FROM peers").fetchall() == [("existing phone",)]
        assert db.execute("SELECT action FROM peer_audit ORDER BY id").fetchall() == [
            ("peer_create",), ("artifact_grant",),
        ]
        assert db.execute("SELECT file_path FROM artifact_grants").fetchall() == [(str(artifact),)]
    with closing(sqlite3.connect(bridge / "_bridge_runs.sqlite3")) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("SELECT status FROM app_runs WHERE run_id = 'upgrade-run'").fetchone() == ("completed",)
    phase = "rollback" if rollback else "commit"
    print(f"[{phase}] {len(tracked_local)} local files byte-identical; "
          "UUID unchanged; peers=1 audit=2 grants=1 runs=1; both SQLite integrity checks=ok")
