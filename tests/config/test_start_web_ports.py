from __future__ import annotations

import os
from unittest.mock import patch

import start_web


def test_web_port_candidates_append_fallback_range_without_duplicates() -> None:
    candidates = list(start_web._web_port_candidates(1357))

    assert candidates[:3] == [(1357, False), (1358, False), (1359, False)]
    assert candidates[9] == (1366, False)
    assert candidates[10] == (24680, True)
    assert len(candidates) == 20
    assert len({port for port, _ in candidates}) == len(candidates)


def test_runtime_web_base_url_normalizes_wildcard_hosts() -> None:
    assert start_web._runtime_web_base_url("0.0.0.0", 24680) == (
        "http://127.0.0.1:24680"
    )
    assert start_web._runtime_web_base_url("::", 24680) == "http://[::1]:24680"


def test_main_publishes_fallback_endpoint_for_bridge_children(tmp_path, monkeypatch) -> None:
    (tmp_path / "users" / "alice").mkdir(parents=True)
    monkeypatch.delenv(start_web._RUNTIME_WEB_ENDPOINT_ENV, raising=False)

    def bind_result(_host: str, port: int) -> tuple[bool, str]:
        if port < start_web._WEB_FALLBACK_PORT:
            return False, "system port exclusion"
        return True, ""

    with patch.object(start_web, "project_root", return_value=tmp_path), \
        patch.object(start_web, "_can_bind", side_effect=bind_result), \
        patch.object(start_web, "_print_banner"), \
        patch.object(start_web, "_check_users", return_value=True), \
        patch.object(start_web.WebAuthConfig, "from_env", return_value=start_web.WebAuthConfig()), \
        patch("uvicorn.run") as run_server:
        assert start_web.main(["--skip-version-check", "--no-host"]) == 0

    assert run_server.call_args.kwargs["port"] == start_web._WEB_FALLBACK_PORT
    assert os.getenv(start_web._RUNTIME_WEB_ENDPOINT_ENV) == (
        "http://127.0.0.1:24680"
    )
