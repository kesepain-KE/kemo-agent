from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest

from web.app import create_app
from web.auth import WebAuthConfig
from web.errors import NotFoundError
from web.service import WebRunService
from web.services.workspace_jobs import LOG_TAIL_BYTES, job_detail, list_jobs


@pytest.fixture
def backend(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/global_config.json").write_text("{}", encoding="utf-8")
    for user in ("alice", "bob"):
        directory = tmp_path / "users" / user
        directory.mkdir(parents=True)
        (directory / "user_config.json").write_text("{}", encoding="utf-8")
    return WebRunService(tmp_path)


def make_job(backend, index=1, *, user="alice", session="chat-one", source="web", status="running"):
    directory = backend.root / "users" / user / "runtime/background_jobs"
    directory.mkdir(parents=True, exist_ok=True)
    job_id = f"job_{index:032x}"
    record = {
        "schema_version": 1, "job_id": job_id, "user": user, "source": source,
        "session_id": session, "status": status, "pid": 123,
        "process_started_at": "2026-10-24T12:00:00Z", "process_name": "python",
        "started_at": f"2026-10-24T12:00:{index % 60:02d}Z", "updated_at": "",
        "finished_at": "", "exit_code": None, "command_digest": "test-command-digest",
        "shell_type": "powershell", "working_dir": str(backend.root),
        "stdout_path": str(directory / f"{job_id}.stdout.log"),
        "stderr_path": str(directory / f"{job_id}.stderr.log"),
    }
    path = directory / f"{job_id}.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return job_id, path, record


def test_empty_view_does_not_create_runtime_or_execute_work(backend):
    assert list_jobs(backend, "alice")["jobs"] == []
    assert not (backend.root / "users/alice/runtime").exists()


def test_list_is_user_scoped_and_filterable_and_paginated(backend):
    for index in range(1, 25):
        make_job(backend, index, session="chat-two" if index % 2 else "chat-one")
    make_job(backend, 25, user="bob")
    make_job(backend, 26, source="cli", status="completed")
    result = list_jobs(backend, "alice", page=2, page_size=10)
    assert result["total"] == 25 and len(result["jobs"]) == 10
    assert result["total_pages"] == 3 and result["active_count"] == 24
    filtered = list_jobs(backend, "alice", session_id="chat-one", source="web")
    assert filtered["total"] == 12
    assert all(job["session_id"] == "chat-one" for job in filtered["jobs"])
    assert list_jobs(backend, "bob")["total"] == 1
    assert result["read_only"] is True


def test_detail_returns_separate_bounded_streams_and_never_uses_record_paths(backend):
    job_id, path, record = make_job(backend)
    private = backend.root / "private.txt"
    private.write_text("must-not-read", encoding="utf-8")
    record["stdout_path"] = str(private)
    path.write_text(json.dumps(record), encoding="utf-8")
    stdout = path.with_suffix(".stdout.log")
    stdout.write_bytes(("中文输出\n" * 20_000).encode("utf-8"))
    path.with_suffix(".stderr.log").write_bytes(b"error line\n")
    before = {p.name: p.read_bytes() for p in path.parent.iterdir()}
    result = job_detail(backend, "alice", job_id)
    assert result["streams"]["stdout"]["truncated"] is True
    assert len(result["streams"]["stdout"]["text"].encode("utf-8")) <= LOG_TAIL_BYTES
    assert not result["streams"]["stdout"]["text"].startswith("�")
    assert "must-not-read" not in str(result)
    assert result["streams"]["stderr"]["text"] == "error line\n"
    assert before == {p.name: p.read_bytes() for p in path.parent.iterdir()}
    assert result["job"]["command_digest"] == "test-command-digest"


@pytest.mark.parametrize("job_id", ["job_bad", "..", "job_" + "f" * 32, "../config"])
def test_missing_and_invalid_jobs_do_not_escape_user_directory(backend, job_id):
    make_job(backend)
    with pytest.raises(NotFoundError):
        job_detail(backend, "alice", job_id)


def test_other_user_and_mismatched_source_or_session_are_not_found(backend):
    job_id, _, _ = make_job(backend)
    for user, kwargs in [("bob", {}), ("alice", {"session_id": "different"}),
                         ("alice", {"source": "cli"})]:
        with pytest.raises(NotFoundError):
            job_detail(backend, user, job_id, **kwargs)


def test_missing_logs_and_live_to_finished_updates(backend):
    job_id, path, record = make_job(backend)
    initial = job_detail(backend, "alice", job_id)
    assert initial["job"]["status"] == "running"
    assert initial["streams"]["stdout"]["text"] == ""
    path.with_suffix(".stdout.log").write_bytes(b"finished\n")
    record.update(status="completed", exit_code=0, finished_at="2026-10-24T12:01:00Z")
    path.write_text(json.dumps(record), encoding="utf-8")
    final = job_detail(backend, "alice", job_id)
    assert final["job"]["exit_code"] == 0 and final["job"]["status"] == "completed"
    assert final["streams"]["stdout"]["text"] == "finished\n"


def test_request_sidecars_and_corrupt_or_mismatched_records_are_never_exposed(backend):
    job_id, path, record = make_job(backend)
    path.with_suffix(".request.json").write_text('{"command":"private-request"}', encoding="utf-8")
    assert list_jobs(backend, "alice")["total"] == 1
    record["user"] = "bob"
    path.write_text(json.dumps(record), encoding="utf-8")
    assert list_jobs(backend, "alice")["total"] == 0
    path.write_text("invalid-json", encoding="utf-8")
    with pytest.raises(NotFoundError):
        job_detail(backend, "alice", job_id)


def test_hardlinked_record_and_log_are_rejected(backend):
    job_id, path, _ = make_job(backend)
    try:
        os.link(path, path.parent / "duplicate-record")
    except OSError:
        pytest.skip("hardlinks unavailable")
    assert list_jobs(backend, "alice")["total"] == 0
    other, other_path, _ = make_job(backend, 2)
    target = backend.root / "secret.txt"
    target.write_text("private", encoding="utf-8")
    os.link(target, other_path.with_suffix(".stdout.log"))
    result = job_detail(backend, "alice", other)
    assert result["streams"]["stdout"]["text"] == ""
    assert result["streams"]["stdout"]["error"]


def test_symlink_and_directory_reparse_are_not_followed(backend):
    job_id, path, _ = make_job(backend)
    target = backend.root / "secret.txt"
    target.write_text("private", encoding="utf-8")
    try:
        path.with_suffix(".stdout.log").symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable")
    assert job_detail(backend, "alice", job_id)["streams"]["stdout"]["error"]


def test_authenticated_readonly_http_contract_and_parameter_validation(backend):
    job_id, _, _ = make_job(backend)
    app = create_app(service=backend, auth_config=WebAuthConfig(access_token="qa-token"))

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            assert (await client.get("/api/users/alice/workspace/jobs")).status_code == 401
            assert (await client.post("/api/auth/token", json={"token": "qa-token"})).status_code == 200
            result = await client.get("/api/users/alice/workspace/jobs")
            assert result.status_code == 200 and result.json()["total"] == 1
            assert result.headers["cache-control"] == "private, no-store"
            assert (await client.get(f"/api/users/alice/workspace/jobs/{job_id}")).status_code == 200
            assert (await client.get(f"/api/users/bob/workspace/jobs/{job_id}")).status_code == 404
            assert (await client.post(f"/api/users/alice/workspace/jobs/{job_id}")).status_code == 405
            assert (await client.get("/api/users/alice/workspace/jobs?page_size=1000")).status_code == 400
            assert (await client.get("/api/users/alice/workspace/jobs?page=0")).status_code == 400
    asyncio.run(exercise())


def test_live_utf8_incomplete_suffix_does_not_corrupt_output(backend):
    job_id, path, _ = make_job(backend)
    data = "中文输出".encode("utf-8")
    path.with_suffix(".stdout.log").write_bytes(data[:-1])
    assert job_detail(backend, "alice", job_id)["streams"]["stdout"]["text"] == "中文输"
    path.with_suffix(".stdout.log").write_bytes(data)
    assert job_detail(backend, "alice", job_id)["streams"]["stdout"]["text"] == "中文输出"


def test_windows_shell_local_encoding_is_readable(backend, monkeypatch):
    monkeypatch.setattr("web.services.workspace_jobs.locale.getpreferredencoding", lambda _: "cp936")
    job_id, path, _ = make_job(backend)
    path.with_suffix(".stdout.log").write_bytes("中文后台输出\n".encode("gbk"))
    assert job_detail(backend, "alice", job_id)["streams"]["stdout"]["text"] == "中文后台输出\n"
