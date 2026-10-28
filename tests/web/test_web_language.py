from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from run.config import ConfigError, load_config, merge_user_config
from run.config.web_ui import DEFAULT_WEB_LANGUAGE, WEB_LANGUAGES, web_language
from web.app import create_app
from web.service import WebRunService


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "config").mkdir()
    (tmp_path / "config/global_config.json").write_text("{}\n", encoding="utf-8")
    for user in ("alice", "bob"):
        (tmp_path / "users" / user).mkdir(parents=True)
        (tmp_path / "users" / user / "user_config.json").write_text(
            '{"schema_version":1,"knowledge":{"use_shared":false}}\n', encoding="utf-8"
        )
    return tmp_path


@pytest.mark.parametrize("language", sorted(WEB_LANGUAGES))
def test_language_persists_and_users_are_isolated(root: Path, language: str) -> None:
    async def exercise() -> None:
        app = create_app(service=WebRunService(root))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            before = await client.get("/api/users/alice/preferences")
            assert before.json()["appearance"]["language"] == DEFAULT_WEB_LANGUAGE
            saved = await client.patch("/api/users/alice/config", json={"changes": {"web_ui": {"language": language}}})
            assert saved.status_code == 200, saved.text
            document = json.loads((root / "users/alice/user_config.json").read_text("utf-8"))
            assert document["web_ui"]["language"] == language
            assert document["knowledge"] == {"use_shared": False}
            assert web_language(load_config("alice", root)) == language
            # Theme changes do not create a second language store.
            theme = await client.patch("/api/users/alice/preferences", json={"theme": "dark"})
            assert theme.status_code == 200
            assert theme.json()["appearance"]["language"] == language
            prefs = json.loads((root / "users/alice/web_preferences.json").read_text("utf-8"))
            assert "language" not in prefs["appearance"]
            assert (await client.get("/api/users/bob/preferences")).json()["appearance"]["language"] == "zh-CN"
            # Recreating the service simulates process/browser restart, not an in-memory preference.
        restarted = create_app(service=WebRunService(root))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=restarted), base_url="http://test") as client:
            assert (await client.get("/api/users/alice/preferences")).json()["appearance"]["language"] == language

    asyncio.run(exercise())


@pytest.mark.parametrize("invalid", ["ar", "xx", "EN", "", None, True, 1, [], {}])
def test_invalid_language_never_changes_config(root: Path, invalid: object) -> None:
    async def exercise() -> None:
        path = root / "users/alice/user_config.json"
        before = path.read_bytes()
        app = create_app(service=WebRunService(root))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.patch("/api/users/alice/config", json={"changes": {"web_ui": {"language": invalid}}})
            assert response.status_code == 400, response.text
            assert path.read_bytes() == before
    asyncio.run(exercise())


def test_language_only_belongs_to_user_config(root: Path) -> None:
    assert "web_ui" not in merge_user_config({"web_ui": {"language": "ar"}}, {})
    assert web_language(load_config("alice", root)) == "zh-CN"
    for invalid in (None, "en", []):
        with pytest.raises(ValueError):
            web_language({"web_ui": invalid}, strict=True)
    path = root / "users/alice/user_config.json"
    path.write_text('{"web_ui":{"language":"xx"}}', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config("alice", root)

    async def exercise() -> None:
        app = create_app(service=WebRunService(root))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.patch("/api/global-config", json={"changes": {"web_ui": {"language": "en"}}})).status_code == 400
            rejected = await client.patch("/api/users/bob/preferences", json={"language": "en"})
            assert rejected.status_code == 400
            assert rejected.json()["error"]["code"] == "invalid_request"
            assert not (root / "users/bob/web_preferences.json").exists()
    asyncio.run(exercise())


def test_template_and_frontend_supported_languages_match() -> None:
    source = Path(__file__).resolve().parents[2]
    template = json.loads((source / "template/user/user_config.json").read_text("utf-8"))
    assert template["web_ui"]["language"] == "zh-CN"
    import re
    locales = (source / "web/frontend/src/i18n/locales.ts").read_text("utf-8")
    assert set(re.findall(r"code: '([^']+)'", locales)) == WEB_LANGUAGES
    assert len(WEB_LANGUAGES) == 9


def test_retired_arabic_config_falls_back_without_breaking_other_saves(root: Path) -> None:
    path = root / "users/alice/user_config.json"
    document = {"web_ui": {"language": "ar"}, "knowledge": {"use_shared": False}}
    path.write_text(json.dumps(document), encoding="utf-8")
    before = path.read_bytes()
    assert load_config("alice", root)["web_ui"]["language"] == "zh-CN"
    assert path.read_bytes() == before  # No read-time migration of users' files.
    assert web_language(document) == "zh-CN"
    with pytest.raises(ValueError):
        web_language(document, strict=True)

    async def exercise() -> None:
        app = create_app(service=WebRunService(root))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            preferences = await client.get("/api/users/alice/preferences")
            assert preferences.json()["appearance"]["language"] == "zh-CN"
            assert path.read_bytes() == before
            saved = await client.patch("/api/users/alice/config", json={"changes": {"knowledge": {"use_shared": True}}})
            assert saved.status_code == 200, saved.text
            current = json.loads(path.read_text("utf-8"))
            assert current["knowledge"]["use_shared"] is True
            assert current["web_ui"]["language"] == "ar"
            assert load_config("alice", root)["web_ui"]["language"] == "zh-CN"
            after_save = path.read_bytes()
            rejected = await client.patch("/api/users/alice/config", json={"changes": {"web_ui": {"language": "ar"}}})
            assert rejected.status_code == 400
            assert path.read_bytes() == after_save
            changed = await client.patch("/api/users/alice/config", json={"changes": {"web_ui": {"language": "en"}}})
            assert changed.status_code == 200, changed.text
            assert load_config("alice", root)["web_ui"]["language"] == "en"
            assert json.loads(path.read_text("utf-8"))["knowledge"]["use_shared"] is True

    asyncio.run(exercise())
