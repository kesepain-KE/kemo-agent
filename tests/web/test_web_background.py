from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from PIL import Image

from run.config import ConfigError, ensure_user, load_config
from run.config.web_ui import DEFAULT_WEB_BACKGROUND, web_background
from web.app import create_app
from web.auth import WebAuthConfig
from web.errors import InvalidRequestError
from web.service import WebRunService
from web.services import background_media
from web.services.background_media import validate_background


def image_bytes(fmt="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", (32, 24), "#9cabce").save(stream, format=fmt)
    return stream.getvalue()


def box(kind, data):
    return (8 + len(data)).to_bytes(4, "big") + kind + data


def mp4_bytes(*, video=True):
    handler = b"\0" * 8 + (b"vide" if video else b"soun") + b"\0" * 12
    return (
        box(b"ftyp", b"isom\0\0\0\0isommp42")
        + box(b"moov", box(b"trak", box(b"mdia", box(b"hdlr", handler))))
        + box(b"mdat", b"\0" * 16)
    )


def ebml(kind, data):
    assert len(data) < 127
    return kind + bytes([0x80 | len(data)]) + data


def webm_bytes(*, video=True):
    header = ebml(b"\x1aE\xdf\xa3", ebml(b"\x42\x82", b"webm"))
    tracks = ebml(b"\x16\x54\xae\x6b", ebml(b"\xae", ebml(b"\x83", b"\x01" if video else b"\x02")))
    return header + ebml(b"\x18\x53\x80\x67", tracks + ebml(b"\x1f\x43\xb6\x75", b"\0" * 8))


@pytest.fixture
def root(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/global_config.json").write_text("{}")
    for name in ("alice", "bob"):
        directory = tmp_path / "users" / name
        directory.mkdir(parents=True)
        (directory / "user_config.json").write_text('{"knowledge":{"use_shared":false}}')
    return tmp_path


@pytest.mark.parametrize("fmt,suffix,mime", [("PNG", ".png", "image/png"), ("JPEG", ".jpg", "image/jpeg"), ("WEBP", ".webp", "image/webp")])
def test_valid_images(fmt, suffix, mime):
    assert validate_background(image_bytes(fmt)) == (suffix, mime)


@pytest.mark.parametrize("payload", [b"", b"<svg/>", b"GIF89a" + b"\0" * 40, b"\xff\xd8invalid", b"RIFF0000WEBPinvalid",
                                      mp4_bytes(video=False), mp4_bytes()[:-1], webm_bytes(video=False), webm_bytes()[:-2]])
def test_invalid_or_audio_only_background_rejected(payload):
    with pytest.raises(InvalidRequestError):
        validate_background(payload)


@pytest.mark.parametrize("payload,expected", [(mp4_bytes(), (".mp4", "video/mp4")), (webm_bytes(), (".webm", "video/webm"))])
def test_video_containers_need_real_structure_and_video_track(payload, expected):
    assert validate_background(payload) == expected


@pytest.mark.parametrize("invalid", [None, "image", [], {"enabled": 1}, {"enabled": "true"}, {"overlay_opacity": True},
    {"overlay_opacity": -0.1}, {"overlay_opacity": 1.01}, {"overlay_opacity": float("nan")},
    {"overlay_opacity": float("inf")}, {"overlay_opacity": 10**400}, {"blur": 1.2}, {"blur": True},
    {"blur": -1}, {"blur": 21}, {"path": "C:/secret"}, {"url": "https://example.test/image"},
    {"ui_opacity": True}, {"ui_opacity": -0.1}, {"ui_opacity": 1.01}, {"ui_opacity": "0.5"},
    {"ui_opacity": float("nan")}, {"ui_opacity": float("inf")}, {"ui_opacity": 10**400},
    {"ui_blur": True}, {"ui_blur": -1}, {"ui_blur": 31}, {"ui_blur": 1.2}, {"ui_blur": "5"}])
def test_background_config_validation(invalid):
    assert web_background({"web_ui": {"background": invalid}}) == DEFAULT_WEB_BACKGROUND
    with pytest.raises(ValueError):
        web_background({"web_ui": {"background": invalid}}, strict=True)


def test_legacy_background_config_defaults_and_ui_endpoints():
    old = {"enabled": True, "overlay_opacity": 0.4, "blur": 6}
    assert web_background({"web_ui": {"background": old}}, strict=True) == {
        **old, "ui_opacity": 0.94, "ui_blur": 0,
    }
    for opacity, blur in ((0, 0), (1, 30)):
        assert web_background({"web_ui": {"background": {
            "ui_opacity": opacity, "ui_blur": blur,
        }}}, strict=True) == {**DEFAULT_WEB_BACKGROUND, "ui_opacity": opacity, "ui_blur": blur}


def test_background_five_times_limits_and_exact_size_boundary(monkeypatch):
    assert background_media.BACKGROUND_IMAGE_MAX_BYTES == 100 * 1024 * 1024
    assert background_media.BACKGROUND_MAX_BYTES == 400 * 1024 * 1024
    # Exercise exact inclusive byte boundaries without allocating 400 MB per test.
    image = image_bytes()
    monkeypatch.setattr(background_media, "BACKGROUND_IMAGE_MAX_BYTES", len(image))
    assert validate_background(image) == (".png", "image/png")
    with pytest.raises(InvalidRequestError):
        validate_background(image + b"\0")
    video = mp4_bytes()
    monkeypatch.setattr(background_media, "BACKGROUND_MAX_BYTES", len(video))
    assert validate_background(video) == (".mp4", "video/mp4")
    with pytest.raises(InvalidRequestError):
        validate_background(video + b"\0")


def test_background_accepts_media_larger_than_previous_limits(root):
    # These payloads exceed the old 20/80 MB caps and use the real byte validator.
    image = image_bytes() + b"\0" * (21 * 1024 * 1024)
    assert validate_background(image) == (".png", "image/png")
    video = mp4_bytes() + box(b"free", b"\0" * (81 * 1024 * 1024))
    backend = WebRunService(root)
    saved = backend.save_background("alice", video)
    assert saved["available"] and saved["size"] == len(video)
    assert backend.background_status("alice")["media_kind"] == "video"


def test_background_api_lifecycle_and_range(root):
    async def exercise():
        backend = WebRunService(root)
        app = create_app(service=backend)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            url = "/api/users/alice/background"
            empty = (await client.get(url + "/status")).json()
            assert empty["settings"] == DEFAULT_WEB_BACKGROUND and not empty["available"]
            assert (await client.get(url)).status_code == 404
            data = image_bytes()
            # Content decides the canonical suffix, not the untrusted filename/MIME.
            uploaded = await client.post(url, files={"file": ("../../arbitrary.jpg", data, "text/plain")})
            assert uploaded.status_code == 200, uploaded.text
            assert uploaded.json()["filename"] == "background.png"
            assert uploaded.json()["settings"]["enabled"] is False
            target = root / "users/alice/background.png"
            assert target.read_bytes() == data
            served = await client.get(url)
            assert served.content == data
            assert served.headers["content-type"] == "image/png"
            assert served.headers["cache-control"] == "private, no-store"
            assert served.headers["x-content-type-options"] == "nosniff"
            partial = await client.get(url, headers={"Range": "bytes=0-15"})
            assert partial.status_code == 206, partial.text
            assert partial.content == data[:16]
            settings = {"enabled": True, "overlay_opacity": 0.4, "blur": 6, "ui_opacity": 0.6, "ui_blur": 12}
            saved = await client.patch("/api/users/alice/config", json={"changes": {"web_ui": {"background": settings}}})
            assert saved.status_code == 200, saved.text
            assert load_config("alice", root)["web_ui"]["background"] == settings
            assert (await client.get(url + "/status")).json()["settings"] == settings
            assert (await client.get("/api/users/alice/preferences")).json()["appearance"]["background"] == settings
            assert backend.background_status("bob")["available"] is False
            assert backend.background_status("bob")["settings"] == DEFAULT_WEB_BACKGROUND
            old_revision = uploaded.json()["revision"]
            replacement = await client.post(url, files={"file": ("movie.mp4", mp4_bytes(), "video/mp4")})
            assert replacement.status_code == 200, replacement.text
            assert replacement.json()["media_kind"] == "video"
            assert replacement.json()["revision"] != old_revision
            assert not target.exists()
            assert (root / "users/alice/background.mp4").is_file()
            assert (await client.get(url, headers={"Range": "bytes=8-19"})).content == mp4_bytes()[8:20]
            rejected = await client.post(url, files={"file": ("fake.mp4", b"bad", "video/mp4")})
            assert rejected.status_code == 400
            assert (root / "users/alice/background.mp4").read_bytes() == mp4_bytes()
            removed = await client.delete(url)
            assert removed.status_code == 200 and removed.json()["deleted"]
            assert removed.json()["available"] is False
            assert removed.json()["settings"] == settings  # Clear doesn't alter other preferences.
            assert (await client.delete(url)).json()["deleted"] is False
            assert (await client.get(url)).status_code == 404
            assert not (root / "users/bob/background.mp4").exists()
            assert not (root / "users/alice/file_upload").exists()
            assert not (root / "users/alice/download").exists()

    asyncio.run(exercise())


def test_config_rejects_bad_values_without_overwriting_other_fields(root):
    service = WebRunService(root)
    path = root / "users/alice/user_config.json"
    original = path.read_bytes()
    for invalid in (
        {"blur": 99}, {"file": "../bob/background.png"}, None,
        {"ui_opacity": 1.1}, {"ui_opacity": True}, {"ui_blur": 31},
    ):
        with pytest.raises(InvalidRequestError):
            service.patch_user_config("alice", {"web_ui": {"background": invalid}})
        assert path.read_bytes() == original
    path.write_text('{"web_ui":{"background":{"blur":99}}}')
    with pytest.raises(ConfigError):
        load_config("alice", root)


def test_manual_files_and_replacement_cache_revision(root):
    backend = WebRunService(root)
    directory = root / "users/alice"
    target = directory / "background.png"
    target.write_bytes(image_bytes())
    first = backend.background_status("alice")
    assert first["available"] and not first["settings"]["enabled"]
    target.write_bytes(image_bytes("WEBP"))
    assert not backend.background_status("alice")["available"]  # Suffix doesn't match.
    other = directory / "background.webp"
    other.write_bytes(image_bytes("WEBP"))
    assert backend.background_status("alice")["filename"] == "background.webp"
    backend.save_background("alice", image_bytes("JPEG"))
    assert sorted(p.name for p in directory.glob("background.*")) == ["background.jpg"]
    assert backend.background_status("alice")["revision"] != first["revision"]


@pytest.mark.parametrize("fmt", ["JPEG", "PNG"])
def test_failed_atomic_upload_preserves_original(root, fmt):
    backend = WebRunService(root)
    backend.save_background("alice", image_bytes())
    target = root / "users/alice/background.png"
    before = target.read_bytes()
    with patch("web.services.backgrounds.atomic_write", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            backend.save_background("alice", image_bytes(fmt))
    assert target.read_bytes() == before
    assert not (root / "users/alice/background.jpg").exists()


def test_locked_file_during_staging_rolls_back_other_files(root, monkeypatch):
    backend = WebRunService(root)
    directory = root / "users/alice"
    png, webp = directory / "background.png", directory / "background.webp"
    webp.write_bytes(image_bytes("WEBP"))
    png.write_bytes(image_bytes())
    os.utime(png, ns=(webp.stat().st_mtime_ns + 1000, webp.stat().st_mtime_ns + 1000))
    original = Path.replace
    def replace(path, target):
        if path == webp:
            raise PermissionError("held by another application")
        return original(path, target)
    monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises(PermissionError):
        backend.save_background("alice", image_bytes("JPEG"))
    assert png.read_bytes() == image_bytes()
    assert webp.read_bytes() == image_bytes("WEBP")
    assert not list(directory.glob(".background-*"))


def test_background_rejects_hardlinked_media(root):
    backend = WebRunService(root)
    outside = root / "outside.png"
    outside.write_bytes(image_bytes())
    try:
        os.link(outside, root / "users/alice/background.png")
    except OSError as exc:
        pytest.skip(str(exc))
    for operation in (lambda: backend.background_status("alice"),
                      lambda: backend.save_background("alice", image_bytes()),
                      lambda: backend.delete_background("alice")):
        with pytest.raises(InvalidRequestError):
            operation()
    assert outside.read_bytes() == image_bytes()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction safety")
def test_background_rejects_windows_directory_junction(root):
    outside = root / "outside-user"
    outside.mkdir()
    (outside / "user_config.json").write_text("{}")
    (outside / "background.png").write_bytes(image_bytes())
    directory = root / "users/alice"
    (directory / "user_config.json").unlink()
    directory.rmdir()
    result = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(directory), str(outside)],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
    backend = WebRunService(root)
    with pytest.raises(InvalidRequestError):
        backend.background_status("alice")
    with pytest.raises(InvalidRequestError):
        backend.patch_user_config("alice", {"web_ui": {"background": {"enabled": True}}})
    assert (outside / "user_config.json").read_text() == "{}"


def test_oversized_manual_file_is_not_loaded(root):
    backend = WebRunService(root)
    target = root / "users/alice/background.mp4"
    with target.open("wb") as stream:
        stream.truncate(401 * 1024 * 1024)
    status = backend.background_status("alice")
    assert not status["available"]
    assert status["error"]


def test_animated_image_is_not_a_background():
    stream = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(
        stream, "WEBP", save_all=True, append_images=[Image.new("RGB", (8, 8), "blue")], duration=100, loop=0,
    )
    with pytest.raises(InvalidRequestError):
        validate_background(stream.getvalue())


@pytest.mark.parametrize("location", ["file", "config", "user", "users"])
def test_background_rejects_redirected_paths(root, tmp_path, location):
    backend = WebRunService(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "user_config.json").write_text("{}")
    (outside / "background.png").write_bytes(image_bytes())
    directory = root / "users/alice"
    try:
        if location == "file":
            (directory / "background.png").symlink_to(outside / "background.png")
        elif location == "config":
            (directory / "user_config.json").unlink()
            (directory / "user_config.json").symlink_to(outside / "user_config.json")
        elif location == "user":
            (directory / "user_config.json").unlink()
            directory.rmdir()
            directory.symlink_to(outside, target_is_directory=True)
        else:
            original = root / "users"
            original.rename(root / "original-users")
            original.symlink_to(root / "original-users", target_is_directory=True)
    except OSError as exc:
        pytest.skip(str(exc))
    before = (outside / "background.png").read_bytes()
    for operation in (lambda: backend.background_status("alice"), lambda: backend.background_media("alice"),
                      lambda: backend.save_background("alice", image_bytes()), lambda: backend.delete_background("alice")):
        with pytest.raises(InvalidRequestError):
            operation()
    assert (outside / "background.png").read_bytes() == before


def test_concurrent_config_updates_and_background_replacements_are_serialized(root):
    backend = WebRunService(root)
    def update(index):
        if index % 2:
            backend.save_background("alice", image_bytes("PNG" if index % 3 else "WEBP"))
        else:
            backend.patch_user_config("alice", {"web_ui": {"background": {"blur": index % 20}}})
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(update, range(12)))
    config = load_config("alice", root)
    assert config["knowledge"]["use_shared"] is False
    assert backend.background_status("alice")["available"]
    assert len(list((root / "users/alice").glob("background.*"))) == 1


def test_background_endpoints_are_not_public(root):
    async def exercise():
        app = create_app(service=WebRunService(root), auth_config=WebAuthConfig(access_token="test-only"))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for method, path in (("GET", "/status"), ("GET", ""), ("DELETE", ""), ("POST", "")):
                response = await client.request(method, "/api/users/alice/background" + path)
                assert response.status_code == 401
    asyncio.run(exercise())


def test_template_background_copies_only_to_new_users(root):
    import shutil
    source = Path(__file__).resolve().parents[2]
    shutil.copytree(source / "template/user", root / "template/user")
    new_user = ensure_user("new-user", root)
    assert (new_user / "background.webp").read_bytes() == (source / "template/user/background.webp").read_bytes()
    assert validate_background((new_user / "background.webp").read_bytes()) == (".webp", "image/webp")
    assert load_config("new-user", root)["web_ui"]["background"] == DEFAULT_WEB_BACKGROUND
    ensure_user("alice", root)
    assert not list((root / "users/alice").glob("background.*"))
    owned = root / "users/bob/background.png"
    owned.write_bytes(image_bytes())
    before = owned.read_bytes()
    ensure_user("bob", root)
    assert owned.read_bytes() == before


def test_background_not_accepted_in_theme_preference_or_global_api(root):
    async def exercise():
        app = create_app(service=WebRunService(root))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.patch("/api/users/alice/preferences", json={"background": {"enabled": True}})).status_code == 400
            assert (await client.patch("/api/global-config", json={"changes": {"web_ui": {"background": {"enabled": True}}}})).status_code == 400
    asyncio.run(exercise())
