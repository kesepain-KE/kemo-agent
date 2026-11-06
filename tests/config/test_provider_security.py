"""R1 3.4: deny credential aliases and destinations outside instance policy."""
from __future__ import annotations

import copy
import json
from unittest.mock import Mock

import pytest

from run.config import ConfigError, load_config, merge_user_config, provider_runtime_config
from run.infra import runtime_event_snapshot


ALLOWED_ENVS = ("KEMO_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY")


def config(**provider):
    return {"provider": {"type": "chat", "model": "offline", "api_key": "offline-key", **provider}}


def write_config(root, global_config, user_config):
    (root / "config").mkdir()
    (root / "users/alice").mkdir(parents=True)
    (root / "config/global_config.yaml").write_text(json.dumps(global_config), encoding="utf-8")
    path = root / "users/alice/user_config.yaml"
    path.write_text(json.dumps(user_config), encoding="utf-8")
    return path


@pytest.mark.parametrize("env_name", ["HF_TOKEN", "WEB_PASSWORD", "PATH", "AWS_SECRET_ACCESS_KEY", "TEST_AGENT_KEY"])
@pytest.mark.parametrize("inline_key", ["", "inline-does-not-waive-validation"])
def test_arbitrary_env_name_rejected_before_read(monkeypatch, env_name, inline_key):
    getenv = Mock(return_value="must-not-read")
    monkeypatch.setattr("run.config.os.getenv", getenv)
    with pytest.raises(ConfigError) as caught:
        provider_runtime_config(config(api_key_env=env_name, api_key=inline_key))
    assert all(name in str(caught.value) for name in ALLOWED_ENVS)
    assert "迁移" in str(caught.value)
    assert not any(call.args[0] == env_name for call in getenv.call_args_list)
    assert "must-not-read" not in str(caught.value)


@pytest.mark.parametrize("env_name", ALLOWED_ENVS)
def test_explicit_provider_env_allowlist_is_usable(monkeypatch, env_name):
    monkeypatch.setenv(env_name, "offline-provider-key")
    assert provider_runtime_config(config(api_key="", api_key_env=env_name))["api_key"] == "offline-provider-key"


@pytest.mark.parametrize("url", [
    "http://api.openai.com/v1", "http://api.deepseek.com", "ftp://localhost/data",
    "https://unapproved.test/v1", "https://api.openai.com.unapproved.test",
    "https://api.openai.com@unapproved.test/v1", "https://user:secret@api.openai.com/v1",
    "//api.openai.com/v1", "https:///v1", "https://api.openai.com:99999/v1",
    "https://api.openai.com\\@unapproved.test/v1", "https://api.openai.com\n/v1",
    "http://localhost.unapproved.test/v1", "http://127.0.0.2/v1",
])
def test_insecure_or_unapproved_provider_url_is_rejected(url):
    with pytest.raises(ConfigError) as caught:
        provider_runtime_config(config(base_url=url))
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("url,expected", [
    ("https://api.openai.com", "https://api.openai.com/v1"),
    ("https://api.deepseek.com/proxy/api?region=a", "https://api.deepseek.com/proxy/api?region=a"),
    ("https://api.anthropic.com/v1", "https://api.anthropic.com/v1"),
    ("http://localhost:8741/proxy", "http://localhost:8741/proxy"),
    ("http://127.0.0.1:8741", "http://127.0.0.1:8741/v1"),
    ("https://API.OPENAI.COM:443/v1", "https://API.OPENAI.COM:443/v1"),
])
def test_builtin_destinations_keep_routes_and_local_http_exception(url, expected):
    assert provider_runtime_config(config(base_url=url))["base_url"] == expected


@pytest.mark.parametrize("provider_type,env_name", [("chat", "OPENAI_BASE_URL"), ("kemo", "KEMO_BASE_URL")])
def test_environment_url_fallback_cannot_bypass_host_policy(monkeypatch, provider_type, env_name):
    monkeypatch.setenv(env_name, "https://unapproved.test/private?token=do-not-log")
    with pytest.raises(ConfigError) as caught:
        provider_runtime_config(config(type=provider_type))
    assert "do-not-log" not in str(caught.value)


@pytest.mark.parametrize("url", ["https://[broken", "https://api.openai.com:bad"])
def test_malformed_environment_url_is_a_config_error(monkeypatch, url):
    monkeypatch.setenv("OPENAI_BASE_URL", url)
    with pytest.raises(ConfigError):
        provider_runtime_config(config())


def test_only_instance_config_can_extend_hosts_and_user_cannot_replace_it(tmp_path):
    global_config = {"provider_security": {"allowed_hosts": ["relay.test"]},
                     "provider_runtime": {"max_concurrent_requests": 7}}
    user_config = {**config(base_url="https://relay.test/proxy?region=a"),
                   "provider_security": {"allowed_hosts": ["unapproved.test"]}}
    before = copy.deepcopy((global_config, user_config))
    path = write_config(tmp_path, global_config, user_config)
    original = path.read_bytes()
    merged = load_config("alice", tmp_path)
    assert merged["provider_security"] == global_config["provider_security"]
    assert merged["provider_runtime"] == global_config["provider_runtime"]
    assert provider_runtime_config(merged)["base_url"] == "https://relay.test/proxy?region=a"
    merged["provider"]["base_url"] = "https://unapproved.test/v1"
    with pytest.raises(ConfigError):
        provider_runtime_config(merged)
    assert (global_config, user_config) == before
    assert path.read_bytes() == original


def test_absent_instance_policy_discards_user_injection():
    merged = merge_user_config({}, {**config(), "provider_security": {"allowed_hosts": ["unapproved.test"]}})
    assert "unapproved.test" not in json.dumps(merged.get("provider_security", {}))
    merged["provider"]["base_url"] = "https://unapproved.test"
    with pytest.raises(ConfigError):
        provider_runtime_config(merged)


@pytest.mark.parametrize("policy", [
    None, [], {"allowed_hosts": None}, {"allowed_hosts": "relay.test"},
    {"allowed_hosts": ["*"]}, {"allowed_hosts": ["*.relay.test"]},
    {"allowed_hosts": ["https://relay.test"]}, {"allowed_hosts": ["relay.test:443"]},
    {"allowed_hosts": ["relay.test/path"]}, {"allowed_hosts": ["relay.test?key=private"]},
    {"allowed_hosts": [" relay.test "]}, {"allowed_hosts": [1]},
])
def test_malformed_instance_host_policy_fails_closed(policy):
    with pytest.raises(ConfigError):
        provider_runtime_config({**config(), "provider_security": policy})


def test_added_host_does_not_allow_plain_http():
    with pytest.raises(ConfigError, match="https"):
        provider_runtime_config({**config(base_url="http://relay.test"),
                                 "provider_security": {"allowed_hosts": ["relay.test"]}})


def test_load_config_rejects_alias_at_startup_without_model_or_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "never-read-this")
    path = write_config(tmp_path, {}, {"provider": {"type": "kemo", "api_key_env": "HF_TOKEN"}})
    original = path.read_bytes()
    with pytest.raises(ConfigError, match="api_key_env"):
        load_config("alice", tmp_path)
    assert path.read_bytes() == original
    events = runtime_event_snapshot(tmp_path, "alice")
    assert any(e["title"] == "provider_config_override" and e["status"] == "failed" for e in events)
    assert "never-read-this" not in json.dumps(events)


def test_user_provider_override_has_value_free_audit_event(tmp_path):
    write_config(tmp_path, {"provider": {"model": "stale-global"}},
                 config(api_key="private-user-key", base_url="https://api.openai.com/v1?token=private-query"))
    merged = load_config("alice", tmp_path)
    assert merged["provider"]["model"] == "offline"
    events = runtime_event_snapshot(tmp_path, "alice")
    assert any(e["title"] == "provider_config_override" and e["status"] == "success" for e in events)
    serialized = json.dumps(events)
    assert "private-user-key" not in serialized and "private-query" not in serialized


@pytest.mark.parametrize("changes", [
    {"provider": {"api_key_env": "HF_TOKEN"}},
    {"provider": {"base_url": "http://api.openai.com/v1"}},
    {"provider": {"base_url": "https://unapproved.test/v1"},
     "provider_security": {"allowed_hosts": ["unapproved.test"]}},
])
def test_web_user_patch_cannot_persist_invalid_selector_or_authorize_own_host(tmp_path, monkeypatch, changes):
    from web.service import WebRunService
    from web.errors import InvalidRequestError

    path = write_config(tmp_path, {}, config())
    original = path.read_bytes()
    network = Mock(side_effect=AssertionError("configuration editing must not send credentials"))
    monkeypatch.setattr("urllib.request.urlopen", network)
    with pytest.raises(InvalidRequestError):
        WebRunService(tmp_path).patch_user_config("alice", changes)
    assert path.read_bytes() == original
    network.assert_not_called()


def test_web_instance_addition_then_user_destination_change_is_explicit_and_preserves_other_settings(tmp_path, monkeypatch):
    from web.service import WebRunService

    runtime = {"max_concurrent_requests": 7, "request_semaphore_timeout": 19}
    write_config(tmp_path, {"provider_runtime": runtime}, config(timeout=37, stream=False))
    service = WebRunService(tmp_path)
    network = Mock(side_effect=AssertionError("configuration editing must not access the gateway"))
    monkeypatch.setattr("urllib.request.urlopen", network)
    service.patch_global_config({"provider_security": {"allowed_hosts": ["relay.test"]}})
    service.patch_user_config("alice", {"provider": {"base_url": "https://relay.test/proxy?region=a"}})
    merged = load_config("alice", tmp_path)
    provider = provider_runtime_config(merged)
    assert provider["base_url"] == "https://relay.test/proxy?region=a"
    assert provider["timeout"] == 37 and provider["stream"] is False
    assert provider["api_key"] == "offline-key" and provider["model"] == "offline"
    assert merged["provider_runtime"] == runtime
    network.assert_not_called()
