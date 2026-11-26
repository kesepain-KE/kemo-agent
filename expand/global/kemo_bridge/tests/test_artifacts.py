import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from conftest import peer_headers

PROJECT = Path(__file__).resolve().parents[4]


def _register(module, client, peer, path, **other):
    return client.post("/internal/artifacts", headers={"X-Kemo-Internal": module.CONFIG["session_secret"]},
                       json={"peer_id": peer["peer_id"], "file_path": str(path), **other})


def test_file_delivery_after_real_core_asset_expiry_and_seven_days(bridge, tmp_path, monkeypatch):
    from run.config import media_asset_ttl_seconds
    from run.extensions import AttachmentError
    from run.extensions import media_asset_cache as cache
    from tests.media_input_support import PNG, enqueue, make_host, make_root

    module, client = bridge
    config, path = make_root(tmp_path)
    installed_config = yaml.safe_load((PROJECT / "config/global_config.yaml").read_text("utf-8"))
    config["media_assets"] = installed_config["media_assets"]
    ttl = media_asset_ttl_seconds(config)
    assert ttl == 120
    started = 1_800_000_000.0
    clock = [started]
    monkeypatch.setattr(cache, "time", lambda: clock[0])
    asset_id = "asset_bridge_lifetime"
    artifact = {"asset_id": asset_id, "project_path": path.relative_to(tmp_path).as_posix(),
                "checksum_sha256": hashlib.sha256(PNG).hexdigest()}
    registered = cache.retain_media_assets(
        tmp_path, "alice", [artifact], source="web", session_id="session", ttl_seconds=ttl,
    )[0]
    assert datetime.fromisoformat(registered["reference_expires_at"]).timestamp() == started + 120
    peer = module.PEERS.create("agent", "alice")
    grant = _register(module, client, peer, path, asset_id=asset_id, media_kind="image")
    assert grant.status_code == 200, grant.text
    grant = grant.json()
    assert grant["asset_id"] == asset_id and "file_path" not in grant
    endpoint = f"/peer/v1/artifacts/{grant['grant_id']}/content"

    clock[0] = started + 119
    assert enqueue(make_host(tmp_path, config), {"asset_ids": [asset_id]})["ok"] is True

    clock[0] = started + 121
    with pytest.raises(AttachmentError, match="已过期.*paths"):
        enqueue(make_host(tmp_path, config), {"asset_ids": [asset_id]})
    assert cache.resolve_retained_media(tmp_path, "alice", [asset_id], source="web",
                                       session_id="session") == ([asset_id], [])
    result = client.get(endpoint, headers=peer_headers(peer))
    assert result.status_code == 200
    assert result.content == path.read_bytes() == PNG

    # Simulate a week passing against the *persisted* grant, and reopen the DB.
    from artifact_store import ArtifactStore
    from peer_store import PeerStore
    week = datetime.now(timezone.utc) - timedelta(days=7, seconds=1)
    with module.PEERS.connection(write=True) as db:
        db.execute("UPDATE artifact_grants SET created_at=? WHERE grant_id=?", (week.isoformat(), grant["grant_id"]))
    fresh = ArtifactStore(PeerStore(module.PEERS.path, module.PEERS.users_root))
    assert fresh.list(peer_id=peer["peer_id"])[0]["created_at"] == week.isoformat()
    result = client.get(endpoint, headers=peer_headers(peer))
    assert result.status_code == 200 and result.content == PNG
    assert fresh.list(peer_id=peer["peer_id"])[0]["download_count"] == 2
    print(f"core attach: t+119s accepted, t+121s rejected; grant: t+121s HTTP 200, "
          f"age>7d HTTP 200, {len(PNG)} bytes, sha256={hashlib.sha256(PNG).hexdigest()}")


def test_peer_grants_ranges_revocation_and_removed_file(bridge, tmp_path):
    module, client = bridge
    a = module.PEERS.create("agent", "alice")
    b = module.PEERS.create("agent", "alice")
    d = module.PEERS.create("device", "alice")
    source = tmp_path / "users/alice/download"
    source.mkdir()
    file = source / "clip.mp4"
    file.write_bytes(b"0123456789")
    forbidden = tmp_path / "users/bob/download"
    forbidden.mkdir()
    (forbidden / file.name).write_bytes(b"private")
    assert _register(module, client, a, forbidden / file.name).status_code == 400
    assert _register(module, client, a, source).status_code == 400
    assert client.post("/internal/artifacts", headers=peer_headers(a), json={
        "peer_id": a["peer_id"], "file_path": str(file),
    }).status_code == 403
    ids = [_register(module, client, peer, file, media_kind="video").json()["grant_id"] for peer in (a,b,d)]
    base = f"/peer/v1/artifacts/{ids[0]}"
    result = client.post(base, headers=peer_headers(a))
    assert result.status_code == 200
    assert result.json()["download_url"] == base + "/content"
    assert "asset_id" not in result.json()
    for method, path in (("POST", base), ("GET", base + "/content")):
        result = client.request(method, path, headers=peer_headers(b))
        assert (result.status_code, result.json()) == (403, {"error": "artifact_not_granted"})
    assert sum(row["action"] == "artifact_denied" for row in module.PEERS.audit_events(peer_id=b["peer_id"])) == 2

    result = client.get(base + "/content", headers={**peer_headers(a), "Range": "bytes=2-5"})
    assert (result.status_code, result.headers["content-range"], result.content) == (206, "bytes 2-5/10", b"2345")
    invalid = client.get(base + "/content", headers={**peer_headers(a), "Range": "bytes=99-"})
    assert invalid.status_code == 416
    assert client.post(base, headers=peer_headers(a)).json()["download_count"] == 1

    device_path = f"/v1/artifacts/{ids[2]}"
    assert [i["grant_id"] for i in client.get("/v1/artifacts", headers=peer_headers(d)).json()["items"]] == [ids[2]]
    assert client.get(device_path + "/content", headers=peer_headers(d)).content == b"0123456789"
    assert client.post(device_path + "/revoke", headers=peer_headers(d)).json()["revoked_at"]
    assert client.get(device_path + "/content", headers=peer_headers(d)).status_code == 403
    assert file.exists()  # Revoking access must not delete the file.
    file.unlink()
    result = client.get(base + "/content", headers=peer_headers(a))
    assert (result.status_code, result.json()) == (410, {"error": "artifact_gone"})


def test_upload_strips_suspicious_filename_checks_policy_and_does_not_attach(bridge, tmp_path):
    from artifact_store import ARTIFACT_LIMITS, FILE_LIMIT_BYTES
    from web import constants

    assert ARTIFACT_LIMITS == {"file": constants.FILE_UPLOAD_MAX_BYTES, "image": constants.IMAGE_PREVIEW_MAX_BYTES,
                               "audio": constants.AUDIO_PREVIEW_MAX_BYTES, "video": constants.VIDEO_PREVIEW_MAX_BYTES}
    assert FILE_LIMIT_BYTES == constants.FILE_UPLOAD_MAX_BYTES

    module, client = bridge
    peer = module.PEERS.create("agent", "alice")
    endpoint = "/peer/v1/upload"
    headers = peer_headers(peer)
    incoming = {"file": ("ignored.txt", b"sample", "text/plain")}

    def upload(name, url=endpoint):
        return client.post(url, headers=headers, files=incoming, data={"filename": name})

    result = upload("../outside/..\\sa\x00fe..txt")
    assert result.status_code == 200, result.text
    receipt = result.json()
    assert receipt["path"] == "users/alice/file_upload/safe.txt"
    assert (tmp_path / receipt["path"]).read_bytes() == b"sample"
    assert not (tmp_path / "users/alice/runtime/media_assets.sqlite3").exists()
    assert upload("safe.txt").json()["path"] != receipt["path"]
    assert upload("not-written.txt", endpoint + "?path=../../").status_code == 400
    module.CONFIG["bridge"]["upload_extensions"] = [".txt"]
    assert upload("image.png").status_code == 415
    assert upload("report.TXT").status_code == 200
    assert not (tmp_path / "users/alice/file_upload/image.png").exists()
    audit = next(r for r in module.PEERS.audit_events(peer_id=peer["peer_id"]) if r["action"] == "artifact_upload")
    assert audit["detail"] == {"file_name": "report.TXT", "size_bytes": 6,
                               "sha256": hashlib.sha256(b"sample").hexdigest()}


def test_upload_size_rejection_never_leaves_partial_file(bridge, tmp_path, monkeypatch):
    import uploads

    module, client = bridge
    peer = module.PEERS.create("device", "alice")
    monkeypatch.setattr(uploads, "FILE_LIMIT_BYTES", 4)
    result = client.post("/v1/upload", headers=peer_headers(peer), files={"file": ("large.txt", b"12345")})
    assert (result.status_code, result.json()) == (413, {"error": "file_too_large"})
    assert not list((tmp_path / "users/alice/file_upload").iterdir())


def test_artifact_refuses_linked_file_and_refuses_replacement_by_link(bridge, tmp_path):
    module, client = bridge
    peer = module.PEERS.create("agent", "alice")
    root = tmp_path / "users/alice/download"
    root.mkdir()
    path = root / "file.txt"
    path.write_text("original", encoding="utf-8")
    other = tmp_path / "users/bob/secret.txt"
    other.write_text("private", encoding="utf-8")
    alias = root / "alias.txt"
    try:
        alias.symlink_to(other)
    except OSError:
        pytest.skip("OS account cannot create file symlinks")
    assert _register(module, client, peer, alias).status_code == 400
    grant = _register(module, client, peer, path).json()
    path.unlink()
    path.symlink_to(other)
    response = client.get(f"/peer/v1/artifacts/{grant['grant_id']}/content", headers=peer_headers(peer))
    assert (response.status_code, response.json()) == (410, {"error": "artifact_gone"})
    assert other.read_text(encoding="utf-8") == "private"
