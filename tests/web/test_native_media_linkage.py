from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
from pathlib import Path

import httpx
import pytest

from events import RunEvent
from provider.schema import ChatResponse, ToolCall
from run.engine import iter_request_events
from run.extensions.media_input_metadata import public_media_assets
from run.history import commit_window, empty_window
from tests.media_input_support import PNG, MediaProvider, make_root, make_host, enqueue
from web.app import create_app
from web.service import WebRunService
from web.services.native_media import media_inputs


def test_native_media_stream_and_history_preserve_status_and_managed_location(tmp_path):
    _, image = make_root(tmp_path)
    provider = MediaProvider([
        ChatResponse(text="", tool_calls=[ToolCall(id="attach-one", name="attach_media", arguments={"paths": [str(image)]})]),
        ChatResponse(text="image received"),
    ])
    events = list(iter_request_events(
        {"user": "alice", "source": "web", "session_id": "native-link", "prompt": "inspect"},
        root=tmp_path, provider_factory=lambda _: provider,
    ))
    updates = [event.metadata["media_inputs"] for event in events if event.type == "media_input_update"]
    assert [items[0]["status"] for items in updates] == ["prepared", "included"]
    for items in updates:
        assert items[0]["scope"] == "download"
        assert items[0]["relative_path"] == "screen.png"
        assert items[0]["available"] is True
        assert "path" not in items[0]
        assert RunEvent.from_dict({"type": "media_input_update", "metadata": {"media_inputs": items}}).type == "media_input_update"
    done = next(event for event in events if event.type == "done")
    assert done.metadata["media_inputs"][0]["status"] == "included"
    history = WebRunService(tmp_path).history("alice", "native-link")
    history_tool = history["round_traces"][0]["tools"][0]
    returned = history_tool["media_inputs"][0]
    assert returned["status"] == "included" and returned["tool_call_id"] == history_tool["call_id"]
    assert returned["scope"] == "download" and returned["available"] is True
    assert str(tmp_path) not in json.dumps(returned)


def test_legacy_success_receipt_links_own_files_without_copying_or_promoting_status(tmp_path):
    config, image = make_root(tmp_path)
    host = make_host(tmp_path, config)
    receipt = enqueue(host, {"paths": [str(image)]}, call_id="old-call")
    # Simulate the previous version's public descriptor, which hid local download paths.
    receipt["assets"][0].update(scope="external", relative_path="")
    window = empty_window("alice", "web", "old-session")
    window["text"]["messages"] = [{"role": "user", "content": "inspect"}, {"role": "assistant", "content": "done"}]
    window["tool"]["rounds"] = [{"round": 1, "calls": [{
        "id": "old-call", "name": "attach_media", "arguments": {"paths": [str(image)]},
        "result": {"ok": True, "result": receipt}, "status": "completed",
    }]}]
    window["data"]["rounds"] = 1
    commit_window(tmp_path / "users/alice/history/window-old", window)
    before = sorted(str(path) for path in (tmp_path / "users/alice/download").rglob("*"))
    result = WebRunService(tmp_path).history("alice", "old-session")
    media = result["round_traces"][0]["tools"][0]["media_inputs"][0]
    assert media["relative_path"] == "screen.png" and media["available"] is True
    assert media["status"] == "unconfirmed", "a legacy receipt proves neither delivery nor non-delivery"
    assert sorted(str(path) for path in (tmp_path / "users/alice/download").rglob("*")) == before


def test_projection_never_grants_private_foreign_or_traversing_paths(tmp_path):
    _, image = make_root(tmp_path)
    base = {"asset_id": "asset_test", "name": "screen.png", "media_kind": "image", "size": len(PNG)}
    for path in [tmp_path / "private.png", tmp_path / "users/bob/download/screen.png"]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(PNG)
        public = public_media_assets([{**base, "path": str(path)}], root=tmp_path, user="alice")[0]
        assert public["scope"] == "external" and public["available"] is False
        assert public["relative_path"] == "" and "path" not in public
    invalid = public_media_assets([{**base, "scope": "download", "relative_path": "../user_config.json"}], root=tmp_path, user="alice")[0]
    assert invalid["scope"] == "external"
    missing = media_inputs(tmp_path, "alice", [{**base, "path": str(image.with_name("missing.png")), "status": "failed"}])[0]
    assert missing["available"] is False and missing["status"] == "failed"
    malformed = media_inputs(tmp_path, "alice", [{**base, "scope": [], "status": []}])[0]
    assert malformed["scope"] == "external" and malformed["status"] == "queued"


def test_download_thumbnail_is_scoped_checks_content_and_retains_only_small_preview(tmp_path):
    _, image = make_root(tmp_path)
    service = WebRunService(tmp_path)
    checksum = hashlib.sha256(PNG).hexdigest()
    app = create_app(service=service)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            endpoint = f"/api/users/alice/attachment-thumbnails/{checksum}"
            assert (await client.get(endpoint, params={"path": image.name})).status_code == 404
            assert (await client.get(endpoint, params={"area": "download", "path": image.name})).status_code == 200
            assert (await client.get(endpoint, params={"area": "../bob", "path": image.name})).status_code == 400
            wrong = await client.get("/api/users/alice/attachment-thumbnails/" + "f" * 64, params={"area": "download", "path": image.name})
            assert wrong.status_code == 404
    asyncio.run(exercise())
    assert [path.name for path in image.parent.iterdir()] == ["screen.png"]
    assert (tmp_path / f"users/alice/history/thumbnails/{checksum}.webp").is_file()


def test_hardlinked_or_changed_assets_remain_unavailable(tmp_path):
    _, image = make_root(tmp_path)
    base = {"asset_id": "asset_test", "name": image.name, "media_kind": "image", "size": len(PNG), "path": str(image)}
    linked = image.with_name("hardlink.png")
    os.link(image, linked)
    assert public_media_assets([base], root=tmp_path, user="alice")[0]["available"] is False
    changed = {**base, "size": len(PNG) + 1}
    assert public_media_assets([changed], root=tmp_path, user="alice")[0]["available"] is False
    from web.errors import NotFoundError
    with pytest.raises(NotFoundError):
        WebRunService(tmp_path).attachment_thumbnail(
            "alice", hashlib.sha256(PNG).hexdigest(), path=image.name, area="download",
        )


def test_cancelled_run_retains_queued_audit_but_does_not_claim_delivery(tmp_path):
    _, image = make_root(tmp_path)
    cancel = threading.Event()
    provider = MediaProvider([
        ChatResponse(text="", tool_calls=[ToolCall(id="attach-one", name="attach_media", arguments={"paths": [str(image)]})]),
        ChatResponse(text="must not be requested"),
    ])
    events = []
    for event in iter_request_events(
        {"user": "alice", "source": "web", "session_id": "cancel-native", "prompt": "inspect"},
        root=tmp_path, provider_factory=lambda _: provider, cancel_event=cancel,
    ):
        events.append(event)
        if event.type == "tool_call_result":
            cancel.set()
    assert len(provider.requests) == 1
    done = next(event for event in events if event.type == "done")
    assert done.metadata["media_inputs"][0]["status"] == "queued"
    history = WebRunService(tmp_path).history("alice", "cancel-native")
    assert history["round_traces"][0]["tools"][0]["media_inputs"][0]["status"] == "not_sent"


def test_linked_download_directory_is_not_projected_or_used_for_thumbnails(tmp_path):
    _, image = make_root(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    (external / image.name).write_bytes(PNG)
    link = image.parent / "linked"
    try:
        link.symlink_to(external, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    raw = {"asset_id": "asset_test", "name": image.name, "media_kind": "image", "size": len(PNG), "path": str(link / image.name)}
    assert public_media_assets([raw], root=tmp_path, user="alice")[0]["scope"] == "external"
    from web.errors import InvalidRequestError
    with pytest.raises(InvalidRequestError):
        WebRunService(tmp_path).attachment_thumbnail(
            "alice", hashlib.sha256(PNG).hexdigest(), path=f"linked/{image.name}", area="download",
        )


def test_changed_media_publishes_failed_terminal_audit_without_sending(tmp_path):
    _, image = make_root(tmp_path)
    provider = MediaProvider([
        ChatResponse(text="", tool_calls=[ToolCall(id="attach-one", name="attach_media", arguments={"paths": [str(image)]})]),
    ])
    events = []
    for event in iter_request_events(
        {"user": "alice", "source": "web", "session_id": "changed-native", "prompt": "inspect"},
        root=tmp_path, provider_factory=lambda _: provider,
    ):
        events.append(event)
        if event.type == "tool_call_result":
            image.write_bytes(b"invalid media after queue")
    assert len(provider.requests) == 1
    terminal = next(event for event in events if event.type == "error" and event.metadata.get("committed") is True)
    assert terminal.metadata["media_inputs"][0]["status"] == "failed"
    returned = WebRunService(tmp_path).history("alice", "changed-native")
    assert returned["round_traces"][0]["tools"][0]["media_inputs"][0]["status"] == "failed"
