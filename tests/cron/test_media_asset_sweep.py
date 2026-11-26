from cron import executor
from run.extensions import media_asset_cache as cache


def _registry(root, user, entries):
    path = root / "users" / user / "runtime" / "media_assets.sqlite3"
    path.parent.mkdir(parents=True)
    with cache._registry(path) as connection:
        connection.executemany(
            "INSERT INTO media_assets VALUES ('web', 'session', ?, '{}', ?)", entries,
        )
    return path


def test_purge_removes_expired_references_but_not_files(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "time", lambda: 100)
    path = _registry(tmp_path, "alice", [("past", 99), ("due", 100), ("live", 101)])
    asset = tmp_path / "users" / "alice" / "file_upload" / "image.png"
    asset.parent.mkdir()
    asset.write_bytes(b"attachment")

    assert cache.purge_expired_media_assets(tmp_path) == 2
    with cache._registry(path) as connection:
        assert connection.execute("SELECT asset_id FROM media_assets").fetchall() == [("live",)]
    assert asset.read_bytes() == b"attachment"


def test_purge_does_not_create_a_missing_database(tmp_path, caplog):
    runtime = tmp_path / "users" / "alice" / "runtime"
    runtime.mkdir(parents=True)

    assert cache.purge_expired_media_assets(tmp_path) == 0
    assert not list(runtime.iterdir())
    assert not caplog.records


def test_purge_logs_a_bad_registry_and_continues(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(cache, "time", lambda: 100)
    bad = tmp_path / "users" / "broken" / "runtime" / "media_assets.sqlite3"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"not a database")
    _registry(tmp_path, "healthy", [("expired", 99)])

    assert cache.purge_expired_media_assets(tmp_path) == 1
    assert bad.read_bytes() == b"not a database"
    assert len(caplog.records) == 1
    assert "broken" in caplog.text


def test_session_lifecycle_cron_purges_references(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "time", lambda: 100)
    path = _registry(tmp_path, "alice", [("expired", 99)])
    result = {"status": "success"}
    monkeypatch.setattr(executor, "execute_session_sweep", lambda *_args, **_kwargs: result)

    assert executor.execute_cron_task(
        root=tmp_path, user="__system__", task_id="session_lifecycle_sweep", config={},
        system_task={"exec_mode": "system", "action": "session_lifecycle_sweep"},
    ) is result
    with cache._registry(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM media_assets").fetchone() == (0,)
