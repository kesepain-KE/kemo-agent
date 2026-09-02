from __future__ import annotations

import asyncio
import copy
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from typing import Any

import httpx
from PIL import Image
from unittest.mock import patch

from events import RunEvent
from agents._runtime.user_packages import create_user_agent_package
from provider.protocol.models import ModelCapabilities, ModelCatalogResponse
from provider.schema import ProviderError
from run.extensions import history_attachment_descriptors
from run.conversation import GuidanceInput
from run.scheduler import CronStore, normalize_task
from run.config import load_config
from run.history import (
    commit_window,
    empty_window,
    load_window,
    runtime_window_path,
    synthesize_items,
)
from run.history import (
    claim_pending_summary,
    close_session,
    finish_summary_claim,
    queue_summary,
    reserve_session,
)
from run.history import window_exists
from run.memory import MemoryStore
from tests.support.memory_db import update_fragment_metadata
from run.config import PROMPT_SECTION_ORDER, build_prompt_bundle
from run.extensions import clear_model_capability_cache
from run.tasks import PlanStore, normalize_plan
from web.app import create_app
from web.auth import WebAuthConfig, WebAuthConfigError, resolve_client_ip
from web.errors import NotFoundError, WebServiceError
from web.service import ActiveRun, WebRunService, _usage_cache_tokens
from web.services import _paths as path_helpers
from web.services.artifact_resolver import DownloadArtifactResolver


class FakeService:
    def __init__(
        self, *, events: list[RunEvent] | None = None, failure: Exception | None = None
    ) -> None:
        self.events = events or []
        self.failure = failure
        self.cancel_event: threading.Event | None = None
        self.seen: dict[str, Any] = {}

    def health(self):
        if self.failure:
            raise self.failure
        return {"status": "ok", "service": "kemo-agent-web", "version": 1}

    def users(self):
        return [{"name": "alice"}]

    def sessions(self, user, *, source="web", query="", limit=50, before=""):
        return {
            "user": user,
            "source": source,
            "query": query,
            "sessions": [],
            "has_more": False,
            "next_cursor": "",
        }

    def history(self, user, session_id, *, source="web"):
        return {
            "user": user,
            "source": source,
            "session_id": session_id,
            "messages": [],
        }

    def rename_session(self, user, session_id, title, *, source="web"):
        self.seen = {
            "user": user,
            "session_id": session_id,
            "title": title,
            "source": source,
        }
        return {
            "user": user,
            "source": source,
            "session": {
                "session_id": session_id,
                "window": "window-1",
                "title": title,
                "rounds": 1,
                "updated_at": "now",
            },
        }

    def delete_session(self, user, session_id, *, source="web", client_id=""):
        self.seen = {"user": user, "session_id": session_id, "source": source}
        if client_id:
            self.seen["client_id"] = client_id
        return {
            "user": user,
            "source": source,
            "session_id": session_id,
            "deleted": True,
        }

    def delete_all_sessions(self, user, *, source="web"):
        self.seen = {"user": user, "source": source}
        return {
            "user": user,
            "source": source,
            "deleted": True,
            "deleted_sessions": 0,
            "deleted_windows": 0,
        }

    def compress_session(self, user, session_id, *, source="web"):
        self.seen = {"user": user, "session_id": session_id, "source": source}
        return {
            "user": user,
            "source": source,
            "session_id": session_id,
            "requested": True,
            "compressed": True,
            "rounds_removed": 2,
            "summary_cache_exists": True,
            "context": {"rounds_removed": 2},
        }

    def extract_session_memory(self, user, session_id, *, source="web"):
        self.seen = {"user": user, "session_id": session_id, "source": source}
        return {
            "status": "completed",
            "user": user,
            "source": source,
            "session_id": session_id,
            "round": 2,
            "candidates": 1,
            "extraction": {"status": "completed", "candidate_count": 1},
        }

    def settings(self, user):
        return {"user": user, "schema_version": 1}

    def stream_chat(
        self,
        user,
        session_id,
        prompt,
        *,
        cancel_event,
        run_id="",
        source="web",
        client_id="",
        **kwargs,
    ):
        self.cancel_event = cancel_event
        self.seen = {
            "user": user,
            "session_id": session_id,
            "prompt": prompt,
            "run_id": run_id,
        }
        if client_id:
            self.seen["client_id"] = client_id
        if source != "web":
            self.seen["source"] = source
        self.seen.update(kwargs)
        return iter(self.events)

    def stream_plan(
        self, user, session_id, plan_id, *, cancel_event, run_id="", source="web", client_id=""
    ):
        self.cancel_event = cancel_event
        self.seen = {
            "user": user,
            "session_id": session_id,
            "plan_id": plan_id,
            "run_id": run_id,
        }
        if client_id:
            self.seen["client_id"] = client_id
        if source != "web":
            self.seen["source"] = source
        return iter(self.events)


class WebBackendTests(unittest.TestCase):
    def test_kemo_model_discovery_uses_only_saved_private_protocol_config(self) -> None:
        _, root = self.make_root()
        (root / "config").mkdir()
        (root / "config" / "global_config.json").write_text(
            json.dumps({"schema_version": 1}), "utf-8"
        )
        config_path = root / "users" / "alice" / "user_config.json"
        config_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "provider": {
                        "type": "chat",
                        "base_url": "https://chat.test/v1",
                        "model": "chat-model",
                        "api_key": "chat-secret",
                    },
                }
            ),
            "utf-8",
        )
        app = create_app(service=WebRunService(root))

        with patch("web.services.settings.KemoGatewayAdapter.models") as discover:
            blocked = self.request(app, "GET", "/api/users/alice/provider/models")
        self.assertEqual(blocked.status_code, 400)
        discover.assert_not_called()

        saved = {
            "schema_version": 1,
            "provider": {
                "type": "kemo",
                "base_url": "https://gateway.test",
                "model": "deepseek-deepseek-v4-flash",
                "api_key": "gateway-secret",
            },
        }
        config_path.write_text(json.dumps(saved), "utf-8")
        original = config_path.read_bytes()
        catalog = ModelCatalogResponse.model_validate(
            {
                "protocol_version": "1.0",
                "object": "kemo.model_list",
                "count": 1,
                "data": [
                    {
                        "id": "deepseek-deepseek-v4-flash",
                        "object": "kemo.model",
                        "provider_id": "deepseek",
                        "provider_model": "deepseek-v4-flash",
                        "task": "llm",
                        "capabilities_available": True,
                        "capabilities_url": "/model/models/deepseek-deepseek-v4-flash/capabilities",
                    }
                ],
            }
        )
        with patch(
            "web.services.settings.KemoGatewayAdapter.models", return_value=catalog
        ) as discover:
            response = self.request(app, "GET", "/api/users/alice/provider/models")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertTrue(response.json()["api_valid"])
        self.assertEqual(response.json()["data"][0]["id"], "deepseek-deepseek-v4-flash")
        discover.assert_called_once_with(task="llm")
        self.assertEqual(config_path.read_bytes(), original)

    def test_kemo_model_capabilities_use_catalog_url_and_keep_credentials_server_side(
        self,
    ) -> None:
        clear_model_capability_cache()
        _, root = self.make_root()
        (root / "config").mkdir()
        (root / "config" / "global_config.json").write_text(
            json.dumps({"schema_version": 1}), "utf-8"
        )
        (root / "users" / "alice" / "user_config.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "provider": {
                        "type": "kemo",
                        "base_url": "https://capabilities-gateway.test",
                        "model": "mapped-model",
                        "api_key": "gateway-secret-never-expose",
                        "reasoning_effort": "max",
                    },
                }
            ),
            "utf-8",
        )
        service = WebRunService(root)
        app = create_app(service=service)
        catalog = ModelCatalogResponse.model_validate(
            {
                "count": 1,
                "data": [
                    {
                        "id": "mapped-model",
                        "provider_id": "test",
                        "provider_model": "mapped-upstream",
                        "task": "llm",
                        "capabilities_available": True,
                        "capabilities_url": "/model/models/mapped-model/capabilities",
                    }
                ],
            }
        )
        capabilities = ModelCapabilities.model_validate(
            {
                "model": "mapped-model",
                "task": "llm",
                "reasoning": {
                    "supported": True,
                    "efforts": ["minimal", "low", "medium", "high", "max"],
                    "summary": True,
                },
                "extensions": {
                    "reasoning_effort_map": {"max": "high"},
                    "reasoning_policy": {"mode": "mapped", "collapsed": True},
                },
            }
        )
        with (
            patch(
                "web.services.settings.KemoGatewayAdapter.models",
                return_value=catalog,
            ) as discover,
            patch(
                "web.services.settings.KemoGatewayAdapter.capabilities",
                return_value=capabilities,
            ) as read_capabilities,
        ):
            response = self.request(
                app,
                "GET",
                "/api/users/alice/provider/model-capabilities?model=mapped-model&refresh=true",
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        payload = response.json()
        self.assertEqual(
            payload["capabilities"]["reasoning"]["efforts"],
            ["minimal", "low", "medium", "high", "max"],
        )
        self.assertTrue(
            payload["capabilities"]["extensions"]["reasoning_policy"]["collapsed"]
        )
        self.assertNotIn("gateway-secret-never-expose", response.text)
        discover.assert_called_once_with(task="llm")
        read_capabilities.assert_called_once_with(
            "mapped-model",
            capabilities_url="/model/models/mapped-model/capabilities",
        )

        for status in (401, 403, 404, 502):
            with self.subTest(status=status):
                clear_model_capability_cache()
                with patch(
                    "web.services.settings.KemoGatewayAdapter.capabilities",
                    side_effect=ProviderError("failed", status_code=status),
                ):
                    failed = self.request(
                        app,
                        "GET",
                        "/api/users/alice/provider/model-capabilities?model=mapped-model&refresh=true",
                    )
                self.assertEqual(failed.status_code, status)

    def test_file_space_lists_six_items_per_page_for_all_areas(self) -> None:
        _, root = self.make_root()
        upload_root = root / "users" / "alice" / "file_upload"
        download_root = root / "users" / "alice" / "download"
        tmp_root = root / "tmp"
        for directory in (upload_root, download_root, tmp_root):
            directory.mkdir(parents=True, exist_ok=True)
            for index in range(7):
                (directory / f"item-{index}.txt").write_text(str(index), "utf-8")
        nested = upload_root / "screenshots"
        nested.mkdir()
        (nested / "target.png").write_bytes(b"png")
        service = WebRunService(root)

        first = service.files("alice", "file_upload", page=1, page_size=6)
        self.assertEqual(len(first["entries"]), 6)
        self.assertEqual(first["entries"][0]["type"], "directory")
        self.assertEqual(first["pagination"]["total_items"], 8)
        self.assertEqual(first["pagination"]["total_pages"], 2)
        self.assertTrue(first["pagination"]["has_next"])

        last = service.files("alice", "file_upload", page=99, page_size=6)
        self.assertEqual(last["pagination"]["page"], 2)
        self.assertEqual(len(last["entries"]), 2)
        self.assertFalse(last["pagination"]["has_next"])

        nested_page = service.files(
            "alice", "file_upload", path="screenshots", page=1, page_size=6
        )
        self.assertEqual(
            [entry["relative_path"] for entry in nested_page["entries"]],
            ["screenshots/target.png"],
        )
        search_page = service.files(
            "alice", "file_upload", search="target", page=1, page_size=6
        )
        self.assertEqual(
            [entry["relative_path"] for entry in search_page["entries"]],
            ["screenshots/target.png"],
        )

        download_page = service.files("alice", "download", page=1, page_size=6)
        tmp_page = service.tmp_files(page=1, page_size=6)
        self.assertEqual(len(download_page["entries"]), 6)
        self.assertEqual(len(tmp_page["entries"]), 6)

        response = self.request(
            create_app(service=service),
            "GET",
            "/api/users/alice/files/file_upload?page=2&page_size=6",
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["pagination"]["page"], 2)
        self.assertEqual(len(response.json()["entries"]), 2)

    def test_normal_file_browsing_keeps_global_summary_but_has_a_hard_scan_budget(self) -> None:
        _, root = self.make_root()
        upload_root = root / "users" / "alice" / "file_upload"
        current = upload_root / "deep"
        for index in range(12):
            current.mkdir(parents=True, exist_ok=True)
            (current / f"level-{index}.txt").write_text("x", "utf-8")
            current = current / f"nested-{index}"
        service = WebRunService(root)

        with (
            patch("web.services._paths._DIRECTORY_SCAN_MAX_ENTRIES", 5),
            patch(
                "web.services._paths._visible_children",
                wraps=path_helpers._visible_children,
            ) as visible_children,
        ):
            page = service.files("alice", "file_upload", page=1, page_size=6)

        self.assertEqual([entry["relative_path"] for entry in page["entries"]], ["deep"])
        self.assertLessEqual(visible_children.call_count, 8)
        self.assertEqual(page["summary"]["scanned_entries"], 5)
        self.assertTrue(page["summary"]["truncated"])

    def test_recursive_file_search_has_a_hard_scan_budget(self) -> None:
        _, root = self.make_root()
        upload_root = root / "users" / "alice" / "file_upload"
        upload_root.mkdir(parents=True, exist_ok=True)
        for index in range(20):
            (upload_root / f"target-{index}.txt").write_text("x", "utf-8")
        service = WebRunService(root)

        with patch("web.services._paths._DIRECTORY_SCAN_MAX_ENTRIES", 5):
            page = service.files(
                "alice",
                "file_upload",
                search="target",
                page=1,
                page_size=100,
            )

        self.assertEqual(page["summary"]["scanned_entries"], 5)
        self.assertTrue(page["summary"]["truncated"])
        self.assertEqual(page["pagination"]["total_items"], 5)

    def test_file_summary_cache_is_reused_and_web_mutations_invalidate_it(self) -> None:
        _, root = self.make_root()
        upload_root = root / "users" / "alice" / "file_upload"
        upload_root.mkdir(parents=True, exist_ok=True)
        (upload_root / "first.txt").write_text("first", "utf-8")
        service = WebRunService(root)

        with patch(
            "web.services._paths._scan_directory_summary",
            wraps=path_helpers._scan_directory_summary,
        ) as scan_summary:
            first = service.files("alice", "file_upload")
            second = service.files("alice", "file_upload")
            self.assertEqual(scan_summary.call_count, 1)
            self.assertEqual(first["summary"], second["summary"])

            service.save_file(
                "alice",
                "file_upload",
                "nested/second.txt",
                b"second",
            )
            refreshed = service.files("alice", "file_upload")

        self.assertEqual(scan_summary.call_count, 2)
        self.assertEqual(refreshed["summary"]["total_files"], 2)
        self.assertEqual(refreshed["summary"]["total_dirs"], 1)

    def test_generated_artifact_resolves_nested_move_by_checksum(self) -> None:
        _, root = self.make_root()
        download_root = root / "users" / "alice" / "download"
        original = download_root / "generated.png"
        original.parent.mkdir(parents=True, exist_ok=True)
        payload = b"generated-media-payload"
        original.write_bytes(payload)
        checksum = hashlib.sha256(payload).hexdigest()

        moved = download_root / "reports" / "figures" / "framework.png"
        moved.parent.mkdir(parents=True)
        original.replace(moved)
        service = WebRunService(root)

        resolved, media_type = service.download_artifact(
            "alice",
            checksum,
            path="generated.png",
            size=len(payload),
        )
        self.assertTrue(resolved.samefile(moved))
        self.assertEqual(media_type, "image/png")
        resolver = service._download_artifact_resolver
        with patch.object(
            resolver,
            "_file_sha256",
            side_effect=AssertionError("缓存命中时不应重新读取完整文件"),
        ):
            cached, _ = service.download_artifact(
                "alice",
                checksum,
                path="generated.png",
                size=len(payload),
            )
        self.assertTrue(cached.samefile(moved))

        response = self.request(
            create_app(service=service),
            "GET",
            f"/api/users/alice/artifacts/{checksum}?path=generated.png&size={len(payload)}",
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, payload)
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertIn("inline", response.headers["content-disposition"])

        missing = self.request(
            create_app(service=service),
            "GET",
            f"/api/users/alice/artifacts/{'0' * 64}?path=generated.png&size={len(payload)}",
        )
        self.assertEqual(missing.status_code, 404)

    def test_generated_artifact_lookup_is_bounded_and_negative_cached(self) -> None:
        _, root = self.make_root()
        download_root = root / "users" / "alice" / "download"
        download_root.mkdir(parents=True, exist_ok=True)
        (download_root / "one.bin").write_bytes(b"a")
        (download_root / "two.bin").write_bytes(b"b")

        resolver = DownloadArtifactResolver(
            max_cache_entries=2,
            negative_ttl_seconds=30,
            max_scanned_files=10,
            max_hash_candidates=1,
        )
        with self.assertRaisesRegex(WebServiceError, "检索范围过大"):
            resolver.resolve(
                download_root,
                hashlib.sha256(b"missing").hexdigest(),
                path="missing.bin",
                expected_size=1,
            )

        with patch(
            "web.services.artifact_resolver.os.walk",
            side_effect=AssertionError("短期失败缓存命中时不应再次扫盘"),
        ):
            with self.assertRaisesRegex(WebServiceError, "检索范围过大"):
                resolver.resolve(
                    download_root,
                    hashlib.sha256(b"missing").hexdigest(),
                    path="missing.bin",
                    expected_size=1,
                )

        empty_root = root / "users" / "alice" / "download-empty"
        empty_root.mkdir(parents=True)
        missing_checksum = hashlib.sha256(b"not-created").hexdigest()
        with self.assertRaises(NotFoundError):
            resolver.resolve(
                empty_root,
                missing_checksum,
                path="missing.bin",
                expected_size=11,
            )
        with patch(
            "web.services.artifact_resolver.os.walk",
            side_effect=AssertionError("负缓存命中时不应再次扫盘"),
        ):
            with self.assertRaises(NotFoundError):
                resolver.resolve(
                    empty_root,
                    missing_checksum,
                    path="missing.bin",
                    expected_size=11,
                )
        self.assertLessEqual(len(resolver._cache), resolver.max_cache_entries)

    def test_disabled_prompt_sections_are_reported_as_disabled(self) -> None:
        _, root = self.make_root()
        (root / "config").mkdir()
        (root / "config" / "global_config.json").write_text(
            json.dumps({"schema_version": 1}), "utf-8"
        )
        (root / "users" / "alice" / "user_config.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "expand": {
                        "prompt_injection": False,
                        "realtime_injection": True,
                    },
                    "perception": {
                        "prompt_injection": False,
                        "realtime_injection": True,
                    },
                }
            ),
            "utf-8",
        )
        app = create_app(service=WebRunService(root))

        prompt = self.request(app, "GET", "/api/users/alice/prompt/sections")
        self.assertEqual(prompt.status_code, 200, prompt.text)
        prompt_states = {
            item["name"]: item["status"] for item in prompt.json()["sections"]
        }
        self.assertEqual(prompt_states["expand_data"], "disabled")
        self.assertEqual(prompt_states["perception"], "disabled")

        runtime = self.request(
            app,
            "GET",
            "/api/users/alice/runtime/status?sections=prompt",
        )
        self.assertEqual(runtime.status_code, 200, runtime.text)
        runtime_states = {
            item["id"]: item["state"]
            for item in runtime.json()["prompt"]["components"]
        }
        self.assertEqual(runtime_states["expand_data"], "disabled")
        self.assertEqual(runtime_states["perception"], "disabled")
        self.assertNotIn("[expand_data]", runtime.json()["prompt"]["content"])
        self.assertNotIn("[perception]", runtime.json()["prompt"]["content"])

    def test_runtime_and_overview_prompt_state_isolated_by_source_and_session(self) -> None:
        _, root = self.make_root()
        (root / "config").mkdir()
        (root / "config" / "global_config.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "history": {"schema_version": 3},
                    "memory": {"storage_schema_version": 4},
                    "agents": {"max_rounds": 30, "token_limit": 100000},
                    "tools": {"enabled": True},
                }
            ),
            "utf-8",
        )
        (root / "users" / "alice" / "user_config.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "provider": {
                        "type": "chat",
                        "base_url": "https://example.test/v1",
                        "model": "test-model",
                    },
                }
            ),
            "utf-8",
        )
        reserve_session(root, "alice", "web", "scope-a")
        reserve_session(root, "alice", "web", "scope-b")
        for session_id in ("scope-a", "scope-b"):
            window = empty_window("alice", "web", session_id)
            commit_window(
                root / "users" / "alice" / "history" / f"{session_id}-window",
                window,
            )

        store = PlanStore(root, "alice")
        for session_id, title in (
            ("scope-a", "A-only plan"),
            ("scope-b", "B-must-not-appear"),
        ):
            store.create(
                normalize_plan(
                    title=title,
                    description=f"{title} description",
                    user="alice",
                    source="web",
                    session_id=session_id,
                    status="approved",
                    steps=[
                        {
                            "step_id": "step_1",
                            "title": "Inspect",
                            "description": "read-only",
                            "tool_name": None,
                            "tool_arguments": {},
                            "critical": True,
                        }
                    ],
                )
            )

        service = WebRunService(root)
        with patch(
            "web.services.runtime_status.build_prompt_bundle",
            wraps=build_prompt_bundle,
        ) as build_bundle:
            scoped = service.runtime_status(
                "alice",
                source="web",
                session_id="scope-a",
                sections="summary,prompt",
            )
            self.assertTrue(
                any(
                    call.kwargs.get("source") == "web"
                    and call.kwargs.get("session_id") == "scope-a"
                    for call in build_bundle.call_args_list
                )
            )

        prompt_text = scoped["prompt"]["content"]
        self.assertIn("A-only plan", prompt_text)
        self.assertNotIn("B-must-not-appear", prompt_text)
        self.assertNotIn("scope-b", prompt_text)

        new_session = service.runtime_status(
            "alice",
            source="web",
            session_id="",
            sections="summary,prompt",
        )
        self.assertNotIn("A-only plan", new_session["prompt"]["content"])
        self.assertNotIn("B-must-not-appear", new_session["prompt"]["content"])

        overview = service.overview(
            "alice",
            source="web",
            session_id="scope-a",
        )
        self.assertEqual(overview["active_plan"]["title"], "A-only plan")
        self.assertTrue(
            all(
                "B-must-not-appear" not in str(item)
                for item in overview["activities"]
            )
        )

    def test_media_preview_is_inline_range_capable_and_enforces_limits(self) -> None:
        _, root = self.make_root()
        upload_root = root / "users" / "alice" / "file_upload"
        tmp_root = root / "tmp"
        upload_root.mkdir(parents=True, exist_ok=True)
        tmp_root.mkdir(parents=True, exist_ok=True)
        (upload_root / "small.png").write_bytes(b"\x89PNG\r\n\x1a\npreview")
        (upload_root / "track.mp3").write_bytes(b"0123456789")
        (tmp_root / "clip.mp4").write_bytes(b"abcdefghij")
        (upload_root / "notes.txt").write_text("not media", "utf-8")
        oversized = {
            "large.png": 10 * 1024 * 1024 + 1,
            "large.mp3": 100 * 1024 * 1024 + 1,
            "large.mp4": 300 * 1024 * 1024 + 1,
        }
        for filename, size in oversized.items():
            with (upload_root / filename).open("wb") as handle:
                handle.truncate(size)

        app = create_app(service=WebRunService(root))
        image = self.request(
            app,
            "GET",
            "/api/users/alice/files/file_upload/preview?path=small.png",
        )
        self.assertEqual(image.status_code, 200, image.text)
        self.assertEqual(image.headers["content-type"], "image/png")
        self.assertIn("inline", image.headers["content-disposition"])
        self.assertEqual(image.headers["x-content-type-options"], "nosniff")
        self.assertEqual(image.headers["cache-control"], "private, max-age=300")

        audio_range = self.request(
            app,
            "GET",
            "/api/users/alice/files/file_upload/preview?path=track.mp3",
            headers={"Range": "bytes=2-5"},
        )
        self.assertEqual(audio_range.status_code, 206, audio_range.text)
        self.assertEqual(audio_range.content, b"2345")
        self.assertEqual(audio_range.headers["content-range"], "bytes 2-5/10")
        self.assertEqual(audio_range.headers["accept-ranges"], "bytes")

        video_range = self.request(
            app,
            "GET",
            "/api/tmp/preview?path=clip.mp4",
            headers={"Range": "bytes=4-7"},
        )
        self.assertEqual(video_range.status_code, 206, video_range.text)
        self.assertEqual(video_range.content, b"efgh")
        self.assertEqual(video_range.headers["content-type"], "video/mp4")

        for filename in oversized:
            rejected = self.request(
                app,
                "GET",
                f"/api/users/alice/files/file_upload/preview?path={filename}",
            )
            self.assertEqual(rejected.status_code, 400, rejected.text)
            self.assertIn("预览上限", rejected.json()["error"]["message"])

        unsupported = self.request(
            app,
            "GET",
            "/api/users/alice/files/file_upload/preview?path=notes.txt",
        )
        self.assertEqual(unsupported.status_code, 400, unsupported.text)
        escaped = self.request(
            app,
            "GET",
            "/api/users/alice/files/file_upload/preview?path=..%2Fsmall.png",
        )
        self.assertEqual(escaped.status_code, 400, escaped.text)

    def test_media_preview_rejects_symbolic_links_when_supported(self) -> None:
        _, root = self.make_root()
        upload_root = root / "users" / "alice" / "file_upload"
        upload_root.mkdir(parents=True, exist_ok=True)
        outside = root / "outside.png"
        outside.write_bytes(b"outside")
        link = upload_root / "linked.png"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("当前系统不允许测试进程创建符号链接")
        response = self.request(
            create_app(service=WebRunService(root)),
            "GET",
            "/api/users/alice/files/file_upload/preview?path=linked.png",
        )
        self.assertEqual(response.status_code, 400, response.text)

    def test_upload_avoids_overwrite_and_chat_validates_attached_file_paths(
        self,
    ) -> None:
        _, root = self.make_root()
        captured: list[dict[str, Any]] = []

        def source(request, **_kwargs):
            captured.append(request)
            yield RunEvent(type="done")

        service = WebRunService(root, event_source=source)
        first = service.save_file("alice", "file_upload", "note.txt", b"first")
        second = service.save_file("alice", "file_upload", "note.txt", b"second")
        self.assertEqual(first["path"], "note.txt")
        self.assertFalse(first["renamed"])
        self.assertEqual(second["path"], "note (2).txt")
        self.assertTrue(second["renamed"])
        self.assertEqual(
            (root / "users" / "alice" / "file_upload" / "note.txt").read_bytes(),
            b"first",
        )
        self.assertEqual(
            (root / "users" / "alice" / "file_upload" / "note (2).txt").read_bytes(),
            b"second",
        )

        events = list(
            service.stream_chat(
                "alice",
                "upload-session",
                "读取附件",
                cancel_event=threading.Event(),
                uploaded_files=[second["path"]],
            )
        )
        self.assertEqual([event.type for event in events], ["done"])
        attached = captured[0]["uploaded_files"][0]
        self.assertEqual(attached["name"], "note (2).txt")
        self.assertEqual(attached["path"], "users/alice/file_upload/note (2).txt")
        self.assertEqual(attached["size"], 6)

        self.assertEqual(attached["mime_type"], "text/plain")
        self.assertEqual(attached["scope"], "file_upload")
        self.assertEqual(attached["relative_path"], "note (2).txt")
        self.assertFalse(attached["is_image"])
        self.assertRegex(attached["asset_id"], r"^asset_[0-9a-f]{32}$")
        self.assertRegex(attached["checksum_sha256"], r"^[0-9a-f]{64}$")
        attachment_only = list(
            service.stream_chat(
                "alice",
                "attachment-only",
                "",
                cancel_event=threading.Event(),
                uploaded_files=[first["path"]],
            )
        )
        self.assertEqual([event.type for event in attachment_only], ["done"])
        self.assertEqual(captured[-1]["prompt"], "")
        self.assertEqual(captured[-1]["uploaded_files"][0]["name"], "note.txt")
        with self.assertRaisesRegex(Exception, "上传文件不存在"):
            list(
                service.stream_chat(
                    "alice",
                    "missing-upload",
                    "读取附件",
                    cancel_event=threading.Event(),
                    uploaded_files=["missing.txt"],
                )
            )

    def test_image_attachment_thumbnail_survives_source_cleanup(self) -> None:
        _, root = self.make_root()
        image_bytes = io.BytesIO()
        Image.new("RGB", (1200, 800), (47, 126, 210)).save(
            image_bytes,
            format="PNG",
        )
        service = WebRunService(root)
        uploaded = service.save_file(
            "alice",
            "file_upload",
            "large-preview.png",
            image_bytes.getvalue(),
        )

        checksum = uploaded["checksum_sha256"]
        self.assertTrue(uploaded["thumbnail_available"])
        self.assertEqual(uploaded["media_kind"], "image")
        app = create_app(service=service)
        thumbnail_url = (
            f"/api/users/alice/attachment-thumbnails/{checksum}"
            "?path=large-preview.png"
        )
        preview = self.request(app, "GET", thumbnail_url)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.headers["content-type"], "image/webp")
        with Image.open(io.BytesIO(preview.content)) as thumbnail:
            self.assertLessEqual(thumbnail.width, 320)
            self.assertLessEqual(thumbnail.height, 240)

        service.delete_file("alice", "file_upload", "large-preview.png")
        retained = self.request(app, "GET", thumbnail_url)
        self.assertEqual(retained.status_code, 200, retained.text)
        self.assertEqual(retained.content, preview.content)

    def test_usage_cache_tokens_prefers_normalized_fields_and_preserves_zero(
        self,
    ) -> None:
        self.assertEqual(
            _usage_cache_tokens(
                {"cached_input_tokens": 12, "provider_raw": [{"cached_tokens": 3}]}
            ),
            12,
        )
        self.assertEqual(_usage_cache_tokens({"cached_prompt_tokens": 0}), 0)
        self.assertEqual(
            _usage_cache_tokens({"provider_raw": [{"cache_read_input_tokens": 7}]}),
            7,
        )

    def request(self, app, method: str, url: str, **kwargs):
        async def invoke():
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                return await client.request(method, url, **kwargs)

        return asyncio.run(invoke())

    def skill_zip(self, files: dict[str, str | bytes]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path, content in files.items():
                archive.writestr(path, content)
        return buffer.getvalue()

    def make_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "users" / "alice" / "history").mkdir(parents=True)
        (root / "users" / "bob" / "history").mkdir(parents=True)
        return temporary, root

    def parse_sse(self, text: str) -> list[tuple[str, dict[str, Any]]]:
        parsed = []
        for block in text.strip().split("\n\n"):
            lines = block.splitlines()
            event = lines[0].removeprefix("event: ")
            data = json.loads(lines[1].removeprefix("data: "))
            parsed.append((event, data))
        return parsed

    def test_health_does_not_touch_run_provider(self) -> None:
        fake = FakeService()
        response = self.request(create_app(service=fake), "GET", "/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertIsNone(fake.cancel_event)

    def test_version_endpoint_returns_sanitized_read_only_manifest(self) -> None:
        _, root = self.make_root()
        (root / "version.json").write_text(
            json.dumps(
                {
                    "name": "kemo-agent",
                    "version": "0.2.0",
                    "schema_version": 1,
                    "private_note": "must not leak",
                    "components": {
                        "web": {
                            "version": "0.2.1",
                            "description": "Web 前端+后端",
                            "secret": "hidden",
                        },
                        "core": {"version": "0.2.0", "description": "核心引擎"},
                    },
                },
                ensure_ascii=False,
            ),
            "utf-8",
        )
        response = self.request(
            create_app(root=root, service=WebRunService(root)),
            "GET",
            "/api/version",
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["version"], "0.2.0")
        self.assertEqual(response.json()["schema_version"], 1)
        self.assertTrue(response.json()["read_only"])
        self.assertEqual(
            [item["id"] for item in response.json()["components"]],
            ["core", "web"],
        )
        self.assertNotIn("must not leak", response.text)
        self.assertNotIn("hidden", response.text)

    def test_version_check_reports_remote_updates_without_changing_files(self) -> None:
        _, root = self.make_root()
        local = {
            "name": "kemo-agent",
            "version": "0.2.0",
            "schema_version": 1,
            "components": {
                "core": {"version": "0.2.0", "description": "核心引擎"},
                "agents": {"version": "0.2.0", "description": "子代理系统"},
                "plugins": {"version": "0.2.0", "description": "工具插件生态"},
                "web": {"version": "0.2.0", "description": "Web 前端+后端"},
            },
        }
        remote = copy.deepcopy(local)
        remote["version"] = "0.3.0"
        remote["components"]["core"]["version"] = "0.3.0"
        remote["components"]["web"]["version"] = "0.3.0"
        (root / "version.json").write_text(
            json.dumps(local, ensure_ascii=False), "utf-8"
        )
        fetches: list[tuple[str, float]] = []

        def fetcher(url: str, timeout: float) -> dict[str, Any]:
            fetches.append((url, timeout))
            return remote

        service = WebRunService(root, version_manifest_fetcher=fetcher)
        app = create_app(root=root, service=service)
        response = self.request(app, "GET", "/api/version/check")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "update_available")
        self.assertEqual(payload["local_version"], "0.2.0")
        self.assertEqual(payload["remote_version"], "0.3.0")
        self.assertTrue(payload["read_only"])
        self.assertEqual(
            payload["commands"]["all"],
            f"{'python' if os.name == 'nt' else 'python3'} update.py --module all",
        )
        self.assertEqual(
            [
                item["id"]
                for item in payload["components"]
                if item["status"] == "update_available"
            ],
            ["core", "web"],
        )
        self.assertEqual(len(fetches), 1)
        self.assertEqual(json.loads((root / "version.json").read_text("utf-8")), local)

        cached = self.request(app, "GET", "/api/version/check")
        self.assertEqual(cached.status_code, 200, cached.text)
        self.assertEqual(len(fetches), 1)
        refreshed = self.request(app, "GET", "/api/version/check?refresh=true")
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        self.assertEqual(len(fetches), 2)

    def test_version_check_reports_invalid_remote_manifest_as_failure(self) -> None:
        _, root = self.make_root()
        manifest = {
            "name": "kemo-agent",
            "version": "0.2.0",
            "schema_version": 1,
            "components": {
                component: {"version": "0.2.0", "description": component}
                for component in ("core", "agents", "plugins", "web")
            },
        }
        (root / "version.json").write_text(json.dumps(manifest), "utf-8")
        service = WebRunService(
            root,
            version_manifest_fetcher=lambda _url, _timeout: {
                "version": "not-a-version"
            },
        )
        response = self.request(
            create_app(root=root, service=service),
            "GET",
            "/api/version/check",
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "check_failed")
        self.assertEqual(response.json()["error"]["code"], "invalid_version_manifest")

    def test_restart_endpoint_launches_detached_helper_for_requested_port(self) -> None:
        _, root = self.make_root()
        app = create_app(root=root, service=FakeService())
        with patch("web.app._spawn_restart_helper", return_value=4321) as launcher:
            response = self.request(
                app,
                "POST",
                "/api/system/restart",
                json={"port": 1360},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(), {"ok": True, "port": 1360, "helper_pid": 4321}
        )
        launcher.assert_called_once_with(root.resolve(), 1360)

    def test_restart_endpoint_rejects_invalid_port_and_active_chat(self) -> None:
        invalid = self.request(
            create_app(service=FakeService()),
            "POST",
            "/api/system/restart",
            json={"port": 0},
        )
        self.assertEqual(invalid.status_code, 400)

        class ActiveService(FakeService):
            def has_active_runs(self) -> bool:
                return True

        active_app = create_app(service=ActiveService())
        with patch("web.app._spawn_restart_helper", return_value=4321) as launcher:
            active = self.request(
                active_app,
                "POST",
                "/api/system/restart",
                json={"port": 1360},
            )
            self.assertEqual(active.status_code, 409)
            self.assertEqual(active.json()["error"]["code"], "conflict")
            launcher.assert_not_called()

            forced = self.request(
                active_app,
                "POST",
                "/api/system/restart",
                json={"port": 1360, "force": True},
            )
        self.assertEqual(forced.status_code, 200, forced.text)
        self.assertEqual(forced.json(), {"ok": True, "port": 1360, "helper_pid": 4321})
        launcher.assert_called_once()

    def test_agents_expose_runtime_details_and_only_delete_user_layer(self) -> None:
        _, root = self.make_root()
        create_user_agent_package(
            root,
            "bob",
            {
                "name": "builtin_agent",
                "version": "2.1.0",
                "description": "global runtime agent",
                "instruction": "Apply the global agent rules.",
                "trigger_condition": "上下文达到全局阈值时",
            },
        )
        (root / "agents").mkdir()
        (root / "users" / "bob" / "agents" / "builtin_agent").replace(
            root / "agents" / "builtin_agent"
        )
        create_user_agent_package(
            root,
            "alice",
            {
                "name": "custom_agent",
                "version": "1.4.0",
                "description": "user runtime agent",
                "instruction": "Apply the user agent rules.",
                "trigger_condition": "用户明确指定 custom_agent 时",
            },
        )
        app = create_app(service=WebRunService(root))

        response = self.request(app, "GET", "/api/users/alice/agents")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(
            payload["summary"], {"total": 2, "enabled": 2, "global": 1, "user": 1}
        )
        user_agent = next(
            item for item in payload["agents"] if item["name"] == "custom_agent"
        )
        self.assertEqual(user_agent["version"], "1.4.0")
        self.assertEqual(user_agent["trigger"], "用户明确指定 custom_agent 时")
        self.assertIn("Apply the user agent rules.", user_agent["rules"])
        self.assertEqual(user_agent["executor"], "builtin:llm")

        rejected = self.request(app, "DELETE", "/api/users/alice/agents/builtin_agent")
        self.assertEqual(rejected.status_code, 404, rejected.text)
        self.assertTrue((root / "agents" / "builtin_agent").is_dir())

        deleted = self.request(app, "DELETE", "/api/users/alice/agents/custom_agent")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertTrue(deleted.json()["deleted"])
        self.assertFalse(
            (root / "users" / "alice" / "agents" / "custom_agent").exists()
        )
        self.assertEqual(
            self.request(
                app, "DELETE", "/api/users/alice/agents/custom_agent"
            ).status_code,
            404,
        )

    def test_skill_registry_management_respects_category_permissions_and_whitelists(
        self,
    ) -> None:
        _, root = self.make_root()
        (root / "config").mkdir()
        (root / "config" / "global_config.json").write_text(
            json.dumps(
                {"schema_version": 1, "tools": {"enabled": True, "timeout": 30}}
            ),
            "utf-8",
        )
        (root / "users" / "alice" / "user_config.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "plugins": {"whitelist": []},
                    "skills": {"shared_whitelist": []},
                }
            ),
            "utf-8",
        )
        plugin = root / "plugins" / "clock"
        plugin.mkdir(parents=True)
        manifest = {
            "name": "clock",
            "description": "读取当前时间",
            "input_schema": {"type": "object", "properties": {}},
            "version": "1.0.0",
            "enabled": True,
            "entrypoint": "tool.py:run",
        }
        (plugin / "SKILL.md").write_text(
            "# clock\n\n读取当前时间。\n\n## Tool\n\n```json\n"
            + json.dumps(manifest, ensure_ascii=False)
            + "\n```\n",
            "utf-8",
        )
        (plugin / "tool.py").write_text(
            "def run(*, context):\n    return {'ok': True}\n", "utf-8"
        )

        shared_root = root / "shared_skills"
        shared_root.mkdir()
        (shared_root / "register.py").write_text(
            "from pathlib import Path\n\ndef register(registry):\n    registry.add_skills('shared', Path(__file__).resolve().parent)\n",
            "utf-8",
        )
        shared = shared_root / "observer"
        shared.mkdir()
        (shared / "SKILL.md").write_text("# observer\n\n共享观察技能。\n", "utf-8")

        agent_skill = (
            root / "users" / "alice" / "user_skills" / "agent_create" / "generated"
        )
        agent_skill.mkdir(parents=True)
        (agent_skill / "SKILL.md").write_text(
            "# generated\n\n智能体生成技能。\n", "utf-8"
        )
        user_skill = root / "users" / "alice" / "user_skills" / "user_create" / "manual"
        user_skill.mkdir(parents=True)
        (user_skill / "SKILL.md").write_text("# manual\n\n用户自建技能。\n", "utf-8")

        app = create_app(service=WebRunService(root))
        listed = self.request(app, "GET", "/api/users/alice/skills")
        self.assertEqual(listed.status_code, 200, listed.text)
        payload = listed.json()
        self.assertEqual(payload["catalog_summary"]["total"], 4)
        self.assertEqual(
            {item["category"] for item in payload["items"]},
            {"builtin", "shared", "agent_generated", "user_created"},
        )

        preview = self.request(
            app,
            "GET",
            "/api/users/alice/skills/builtin/document?name=clock",
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertIn("读取当前时间", preview.json()["content"])
        archive = self.request(
            app,
            "GET",
            "/api/users/alice/skills/builtin/download?name=clock",
        )
        self.assertEqual(archive.status_code, 200, archive.text)
        self.assertTrue(archive.content.startswith(b"PK"))

        disabled = self.request(
            app,
            "PATCH",
            "/api/users/alice/skills/builtin/enabled?name=clock",
            json={"enabled": False},
        )
        self.assertEqual(disabled.status_code, 200, disabled.text)
        stored = json.loads(
            (root / "users" / "alice" / "user_config.json").read_text("utf-8")
        )
        self.assertEqual(stored["plugins"]["whitelist"], ["__kemo_none__"])
        refreshed = self.request(app, "GET", "/api/users/alice/skills").json()
        self.assertFalse(
            next(item for item in refreshed["items"] if item["id"] == "builtin:clock")[
                "enabled"
            ]
        )

        updated = self.request(
            app,
            "PUT",
            "/api/users/alice/skills/user_created/document?name=user_create%2Fmanual",
            json={"content": "# manual\n\n更新后的技能正文。\n"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertIn("更新后的技能正文", (user_skill / "SKILL.md").read_text("utf-8"))
        rejected = self.request(
            app,
            "DELETE",
            "/api/users/alice/skills/builtin?name=clock",
        )
        self.assertEqual(rejected.status_code, 400, rejected.text)
        deleted = self.request(
            app,
            "DELETE",
            "/api/users/alice/skills/user_created?name=user_create%2Fmanual",
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertFalse(user_skill.exists())

    def test_user_skill_zip_upload_installs_nested_packages_with_all_internal_files(
        self,
    ) -> None:
        _, root = self.make_root()
        (root / "config").mkdir()
        (root / "config" / "global_config.json").write_text(
            json.dumps({"schema_version": 1}), "utf-8"
        )
        (root / "users" / "alice" / "user_config.json").write_text(
            json.dumps({"schema_version": 1}), "utf-8"
        )
        app = create_app(service=WebRunService(root))
        archive = self.skill_zip(
            {
                "outer/alpha/skill.md": "# Alpha skill\n\n第一个技能。\n",
                "outer/alpha/tools/run.py": "print('alpha')\n",
                "outer/alpha/assets/prompt.txt": "resource",
                "another/beta/SKILL.md": "# Beta skill\n\n第二个技能。\n",
                "another/beta/scripts/build.sh": "echo beta\n",
            }
        )

        response = self.request(
            app,
            "POST",
            "/api/users/alice/skills/user-created/upload",
            files={"file": ("skills.zip", archive, "application/zip")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["category"], "user_created")
        self.assertEqual(payload["count"], 2)
        self.assertEqual(
            {item["name"] for item in payload["installed"]},
            {"user_create/alpha", "user_create/beta"},
        )
        destination = root / "users" / "alice" / "user_skills" / "user_create"
        self.assertTrue((destination / "alpha" / "SKILL.md").is_file())
        self.assertIn(
            "SKILL.md", {path.name for path in (destination / "alpha").iterdir()}
        )
        self.assertEqual(
            (destination / "alpha" / "tools" / "run.py").read_text("utf-8"),
            "print('alpha')\n",
        )
        self.assertEqual(
            (destination / "alpha" / "assets" / "prompt.txt").read_text("utf-8"),
            "resource",
        )
        listed = self.request(app, "GET", "/api/users/alice/skills").json()
        user_names = {
            item["name"]
            for item in listed["items"]
            if item["category"] == "user_created"
        }
        self.assertEqual(user_names, {"user_create/alpha", "user_create/beta"})

    def test_user_skill_zip_upload_rejects_invalid_archives_without_partial_install(
        self,
    ) -> None:
        _, root = self.make_root()
        service = WebRunService(root)
        app = create_app(service=service)
        endpoint = "/api/users/alice/skills/user-created/upload"

        not_zip = self.request(
            app,
            "POST",
            endpoint,
            files={"file": ("skill.zip", b"not-a-zip", "application/zip")},
        )
        self.assertEqual(not_zip.status_code, 400, not_zip.text)
        wrong_extension = self.request(
            app,
            "POST",
            endpoint,
            files={
                "file": (
                    "skill.tar",
                    self.skill_zip({"x/SKILL.md": "# X"}),
                    "application/zip",
                )
            },
        )
        self.assertEqual(wrong_extension.status_code, 400, wrong_extension.text)
        missing_manifest = self.request(
            app,
            "POST",
            endpoint,
            files={
                "file": (
                    "skill.zip",
                    self.skill_zip({"x/readme.md": "# X"}),
                    "application/zip",
                )
            },
        )
        self.assertEqual(missing_manifest.status_code, 400, missing_manifest.text)
        traversal = self.request(
            app,
            "POST",
            endpoint,
            files={
                "file": (
                    "skill.zip",
                    self.skill_zip({"../escape/SKILL.md": "# Escape"}),
                    "application/zip",
                )
            },
        )
        self.assertEqual(traversal.status_code, 400, traversal.text)
        self.assertFalse((root / "escape").exists())

        invalid_manifest = self.request(
            app,
            "POST",
            endpoint,
            files={
                "file": (
                    "skill.zip",
                    self.skill_zip(
                        {
                            "good/SKILL.md": "# Good\n",
                            "bad/SKILL.md": "missing level-one title\n",
                        }
                    ),
                    "application/zip",
                )
            },
        )
        self.assertEqual(invalid_manifest.status_code, 400, invalid_manifest.text)
        destination = root / "users" / "alice" / "user_skills" / "user_create"
        self.assertFalse((destination / "good").exists())
        self.assertFalse((destination / "bad").exists())

    def test_user_skill_zip_upload_conflict_does_not_overwrite_or_install_siblings(
        self,
    ) -> None:
        _, root = self.make_root()
        destination = root / "users" / "alice" / "user_skills" / "user_create"
        existing = destination / "existing"
        existing.mkdir(parents=True)
        original = "# Existing\n\n原始内容。\n"
        (existing / "SKILL.md").write_text(original, "utf-8")
        app = create_app(service=WebRunService(root))
        archive = self.skill_zip(
            {
                "upload/new-skill/SKILL.md": "# New skill\n",
                "upload/existing/SKILL.md": "# Replacement\n",
            }
        )

        response = self.request(
            app,
            "POST",
            "/api/users/alice/skills/user-created/upload",
            files={"file": ("skills.zip", archive, "application/zip")},
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual((existing / "SKILL.md").read_text("utf-8"), original)
        self.assertFalse((destination / "new-skill").exists())

    def test_auth_config_rejects_partial_password_and_generates_session_secret(
        self,
    ) -> None:
        with self.assertRaisesRegex(WebAuthConfigError, "必须同时配置"):
            WebAuthConfig(username="alice")
        generated = WebAuthConfig(access_token="token")
        another = WebAuthConfig(access_token="token")
        self.assertEqual(len(generated.session_secret), 64)
        self.assertNotEqual(generated.session_secret, another.session_secret)
        disabled = WebAuthConfig()
        self.assertFalse(disabled.enabled)
        self.assertEqual(disabled.public_summary()["enabled"], False)
        unlimited = WebAuthConfig.from_env({"WEB_AUTH_IP_MAX_FAILURES": ""})
        self.assertEqual(unlimited.ip_max_failures, 0)
        limited = WebAuthConfig.from_env(
            {
                "WEB_AUTH_IP_MAX_FAILURES": "5",
                "WEB_AUTH_IP_WINDOW_SECONDS": "600",
                "WEB_AUTH_IP_LOCK_SECONDS": "900",
                "WEB_AUTH_TRUSTED_PROXIES": "127.0.0.1,10.0.0.0/8",
            }
        )
        self.assertEqual(limited.ip_max_failures, 5)
        self.assertTrue(limited.public_summary()["ip_rate_limit_enabled"])
        self.assertTrue(WebAuthConfig.from_env({"WEB_SESSION_COOKIE_SECURE": "true"}).cookie_secure)
        self.assertFalse(WebAuthConfig.from_env({"WEB_SESSION_COOKIE_SECURE": "false"}).cookie_secure)
        with self.assertRaisesRegex(WebAuthConfigError, "必须是整数"):
            WebAuthConfig.from_env({"WEB_AUTH_IP_MAX_FAILURES": "five"})
        with self.assertRaisesRegex(WebAuthConfigError, "true/false"):
            WebAuthConfig.from_env({"WEB_SESSION_COOKIE_SECURE": "sometimes"})
        with self.assertRaisesRegex(WebAuthConfigError, "无效 IP"):
            WebAuthConfig.from_env({"WEB_AUTH_TRUSTED_PROXIES": "not-an-ip"})

    def test_client_ip_only_trusts_forwarded_header_from_configured_proxy(self) -> None:
        self.assertEqual(
            resolve_client_ip("203.0.113.9", "198.51.100.8", ("127.0.0.1",)),
            "203.0.113.9",
        )
        self.assertEqual(
            resolve_client_ip("127.0.0.1", "198.51.100.8", ("127.0.0.1",)),
            "198.51.100.8",
        )

    def test_secure_cookie_flag_is_applied_only_when_explicitly_enabled(self) -> None:
        app = create_app(
            service=FakeService(),
            auth_config=WebAuthConfig(
                access_token="token-secret",
                session_secret="session-secret",
                cookie_secure=True,
            ),
        )

        async def invoke():
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
                return await client.post("/api/auth/token", json={"token": "token-secret"})

        response = asyncio.run(invoke())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("secure", response.headers["set-cookie"].casefold())
        self.assertEqual(
            resolve_client_ip(
                "127.0.0.1",
                "198.51.100.8, 10.0.0.2",
                ("127.0.0.1", "10.0.0.0/8"),
            ),
            "198.51.100.8",
        )

    def test_token_and_password_auth_protect_business_api_and_persist_session(
        self,
    ) -> None:
        fake = FakeService()
        config = WebAuthConfig(
            access_token="token-secret",
            username="alice",
            password="password-secret",
            session_secret="session-secret",
            cookie_name="kemo_test_session",
        )
        app = create_app(service=fake, auth_config=config)

        async def invoke():
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                status = await client.get("/api/auth/status")
                health = await client.get("/api/health")
                denied = await client.get("/api/users")
                denied_chat = await client.post(
                    "/api/chat",
                    json={"user": "alice", "session_id": "s1", "prompt": "hello"},
                )
                header_only = await client.get(
                    "/api/users",
                    headers={"Authorization": "Bearer token-secret"},
                )
                wrong = await client.post("/api/auth/token", json={"token": "wrong"})
                password_before_token = await client.post(
                    "/api/auth/login",
                    json={"username": "alice", "password": "password-secret"},
                )
                token_step = await client.post(
                    "/api/auth/token", json={"token": "token-secret"}
                )
                denied_after_token = await client.get("/api/users")
                login = await client.post(
                    "/api/auth/login",
                    json={"username": "alice", "password": "password-secret"},
                )
                allowed = await client.get("/api/users")
                settings = await client.get("/api/users/alice/settings")
                refreshed = await client.get("/api/auth/status")
                logout = await client.post("/api/auth/logout")
                denied_again = await client.get("/api/users")
                token_step_again = await client.post(
                    "/api/auth/token", json={"token": "token-secret"}
                )
                login_again = await client.post(
                    "/api/auth/login",
                    json={"username": "alice", "password": "password-secret"},
                )
                allowed_by_password = await client.get("/api/users")
                return {
                    "status": status,
                    "health": health,
                    "denied": denied,
                    "denied_chat": denied_chat,
                    "header_only": header_only,
                    "wrong": wrong,
                    "password_before_token": password_before_token,
                    "token_step": token_step,
                    "denied_after_token": denied_after_token,
                    "allowed": allowed,
                    "settings": settings,
                    "refreshed": refreshed,
                    "logout": logout,
                    "denied_again": denied_again,
                    "login": login,
                    "token_step_again": token_step_again,
                    "login_again": login_again,
                    "allowed_by_password": allowed_by_password,
                }

        result = asyncio.run(invoke())
        self.assertEqual(result["status"].status_code, 200)
        self.assertFalse(result["status"].json()["authenticated"])
        self.assertEqual(result["health"].status_code, 200)
        self.assertEqual(result["denied"].status_code, 401)
        self.assertEqual(
            result["denied"].json()["error"]["code"], "authentication_required"
        )
        self.assertEqual(result["denied_chat"].status_code, 401)
        self.assertTrue(
            result["denied_chat"].headers["content-type"].startswith("application/json")
        )
        self.assertIsNone(fake.cancel_event)
        self.assertEqual(result["header_only"].status_code, 401)
        self.assertEqual(result["wrong"].status_code, 401)
        self.assertEqual(result["password_before_token"].status_code, 409)
        self.assertEqual(result["token_step"].status_code, 200)
        self.assertFalse(result["token_step"].json()["authenticated"])
        self.assertEqual(result["token_step"].json()["stage"], "password")
        self.assertEqual(result["denied_after_token"].status_code, 401)
        self.assertEqual(result["login"].status_code, 200)
        self.assertTrue(result["login"].json()["authenticated"])
        cookie = result["token_step"].headers["set-cookie"]
        self.assertIn("kemo_test_session=", cookie)
        self.assertIn("httponly", cookie.lower())
        self.assertIn("samesite=lax", cookie.lower())
        self.assertIn("max-age=7200", cookie.lower())
        self.assertEqual(result["allowed"].status_code, 200)
        self.assertTrue(result["settings"].json()["authentication"]["enabled"])
        for secret in ("token-secret", "password-secret", "session-secret"):
            self.assertNotIn(secret, result["settings"].text)
        self.assertTrue(result["refreshed"].json()["authenticated"])
        self.assertEqual(result["logout"].status_code, 200)
        self.assertEqual(result["denied_again"].status_code, 401)
        self.assertEqual(result["token_step_again"].status_code, 200)
        self.assertEqual(result["login_again"].status_code, 200)
        self.assertEqual(result["allowed_by_password"].status_code, 200)

    def test_password_only_authentication_establishes_full_session(self) -> None:
        app = create_app(
            service=FakeService(),
            auth_config=WebAuthConfig(
                username="alice",
                password="password-secret",
                session_secret="session-secret",
            ),
        )

        async def invoke():
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                status = await client.get("/api/auth/status")
                login = await client.post(
                    "/api/auth/login",
                    json={"username": "alice", "password": "password-secret"},
                )
                allowed = await client.get("/api/users")
                return status, login, allowed

        status, login, allowed = asyncio.run(invoke())
        self.assertEqual(status.json()["stage"], "password")
        self.assertFalse(status.json()["requires_both"])
        self.assertTrue(login.json()["authenticated"])
        self.assertEqual(login.json()["stage"], "authenticated")
        self.assertEqual(allowed.status_code, 200)

    def test_authentication_failures_are_limited_per_ip_and_stage(self) -> None:
        app = create_app(
            service=FakeService(),
            auth_config=WebAuthConfig(
                access_token="token-secret",
                session_secret="session-secret",
                ip_max_failures=2,
                ip_window_seconds=60,
                ip_lock_seconds=30,
            ),
        )

        async def invoke():
            first_transport = httpx.ASGITransport(
                app=app,
                raise_app_exceptions=False,
                client=("198.51.100.10", 1234),
            )
            second_transport = httpx.ASGITransport(
                app=app,
                raise_app_exceptions=False,
                client=("198.51.100.11", 1234),
            )
            async with httpx.AsyncClient(
                transport=first_transport, base_url="http://test"
            ) as first:
                first_failure = await first.post(
                    "/api/auth/token", json={"token": "wrong"}
                )
                locked = await first.post("/api/auth/token", json={"token": "wrong"})
                blocked_correct = await first.post(
                    "/api/auth/token", json={"token": "token-secret"}
                )
            async with httpx.AsyncClient(
                transport=second_transport, base_url="http://test"
            ) as second:
                other_ip = await second.post(
                    "/api/auth/token", json={"token": "token-secret"}
                )
            return first_failure, locked, blocked_correct, other_ip

        first_failure, locked, blocked_correct, other_ip = asyncio.run(invoke())
        self.assertEqual(first_failure.status_code, 401)
        self.assertEqual(locked.status_code, 429)
        self.assertEqual(locked.json()["error"]["code"], "auth_rate_limited")
        self.assertEqual(locked.headers["retry-after"], "30")
        self.assertEqual(blocked_correct.status_code, 429)
        self.assertEqual(other_ip.status_code, 200)

    def test_user_config_api_is_redacted_and_read_only(self) -> None:
        _, root = self.make_root()
        user_config_path = root / "users" / "alice" / "user_config.json"
        user_config_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "provider": {
                        "type": "kemo",
                        "base_url": "http://127.0.0.1:8741",
                        "model": "old-model",
                        "api_key": "disk-secret",
                        "stream": False,
                    },
                }
            ),
            "utf-8",
        )
        original = user_config_path.read_bytes()
        app = create_app(
            service=WebRunService(root),
            auth_config=WebAuthConfig(access_token="view-token"),
        )

        async def invoke():
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                denied = await client.get("/api/users/alice/config/full")
                await client.post("/api/auth/token", json={"token": "view-token"})
                loaded = await client.get("/api/users/alice/config/full")
                blocked = await client.put(
                    "/api/users/alice/config",
                    json={"config": {"schema_version": 1}},
                )
                return denied, loaded, blocked

        denied, loaded, blocked = asyncio.run(invoke())
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(loaded.status_code, 200)
        self.assertEqual(loaded.json()["config"]["provider"]["api_key"], "***")
        self.assertNotIn("disk-secret", loaded.text)
        self.assertEqual(
            set(loaded.json()),
            {"user", "config", "redacted_paths"},
        )
        self.assertEqual(blocked.status_code, 405)
        self.assertEqual(user_config_path.read_bytes(), original)

    def test_cookie_names_isolate_web_instances(self) -> None:
        first = create_app(
            service=FakeService(),
            auth_config=WebAuthConfig(
                access_token="token",
                session_secret="shared-secret",
                cookie_name="instance_one",
            ),
        )
        second = create_app(
            service=FakeService(),
            auth_config=WebAuthConfig(
                access_token="token",
                session_secret="shared-secret",
                cookie_name="instance_two",
            ),
        )

        async def invoke():
            first_transport = httpx.ASGITransport(app=first, raise_app_exceptions=False)
            second_transport = httpx.ASGITransport(
                app=second, raise_app_exceptions=False
            )
            async with httpx.AsyncClient(
                transport=first_transport, base_url="http://test"
            ) as first_client:
                login = await first_client.post(
                    "/api/auth/token", json={"token": "token"}
                )
                cookie = first_client.cookies.get("instance_one")
            async with httpx.AsyncClient(
                transport=second_transport, base_url="http://test"
            ) as second_client:
                second_client.cookies.set("instance_one", cookie, domain="test.local")
                denied = await second_client.get("/api/users")
            return login, denied

        login, denied = asyncio.run(invoke())
        self.assertEqual(login.status_code, 200)
        self.assertEqual(denied.status_code, 401)

    def test_frontend_dist_and_spa_routes_are_served(self) -> None:
        _, root = self.make_root()
        dist = root / "web" / "frontend" / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text(
            "<!doctype html><html><body><div id='root'>kemo UI</div></body></html>",
            "utf-8",
        )
        (dist / "assets" / "app.js").write_text("window.KEMO = true", "utf-8")
        (dist / "kemo-agent.jpg").write_bytes(b"kemo-image")
        app = create_app(root=root, service=FakeService())

        home = self.request(app, "GET", "/")
        self.assertEqual(home.status_code, 200)
        self.assertIn("text/html", home.headers["content-type"])
        self.assertIn("kemo UI", home.text)

        tasks = self.request(app, "GET", "/tasks?user=alice")
        self.assertEqual(tasks.status_code, 200)
        self.assertIn("kemo UI", tasks.text)

        asset = self.request(app, "GET", "/assets/app.js")
        self.assertEqual(asset.status_code, 200)
        self.assertIn("text/javascript", asset.headers["content-type"])
        self.assertEqual(asset.text, "window.KEMO = true")
        image = self.request(app, "GET", "/kemo-agent.jpg")
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.content, b"kemo-image")

        missing_api = self.request(app, "GET", "/api/does-not-exist")
        self.assertEqual(missing_api.status_code, 404)
        self.assertEqual(missing_api.headers["content-type"], "application/json")
        self.assertEqual(missing_api.json()["error"]["code"], "not_found")

    def test_frontend_reports_when_build_is_missing(self) -> None:
        _, root = self.make_root()
        response = self.request(
            create_app(root=root, service=FakeService()),
            "GET",
            "/",
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "ui_not_built")

    def test_users_sessions_and_history_use_real_service(self) -> None:
        _, root = self.make_root()
        upload = root / "users" / "alice" / "file_upload" / "history-note.txt"
        upload.parent.mkdir(parents=True, exist_ok=True)
        upload.write_bytes(b"history attachment")
        service = WebRunService(root)
        attachment = history_attachment_descriptors(
            service.require_uploaded_files("alice", ["history-note.txt"])
        )[0]
        window = empty_window("alice", "web", "s1")
        window["text"]["messages"] = [
            {"role": "user", "content": "hello", "attachments": [attachment]},
            {"role": "assistant", "content": "world"},
        ]
        window["think"]["rounds"] = [{"round": 1, "content": "inspect first"}]
        window["tool"]["rounds"] = [
            {
                "round": 1,
                "calls": [
                    {
                        "id": "call-1",
                        "name": "clock",
                        "arguments": {"zone": "local"},
                        "result": "x" * 5200,
                        "status": "completed",
                        "elapsed_ms": 12,
                    }
                ],
            }
        ]
        window["data"]["rounds"] = 1
        commit_window(root / "users" / "alice" / "history" / "window-1", window)
        app = create_app(service=WebRunService(root))

        users = self.request(app, "GET", "/api/users")
        self.assertEqual(
            [item["name"] for item in users.json()["users"]], ["alice", "bob"]
        )
        sessions = self.request(app, "GET", "/api/users/alice/sessions")
        self.assertEqual(sessions.json()["sessions"][0]["session_id"], "s1")
        history = self.request(app, "GET", "/api/users/alice/sessions/s1/history")
        self.assertEqual(len(history.json()["messages"]), 2)
        returned_attachment = history.json()["messages"][0]["attachments"][0]
        self.assertEqual(returned_attachment["name"], "history-note.txt")
        self.assertEqual(returned_attachment["relative_path"], "history-note.txt")
        self.assertTrue(returned_attachment["available"])
        self.assertNotIn("path", returned_attachment)
        trace = history.json()["round_traces"][0]
        self.assertEqual(trace["reasoning"], "inspect first")
        self.assertEqual(trace["tools"][0]["call_id"], "call-1")
        self.assertEqual(trace["tools"][0]["status"], "success")
        self.assertEqual(len(trace["tools"][0]["result_text"]), 5000)
        self.assertTrue(trace["tools"][0]["result_truncated"])

        upload.unlink()
        missing_history = self.request(
            app, "GET", "/api/users/alice/sessions/s1/history"
        ).json()
        self.assertFalse(missing_history["messages"][0]["attachments"][0]["available"])

    def test_history_paginates_complete_rounds_from_newest_to_oldest(self) -> None:
        _, root = self.make_root()
        window = empty_window("alice", "web", "s1")
        for round_number in range(1, 46):
            window["text"]["messages"].extend(
                [
                    {"role": "user", "content": f"question {round_number}"},
                    {"role": "assistant", "content": f"answer {round_number}"},
                ]
            )
            window["think"]["rounds"].append(
                {"round": round_number, "content": f"reasoning {round_number}"}
            )
            window["data"]["round_metrics"].append(
                {
                    "round": round_number,
                    "usage": {"total_tokens": round_number},
                    "elapsed_ms": round_number,
                    "tool_calls": 0,
                    "guidance": [],
                }
            )
        window["data"]["rounds"] = 45
        commit_window(root / "users" / "alice" / "history" / "window-1", window)
        app = create_app(service=WebRunService(root))

        latest = self.request(
            app,
            "GET",
            "/api/users/alice/sessions/s1/history?limit=20",
        ).json()
        self.assertEqual(len(latest["messages"]), 40)
        self.assertEqual(latest["messages"][0]["content"], "question 26")
        self.assertEqual(latest["messages"][-1]["content"], "answer 45")
        self.assertEqual(
            latest["pagination"],
            {
                "limit": 20,
                "total_rounds": 45,
                "first_round": 26,
                "last_round": 45,
                "has_more_before": True,
                "next_before": 26,
            },
        )
        self.assertEqual(
            [item["round"] for item in latest["round_metrics"]],
            list(range(26, 46)),
        )
        self.assertEqual(
            [item["round"] for item in latest["round_traces"]],
            list(range(26, 46)),
        )

        earlier = self.request(
            app,
            "GET",
            "/api/users/alice/sessions/s1/history?limit=20&before=26",
        ).json()
        self.assertEqual(earlier["messages"][0]["content"], "question 6")
        self.assertEqual(earlier["messages"][-1]["content"], "answer 25")
        self.assertEqual(earlier["pagination"]["next_before"], 6)
        self.assertTrue(earlier["pagination"]["has_more_before"])

        oldest = self.request(
            app,
            "GET",
            "/api/users/alice/sessions/s1/history?limit=20&before=6",
        ).json()
        self.assertEqual(oldest["messages"][0]["content"], "question 1")
        self.assertEqual(oldest["messages"][-1]["content"], "answer 5")
        self.assertFalse(oldest["pagination"]["has_more_before"])
        self.assertIsNone(oldest["pagination"]["next_before"])

        full = self.request(app, "GET", "/api/users/alice/sessions/s1/history").json()
        self.assertEqual(len(full["messages"]), 90)

    def test_active_create_and_close_session_api_uses_durable_reservations(
        self,
    ) -> None:
        _, root = self.make_root()
        app = create_app(service=WebRunService(root))

        first = self.request(app, "GET", "/api/users/alice/sessions/active")
        self.assertEqual(first.status_code, 200)
        first_payload = first.json()
        first_id = first_payload["session"]["session_id"]
        self.assertTrue(first_payload["created"])
        self.assertTrue(first_id.startswith("conv_"))
        history_dir = root / "users" / "alice" / "history"
        self.assertFalse((history_dir / first_id).exists())
        renamed = self.request(
            app,
            "PATCH",
            f"/api/users/alice/sessions/{first_id}",
            json={"title": "零轮会话"},
        )
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.json()["session"]["title"], "零轮会话")

        restored = self.request(app, "GET", "/api/users/alice/sessions/active")
        self.assertFalse(restored.json()["created"])
        self.assertEqual(restored.json()["session"]["session_id"], first_id)

        created = self.request(app, "POST", "/api/users/alice/sessions")
        second_id = created.json()["session"]["session_id"]
        self.assertNotEqual(second_id, first_id)
        closed = self.request(
            app,
            "POST",
            f"/api/users/alice/sessions/{second_id}/close",
        )
        self.assertEqual(closed.status_code, 200)
        self.assertEqual(closed.json()["session"]["state"], "closed")
        self.assertEqual(closed.json()["memory"]["reason"], "no_archive")

        replacement = self.request(app, "GET", "/api/users/alice/sessions/active")
        replacement_id = replacement.json()["session"]["session_id"]
        self.assertTrue(replacement.json()["created"])
        self.assertNotIn(replacement_id, {first_id, second_id})
        deleted = self.request(
            app,
            "DELETE",
            f"/api/users/alice/sessions/{replacement_id}",
        )
        self.assertEqual(deleted.status_code, 200)

    def test_client_scoped_sessions_and_leases_isolate_browser_pages(self) -> None:
        _, root = self.make_root()
        service = WebRunService(root)
        app = create_app(service=service)
        client_a = "web_client_a"
        client_b = "web_client_b"

        active_a = self.request(
            app, "GET", f"/api/users/alice/sessions/active?client_id={client_a}"
        ).json()
        active_b = self.request(
            app, "GET", f"/api/users/alice/sessions/active?client_id={client_b}"
        ).json()
        self.assertEqual(active_a["active_key"], f"interactive:alice:{client_a}")
        self.assertEqual(active_b["active_key"], f"interactive:alice:{client_b}")
        self.assertNotEqual(active_a["active_key"], active_b["active_key"])

        service._active_runs["run_client_a"] = ActiveRun(
            "run_client_a", "alice", active_a["session"]["session_id"]
        )
        created = self.request(
            app,
            "POST",
            "/api/users/alice/sessions",
            json={"client_id": client_b},
        )
        self.assertEqual(created.status_code, 200, created.text)
        service._active_runs.pop("run_client_a", None)

        session_id = created.json()["session"]["session_id"]
        leased = self.request(
            app,
            "POST",
            f"/api/users/alice/sessions/{session_id}/lease",
            json={"client_id": client_a},
        )
        self.assertEqual(leased.status_code, 200, leased.text)
        self.assertEqual(leased.json()["active_clients"], 2)

        deferred = self.request(
            app,
            "POST",
            f"/api/users/alice/sessions/{session_id}/close?client_id={client_a}",
        )
        self.assertEqual(deferred.status_code, 200, deferred.text)
        self.assertFalse(deferred.json()["closed"])
        self.assertTrue(deferred.json()["deferred"])
        self.assertEqual(deferred.json()["active_clients"], 1)

        closed = self.request(
            app,
            "POST",
            f"/api/users/alice/sessions/{session_id}/close?client_id={client_b}",
        )
        self.assertEqual(closed.status_code, 200, closed.text)
        self.assertTrue(closed.json()["closed"])
        self.assertFalse(closed.json()["deferred"])

    def test_active_session_supports_app_source_without_sharing_web_binding(self) -> None:
        _, root = self.make_root()
        app = create_app(service=WebRunService(root))
        client_id = "app_android-device"

        app_active = self.request(
            app,
            "GET",
            f"/api/users/alice/sessions/active?source=app&client_id={client_id}",
        )
        self.assertEqual(app_active.status_code, 200, app_active.text)
        app_payload = app_active.json()
        self.assertEqual(app_payload["active_key"], f"app:alice:{client_id}")
        self.assertEqual(app_payload["session"]["source"], "app")
        self.assertTrue(app_payload["session"]["session_id"].startswith("app-"))

        restored = self.request(
            app,
            "GET",
            f"/api/users/alice/sessions/active?source=app&client_id={client_id}",
        ).json()
        self.assertFalse(restored["created"])
        self.assertEqual(
            restored["session"]["session_id"],
            app_payload["session"]["session_id"],
        )

        web_active = self.request(
            app,
            "GET",
            f"/api/users/alice/sessions/active?client_id={client_id}",
        ).json()
        self.assertEqual(web_active["active_key"], f"interactive:alice:{client_id}")
        self.assertEqual(web_active["session"]["source"], "web")
        self.assertNotEqual(
            web_active["session"]["session_id"],
            app_payload["session"]["session_id"],
        )

    def test_session_delete_rejects_other_page_lease_and_allows_expired_lease(
        self,
    ) -> None:
        _, root = self.make_root()
        service = WebRunService(root)
        app = create_app(service=service)
        client_a = "web_client_a"
        client_b = "web_client_b"
        created = self.request(
            app,
            "POST",
            "/api/users/alice/sessions",
            json={"client_id": client_a},
        ).json()
        session_id = created["session"]["session_id"]
        self.request(
            app,
            "POST",
            f"/api/users/alice/sessions/{session_id}/lease",
            json={"client_id": client_b},
        )

        blocked = self.request(
            app,
            "DELETE",
            f"/api/users/alice/sessions/{session_id}?client_id={client_a}",
        )
        self.assertEqual(blocked.status_code, 409, blocked.text)

        with service._active_runs_lock:
            service._session_leases[("alice", "web", session_id)][client_b] = 0.0
        deleted = self.request(
            app,
            "DELETE",
            f"/api/users/alice/sessions/{session_id}?client_id={client_a}",
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)

    def test_stream_chat_uses_client_scoped_history_key_and_touches_lease(self) -> None:
        _, root = self.make_root()
        requests: list[dict[str, Any]] = []

        def source(request, **_kwargs):
            requests.append(request)
            yield RunEvent(type="done")

        service = WebRunService(root, event_source=source)
        events = list(
            service.stream_chat(
                "alice",
                "client-session",
                "hello",
                cancel_event=threading.Event(),
                client_id="web_client_a",
            )
        )
        self.assertEqual([event.type for event in events], ["done"])
        self.assertEqual(
            requests[0]["_history_active_key"],
            "interactive:alice:web_client_a",
        )
        self.assertIn(
            "web_client_a",
            service._session_leases[("alice", "web", "client-session")],
        )

    def test_stream_chat_keeps_app_source_and_device_scoped_history_key(self) -> None:
        _, root = self.make_root()
        requests: list[dict[str, Any]] = []

        def source(request, **_kwargs):
            requests.append(request)
            yield RunEvent(type="done")

        service = WebRunService(root, event_source=source)
        events = list(
            service.stream_chat(
                "alice",
                "app-session",
                "hello",
                cancel_event=threading.Event(),
                source="app",
                client_id="app_android-device-a",
            )
        )
        self.assertEqual([event.type for event in events], ["done"])
        self.assertEqual(requests[0]["source"], "app")
        self.assertEqual(
            requests[0]["_history_active_key"],
            "app:alice:app_android-device-a",
        )
        self.assertIn(
            "app_android-device-a",
            service._session_leases[("alice", "app", "app-session")],
        )

    def test_delete_all_sessions_includes_uncommitted_reservations(self) -> None:
        _, root = self.make_root()
        app = create_app(service=WebRunService(root))
        self.request(app, "GET", "/api/users/alice/sessions/active")
        self.request(app, "POST", "/api/users/alice/sessions")

        deleted = self.request(app, "DELETE", "/api/users/alice/sessions")

        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["deleted_sessions"], 2)
        sessions = self.request(app, "GET", "/api/users/alice/sessions")
        self.assertEqual(sessions.json()["sessions"], [])

    def test_session_rename_is_persisted_without_changing_sort_time(self) -> None:
        _, root = self.make_root()
        window_dir = root / "users" / "alice" / "history" / "window-1"
        commit_window(window_dir, empty_window("alice", "web", "s1"))
        app = create_app(service=WebRunService(root))
        before = self.request(app, "GET", "/api/users/alice/sessions").json()[
            "sessions"
        ][0]
        stale_window = load_window(window_dir)

        response = self.request(
            app,
            "PATCH",
            "/api/users/alice/sessions/s1",
            json={"title": "  我的重要对话  "},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["session"]["title"], "我的重要对话")
        after = self.request(app, "GET", "/api/users/alice/sessions").json()[
            "sessions"
        ][0]
        self.assertEqual(after["title"], "我的重要对话")
        self.assertEqual(after["updated_at"], before["updated_at"])
        stored = load_window(window_dir)["data"]
        self.assertEqual(stored["title"], "我的重要对话")
        commit_window(window_dir, stale_window)
        stored_after_stale_commit = load_window(window_dir)["data"]
        self.assertEqual(stored_after_stale_commit["title"], "我的重要对话")

    def test_session_list_uses_cursor_pagination_and_table_body_search(self) -> None:
        _, root = self.make_root()
        for index in range(12):
            window = empty_window("alice", "web", f"page-{index:02d}")
            window["text"]["messages"] = [
                {"role": "user", "content": f"普通消息 {index}"},
                {
                    "role": "assistant",
                    "content": "只有第七条包含检索暗号" if index == 7 else "普通回答",
                },
            ]
            window["data"]["rounds"] = 1
            commit_window(
                root / "users" / "alice" / "history" / f"window-{index:02d}",
                window,
            )
        app = create_app(service=WebRunService(root))

        first = self.request(app, "GET", "/api/users/alice/sessions?limit=5").json()
        self.assertEqual(len(first["sessions"]), 5)
        self.assertTrue(first["has_more"])
        self.assertTrue(first["next_cursor"])
        second = self.request(
            app,
            "GET",
            f"/api/users/alice/sessions?limit=5&before={first['next_cursor'].replace('+', '%2B')}",
        ).json()
        self.assertEqual(len(second["sessions"]), 5)
        self.assertTrue(
            {item["session_id"] for item in first["sessions"]}.isdisjoint(
                {item["session_id"] for item in second["sessions"]}
            )
        )

        searched = self.request(
            app,
            "GET",
            "/api/users/alice/sessions?query=%E6%A3%80%E7%B4%A2%E6%9A%97%E5%8F%B7",
        ).json()
        self.assertEqual(
            [item["session_id"] for item in searched["sessions"]],
            ["page-07"],
        )

    def test_session_rename_validates_title(self) -> None:
        _, root = self.make_root()
        commit_window(
            root / "users" / "alice" / "history" / "window-1",
            empty_window("alice", "web", "s1"),
        )
        app = create_app(service=WebRunService(root))
        for title in ("", "   ", "bad\nname", "x" * 81):
            with self.subTest(title=repr(title)):
                response = self.request(
                    app,
                    "PATCH",
                    "/api/users/alice/sessions/s1",
                    json={"title": title},
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"]["code"], "invalid_request")

    def test_session_delete_removes_all_matching_windows_and_preserves_other_users(
        self,
    ) -> None:
        _, root = self.make_root()
        for name in ("window-1", "window-2"):
            commit_window(
                root / "users" / "alice" / "history" / name,
                empty_window("alice", "web", "s1"),
            )
        bob_window = root / "users" / "bob" / "history" / "window-1"
        commit_window(bob_window, empty_window("bob", "web", "s1"))
        app = create_app(service=WebRunService(root))

        response = self.request(app, "DELETE", "/api/users/alice/sessions/s1")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["deleted"])
        self.assertEqual(
            self.request(app, "GET", "/api/users/alice/sessions").json()["sessions"],
            [],
        )
        self.assertEqual(
            self.request(
                app, "GET", "/api/users/alice/sessions/s1/history"
            ).status_code,
            404,
        )
        self.assertTrue(window_exists(bob_window))
        self.assertEqual(
            self.request(app, "DELETE", "/api/users/alice/sessions/s1").status_code,
            404,
        )

    def test_session_manual_compression_uses_runtime_compressor(self) -> None:
        _, root = self.make_root()
        commit_window(
            root / "users" / "alice" / "history" / "window-1",
            empty_window("alice", "web", "s1"),
        )
        observed: dict[str, Any] = {}

        def compressor(request: dict[str, Any], *, root: Path) -> dict[str, Any]:
            observed.update({"request": request, "root": root})
            return {
                "context": {"rounds_removed": 3},
                "summary_cache": "history.sqlite3#history_context_summaries",
                "compressed": True,
                "compression_verified": True,
            }

        app = create_app(service=WebRunService(root, context_compressor=compressor))
        response = self.request(
            app,
            "POST",
            "/api/users/alice/sessions/s1/compress",
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["compressed"])
        self.assertEqual(response.json()["rounds_removed"], 3)
        self.assertEqual(response.json()["memory"]["status"], "skipped")
        self.assertEqual(response.json()["memory"]["reason"], "already_processed")
        self.assertFalse(response.json()["memory"]["retry_pending"])
        self.assertEqual(
            observed["request"],
            {
                "user": "alice",
                "source": "web",
                "session_id": "s1",
                "memory_extraction_policy": "queue",
            },
        )
        self.assertEqual(observed["root"], root.resolve())

    def test_session_manual_compression_extracts_pending_memory(self) -> None:
        _, root = self.make_root()
        (root / "config").mkdir()
        (root / "config" / "global_config.json").write_text("{}", "utf-8")
        window = empty_window("alice", "web", "s1")
        window["text"]["messages"] = [
            {"role": "user", "content": "remember this"},
            {"role": "assistant", "content": "saved answer"},
        ]
        window["think"]["rounds"] = [{"round": 1, "content": "reasoning"}]
        window["tool"]["rounds"] = [{"round": 1, "calls": []}]
        window["data"]["rounds"] = 1
        window["data"]["memory_processed_round"] = 0
        archive = root / "users" / "alice" / "history" / "window-1"
        commit_window(archive, window)

        def compressor(request: dict[str, Any], *, root: Path) -> dict[str, Any]:
            return {
                "context": {"rounds_removed": 1},
                "summary_cache": "history.sqlite3#history_context_summaries",
                "compressed": True,
                "compression_verified": True,
            }

        with (
            patch("web.service.AgentRunner", return_value=object()),
            patch(
                "run.conversation.runtime._extract_round_memory",
                return_value={
                    "status": "completed",
                    "candidate_count": 1,
                    "error": None,
                },
            ) as extracted,
        ):
            response = self.request(
                create_app(service=WebRunService(root, context_compressor=compressor)),
                "POST",
                "/api/users/alice/sessions/s1/compress",
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["compressed"])
        self.assertEqual(payload["memory"]["status"], "queued")
        self.assertEqual(payload["memory"]["pending_rounds"], 1)
        self.assertEqual(payload["memory"]["target_round"], 1)
        self.assertFalse(payload["memory"]["retry_pending"])
        extracted.assert_not_called()
        stored = load_window(archive)
        self.assertEqual(stored["data"]["memory_processed_round"], 0)
        self.assertEqual(stored["data"]["memory_status"], "queued")
        self.assertEqual(stored["data"]["memory_target_round"], 1)

    def test_session_manual_compression_fails_when_memory_queue_registration_fails(
        self,
    ) -> None:
        _, root = self.make_root()
        commit_window(
            root / "users" / "alice" / "history" / "window-1",
            empty_window("alice", "web", "s1"),
        )

        def compressor(request: dict[str, Any], *, root: Path) -> dict[str, Any]:
            return {
                "context": {"rounds_removed": 2},
                "summary_cache": "history.sqlite3#history_context_summaries",
                "compressed": True,
                "compression_verified": True,
            }

        service = WebRunService(root, context_compressor=compressor)
        with patch(
            "web.service.queue_memory_extraction",
            side_effect=RuntimeError("memory queue unavailable"),
        ):
            response = self.request(
                create_app(service=service),
                "POST",
                "/api/users/alice/sessions/s1/compress",
            )

        self.assertEqual(response.status_code, 500, response.text)
        self.assertEqual(
            response.json()["error"]["message"],
            "上下文压缩成功，但后台记忆任务登记失败",
        )

    def test_session_manual_compression_reports_summary_failure_without_queueing(
        self,
    ) -> None:
        _, root = self.make_root()
        commit_window(
            root / "users" / "alice" / "history" / "window-1",
            empty_window("alice", "web", "s1"),
        )

        def compressor(request: dict[str, Any], *, root: Path) -> dict[str, Any]:
            return {
                "context": {
                    "rounds_removed": 2,
                    "summary": {
                        "failed": True,
                        "error": "摘要响应格式无效",
                    },
                },
                "memory": {
                    "status": "failed",
                    "reason": "context_summary_failed",
                },
            }

        with patch("web.service.queue_memory_extraction") as queued:
            response = self.request(
                create_app(service=WebRunService(root, context_compressor=compressor)),
                "POST",
                "/api/users/alice/sessions/s1/compress",
            )

        self.assertEqual(response.status_code, 500, response.text)
        self.assertEqual(
            response.json()["error"]["message"],
            "手动上下文压缩失败：摘要响应格式无效",
        )
        queued.assert_not_called()

    def test_session_memory_extraction_uses_latest_complete_round(self) -> None:
        _, root = self.make_root()
        (root / "config").mkdir()
        (root / "config" / "global_config.json").write_text("{}", "utf-8")
        window = empty_window("alice", "web", "s1")
        window["text"]["messages"] = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "answer one"},
            {"role": "user", "content": "remember latest"},
            {"role": "assistant", "content": "answer two"},
        ]
        window["think"]["rounds"] = [
            {"round": 1, "content": "think one"},
            {"round": 2, "content": "think two"},
        ]
        window["tool"]["rounds"] = [
            {"round": 1, "calls": []},
            {"round": 2, "calls": [{"name": "lookup", "status": "completed"}]},
        ]
        window["data"]["rounds"] = 2
        commit_window(root / "users" / "alice" / "history" / "window-1", window)
        observed: dict[str, Any] = {}

        def extract(**kwargs):
            observed.update(kwargs)
            return {
                "status": "completed",
                "candidate_count": 2,
                "candidates": [],
                "source": {"source": "round_commit"},
                "error": None,
            }

        def persist(**kwargs):
            return {
                "status": "completed",
                "candidate_count": kwargs["analysis"]["candidate_count"],
                "error": None,
            }

        with (
            patch("web.service.AgentRunner", return_value=object()),
            patch(
                "run.memory.analysis.analyze_memory_batch", side_effect=extract
            ) as extracted,
            patch(
                "run.memory.analysis.persist_round_memory_analysis", side_effect=persist
            ),
        ):
            app = create_app(service=WebRunService(root))
            response = self.request(
                app,
                "POST",
                "/api/users/alice/sessions/s1/extract-memory",
            )
            repeated = self.request(
                app,
                "POST",
                "/api/users/alice/sessions/s1/extract-memory",
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["candidates"], 2)
        self.assertEqual(len(observed["rounds"]), 1)
        extracted_round = observed["rounds"][0]
        self.assertEqual(extracted_round["round"], 2)
        self.assertEqual(extracted_round["messages"][0]["content"], "remember latest")
        self.assertEqual(extracted_round["messages"][1]["content"], "answer two")
        self.assertEqual(extracted_round["think"]["content"], "think two")
        self.assertEqual(extracted_round["tools"][0]["name"], "lookup")
        self.assertEqual(extracted.call_count, 1)
        self.assertEqual(repeated.json()["reason"], "already_processed")
        stored = load_window(root / "users" / "alice" / "history" / "window-1")
        self.assertEqual(stored["data"]["memory_processed_round"], 2)

    def test_extract_memory_route_forwards_to_service(self) -> None:
        fake = FakeService()
        response = self.request(
            create_app(service=fake),
            "POST",
            "/api/users/alice/sessions/s1/extract-memory",
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["candidates"], 1)
        self.assertEqual(
            fake.seen,
            {"user": "alice", "session_id": "s1", "source": "web"},
        )

    def test_session_undo_last_round_updates_archive_and_runtime(self) -> None:
        _, root = self.make_root()
        window = empty_window("alice", "web", "s1")
        window["text"]["messages"] = [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "retry me"},
            {"role": "assistant", "content": "partial answer"},
        ]
        window["think"]["rounds"] = [
            {"round": 1, "content": "think one"},
            {"round": 2, "content": "think two"},
        ]
        window["tool"]["rounds"] = [
            {"round": 1, "calls": []},
            {"round": 2, "calls": [{"id": "call-2", "name": "demo"}]},
        ]
        window["data"]["rounds"] = 2
        window["data"]["memory_processed_round"] = 2
        window["data"]["memory_status"] = "completed"
        window["data"]["round_metrics"] = [
            {
                "round": 1,
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                    "estimated": False,
                },
            },
            {
                "round": 2,
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 4,
                    "total_tokens": 24,
                    "estimated": False,
                },
            },
        ]
        window["data"]["token_usage"] = {
            "prompt_tokens": 30,
            "completion_tokens": 6,
            "total_tokens": 36,
            "estimated": False,
        }
        window["items"] = synthesize_items(window)
        archive_path = root / "users" / "alice" / "history" / "window-1"
        commit_window(archive_path, window)
        runtime_path = runtime_window_path(archive_path)
        runtime_window = copy.deepcopy(window)
        runtime_window["data"]["context"] = {
            "round_offset": 0,
            "workspace_rounds": 2,
        }
        commit_window(runtime_path, runtime_window)
        app = create_app(service=WebRunService(root))

        response = self.request(
            app,
            "POST",
            "/api/users/alice/sessions/s1/undo-last-round",
            json={"expected_round": 2, "prompt": "retry me"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["rolled_back"])
        self.assertEqual(response.json()["remaining_rounds"], 1)
        self.assertEqual(
            response.json()["content"], [{"type": "text", "text": "retry me"}]
        )
        for path in (archive_path, runtime_path):
            rolled_back = load_window(path)
            self.assertEqual(rolled_back["data"]["rounds"], 1)
            self.assertEqual(
                rolled_back["text"]["messages"],
                [
                    {"role": "user", "content": "first question"},
                    {"role": "assistant", "content": "first answer"},
                ],
            )
            self.assertEqual(
                [item["round"] for item in rolled_back["think"]["rounds"]], [1]
            )
            self.assertEqual(
                [item["round"] for item in rolled_back["tool"]["rounds"]], [1]
            )
            self.assertTrue(
                all(
                    (item.get("metadata") or {}).get("round") == 1
                    for item in rolled_back["items"]["items"]
                )
            )
            self.assertEqual(rolled_back["data"]["token_usage"]["total_tokens"], 12)
            self.assertEqual(rolled_back["data"]["memory_processed_round"], 1)
            self.assertEqual(rolled_back["data"]["memory_status"], "completed")
            self.assertNotIn("context", rolled_back["data"])

        interrupted = self.request(
            app,
            "POST",
            "/api/users/alice/sessions/s1/undo-last-round",
            json={"expected_round": 2, "prompt": "retry me"},
        )
        self.assertEqual(interrupted.status_code, 200, interrupted.text)
        self.assertFalse(interrupted.json()["rolled_back"])
        self.assertEqual(load_window(archive_path)["data"]["rounds"], 1)

    def test_session_undo_last_round_rejects_stale_or_active_requests(self) -> None:
        _, root = self.make_root()
        window = empty_window("alice", "web", "s1")
        window["text"]["messages"] = [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "answer"},
        ]
        window["think"]["rounds"] = [{"round": 1, "content": "think"}]
        window["tool"]["rounds"] = [{"round": 1, "calls": []}]
        window["data"]["rounds"] = 1
        window["items"] = synthesize_items(window)
        archive_path = root / "users" / "alice" / "history" / "window-1"
        commit_window(archive_path, window)
        service = WebRunService(root)
        app = create_app(service=service)

        stale = self.request(
            app,
            "POST",
            "/api/users/alice/sessions/s1/undo-last-round",
            json={"expected_round": 1, "prompt": "different"},
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(load_window(archive_path)["data"]["rounds"], 1)

        service._active_runs["run_busy_undo"] = ActiveRun(
            "run_busy_undo", "alice", "s1"
        )
        active = self.request(
            app,
            "POST",
            "/api/users/alice/sessions/s1/undo-last-round",
            json={"expected_round": 1, "prompt": "question"},
        )
        self.assertEqual(active.status_code, 409)
        self.assertEqual(load_window(archive_path)["data"]["rounds"], 1)

    def test_delete_all_sessions_is_scoped_and_reports_counts(self) -> None:
        _, root = self.make_root()
        for directory, session_id in (
            ("window-1", "s1"),
            ("window-2", "s1"),
            ("window-3", "s2"),
        ):
            commit_window(
                root / "users" / "alice" / "history" / directory,
                empty_window("alice", "web", session_id),
            )
        bob_window = root / "users" / "bob" / "history" / "window-1"
        commit_window(bob_window, empty_window("bob", "web", "s1"))
        app = create_app(service=WebRunService(root))

        response = self.request(app, "DELETE", "/api/users/alice/sessions")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["deleted_sessions"], 2)
        self.assertEqual(response.json()["deleted_windows"], 3)
        self.assertEqual(
            self.request(app, "GET", "/api/users/alice/sessions").json()["sessions"],
            [],
        )
        self.assertTrue(window_exists(bob_window))

    def test_active_session_cannot_be_deleted(self) -> None:
        _, root = self.make_root()
        window_dir = root / "users" / "alice" / "history" / "window-1"
        commit_window(window_dir, empty_window("alice", "web", "busy"))
        service = WebRunService(root)
        service._active_runs["run_busy_123"] = ActiveRun(
            "run_busy_123", "alice", "busy"
        )
        app = create_app(service=service)

        response = self.request(app, "DELETE", "/api/users/alice/sessions/busy")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "conflict")
        self.assertTrue(window_exists(window_dir))

        bulk_response = self.request(app, "DELETE", "/api/users/alice/sessions")
        self.assertEqual(bulk_response.status_code, 409)
        self.assertEqual(bulk_response.json()["error"]["code"], "conflict")
        self.assertTrue(window_exists(window_dir))

    def test_history_summary_manual_retry_api_requeues_and_wakes_worker(self) -> None:
        _, root = self.make_root()
        archive = root / "users" / "alice" / "history" / "summary-window"
        window = empty_window("alice", "web", "summary-retry")
        window["text"]["messages"] = [
            {"role": "user", "content": "请总结"},
            {"role": "assistant", "content": "稍后生成摘要"},
        ]
        window["data"]["rounds"] = 1
        commit_window(archive, window)
        close_session(root, "alice", "web", "summary-retry")
        queue_summary(root, "alice", "web", "summary-retry")
        claim = claim_pending_summary(root, "alice")
        finish_summary_claim(
            root,
            "alice",
            "web",
            "summary-retry",
            claim_id=claim["summary_claim_id"],
            error={"message": "首次生成失败"},
        )
        wakes: list[bool] = []
        service = WebRunService(root, summary_waker=lambda: wakes.append(True))
        app = create_app(service=service)

        response = self.request(
            app,
            "POST",
            "/api/users/alice/sessions/summary-retry/summary/retry",
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["queued"])
        self.assertEqual(response.json()["session"]["summary_status"], "queued")
        self.assertEqual(wakes, [True])
        missing = self.request(
            app,
            "POST",
            "/api/users/alice/sessions/missing/summary/retry",
        )
        self.assertEqual(missing.status_code, 404)

        open_window = empty_window("alice", "web", "open-summary")
        open_window["text"]["messages"] = [{"role": "user", "content": "尚未关闭"}]
        open_window["data"]["rounds"] = 1
        commit_window(
            root / "users" / "alice" / "history" / "open-summary-window",
            open_window,
        )
        conflict = self.request(
            app,
            "POST",
            "/api/users/alice/sessions/open-summary/summary/retry",
        )
        self.assertEqual(conflict.status_code, 409)

    def test_not_found_invalid_source_and_cross_user_session(self) -> None:
        _, root = self.make_root()
        window = empty_window("alice", "web", "private")
        window["text"]["messages"] = [{"role": "user", "content": "secret"}]
        commit_window(root / "users" / "alice" / "history" / "window-1", window)
        app = create_app(service=WebRunService(root))

        missing_user = self.request(app, "GET", "/api/users/mallory/sessions")
        self.assertEqual(missing_user.status_code, 404)
        self.assertEqual(missing_user.json()["error"]["code"], "not_found")
        invalid_source = self.request(
            app, "GET", "/api/users/alice/sessions?source=message%3A..%2Fescape"
        )
        self.assertEqual(invalid_source.status_code, 400)
        cross_user = self.request(app, "GET", "/api/users/bob/sessions/private/history")
        self.assertEqual(cross_user.status_code, 404)
        self.assertNotIn("secret", cross_user.text)

    def test_history_read_api_treats_web_cli_and_message_archives_equally(self) -> None:
        _, root = self.make_root()
        for source, session_id, content in (
            ("web", "web-session", "web content"),
            ("app", "app-session", "app content"),
            ("cli", "cli-session", "cli content"),
            ("message:telegram", "telegram-session", "telegram content"),
        ):
            window = empty_window("alice", source, session_id)
            window["text"]["messages"] = [
                {"role": "user", "content": content},
                {"role": "assistant", "content": f"reply {content}"},
            ]
            window["data"].update(
                {
                    "rounds": 1,
                    "memory_status": "queued" if source.startswith("message:") else "completed",
                    "memory_processed_round": 0 if source.startswith("message:") else 1,
                    "memory_target_round": 1,
                    "memory_queue_reason": "session_closed",
                }
            )
            commit_window(
                root / "users" / "alice" / "history" / f"window-{session_id}",
                window,
            )
            if source != "web":
                close_session(root, "alice", source, session_id)

        app = create_app(service=WebRunService(root))
        all_sessions = self.request(
            app, "GET", "/api/users/alice/sessions?source=all"
        )
        self.assertEqual(all_sessions.status_code, 200, all_sessions.text)
        payload = all_sessions.json()
        self.assertEqual(payload["source"], "all")
        self.assertEqual(
            {item["source"] for item in payload["sessions"]},
            {"web", "app", "cli", "message:telegram"},
        )
        app_session = next(
            item for item in payload["sessions"] if item["source"] == "app"
        )
        self.assertEqual(app_session["chain"], "interactive")
        telegram = next(
            item
            for item in payload["sessions"]
            if item["source"] == "message:telegram"
        )
        self.assertEqual(telegram["chain"], "message")
        self.assertEqual(telegram["bound_platform"], "telegram")
        self.assertEqual(telegram["memory_status"], "queued")
        self.assertEqual(telegram["memory_processed_round"], 0)
        self.assertEqual(telegram["memory_target_round"], 1)
        self.assertEqual(telegram["memory_queue_reason"], "session_closed")

        cli_history = self.request(
            app,
            "GET",
            "/api/users/alice/sessions/cli-session/history?source=cli",
        )
        self.assertEqual(cli_history.status_code, 200, cli_history.text)
        self.assertEqual(cli_history.json()["messages"][0]["content"], "cli content")
        app_history = self.request(
            app,
            "GET",
            "/api/users/alice/sessions/app-session/history?source=app",
        )
        self.assertEqual(app_history.status_code, 200, app_history.text)
        self.assertEqual(app_history.json()["messages"][0]["content"], "app content")
        telegram_history = self.request(
            app,
            "GET",
            "/api/users/alice/sessions/telegram-session/history?source=message%3Atelegram",
        )
        self.assertEqual(telegram_history.status_code, 200, telegram_history.text)
        self.assertEqual(
            telegram_history.json()["messages"][0]["content"],
            "telegram content",
        )

        read_only_boundary = self.request(
            app,
            "DELETE",
            "/api/users/alice/sessions/telegram-session?source=message%3Atelegram",
        )
        self.assertEqual(read_only_boundary.status_code, 400)
        app_delete = self.request(
            app,
            "DELETE",
            "/api/users/alice/sessions/app-session?source=app",
        )
        self.assertEqual(app_delete.status_code, 200, app_delete.text)

    def test_validation_and_internal_error_are_sanitized(self) -> None:
        invalid = self.request(
            create_app(service=FakeService()), "POST", "/api/chat", json={}
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertNotIn("input", invalid.text)
        secret = "API_KEY=super-secret"
        failed = self.request(
            create_app(service=FakeService(failure=RuntimeError(secret))),
            "GET",
            "/api/health",
        )
        self.assertEqual(failed.status_code, 500)
        self.assertNotIn(secret, failed.text)
        self.assertEqual(failed.json()["error"]["message"], "Web 服务处理请求失败")

    def test_web_run_generator_keeps_thread_affinity(self) -> None:
        _, root = self.make_root()
        lock = threading.RLock()
        thread_ids: list[int] = []

        def source(*_args, **_kwargs):
            with lock:
                thread_ids.append(threading.get_ident())
                try:
                    yield RunEvent(type="text_delta", content="ok")
                    yield RunEvent(type="done")
                finally:
                    thread_ids.append(threading.get_ident())

        service = WebRunService(root, event_source=source)
        events = list(
            service.stream_chat(
                "alice", "thread-affinity", "hello", cancel_event=threading.Event()
            )
        )
        self.assertEqual([event.type for event in events], ["text_delta", "done"])
        self.assertEqual(len(set(thread_ids)), 1)
        self.assertNotEqual(thread_ids[0], threading.get_ident())

    def test_closing_stream_consumer_releases_worker_when_output_queue_is_full(self) -> None:
        _, root = self.make_root()
        producer_reached_full_queue = threading.Event()
        session_id = "consumer-disconnect-full-queue"

        def source(*_args, **_kwargs):
            for index in range(1_000):
                if index == 33:
                    producer_reached_full_queue.set()
                yield RunEvent(type="text_delta", content=str(index))

        service = WebRunService(root, event_source=source)
        iterator = service.stream_chat(
            "alice",
            session_id,
            "start",
            cancel_event=threading.Event(),
        )
        self.assertEqual(next(iterator).type, "text_delta")
        self.assertTrue(producer_reached_full_queue.wait(timeout=2))

        iterator.close()

        worker_name = f"web-run-alice-{session_id}"
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and any(
            thread.name == worker_name and thread.is_alive()
            for thread in threading.enumerate()
        ):
            time.sleep(0.01)
        self.assertFalse(any(
            thread.name == worker_name and thread.is_alive()
            for thread in threading.enumerate()
        ))
        with service._active_runs_lock:
            self.assertFalse(any(
                active.session_id == session_id
                for active in service._active_runs.values()
            ))

    def test_disconnected_consumer_keeps_slow_run_scoped_and_cancellable(self) -> None:
        _, root = self.make_root()
        session_id = "consumer-disconnect-slow"
        worker_started = threading.Event()
        release_worker = threading.Event()

        def source(*_args, **kwargs):
            worker_started.set()
            yield RunEvent(type="text_delta", content="partial")
            # Simulate a provider/tool that does not return immediately after
            # the client disconnects.  The ActiveRun must remain addressable
            # until this worker really exits.
            release_worker.wait(timeout=10)
            yield RunEvent(type="done")

        service = WebRunService(root, event_source=source)
        iterator = service.stream_chat(
            "alice",
            session_id,
            "start",
            cancel_event=threading.Event(),
            run_id="run_slow_disconnect_123",
        )
        self.assertEqual(next(iterator).type, "text_delta")
        self.assertTrue(worker_started.wait(timeout=2))

        close_thread = threading.Thread(target=iterator.close)
        close_thread.start()
        try:
            deadline = time.monotonic() + 2
            active = None
            while time.monotonic() < deadline:
                with service._active_runs_lock:
                    active = service._active_runs.get("run_slow_disconnect_123")
                if active is not None:
                    break
                time.sleep(0.01)
            self.assertIsNotNone(active)
            self.assertTrue(active.cancel_event.is_set())

            stopping = service.cancel_run(
                "alice",
                "run_slow_disconnect_123",
                source="web",
                session_id=session_id,
            )
            self.assertEqual(stopping["status"], "stopping")
            self.assertTrue(active.cancel_event.is_set())
        finally:
            release_worker.set()
            close_thread.join(timeout=3)
            self.assertFalse(close_thread.is_alive())

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with service._active_runs_lock:
                if "run_slow_disconnect_123" not in service._active_runs:
                    break
            time.sleep(0.01)
        with service._active_runs_lock:
            self.assertNotIn("run_slow_disconnect_123", service._active_runs)

    def test_web_guidance_queue_is_user_scoped_and_removed_after_run(self) -> None:
        _, root = self.make_root()
        seen: list[str] = []

        def source(request, **_kwargs):
            seen.append(request["_guidance_queue"].get(timeout=2))
            yield RunEvent(type="done", metadata={"run_id": request["run_id"]})

        service = WebRunService(root, event_source=source)
        iterator = service.stream_chat(
            "alice",
            "guided-session",
            "start",
            cancel_event=threading.Event(),
            run_id="run_guidance_123",
        )
        captured: list[RunEvent] = []
        worker = threading.Thread(target=lambda: captured.extend(iterator))
        worker.start()
        queued = service.submit_guidance(
            "alice",
            "run_guidance_123",
            "adjust target",
            source="web",
            session_id="guided-session",
        )
        with self.assertRaisesRegex(Exception, "运行不存在"):
            service.submit_guidance(
                "bob",
                "run_guidance_123",
                "cross user",
                source="web",
                session_id="guided-session",
            )
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(queued["status"], "accepted_current_run")
        self.assertEqual(seen, ["adjust target"])
        self.assertEqual(captured[-1].metadata["run_id"], "run_guidance_123")
        with self.assertRaisesRegex(Exception, "运行不存在"):
            service.submit_guidance(
                "alice",
                "run_guidance_123",
                "too late",
                source="web",
                session_id="guided-session",
            )

    def test_web_guidance_after_final_boundary_is_queued_for_next_turn(self) -> None:
        _, root = self.make_root()
        service = WebRunService(root)
        active = ActiveRun("run_guidance_closed", "alice", "guided-session")
        active.guidance.close()
        service._active_runs[active.run_id] = active

        response = service.submit_guidance(
            "alice",
            active.run_id,
            "continue as a new turn",
            source="web",
            session_id="guided-session",
        )

        self.assertEqual(response["status"], "queued_next_turn")
        self.assertEqual(response["queued"], 0)
        self.assertEqual(active.guidance.qsize(), 0)

    def test_web_run_controls_require_matching_session_and_source(self) -> None:
        _, root = self.make_root()
        service = WebRunService(root)
        active = ActiveRun(
            "run_scope_guard",
            "alice",
            "scope-a",
            source="web",
        )
        service._active_runs[active.run_id] = active

        with self.assertRaisesRegex(Exception, "运行不存在"):
            service.submit_guidance(
                "alice",
                active.run_id,
                "wrong session",
                source="web",
                session_id="scope-b",
            )
        with self.assertRaisesRegex(Exception, "运行不存在"):
            service.submit_guidance(
                "alice",
                active.run_id,
                "wrong source",
                source="app",
                session_id="scope-a",
            )
        with self.assertRaisesRegex(Exception, "运行不存在"):
            service.cancel_run(
                "alice",
                active.run_id,
                source="web",
                session_id="scope-b",
            )
        self.assertFalse(active.cancel_event.is_set())

        accepted = service.submit_guidance(
            "alice",
            active.run_id,
            "matching scope",
            source="web",
            session_id="scope-a",
        )
        self.assertEqual(accepted["status"], "accepted_current_run")
        self.assertEqual(active.guidance.get_nowait(), "matching scope")
        cancelled = service.cancel_run(
            "alice",
            active.run_id,
            source="web",
            session_id="scope-a",
        )
        self.assertEqual(cancelled["status"], "stopping")
        self.assertTrue(active.cancel_event.is_set())

    def test_interleaved_runs_keep_guidance_cancel_and_event_metadata_in_scope(self) -> None:
        _, root = self.make_root()
        started = {session: threading.Event() for session in ("space-a", "space-b")}
        release = {session: threading.Event() for session in ("space-a", "space-b")}

        def source(request, **_kwargs):
            session_id = str(request["session_id"])
            started[session_id].set()
            # Deliberately provide forged event metadata; WebRunService must
            # replace it with the request's authoritative scope.
            yield RunEvent(
                type="text_delta",
                content=session_id,
                metadata={"source": "app", "session_id": "other-space"},
            )
            release[session_id].wait(timeout=2)
            yield RunEvent(
                type="done",
                metadata={"source": "app", "session_id": "other-space"},
            )

        service = WebRunService(root, event_source=source)
        streams = {
            session: service.stream_chat(
                "alice",
                session,
                "开始",
                cancel_event=threading.Event(),
                run_id=f"run_{session.replace('-', '_')}_123",
            )
            for session in ("space-a", "space-b")
        }
        captured: dict[str, list[RunEvent]] = {session: [] for session in streams}
        workers = [
            threading.Thread(
                target=lambda current=session: captured[current].extend(streams[current]),
                name=f"scope-test-{session}",
            )
            for session in streams
        ]
        for worker in workers:
            worker.start()
        self.assertTrue(started["space-a"].wait(timeout=2))
        self.assertTrue(started["space-b"].wait(timeout=2))

        with self.assertRaisesRegex(Exception, "运行不存在"):
            service.submit_guidance(
                "alice",
                "run_space_a_123",
                "不能进入 A",
                source="web",
                session_id="space-b",
            )
        accepted = service.submit_guidance(
            "alice",
            "run_space_a_123",
            "只给 A",
            source="web",
            session_id="space-a",
        )
        self.assertEqual(accepted["session_id"], "space-a")
        service.cancel_run(
            "alice",
            "run_space_a_123",
            source="web",
            session_id="space-a",
        )
        with service._active_runs_lock:
            active_b = service._active_runs.get("run_space_b_123")
            self.assertIsNotNone(active_b)
            self.assertFalse(active_b.cancel_event.is_set())

        release["space-a"].set()
        release["space-b"].set()
        for worker in workers:
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())

        for session, events in captured.items():
            self.assertEqual([event.type for event in events], ["text_delta", "done"])
            self.assertTrue(all(event.metadata["source"] == "web" for event in events))
            self.assertTrue(all(event.metadata["session_id"] == session for event in events))

    def test_web_run_control_http_requires_scope_fields(self) -> None:
        _, root = self.make_root()
        service = WebRunService(root)
        active = ActiveRun("run_scope_http", "alice", "scope-http", source="web")
        service._active_runs[active.run_id] = active
        app = create_app(service=service)

        missing_guidance_scope = self.request(
            app,
            "POST",
            "/api/runs/run_scope_http/guidance",
            json={"user": "alice", "guidance": "hello"},
        )
        self.assertEqual(missing_guidance_scope.status_code, 400, missing_guidance_scope.text)
        missing_cancel_scope = self.request(
            app,
            "POST",
            "/api/runs/run_scope_http/cancel",
            json={"user": "alice"},
        )
        self.assertEqual(missing_cancel_scope.status_code, 400, missing_cancel_scope.text)

    def test_web_guidance_accepts_attachment_only_and_revalidates_user_scope(
        self,
    ) -> None:
        _, root = self.make_root()
        upload = root / "users" / "alice" / "file_upload"
        upload.mkdir(parents=True)
        (upload / "clip.txt").write_text("media sidecar", "utf-8")
        service = WebRunService(root)
        active = ActiveRun("run_guidance_media", "alice", "guided-session")
        service._active_runs[active.run_id] = active

        response = service.submit_guidance(
            "alice",
            active.run_id,
            "",
            source="web",
            session_id="guided-session",
            guidance_id="guidance_media",
            uploaded_files=["clip.txt"],
        )

        queued = active.guidance.get_nowait()
        self.assertIsInstance(queued, GuidanceInput)
        self.assertEqual(queued.id, "guidance_media")
        self.assertEqual(queued.uploaded_files[0]["media_kind"], "file")
        self.assertEqual(response["status"], "accepted_current_run")
        self.assertEqual(response["uploaded_files"][0]["relative_path"], "clip.txt")
        with self.assertRaisesRegex(Exception, "上传文件不存在"):
            service.submit_guidance(
                "alice",
                active.run_id,
                "",
                source="web",
                session_id="guided-session",
                guidance_id="missing_media",
                uploaded_files=["missing.mp4"],
            )

    def test_web_cancel_run_is_user_scoped_and_sets_active_event(self) -> None:
        _, root = self.make_root()
        service = WebRunService(root)
        active = ActiveRun("run_cancel_123", "alice", "cancel-session")
        service._active_runs[active.run_id] = active

        response = self.request(
            create_app(service=service),
            "POST",
            "/api/runs/run_cancel_123/cancel",
            json={"user": "alice", "source": "web", "session_id": "cancel-session"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "stopping")
        self.assertTrue(active.cancel_event.is_set())

        active.cancel_event.clear()
        denied = self.request(
            create_app(service=service),
            "POST",
            "/api/runs/run_cancel_123/cancel",
            json={"user": "bob", "source": "web", "session_id": "cancel-session"},
        )
        self.assertEqual(denied.status_code, 404, denied.text)
        self.assertFalse(active.cancel_event.is_set())

    def test_long_task_session_api_is_isolated_and_cancels_matching_run(self) -> None:
        _, root = self.make_root()
        reserve_session(root, "alice", "web", "long-a")
        reserve_session(root, "alice", "web", "long-b")
        reserve_session(root, "alice", "app", "long-a")
        service = WebRunService(root)
        app = create_app(service=service)

        initial = self.request(
            app,
            "GET",
            "/api/users/alice/sessions/long-a/long-task?source=web",
        )
        self.assertEqual(initial.status_code, 200, initial.text)
        self.assertFalse(initial.json()["long_task"]["enabled"])

        enabled = self.request(
            app,
            "PUT",
            "/api/users/alice/sessions/long-a/long-task?source=web",
            json={"enabled": True},
        )
        self.assertEqual(enabled.status_code, 200, enabled.text)
        self.assertTrue(enabled.json()["long_task"]["enabled"])
        self.assertFalse(
            self.request(
                app,
                "GET",
                "/api/users/alice/sessions/long-b/long-task?source=web",
            ).json()["long_task"]["enabled"]
        )
        self.assertFalse(
            self.request(
                app,
                "GET",
                "/api/users/alice/sessions/long-a/long-task?source=app",
            ).json()["long_task"]["enabled"]
        )

        from run.long_task import activate_long_task

        activate_long_task(root, "alice", "web", "long-a", original_prompt="执行长任务")
        active = ActiveRun("run_long_api", "alice", "long-a", source="web")
        other = ActiveRun("run_other_api", "alice", "long-b", source="web")
        service._active_runs[active.run_id] = active
        service._active_runs[other.run_id] = other

        cancelled = self.request(
            app,
            "POST",
            "/api/users/alice/sessions/long-a/long-task/cancel?source=web",
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["long_task"]["status"], "cancelling")
        self.assertTrue(active.cancel_event.is_set())
        self.assertFalse(other.cancel_event.is_set())

        missing = self.request(
            app,
            "PUT",
            "/api/users/alice/sessions/missing/long-task?source=web",
            json={"enabled": True},
        )
        self.assertEqual(missing.status_code, 404, missing.text)

        invalid = self.request(
            app,
            "PUT",
            "/api/users/alice/sessions/long-a/long-task?source=web",
            json={"enabled": "yes"},
        )
        self.assertEqual(invalid.status_code, 400, invalid.text)

    def test_long_task_state_reconciles_stale_orphan_after_restart(self) -> None:
        _, root = self.make_root()
        reserve_session(root, "alice", "web", "long-orphaned")
        from run.long_task import activate_long_task, set_long_task_enabled

        with patch("run.long_task._now", return_value="2020-01-01T00:00:00+00:00"):
            set_long_task_enabled(root, "alice", "web", "long-orphaned", True)
            activate_long_task(
                root,
                "alice",
                "web",
                "long-orphaned",
                original_prompt="跨进程长任务",
            )

        service = WebRunService(root)
        response = self.request(
            create_app(service=service),
            "GET",
            "/api/users/alice/sessions/long-orphaned/long-task?source=web",
        )

        self.assertEqual(response.status_code, 200, response.text)
        state = response.json()["long_task"]
        self.assertEqual(state["status"], "interrupted")
        self.assertEqual(state["last_stop_reason"], "orphaned_run")
        self.assertEqual(state["last_error"]["code"], "orphaned_long_task")

    def test_long_task_cancel_finishes_orphan_instead_of_sticking_cancelling(self) -> None:
        _, root = self.make_root()
        reserve_session(root, "alice", "web", "long-orphan-cancel")
        from run.long_task import activate_long_task, set_long_task_enabled

        set_long_task_enabled(root, "alice", "web", "long-orphan-cancel", True)
        activate_long_task(
            root,
            "alice",
            "web",
            "long-orphan-cancel",
            original_prompt="等待用户终止",
        )
        service = WebRunService(root)
        response = self.request(
            create_app(service=service),
            "POST",
            "/api/users/alice/sessions/long-orphan-cancel/long-task/cancel?source=web",
        )

        self.assertEqual(response.status_code, 200, response.text)
        state = response.json()["long_task"]
        self.assertEqual(state["status"], "cancelled")
        self.assertEqual(state["last_stop_reason"], "orphaned_user_cancel")
        self.assertIsNone(state["last_error"])

    def test_explicit_cancel_keeps_terminal_done_visible_to_stream_consumer(
        self,
    ) -> None:
        _, root = self.make_root()

        def source(request, *, cancel_event, **_kwargs):
            yield RunEvent(type="text_delta", content="partial")
            self.assertTrue(cancel_event.wait(timeout=2))
            yield RunEvent(
                type="done",
                metadata={
                    "run_id": request["run_id"],
                    "committed": True,
                    "status": "cancelled",
                    "cancelled": True,
                },
            )

        service = WebRunService(root, event_source=source)
        iterator = service.stream_chat(
            "alice",
            "cancel-stream",
            "start",
            cancel_event=threading.Event(),
            run_id="run_cancel_stream_123",
        )
        self.assertEqual(next(iterator).type, "text_delta")
        service.cancel_run(
            "alice",
            "run_cancel_stream_123",
            source="web",
            session_id="cancel-stream",
        )
        terminal = list(iterator)
        self.assertEqual([event.type for event in terminal], ["done"])
        self.assertEqual(terminal[0].metadata["status"], "cancelled")

    def test_sse_order_and_payload_are_preserved(self) -> None:
        events = [
            RunEvent(type="reasoning_delta", content="think"),
            RunEvent(type="text_delta", content="hello"),
            RunEvent(
                type="tool_call_start",
                tool_call_id="c1",
                tool_name="clock",
                arguments={"x": 1},
            ),
            RunEvent(
                type="tool_call_result",
                tool_call_id="c1",
                tool_name="clock",
                result={"ok": True},
            ),
            RunEvent(type="usage", usage={"total_tokens": 3}),
            RunEvent(
                type="done", usage={"total_tokens": 3}, metadata={"committed": True}
            ),
        ]
        fake = FakeService(events=events)
        response = self.request(
            create_app(service=fake),
            "POST",
            "/api/chat",
            json={"user": "alice", "session_id": "s1", "prompt": "hello"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            response.headers["content-type"].startswith("text/event-stream")
        )
        parsed = self.parse_sse(response.text)
        self.assertEqual([item[0] for item in parsed], [event.type for event in events])
        self.assertEqual(parsed[2][1]["arguments"], {"x": 1})
        self.assertEqual(parsed[3][1]["result"], {"ok": True})
        self.assertEqual(fake.seen["session_id"], "s1")
        self.assertTrue(fake.cancel_event.is_set())

    def test_chat_route_accepts_uploaded_files_without_text(self) -> None:
        fake = FakeService(events=[RunEvent(type="done")])
        response = self.request(
            create_app(service=fake),
            "POST",
            "/api/chat",
            json={
                "user": "alice",
                "session_id": "attachment-only",
                "prompt": "",
                "uploaded_files": ["screenshot.png"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(fake.seen["prompt"], "")
        self.assertEqual(fake.seen["uploaded_files"], ["screenshot.png"])

    def test_chat_route_forwards_app_source(self) -> None:
        fake = FakeService(events=[RunEvent(type="done")])
        response = self.request(
            create_app(service=fake),
            "POST",
            "/api/chat",
            json={
                "user": "alice",
                "source": "app",
                "session_id": "app-session",
                "prompt": "hello from app",
                "client_id": "app_android-device-a",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(fake.seen["source"], "app")
        self.assertEqual(fake.seen["client_id"], "app_android-device-a")

    def test_plan_chat_route_starts_plan_stream_without_a_prompt(self) -> None:
        fake = FakeService(
            events=[
                RunEvent(type="text_delta", content="执行中"),
                RunEvent(type="done"),
            ]
        )
        response = self.request(
            create_app(service=fake),
            "POST",
            "/api/chat",
            json={
                "user": "alice",
                "session_id": "s1",
                "prompt": "",
                "plan_id": "plan_12345678",
                "run_id": "run_plan_123",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            [item[0] for item in self.parse_sse(response.text)], ["text_delta", "done"]
        )
        self.assertEqual(
            fake.seen,
            {
                "user": "alice",
                "session_id": "s1",
                "plan_id": "plan_12345678",
                "run_id": "run_plan_123",
            },
        )

    def test_stream_plan_uses_one_agent_run_for_multiple_steps(self) -> None:
        _, root = self.make_root()
        store = PlanStore(root, "alice")
        plan = store.create(
            normalize_plan(
                title="连续执行",
                description="单轮完成两步",
                user="alice",
                source="web",
                session_id="s1",
                steps=[
                    {
                        "step_id": "step_1",
                        "title": "第一步",
                        "description": "执行第一步",
                        "critical": True,
                    },
                    {
                        "step_id": "step_2",
                        "title": "第二步",
                        "description": "执行第二步",
                        "depends_on": ["step_1"],
                        "critical": True,
                    },
                ],
            )
        )
        requests: list[dict[str, Any]] = []

        def source(request, **_kwargs):
            requests.append(request)
            context = {
                "root": str(root),
                "user": "alice",
                "source": "web",
                "session_id": "s1",
                "task_plan_id": plan["plan_id"],
                "task_plan_mode": request["_task_plan_mode"],
            }
            from plugins.task_plan.tool import run as run_task_plan_tool

            for index in (1, 2):
                result = run_task_plan_tool(
                    action="step_done",
                    plan_id=plan["plan_id"],
                    step_id=f"step_{index}",
                    result=f"步骤 {index} 完成",
                    context=context,
                )
                yield RunEvent(
                    type="tool_call_result",
                    tool_call_id=f"call_{index}",
                    tool_name="task_plan",
                    result=result,
                )
            yield RunEvent(type="text_delta", content="全部完成")
            yield RunEvent(type="done")

        service = WebRunService(root, event_source=source)
        events = list(
            service.stream_plan(
                "alice",
                "s1",
                plan["plan_id"],
                cancel_event=threading.Event(),
                run_id="run_plan_single",
            )
        )

        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["_task_plan_id"], plan["plan_id"])
        self.assertEqual(requests[0]["_task_plan_mode"], "agent_managed")
        self.assertIn("【任务计划连续执行】", requests[0]["prompt"])
        self.assertEqual(
            [event.type for event in events],
            ["tool_call_result", "tool_call_result", "text_delta", "done"],
        )
        stored = store.read(plan["plan_id"])
        self.assertEqual(stored["status"], "completed")
        self.assertTrue(all(step["status"] == "completed" for step in stored["steps"]))

    def test_stream_plan_rejects_cross_conversation_execution_without_mutation(self) -> None:
        _, root = self.make_root()
        store = PlanStore(root, "alice")
        plan = store.create(
            normalize_plan(
                title="A 对话计划",
                description="不能挂到 B 对话执行",
                user="alice",
                source="web",
                session_id="conversation-a",
                steps=[{
                    "step_id": "step_1",
                    "title": "执行",
                    "description": "执行",
                    "critical": True,
                }],
            )
        )
        service = WebRunService(root, event_source=lambda *_args, **_kwargs: iter(()))

        with self.assertRaisesRegex(WebServiceError, "不属于当前对话空间"):
            service.stream_plan(
                "alice",
                "conversation-b",
                plan["plan_id"],
                cancel_event=threading.Event(),
                run_id="run_cross_space",
                source="web",
            )

        stored = store.read(plan["plan_id"])
        self.assertEqual(stored["status"], "pending")
        self.assertEqual(stored["session_id"], "conversation-a")

    def test_task_list_isolated_by_source_and_optional_conversation(self) -> None:
        _, root = self.make_root()
        store = PlanStore(root, "alice")

        def create(title: str, source: str, session_id: str) -> dict[str, Any]:
            return store.create(
                normalize_plan(
                    title=title,
                    description=title,
                    user="alice",
                    source=source,
                    session_id=session_id,
                    steps=[
                        {
                            "step_id": "step_1",
                            "title": "执行",
                            "description": "执行",
                            "critical": True,
                        }
                    ],
                )
            )

        web_a = create("Web A", "web", "session-a")
        web_b = create("Web B", "web", "session-b")
        app_a = create("App A", "app", "session-a")
        service = WebRunService(root)

        web_all = service.tasks("alice", source="web")
        self.assertEqual({item["plan_id"] for item in web_all["plans"]}, {web_a["plan_id"], web_b["plan_id"]})
        web_session = service.tasks("alice", source="web", session_id="session-a")
        self.assertEqual([item["plan_id"] for item in web_session["plans"]], [web_a["plan_id"]])
        app_session = service.tasks("alice", source="app", session_id="session-a")
        self.assertEqual([item["plan_id"] for item in app_session["plans"]], [app_a["plan_id"]])

        app = create_app(service=service)
        response = self.request(
            app,
            "GET",
            "/api/users/alice/tasks?source=web&session_id=session-a",
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            [item["plan_id"] for item in response.json()["plans"]],
            [web_a["plan_id"]],
        )

    def test_plan_creation_requires_scoped_session_and_ignores_body_scope(self) -> None:
        _, root = self.make_root()
        reserve_session(root, "alice", "web", "session-a")
        reserve_session(root, "alice", "web", "session-b")
        reserve_session(root, "alice", "app", "session-a")
        app = create_app(service=WebRunService(root))

        missing_scope = self.request(
            app,
            "POST",
            "/api/users/alice/tasks/plans",
            json={
                "title": "没有作用域的计划",
                "description": "必须拒绝",
                "session_id": "session-a",
                "source": "app",
                "steps": [{
                    "step_id": "step_1",
                    "title": "执行",
                    "description": "执行",
                }],
            },
        )
        self.assertEqual(missing_scope.status_code, 400, missing_scope.text)

        created = self.request(
            app,
            "POST",
            "/api/users/alice/tasks/plans?source=web&session_id=session-a",
            json={
                "title": "A 空间计划",
                "description": "只能属于 A",
                # Body scope is untrusted and must not redirect ownership.
                "session_id": "session-b",
                "source": "app",
                "steps": [{
                    "step_id": "step_1",
                    "title": "执行",
                    "description": "执行",
                }],
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        plan = created.json()["plan"]
        self.assertEqual(plan["source"], "web")
        self.assertEqual(plan["session_id"], "session-a")

        in_a = self.request(
            app,
            "GET",
            "/api/users/alice/tasks?source=web&session_id=session-a",
        )
        in_b = self.request(
            app,
            "GET",
            "/api/users/alice/tasks?source=web&session_id=session-b",
        )
        in_app = self.request(
            app,
            "GET",
            "/api/users/alice/tasks?source=app&session_id=session-a",
        )
        self.assertEqual([item["plan_id"] for item in in_a.json()["plans"]], [plan["plan_id"]])
        self.assertEqual(in_b.json()["plans"], [])
        self.assertEqual(in_app.json()["plans"], [])

        cross_space = self.request(
            app,
            "PATCH",
            f"/api/users/alice/tasks/plans/{plan['plan_id']}/edit?source=web&session_id=session-b",
            json={"revision": plan["revision"], "title": "越权修改"},
        )
        self.assertEqual(cross_space.status_code, 400, cross_space.text)

    def test_plan_pause_command_uses_latest_disk_state_without_revision(self) -> None:
        _, root = self.make_root()
        store = PlanStore(root, "alice")
        plan = store.create(
            normalize_plan(
                title="可暂停计划",
                description="验证无 revision 指令",
                user="alice",
                source="web",
                session_id="web",
                status="running",
                steps=[
                    {
                        "step_id": "step_1",
                        "title": "执行",
                        "description": "执行中",
                        "critical": True,
                    }
                ],
            )
        )
        store.update(
            plan["plan_id"], lambda current: {**current, "current_step": "step_1"}
        )
        app = create_app(service=WebRunService(root))

        response = self.request(
            app,
            "POST",
            f"/api/users/alice/tasks/plans/{plan['plan_id']}/actions/pause?source=web&session_id=web",
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["plan"]["status"], "paused")
        self.assertEqual(store.read(plan["plan_id"])["status"], "paused")

    def test_plan_edit_and_retry_endpoints_use_revision_and_preserve_completed(self) -> None:
        _, root = self.make_root()
        (root / "config").mkdir()
        (root / "config" / "global_config.json").write_text(
            json.dumps({"task_plan": {"auto_retry_on_fix": False}}),
            "utf-8",
        )
        store = PlanStore(root, "alice")
        plan = store.create(
            normalize_plan(
                title="待修正计划",
                description="旧描述",
                user="alice",
                source="web",
                session_id="s1",
                status="paused",
                steps=[
                    {
                        "step_id": "step_1",
                        "title": "已完成",
                        "description": "不能修改",
                        "status": "completed",
                        "result": {"ok": True},
                        "critical": True,
                    },
                    {
                        "step_id": "step_2",
                        "title": "失败步骤",
                        "description": "等待重试",
                        "status": "failed",
                        "depends_on": ["step_1"],
                        "error": {"message": "旧错误"},
                        "finished_at": "2026-08-22T01:00:00+00:00",
                        "critical": True,
                    },
                ],
            )
        )
        wake_count = 0

        def wake() -> None:
            nonlocal wake_count
            wake_count += 1

        app = create_app(service=WebRunService(root, plan_waker=wake))
        wrong_session_edit = self.request(
            app,
            "PATCH",
            f"/api/users/alice/tasks/plans/{plan['plan_id']}/edit?session_id=s2",
            json={"revision": plan["revision"], "description": "跨空间修改"},
        )
        self.assertEqual(wrong_session_edit.status_code, 400, wrong_session_edit.text)
        wrong_session_retry = self.request(
            app,
            "POST",
            f"/api/users/alice/tasks/plans/{plan['plan_id']}/steps/step_2/retry?session_id=s2",
            json={"revision": plan["revision"]},
        )
        self.assertEqual(wrong_session_retry.status_code, 400, wrong_session_retry.text)
        edited = self.request(
            app,
            "PATCH",
            f"/api/users/alice/tasks/plans/{plan['plan_id']}/edit?session_id=s1",
            json={
                "revision": plan["revision"],
                "title": "已修正计划",
                "steps": [{
                    "step_id": "step_2",
                    "tool_name": "shell",
                    "tool_arguments": {"command": "pwd"},
                    "critical": False,
                }],
            },
        )
        self.assertEqual(edited.status_code, 200, edited.text)
        edited_plan = edited.json()["plan"]
        self.assertEqual(edited_plan["title"], "已修正计划")
        self.assertEqual(edited_plan["steps"][0]["result"], {"ok": True})
        self.assertEqual(edited_plan["steps"][1]["tool_arguments"], {"command": "pwd"})
        self.assertFalse(edited.json()["activated"])
        self.assertEqual(edited.json()["reason"], "activation_disabled")

        stale = self.request(
            app,
            "PATCH",
            f"/api/users/alice/tasks/plans/{plan['plan_id']}/edit?session_id=s1",
            json={"revision": plan["revision"], "description": "陈旧修改"},
        )
        self.assertEqual(stale.status_code, 409, stale.text)

        protected = self.request(
            app,
            "PATCH",
            f"/api/users/alice/tasks/plans/{plan['plan_id']}/edit?session_id=s1",
            json={
                "revision": edited_plan["revision"],
                "steps": [{"step_id": "step_1", "critical": False}],
            },
        )
        self.assertEqual(protected.status_code, 400, protected.text)

        retried = self.request(
            app,
            "POST",
            f"/api/users/alice/tasks/plans/{plan['plan_id']}/steps/step_2/retry?session_id=s1",
            json={"revision": edited_plan["revision"]},
        )
        self.assertEqual(retried.status_code, 200, retried.text)
        retried_plan = retried.json()["plan"]
        self.assertEqual(retried_plan["status"], "paused")
        self.assertEqual(retried_plan["steps"][1]["status"], "pending")
        self.assertIsNone(retried_plan["steps"][1]["error"])
        self.assertEqual(retried_plan["steps"][1]["finished_at"], "")
        self.assertFalse(retried.json()["activated"])
        self.assertEqual(retried.json()["reason"], "activation_disabled")
        self.assertEqual(wake_count, 0)

    def test_plan_fix_auto_activation_wakes_only_after_approved_transition(self) -> None:
        _, root = self.make_root()
        (root / "config").mkdir()
        (root / "config" / "global_config.json").write_text(
            json.dumps({"task_plan": {"auto_retry_on_fix": False}}),
            "utf-8",
        )
        (root / "users" / "alice" / "user_config.json").write_text(
            json.dumps({"task_plan": {"auto_retry_on_fix": True}}),
            "utf-8",
        )
        store = PlanStore(root, "alice")
        plan = store.create(normalize_plan(
            title="失败计划",
            description="等待修正",
            user="alice",
            source="web",
            session_id="s1",
            status="failed",
            steps=[{
                "step_id": "step_1",
                "title": "失败步骤",
                "description": "等待重试",
                "status": "failed",
                "error": {"message": "旧错误"},
                "critical": True,
            }],
        ))
        wake_count = 0

        def wake() -> None:
            nonlocal wake_count
            wake_count += 1

        app = create_app(service=WebRunService(root, plan_waker=wake))
        response = self.request(
            app,
            "POST",
            f"/api/users/alice/tasks/plans/{plan['plan_id']}/steps/step_1/retry?session_id=s1",
            json={"revision": plan["revision"]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["activated"])
        self.assertEqual(response.json()["reason"], "auto_retry_on_fix")
        self.assertEqual(response.json()["plan"]["status"], "approved")
        self.assertEqual(wake_count, 1)

        edit_plan = store.create(normalize_plan(
            title="暂停计划",
            description="等待编辑",
            user="alice",
            source="web",
            session_id="s1",
            status="paused",
            auto_accept=True,
            steps=[{
                "step_id": "step_1",
                "title": "待修正步骤",
                "description": "等待编辑",
                "critical": True,
            }],
        ))
        edited = self.request(
            app,
            "PATCH",
            f"/api/users/alice/tasks/plans/{edit_plan['plan_id']}/edit?session_id=s1",
            json={"revision": edit_plan["revision"], "description": "已修正"},
        )
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertTrue(edited.json()["activated"])
        self.assertEqual(edited.json()["reason"], "auto_accept")
        self.assertEqual(edited.json()["plan"]["status"], "approved")
        self.assertEqual(wake_count, 2)

    def test_plan_revision_endpoints_are_session_scoped_and_rollback_is_append_only(self) -> None:
        _, root = self.make_root()
        store = PlanStore(root, "alice")
        plan = store.create(
            normalize_plan(
                title="第一版",
                description="初始计划",
                user="alice",
                source="web",
                session_id="conversation-a",
                steps=[{
                    "step_id": "step_1",
                    "title": "初始步骤",
                    "description": "执行第一版",
                    "critical": True,
                }],
            )
        )
        second = store.update(
            plan["plan_id"],
            lambda current: {**current, "title": "第二版", "status": "paused"},
            note="修改为第二版",
        )
        app = create_app(service=WebRunService(root))
        base = f"/api/users/alice/tasks/plans/{plan['plan_id']}"

        denied = self.request(
            app,
            "GET",
            f"{base}/revisions?session_id=conversation-b",
        )
        self.assertEqual(denied.status_code, 400, denied.text)

        listed = self.request(
            app,
            "GET",
            f"{base}/revisions?session_id=conversation-a",
        )
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(
            [item["revision"] for item in listed.json()["revisions"]],
            [2, 1],
        )

        viewed = self.request(
            app,
            "GET",
            f"{base}/revisions/1?session_id=conversation-a",
        )
        self.assertEqual(viewed.status_code, 200, viewed.text)
        self.assertEqual(viewed.json()["plan"]["title"], "第一版")

        secret_revision = copy.deepcopy(second)
        secret_revision["revision"] = 3
        secret_revision["steps"][0]["tool_arguments"] = {
            "authorization": "Bearer example-sensitive-value",
            "apiKey": "synthetic-api-key-value",
            "accessToken": "synthetic-access-token-value",
            "refreshToken": "synthetic-refresh-token-value",
            "sessionSecret": "synthetic-session-secret-value",
            "api-key": "synthetic-hyphen-key-value",
            "nested": {
                "Authorization": "synthetic-nested-authorization-value",
                "keep": "safe-nested-value",
            },
            "command": "safe-command",
        }
        secret_revision["steps"][0]["result"] = "Bearer example-sensitive-value"
        database = sqlite3.connect(store.path)
        try:
            database.execute(
                "UPDATE task_plans SET revision=3 WHERE plan_id=?",
                (plan["plan_id"],),
            )
            database.execute(
                """
                UPDATE task_plan_steps
                SET tool_arguments_json=?, result_json=?
                WHERE plan_id=? AND step_id='step_1'
                """,
                (
                    json.dumps(secret_revision["steps"][0]["tool_arguments"]),
                    json.dumps(secret_revision["steps"][0]["result"]),
                    plan["plan_id"],
                ),
            )
            database.execute(
                """
                INSERT INTO task_plan_revisions(
                    plan_id, revision, plan_json, note, created_at
                ) VALUES(?, 3, ?, 'token=old-sensitive', ?)
                """,
                (
                    plan["plan_id"],
                    json.dumps(secret_revision, ensure_ascii=False),
                    secret_revision["updated_at"],
                ),
            )
            database.commit()
        finally:
            database.close()
        secret_view = self.request(
            app,
            "GET",
            f"{base}/revisions/3?session_id=conversation-a",
        )
        self.assertEqual(secret_view.status_code, 200, secret_view.text)
        secret_step = secret_view.json()["plan"]["steps"][0]
        self.assertEqual(secret_step["tool_arguments"]["authorization"], "***")
        self.assertEqual(secret_step["tool_arguments"]["apiKey"], "***")
        self.assertEqual(secret_step["tool_arguments"]["accessToken"], "***")
        self.assertEqual(secret_step["tool_arguments"]["refreshToken"], "***")
        self.assertEqual(secret_step["tool_arguments"]["sessionSecret"], "***")
        self.assertEqual(secret_step["tool_arguments"]["api-key"], "***")
        self.assertEqual(
            secret_step["tool_arguments"]["nested"]["Authorization"], "***"
        )
        self.assertEqual(
            secret_step["tool_arguments"]["nested"]["keep"], "safe-nested-value"
        )
        self.assertEqual(secret_step["tool_arguments"]["command"], "safe-command")
        self.assertEqual(secret_step["result"], "***")

        listed_secret = self.request(
            app,
            "GET",
            f"{base}/revisions?session_id=conversation-a",
        )
        self.assertEqual(listed_secret.status_code, 200, listed_secret.text)
        self.assertEqual(listed_secret.json()["revisions"][0]["note"], "***")

        tasks = self.request(app, "GET", "/api/users/alice/tasks")
        self.assertEqual(tasks.status_code, 200, tasks.text)
        self.assertNotIn("example-sensitive-value", tasks.text)
        task_step = tasks.json()["plans"][0]["steps"][0]
        self.assertEqual(task_step["tool_arguments"]["authorization"], "***")
        self.assertEqual(task_step["result"], "***")

        rolled_back = self.request(
            app,
            "POST",
            f"{base}/rollback?session_id=conversation-a",
            json={"revision": 1, "current_revision": 3},
        )
        self.assertEqual(rolled_back.status_code, 200, rolled_back.text)
        rolled_plan = rolled_back.json()["plan"]
        self.assertEqual(rolled_plan["revision"], 4)
        self.assertEqual(rolled_plan["title"], "第一版")
        self.assertEqual(rolled_plan["status"], second["status"])
        self.assertEqual(store.get_revision(plan["plan_id"], 2)["title"], "第二版")
        self.assertEqual(
            [item["revision"] for item in store.list_revisions(plan["plan_id"])],
            [4, 3, 2, 1],
        )

        stale_rollback = self.request(
            app,
            "POST",
            f"{base}/rollback?session_id=conversation-a",
            json={"revision": 2, "current_revision": 3},
        )
        self.assertEqual(stale_rollback.status_code, 409, stale_rollback.text)

        running = store.update(
            plan["plan_id"],
            lambda current: {**current, "status": "running"},
        )
        running_rollback = self.request(
            app,
            "POST",
            f"{base}/rollback?session_id=conversation-a",
            json={"revision": 1, "current_revision": running["revision"]},
        )
        self.assertEqual(running_rollback.status_code, 400, running_rollback.text)
        self.assertIn("只能在 pending/paused/failed 状态回滚", running_rollback.text)
        self.assertEqual(store.read(plan["plan_id"])["status"], "running")

        missing = self.request(
            app,
            "GET",
            f"{base}/revisions/99?session_id=conversation-a",
        )
        self.assertEqual(missing.status_code, 404, missing.text)

    def test_missing_plan_mutation_endpoints_return_not_found(self) -> None:
        _, root = self.make_root()
        app = create_app(service=WebRunService(root))
        plan_id = "plan_missing"

        update = self.request(
            app,
            "PUT",
            f"/api/users/alice/tasks/plans/{plan_id}?session_id=session-a",
            json={},
        )
        self.assertEqual(update.status_code, 404, update.text)

        pause = self.request(
            app,
            "POST",
            f"/api/users/alice/tasks/plans/{plan_id}/actions/pause?session_id=session-a",
        )
        self.assertEqual(pause.status_code, 404, pause.text)

        delete = self.request(
            app,
            "DELETE",
            f"/api/users/alice/tasks/plans/{plan_id}?session_id=session-a",
        )
        self.assertEqual(delete.status_code, 404, delete.text)

    def test_knowledge_api_hides_and_protects_kemo_graph_storage(self) -> None:
        _, root = self.make_root()
        (root / "config").mkdir()
        (root / "config" / "global_config.json").write_text(
            json.dumps({"schema_version": 1}),
            "utf-8",
        )
        (root / "users" / "alice" / "user_config.json").write_text(
            json.dumps({"schema_version": 1, "knowledge": {}}),
            "utf-8",
        )
        scope_roots = {
            "user": root / "users" / "alice" / "knowledge",
            "shared": root / "shared_knowledge",
            "global": root / "global_knowledge",
        }
        for scope, base in scope_roots.items():
            base.mkdir(parents=True)
            (base / f"{scope}.md").write_text(f"# {scope}", "utf-8")
            runtime = base / "kemo-graph-storage"
            (runtime / "content" / "markdown").mkdir(parents=True)
            (runtime / "manifest.json").write_text('{"runtime": true}', "utf-8")
            (runtime / "content" / "markdown" / "derived.md").write_text(
                "RUNTIME_DERIVED_CONTENT",
                "utf-8",
            )

        app = create_app(service=WebRunService(root))
        listed = self.request(app, "GET", "/api/users/alice/knowledge")
        self.assertEqual(listed.status_code, 200, listed.text)
        payload = listed.json()
        self.assertEqual(payload["summary"]["documents"], 3)
        self.assertEqual(
            {item["relative_path"] for item in payload["documents"]},
            {"user.md", "shared.md", "global.md"},
        )
        self.assertNotIn("kemo-graph-storage", listed.text)
        self.assertNotIn("RUNTIME_DERIVED_CONTENT", listed.text)

        for scope, base in scope_roots.items():
            endpoint = f"/api/users/alice/knowledge/{scope}/document"
            read = self.request(
                app,
                "GET",
                endpoint,
                params={"path": "kemo-graph-storage/manifest.json"},
            )
            self.assertEqual(read.status_code, 400, read.text)
            create = self.request(
                app,
                "PUT",
                endpoint,
                params={"path": "kemo-graph-storage/new.md"},
                json={"content": "blocked"},
            )
            self.assertEqual(create.status_code, 400, create.text)
            delete = self.request(
                app,
                "DELETE",
                endpoint,
                params={"path": "kemo-graph-storage/manifest.json"},
            )
            self.assertEqual(delete.status_code, 400, delete.text)
            move_into = self.request(
                app,
                "PATCH",
                endpoint,
                params={
                    "path": f"{scope}.md",
                    "new_path": "kemo-graph-storage/moved.md",
                },
            )
            self.assertEqual(move_into.status_code, 400, move_into.text)
            move_out = self.request(
                app,
                "PATCH",
                endpoint,
                params={
                    "path": "kemo-graph-storage/manifest.json",
                    "new_path": "escaped.json",
                },
            )
            self.assertEqual(move_out.status_code, 400, move_out.text)

            self.assertTrue((base / f"{scope}.md").is_file())
            self.assertEqual(
                (base / "kemo-graph-storage" / "manifest.json").read_text("utf-8"),
                '{"runtime": true}',
            )

    def test_important_memory_write_uses_output_limit_not_prompt_budget(self) -> None:
        _, root = self.make_root()
        (root / "config").mkdir()
        (root / "config" / "global_config.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "memory": {
                        "important_memory_max_chars": 4,
                        "important_memory_output_max_chars": 8,
                    },
                }
            ),
            "utf-8",
        )
        (root / "users" / "alice" / "user_config.json").write_text(
            json.dumps({"schema_version": 1}),
            "utf-8",
        )
        app = create_app(service=WebRunService(root))

        accepted = self.request(
            app,
            "PUT",
            "/api/users/alice/memory/important",
            json={"content": "12345678"},
        )
        rejected = self.request(
            app,
            "PUT",
            "/api/users/alice/memory/important",
            json={"content": "123456789"},
        )

        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()["content"], "12345678")
        self.assertEqual(rejected.status_code, 400, rejected.text)

    def test_editable_web_resource_apis_are_scoped_and_validated(self) -> None:
        _, root = self.make_root()
        (root / "config").mkdir()
        (root / "config" / "global_config.json").write_text(
            json.dumps({"schema_version": 1, "tools": {"enabled": True}}), "utf-8"
        )
        (root / "users" / "alice" / "user_config.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "provider": {
                        "type": "chat",
                        "base_url": "https://example.test/v1",
                        "model": "test",
                        "api_key": "keep-secret",
                    },
                }
            ),
            "utf-8",
        )
        reserve_session(root, "alice", "web", "web")
        app = create_app(service=WebRunService(root))

        plan = self.request(
            app,
            "POST",
            "/api/users/alice/tasks/plans?source=web&session_id=web",
            json={
                "title": "Web plan",
                "description": "created by web",
                "steps": [
                    {
                        "step_id": "step_1",
                        "title": "First",
                        "description": "Do first",
                        "critical": True,
                    }
                ],
            },
        )
        self.assertEqual(plan.status_code, 200, plan.text)
        plan_id = plan.json()["plan"]["plan_id"]
        paused = self.request(
            app,
            "PUT",
            f"/api/users/alice/tasks/plans/{plan_id}?source=web&session_id=web",
            json={"revision": 1, "status": "paused"},
        )
        self.assertEqual(paused.json()["plan"]["status"], "paused")

        cron = self.request(
            app,
            "POST",
            "/api/users/alice/tasks/crons",
            json={
                "title": "Hourly",
                "prompt": "run hourly",
                "type": "recurring",
                "interval_seconds": 3600,
            },
        )
        self.assertEqual(cron.status_code, 200, cron.text)
        too_fast = self.request(
            app,
            "POST",
            "/api/users/alice/tasks/crons",
            json={
                "title": "Too fast",
                "prompt": "x",
                "type": "recurring",
                "interval_seconds": 10,
            },
        )
        self.assertEqual(too_fast.status_code, 400)

        put_knowledge = self.request(
            app,
            "PUT",
            "/api/users/alice/knowledge/user/document?path=notes%2Fweb.md",
            json={"content": "# Web knowledge"},
        )
        self.assertEqual(put_knowledge.status_code, 200, put_knowledge.text)
        knowledge = self.request(
            app,
            "GET",
            "/api/users/alice/knowledge/user/document?path=notes%2Fweb.md",
        )
        self.assertEqual(knowledge.json()["content"], "# Web knowledge")
        escaped_knowledge = self.request(
            app,
            "PUT",
            "/api/users/alice/knowledge/user/document?path=..%2Fescape.md",
            json={"content": "bad"},
        )
        self.assertEqual(escaped_knowledge.status_code, 400)

        memory = self.request(
            app,
            "PUT",
            "/api/users/alice/memory/item?filename=web-memory.md",
            json={"content": "remember this", "tier": "one_month"},
        )
        self.assertEqual(memory.status_code, 200, memory.text)
        self.assertEqual(memory.json()["tier"], "one_month")
        self.assertEqual(memory.json()["memory_ref"], "one_month:web-memory.md")
        fetched_memory = self.request(
            app,
            "GET",
            "/api/users/alice/memory/item?tier=one_month&filename=web-memory.md",
        )
        self.assertEqual(fetched_memory.status_code, 200, fetched_memory.text)
        self.assertEqual(fetched_memory.json()["content"], "remember this")
        self.assertEqual(fetched_memory.json()["memory_ref"], "one_month:web-memory.md")
        deleted_memory = self.request(
            app,
            "DELETE",
            "/api/users/alice/memory/item?tier=one_month&filename=web-memory.md",
        )
        self.assertEqual(deleted_memory.status_code, 200, deleted_memory.text)
        self.assertTrue(deleted_memory.json()["deleted"])
        self.assertEqual(deleted_memory.json()["tier"], "one_month")
        important = self.request(
            app,
            "PUT",
            "/api/users/alice/memory/important",
            json={"content": "important context"},
        )
        self.assertEqual(important.status_code, 200, important.text)
        deleted_important = self.request(
            app,
            "DELETE",
            "/api/users/alice/memory/important",
        )
        self.assertEqual(deleted_important.status_code, 405, deleted_important.text)
        cleared_important = self.request(
            app,
            "PUT",
            "/api/users/alice/memory/important",
            json={"content": "   "},
        )
        self.assertEqual(cleared_important.status_code, 400, cleared_important.text)
        preserved_important = self.request(
            app,
            "GET",
            "/api/users/alice/memory/important",
        )
        self.assertEqual(preserved_important.status_code, 200, preserved_important.text)
        self.assertEqual(preserved_important.json()["content"], "important context")

        upload = self.request(
            app,
            "POST",
            "/api/users/alice/files/file_upload/upload?path=folder%2Fnote.txt",
            files={"file": ("note.txt", b"hello", "text/plain")},
        )
        self.assertEqual(upload.status_code, 200, upload.text)
        moved = self.request(
            app,
            "PATCH",
            "/api/users/alice/files/file_upload/move?path=folder%2Fnote.txt&new_path=renamed.txt",
        )
        self.assertTrue(moved.json()["moved"])
        escaped_upload = self.request(
            app,
            "POST",
            "/api/users/alice/files/file_upload/upload?path=..%2Fescape.txt",
            files={"file": ("escape.txt", b"bad", "text/plain")},
        )
        self.assertEqual(escaped_upload.status_code, 400)

        patched = self.request(
            app,
            "PATCH",
            "/api/users/alice/config",
            json={
                "changes": {
                    "tools": {"enabled": False},
                    "agent_models": {
                        "default": "",
                        "cheap": "summary-test-model",
                        "reasoning": "",
                    },
                }
            },
        )
        self.assertEqual(patched.status_code, 200, patched.text)
        stored_config = json.loads(
            (root / "users" / "alice" / "user_config.json").read_text("utf-8")
        )
        self.assertEqual(stored_config["provider"]["api_key"], "keep-secret")
        self.assertFalse(stored_config["tools"]["enabled"])
        self.assertEqual(stored_config["agent_models"]["cheap"], "summary-test-model")
        rejected_placeholder = self.request(
            app,
            "PATCH",
            "/api/users/alice/config",
            json={"changes": {"provider": {"api_key": "***"}}},
        )
        self.assertEqual(rejected_placeholder.status_code, 400)
        preferences = self.request(
            app,
            "PATCH",
            "/api/users/alice/preferences",
            json={"theme": "dark", "font_size": "large"},
        )
        self.assertEqual(preferences.json()["appearance"]["theme"], "dark")

    def test_completion_sound_is_user_scoped_validated_and_deletable(self) -> None:
        _, root = self.make_root()
        app = create_app(service=WebRunService(root))
        endpoint = "/api/users/alice/completion-sound"

        missing_status = self.request(app, "GET", f"{endpoint}/status")
        self.assertEqual(missing_status.status_code, 200, missing_status.text)
        self.assertFalse(missing_status.json()["available"])
        self.assertEqual(self.request(app, "GET", endpoint).status_code, 204)

        mp3 = b"ID3\x04\x00\x00\x00\x00\x00\x08testdata"
        uploaded = self.request(
            app,
            "POST",
            endpoint,
            files={"file": ("done.mp3", mp3, "audio/mpeg")},
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        status = uploaded.json()["status"]
        self.assertTrue(status["available"])
        self.assertTrue(status["enabled"])
        self.assertEqual(status["filename"], "completion_sound.mp3")
        self.assertEqual(status["mime_type"], "audio/mpeg")
        target = root / "users" / "alice" / "completion_sound.mp3"
        self.assertEqual(target.read_bytes(), mp3)
        self.assertFalse((root / "users" / "alice" / "download" / target.name).exists())
        self.assertFalse((root / "users" / "alice" / "file_upload" / target.name).exists())

        audio = self.request(app, "GET", endpoint)
        self.assertEqual(audio.status_code, 200, audio.text)
        self.assertEqual(audio.headers["content-type"], "audio/mpeg")
        self.assertEqual(audio.content, mp3)
        self.assertEqual(audio.headers["cache-control"], "private, no-cache")
        self.assertTrue(audio.headers["etag"].startswith('W/"'))
        cached_audio = self.request(
            app,
            "GET",
            endpoint,
            headers={"If-None-Match": audio.headers["etag"]},
        )
        self.assertEqual(cached_audio.status_code, 304, cached_audio.text)
        self.assertEqual(cached_audio.content, b"")

        with patch(
            "web.services.files._play_windows_completion_sound",
            return_value="user_mp3_mci",
        ):
            mp3_fallback = self.request(app, "POST", f"{endpoint}/fallback")
        self.assertTrue(mp3_fallback.json()["played"])
        self.assertEqual(mp3_fallback.json()["mode"], "user_mp3_mci")

        wav = b"RIFF" + (4).to_bytes(4, "little") + b"WAVEdata"
        replaced = self.request(
            app,
            "POST",
            endpoint,
            files={"file": ("done.wav", wav, "audio/wav")},
        )
        self.assertEqual(replaced.status_code, 200, replaced.text)
        self.assertFalse(target.exists())
        self.assertEqual(
            (root / "users" / "alice" / "completion_sound.wav").read_bytes(),
            wav,
        )

        invalid_type = self.request(
            app,
            "POST",
            endpoint,
            files={"file": ("done.txt", b"not audio", "text/plain")},
        )
        self.assertEqual(invalid_type.status_code, 400, invalid_type.text)
        mismatched = self.request(
            app,
            "POST",
            endpoint,
            files={"file": ("fake.mp3", b"OggSnot-mp3", "audio/mpeg")},
        )
        self.assertEqual(mismatched.status_code, 400, mismatched.text)
        oversized = self.request(
            app,
            "POST",
            endpoint,
            files={"file": ("large.mp3", b"ID3" + b"x" * (5 * 1024 * 1024), "audio/mpeg")},
        )
        self.assertEqual(oversized.status_code, 400, oversized.text)
        self.assertTrue((root / "users" / "alice" / "completion_sound.wav").exists())

        with patch(
            "web.services.files._play_windows_completion_sound",
            return_value="user_wav",
        ) as playback:
            fallback = self.request(app, "POST", f"{endpoint}/fallback")
        self.assertEqual(fallback.status_code, 200, fallback.text)
        self.assertTrue(fallback.json()["played"])
        self.assertEqual(fallback.json()["mode"], "user_wav")
        playback.assert_called_once()
        self.assertEqual(
            playback.call_args.args[0].resolve(),
            (root / "users" / "alice" / "completion_sound.wav").resolve(),
        )

        with patch(
            "web.services.files._play_windows_completion_sound",
            return_value="",
        ):
            unsupported = self.request(app, "POST", f"{endpoint}/fallback")
        self.assertFalse(unsupported.json()["played"])
        self.assertEqual(unsupported.json()["reason"], "unsupported_host")

        deleted = self.request(app, "DELETE", endpoint)
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertTrue(deleted.json()["deleted"])
        self.assertEqual(self.request(app, "GET", endpoint).status_code, 204)
        self.assertFalse(self.request(app, "GET", f"{endpoint}/status").json()["available"])
        missing_fallback = self.request(app, "POST", f"{endpoint}/fallback")
        self.assertFalse(missing_fallback.json()["played"])
        self.assertEqual(missing_fallback.json()["reason"], "not_configured")

    def test_completion_sound_terminal_fallback_is_windows_only(self) -> None:
        _, root = self.make_root()
        backend = WebRunService(root)
        backend.save_completion_sound(
            "alice",
            b"RIFF" + (4).to_bytes(4, "little") + b"WAVEdata",
            "audio/wav",
        )

        with patch("web.services.files.platform.system", return_value="Linux"):
            status = backend.completion_sound_status("alice")
            fallback = backend.play_completion_sound_fallback("alice")

        self.assertFalse(status["terminal_fallback_supported"])
        self.assertFalse(fallback["played"])
        self.assertEqual(fallback["reason"], "unsupported_host")

    def test_failure_sound_is_user_scoped_validated_and_deletable(self) -> None:
        _, root = self.make_root()
        app = create_app(service=WebRunService(root))
        endpoint = "/api/users/alice/failure-sound"

        missing_status = self.request(app, "GET", f"{endpoint}/status")
        self.assertEqual(missing_status.status_code, 200, missing_status.text)
        self.assertFalse(missing_status.json()["available"])
        self.assertEqual(self.request(app, "GET", endpoint).status_code, 204)

        mp3 = b"ID3\x04\x00\x00\x00\x00\x00\x08failure"
        uploaded = self.request(
            app,
            "POST",
            endpoint,
            files={"file": ("failed.mp3", mp3, "audio/mpeg")},
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        status = uploaded.json()["status"]
        self.assertTrue(status["available"])
        self.assertEqual(status["filename"], "failure_sound.mp3")
        target = root / "users" / "alice" / "failure_sound.mp3"
        self.assertEqual(target.read_bytes(), mp3)
        self.assertFalse((root / "users" / "alice" / "completion_sound.mp3").exists())

        audio = self.request(app, "GET", endpoint)
        self.assertEqual(audio.status_code, 200, audio.text)
        self.assertEqual(audio.headers["content-type"], "audio/mpeg")
        self.assertEqual(audio.content, mp3)

        with patch(
            "web.services.files._play_windows_failure_sound",
            return_value="user_mp3_mci",
        ) as playback:
            fallback = self.request(app, "POST", f"{endpoint}/fallback")
        self.assertTrue(fallback.json()["played"])
        self.assertEqual(fallback.json()["mode"], "user_mp3_mci")
        playback.assert_called_once()

        invalid = self.request(
            app,
            "POST",
            endpoint,
            files={"file": ("bad.txt", b"not audio", "text/plain")},
        )
        self.assertEqual(invalid.status_code, 400, invalid.text)
        self.assertEqual(target.read_bytes(), mp3)

        deleted = self.request(app, "DELETE", endpoint)
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertTrue(deleted.json()["deleted"])
        self.assertEqual(self.request(app, "GET", endpoint).status_code, 204)

    def test_failure_sound_terminal_fallback_is_windows_only(self) -> None:
        _, root = self.make_root()
        backend = WebRunService(root)
        backend.save_failure_sound(
            "alice",
            b"RIFF" + (4).to_bytes(4, "little") + b"WAVEdata",
            "audio/wav",
        )

        with patch("web.services.files.platform.system", return_value="Linux"):
            status = backend.failure_sound_status("alice")
            fallback = backend.play_failure_sound_fallback("alice")
        self.assertFalse(status["terminal_fallback_supported"])
        self.assertFalse(fallback["played"])
        self.assertEqual(fallback["reason"], "unsupported_host")

    def test_failure_sound_rejects_old_symlink_before_writing_new_format(self) -> None:
        _, root = self.make_root()
        outside = root / "outside.mp3"
        outside.write_bytes(b"outside")
        link = root / "users" / "alice" / "failure_sound.mp3"
        try:
            link.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"当前环境不能创建符号链接：{exc}")
        app = create_app(service=WebRunService(root))
        wav = b"RIFF" + (4).to_bytes(4, "little") + b"WAVEdata"
        response = self.request(
            app,
            "POST",
            "/api/users/alice/failure-sound",
            files={"file": ("failed.wav", wav, "audio/wav")},
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertFalse((root / "users" / "alice" / "failure_sound.wav").exists())
        self.assertTrue(link.is_symlink())
        self.assertEqual(outside.read_bytes(), b"outside")

    def test_completion_sound_rejects_fixed_path_symlink(self) -> None:
        _, root = self.make_root()
        outside = root / "outside.mp3"
        outside.write_bytes(b"outside")
        link = root / "users" / "alice" / "completion_sound.mp3"
        try:
            link.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"当前环境不能创建符号链接：{exc}")
        app = create_app(service=WebRunService(root))
        response = self.request(
            app,
            "POST",
            "/api/users/alice/completion-sound",
            files={"file": ("done.mp3", b"ID3valid", "audio/mpeg")},
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(outside.read_bytes(), b"outside")

    def test_startup_options_without_provider(self) -> None:
        _, root = self.make_root()
        (root / "config").mkdir()
        (root / "config" / "global_config.json").write_text(
            json.dumps(
                {"web": {"host": "127.0.0.1", "port": 1478, "log_level": "info"}}
            ),
            "utf-8",
        )
        import start_web

        with (
            patch.object(start_web, "project_root", return_value=root),
            patch.object(start_web, "_check_users", return_value=True),
            patch.object(start_web, "_can_bind", return_value=(True, "")),
            patch("uvicorn.run") as run,
        ):
            self.assertEqual(
                start_web.main(
                    [
                        "--host=0.0.0.0",
                        "--port=19000",
                        "--log-level=debug",
                        "--no-host",
                    ]
                ),
                0,
            )
        self.assertEqual(run.call_args.kwargs["host"], "0.0.0.0")
        self.assertEqual(run.call_args.kwargs["port"], 19000)
        self.assertEqual(run.call_args.kwargs["log_level"], "debug")

    def test_startup_uses_web_environment_defaults_and_cli_overrides(self) -> None:
        _, root = self.make_root()
        import start_web

        with (
            patch.dict(os.environ, {"WEB_HOST": "0.0.0.0", "WEB_PORT": "19001"}),
            patch.object(start_web, "project_root", return_value=root),
            patch.object(start_web, "_check_users", return_value=True),
            patch.object(start_web, "_can_bind", return_value=(True, "")),
            patch("uvicorn.run") as run,
        ):
            self.assertEqual(start_web.main(["--no-host"]), 0)
        self.assertEqual(run.call_args.kwargs["host"], "0.0.0.0")
        self.assertEqual(run.call_args.kwargs["port"], 19001)

        with (
            patch.dict(os.environ, {"WEB_HOST": "127.0.0.2", "WEB_PORT": "19002"}),
            patch.object(start_web, "project_root", return_value=root),
            patch.object(start_web, "_check_users", return_value=True),
            patch.object(start_web, "_can_bind", return_value=(True, "")),
            patch("uvicorn.run") as run,
        ):
            self.assertEqual(
                start_web.main(["--host=127.0.0.3", "--port=19003", "--no-host"]),
                0,
            )
        self.assertEqual(run.call_args.kwargs["host"], "127.0.0.3")
        self.assertEqual(run.call_args.kwargs["port"], 19003)

    def test_startup_rejects_invalid_web_port_environment(self) -> None:
        _, root = self.make_root()
        import start_web

        with (
            patch.dict(os.environ, {"WEB_PORT": "not-a-port"}),
            patch.object(start_web, "project_root", return_value=root),
            patch.object(start_web, "_check_users") as check_users,
            patch("uvicorn.run") as run,
        ):
            self.assertEqual(start_web.main(["--no-host"]), 1)
        check_users.assert_not_called()
        run.assert_not_called()

    def test_missing_terminal_and_invalid_event_become_sse_error(self) -> None:
        missing = self.request(
            create_app(
                service=FakeService(
                    events=[RunEvent(type="text_delta", content="partial")]
                )
            ),
            "POST",
            "/api/chat",
            json={"user": "alice", "session_id": "s1", "prompt": "hello"},
        )
        parsed = self.parse_sse(missing.text)
        self.assertEqual([item[0] for item in parsed], ["text_delta", "error"])
        self.assertEqual(
            parsed[-1][1]["error"]["exception_type"], "MissingTerminalEvent"
        )

        invalid = FakeService(events=["bad"])  # type: ignore[list-item]
        response = self.request(
            create_app(service=invalid),
            "POST",
            "/api/chat",
            json={"user": "alice", "session_id": "s1", "prompt": "hello"},
        )
        parsed = self.parse_sse(response.text)
        self.assertEqual(parsed[-1][0], "error")
        self.assertEqual(parsed[-1][1]["error"]["exception_type"], "InvalidRunEvent")

    def test_observer_endpoints_return_real_sanitized_state(self) -> None:
        _, root = self.make_root()
        (root / "config").mkdir()
        (root / "config" / "global_config.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "tools": {"enabled": True, "max_iterations": 4, "timeout": 10},
                    "history": {"schema_version": 3},
                    "memory": {
                        "storage_schema_version": 4,
                        "history_read_enabled": True,
                    },
                    "task_plan": {"auto_accept": False, "max_steps": 8},
                    "cron": {"enabled": True},
                    "task_cron_system": {"sense_update_rate": 12},
                    "agents": {"max_rounds": 30, "token_limit": 100000},
                }
            ),
            "utf-8",
        )
        (root / "users" / "alice" / "user_config.json").write_text(
            json.dumps(
                {
                    "schema_version": 9,
                    "provider": {
                        "type": "chat",
                        "base_url": "https://example.test/v1",
                        "model": "test-model",
                        "api_key": "super-secret",
                        "timeout": 45,
                    },
                    "knowledge": {
                        "use_shared": False,
                        "use_global": True,
                    },
                    "skills": {
                        "shared_whitelist": ["observer"],
                    },
                    "expand": {
                        "global_whitelist": [],
                        "shared_whitelist": [],
                    },
                    "perception": {"global_whitelist": ["runtime"]},
                    "plugins": {"whitelist": []},
                    "task_cron_system": {"sense_update_rate": 99},
                }
            ),
            "utf-8",
        )
        (root / "users" / "alice" / "knowledge").mkdir()
        (root / "users" / "alice" / "knowledge" / "notes.md").write_text(
            "# Alice Notes\nprivate index", "utf-8"
        )
        (root / "shared_knowledge").mkdir()
        (root / "shared_knowledge" / "team.md").write_text("# Team Shared", "utf-8")
        (root / "global_knowledge").mkdir()
        (root / "global_knowledge" / "shared.md").write_text("# Shared", "utf-8")
        (root / "global_sense").mkdir()
        (root / "global_sense" / "README.md").write_text("observer core", "utf-8")
        for module_name in ("runtime", "network"):
            module = root / "global_sense" / module_name
            module.mkdir()
            (module / "sense.md").write_text(module_name, "utf-8")
            (module / "data_update.py").write_text(
                "from pathlib import Path\n"
                f"Path('sense.md').write_text('{module_name} refreshed', encoding='utf-8')\n",
                "utf-8",
            )
            (module / "sense.json").write_text(
                json.dumps(
                    {
                        "name": f"{module_name} display",
                        "data_md": "sense.md",
                        "recent_update": "2026-07-19 12:00:00",
                        "health": "正常" if module_name == "runtime" else "异常",
                        "start_update": "data_update.py",
                    },
                    ensure_ascii=False,
                ),
                "utf-8",
            )
        broken_module = root / "global_sense" / "broken"
        broken_module.mkdir()
        (broken_module / "legacy.md").write_text("must not be injected", "utf-8")
        (root / "global_sense" / "register.py").write_text(
            "from pathlib import Path\n\n"
            "def register(registry):\n"
            "    registry.add_perception(Path(__file__).resolve().parent)\n",
            "utf-8",
        )
        shared_skills = root / "shared_skills"
        shared_skills.mkdir()
        (shared_skills / "register.py").write_text(
            "from pathlib import Path\n\n"
            "def register(registry):\n"
            "    registry.add_skills('shared', Path(__file__).resolve().parent)\n",
            "utf-8",
        )
        for skill_name in ("observer", "filtered"):
            skill = shared_skills / skill_name
            skill.mkdir()
            (skill / "SKILL.md").write_text(
                f"# {skill_name}\n{skill_name} description", "utf-8"
            )
        expand_roots = {
            "global": root / "global_expand",
            "shared": root / "shared_expand",
            "user": root / "users" / "alice" / "expand",
        }
        for scope, expand_root in expand_roots.items():
            expand_root.mkdir(parents=True)
            if scope != "user":
                (expand_root / "register.py").write_text(
                    "from pathlib import Path\n\n"
                    "def register(registry):\n"
                    f"    registry.add_expand_root('{scope}', Path(__file__).resolve().parent)\n",
                    "utf-8",
                )
            module_name = {"global": "lights", "shared": "bridge", "user": "personal"}[
                scope
            ]
            module = expand_root / module_name
            module.mkdir()
            (module / "input_data.md").write_text(f"# {scope} data\nready", "utf-8")
            (module / "expand_control.md").write_text(
                "## 注入层\n\n可执行安全操作。\n\n"
                "## 操作层\n\n### 触发场景\n用户明确请求时。\n\n"
                "### 使用操作\n运行 start_expand.py。",
                "utf-8",
            )
            (module / "data_update.py").write_text(
                "from pathlib import Path\n"
                "def update():\n"
                f"    Path('input_data.md').write_text('# {scope} data\\nrefreshed', encoding='utf-8')\n"
                "    return {'ok': True}\n",
                "utf-8",
            )
            (module / "start_expand.py").write_text("print('ok')\n", "utf-8")
            (module / "expand.json").write_text(
                json.dumps(
                    {
                        "name": f"{scope} display",
                        "explain": f"{scope} extension",
                        "open_input": True,
                        "input_data": "input_data.md",
                        "input_health": "正常",
                        "start_update": "data_update.py",
                        "open_control": True,
                        "start_expand": "start_expand.py",
                        "start_control": "expand_control.md",
                    },
                    ensure_ascii=False,
                ),
                "utf-8",
            )
        plugin = root / "plugins" / "clock"
        plugin.mkdir(parents=True)
        clock_manifest = {
            "name": "clock",
            "description": "read time",
            "input_schema": {"type": "object", "properties": {}},
            "version": "1",
            "enabled": True,
            "entrypoint": "tool.py:run",
        }
        (plugin / "SKILL.md").write_text(
            "# clock\nread time\n\n## Tool\n\n```json\n"
            + json.dumps(clock_manifest)
            + "\n```\n",
            "utf-8",
        )
        PlanStore(root, "alice").create(
            normalize_plan(
                title="Observer plan",
                description="safe metadata",
                user="alice",
                source="web",
                session_id="observer-session",
                steps=[
                    {
                        "step_id": "step_1",
                        "title": "Inspect",
                        "description": "read only",
                        "critical": True,
                    }
                ],
            )
        )
        CronStore(root, "alice").create(
            normalize_task(
                title="Daily check",
                prompt="do not expose this prompt",
                user="alice",
                type="daily",
                time="09:00",
                next_run_at="2026-07-20T09:00:00+08:00",
            )
        )
        window = empty_window("alice", "web", "observer-session")
        window["text"]["messages"] = [
            {"role": "user", "content": "observer prompt"},
            {"role": "assistant", "content": "observer response"},
        ]
        window["data"]["rounds"] = 1
        window["data"]["token_usage"] = {
            "prompt_tokens": 1200,
            "completion_tokens": 300,
            "total_tokens": 1500,
            "estimated": False,
        }
        commit_window(root / "users" / "alice" / "history" / "observer-window", window)
        app_window = empty_window("alice", "app", "observer-app-session")
        app_window["text"]["messages"] = [
            {"role": "user", "content": "app observer prompt"},
            {"role": "assistant", "content": "app observer response"},
        ]
        app_window["data"]["rounds"] = 1
        app_window["data"]["token_usage"] = {}
        commit_window(
            root / "users" / "alice" / "history" / "observer-app-window",
            app_window,
        )
        other_window = empty_window("alice", "web", "other-session")
        other_window["text"]["messages"] = [
            {"role": "user", "content": "old prompt"},
            {"role": "assistant", "content": "old response"},
        ]
        other_window["data"]["rounds"] = 1
        other_window["data"]["token_usage"] = {}
        other_window["data"]["updated_at"] = "2020-01-01T00:00:00+00:00"
        other_window["data"]["round_metrics"] = [
            {
                "round": 1,
                "usage": {},
                "tool_calls": 7,
                "provider_responses": [
                    {
                        "created_at": "2020-01-01T00:00:00+00:00",
                        "usage": {
                            "input_tokens": 10,
                            "output_tokens": 1,
                            "total_tokens": 11,
                        },
                    }
                ],
            }
        ]
        commit_window(
            root / "users" / "alice" / "history" / "other-window",
            other_window,
        )
        runtime_path = runtime_window_path(
            root / "users" / "alice" / "history" / "observer-window"
        )
        commit_window(
            runtime_path,
            window,
            summary_cache={
                "schema_version": 3,
                "source_hash": "hash",
                "covered_rounds": [1],
                "covered_through_round": 1,
                "created_at": "2026-07-18T00:00:00+00:00",
                "summary": {"narrative": "must not be exposed"},
                "memory_extractions": [],
            },
        )
        memory_store = MemoryStore(root, "alice", load_config("alice", root))
        memory_store.create_fragment(
            "seven_days", "safe-memory.md", "safe memory preview"
        )
        update_fragment_metadata(
            memory_store,
            "seven_days",
            "safe-memory.md",
            weight=2,
        )
        create_user_agent_package(
            root,
            "alice",
            {
                "name": "observer_agent",
                "description": "user hot-plug agent",
                "instruction": "Return JSON.",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
            },
        )
        app = create_app(
            service=WebRunService(
                root,
                runtime_status_provider=lambda: {
                    "state": "running",
                    "components": {"cron": {"name": "cron", "state": "running"}},
                },
            )
        )

        overview = self.request(
            app,
            "GET",
            "/api/users/alice/overview?session_id=observer-session",
        )
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.json()["counts"]["knowledge_documents"], 3)
        self.assertEqual(overview.json()["counts"]["enabled_tools"], 1)
        self.assertEqual(overview.json()["context"]["usage"]["total_tokens"], 1500)
        self.assertEqual(overview.json()["context"]["rounds"], 1)
        self.assertEqual(overview.json()["context"]["round_limit"], 30)
        self.assertEqual(overview.json()["agents"][0]["name"], "observer_agent")
        self.assertEqual(overview.json()["agents"][0]["source"], "user")
        self.assertEqual(overview.json()["summary_cache"]["covered_rounds"], [1])
        self.assertNotIn("must not be exposed", overview.text)
        self.assertEqual(overview.json()["runtime_host"]["state"], "running")
        context_window = overview.json()["context_window"]
        self.assertEqual(
            context_window["tokens"]["total_tokens"],
            context_window["tokens"]["system_prompt_tokens"]
            + context_window["tokens"]["context_tokens"],
        )
        self.assertEqual(
            context_window["tokens"]["capacity_tokens"],
            overview.json()["context"]["limit"],
        )
        self.assertEqual(context_window["conversation"]["foreground_rounds"], 1)
        self.assertEqual(context_window["conversation"]["archived_rounds"], 0)
        self.assertEqual(context_window["conversation"]["total_tool_calls"], 0)
        self.assertEqual(context_window["knowledge"]["enabled"], 2)
        self.assertNotIn("graph_enabled", context_window["knowledge"])
        self.assertIn("connected", context_window["messages"])
        self.assertIn("expands", context_window["integrations"])
        self.assertIn("senses", context_window["integrations"])
        self.assertEqual(
            context_window["injection_policy"],
            {"expand": "round", "perception": "round"},
        )

        app_overview = self.request(
            app,
            "GET",
            "/api/users/alice/overview?source=app&session_id=observer-app-session",
        )
        self.assertEqual(app_overview.status_code, 200, app_overview.text)
        self.assertEqual(app_overview.json()["session_id"], "observer-app-session")
        self.assertEqual(app_overview.json()["context_window"]["conversation"]["foreground_rounds"], 1)
        self.assertTrue(app_overview.json()["context_snapshot"]["available"])

        runtime_status = self.request(
            app,
            "GET",
            "/api/users/alice/runtime/status?session_id=observer-session",
        )
        self.assertEqual(runtime_status.status_code, 200, runtime_status.text)
        runtime_payload = runtime_status.json()
        self.assertEqual(runtime_payload["user"], "alice")
        self.assertEqual(runtime_payload["context"]["rounds"], 1)
        self.assertEqual(runtime_payload["tokens"]["total_tokens"], 1500)
        self.assertEqual(runtime_payload["tokens"]["request_count"], 1)
        self.assertTrue(runtime_payload["prompt"]["content"])

        app_runtime_status = self.request(
            app,
            "GET",
            "/api/users/alice/runtime/status?source=app&session_id=observer-app-session",
        )
        self.assertEqual(app_runtime_status.status_code, 200, app_runtime_status.text)
        self.assertEqual(app_runtime_status.json()["context"]["rounds"], 1)
        self.assertTrue(app_runtime_status.json()["context"]["context_snapshot"]["available"])
        self.assertEqual(
            [item["id"] for item in runtime_payload["prompt"]["components"]],
            list(PROMPT_SECTION_ORDER),
        )
        runtime_sense = next(
            item
            for item in runtime_payload["components"]["sense"]
            if item["id"] == "runtime"
        )
        self.assertEqual(runtime_sense["name"], "runtime display")
        self.assertEqual(len(runtime_payload["components"]["expand"]), 3)
        self.assertEqual(runtime_payload["runtime_host"]["state"], "running")
        self.assertEqual(
            set(runtime_payload["congestion"]),
            {"provider", "web", "message_router"},
        )
        self.assertIn("active_requests", runtime_payload["congestion"]["provider"])
        self.assertIn("active_chats", runtime_payload["congestion"]["web"])
        self.assertIn(
            "queued_messages", runtime_payload["congestion"]["message_router"]
        )
        self.assertNotIn("api_key", runtime_status.text)

        prompt_status = self.request(
            app,
            "GET",
            "/api/users/alice/runtime/status?session_id=observer-session&sections=summary,prompt",
        )
        self.assertEqual(prompt_status.status_code, 200, prompt_status.text)
        prompt_payload = prompt_status.json()
        self.assertEqual(
            set(prompt_payload["included_sections"]),
            {"summary", "prompt"},
        )
        self.assertTrue(prompt_payload["prompt"]["content"])
        self.assertEqual(prompt_payload["context"]["rounds"], 1)
        self.assertEqual(prompt_payload["tokens"]["total_tokens"], 0)
        self.assertEqual(prompt_payload["components"], {"sense": [], "expand": []})
        self.assertEqual(prompt_payload["system_cron"]["tracking"], "not_requested")

        token_status = self.request(
            app,
            "GET",
            "/api/users/alice/runtime/status?session_id=observer-session&sections=summary,tokens",
        )
        self.assertEqual(token_status.status_code, 200, token_status.text)
        token_payload = token_status.json()
        self.assertEqual(
            set(token_payload["included_sections"]),
            {"summary", "tokens"},
        )
        self.assertEqual(token_payload["tokens"]["total_tokens"], 1500)
        self.assertEqual(token_payload["tokens"]["request_count"], 1)
        self.assertEqual(token_payload["prompt"]["content"], "")
        self.assertEqual(token_payload["runtime_host"]["state"], "not_requested")

        tasks = self.request(app, "GET", "/api/users/alice/tasks")
        self.assertEqual(len(tasks.json()["plans"]), 1)
        self.assertEqual(len(tasks.json()["cron_tasks"]), 1)
        self.assertTrue(tasks.json()["cron_tasks"][0]["user_defined"])
        self.assertFalse(
            WebRunService._cron_summary({"exec_mode": "system"})["user_defined"]
        )
        self.assertNotIn("do not expose", tasks.text)

        knowledge = self.request(app, "GET", "/api/users/alice/knowledge")
        self.assertEqual(knowledge.json()["summary"]["user_documents"], 1)
        self.assertEqual(knowledge.json()["summary"]["shared_documents"], 1)
        self.assertEqual(knowledge.json()["summary"]["global_documents"], 1)
        self.assertEqual(
            [item["scope"] for item in knowledge.json()["documents"]],
            ["user", "shared", "global"],
        )
        self.assertEqual(
            [item["active_for_main_agent"] for item in knowledge.json()["documents"]],
            [True, False, True],
        )
        self.assertEqual(
            knowledge.json()["source_policy"]["knowledge"]["effective_scopes"],
            ["user", "global"],
        )
        self.assertNotIn("extensions", knowledge.json())
        self.assertNotIn("kemo_graph", knowledge.json()["source_policy"])
        self.assertNotIn("private index", knowledge.text)

        skills = self.request(app, "GET", "/api/users/alice/skills")
        self.assertEqual(skills.json()["tools"][0]["name"], "clock")
        self.assertEqual(skills.json()["prompt_summary"]["registered"], 2)
        self.assertEqual(skills.json()["prompt_summary"]["active"], 1)
        self.assertNotIn("project", skills.text)

        expands = self.request(app, "GET", "/api/users/alice/expand")
        self.assertEqual(expands.status_code, 200)
        self.assertEqual(expands.json()["summary"]["total"], 3)
        self.assertEqual(expands.json()["status_summary"]["enabled"], 3)
        self.assertEqual(expands.json()["status_summary"]["healthy"], 3)
        self.assertEqual(expands.json()["injection"]["injected_items"], 3)
        self.assertGreater(expands.json()["injection"]["estimated_tokens"], 0)
        expand_items = {
            item["id"]: item
            for group in expands.json()["expands"]
            for item in group["items"]
        }
        lights = expand_items["global:lights"]
        self.assertEqual(lights["display_name"], "global display")
        self.assertEqual(lights["collected_markdown"], "# global data\nready")
        self.assertEqual(lights["control_injection_markdown"], "可执行安全操作。")
        self.assertIn("用户明确请求时", lights["control_operation_markdown"])
        self.assertIn("[global:lights]", lights["injected_markdown"])
        self.assertTrue(lights["whitelisted"])
        self.assertTrue(lights["open_control"])

        refreshed_expand = self.request(
            app, "POST", "/api/users/alice/expand/global/lights/refresh"
        )
        self.assertEqual(refreshed_expand.status_code, 200)
        self.assertTrue(refreshed_expand.json()["updated"])
        self.assertIn(
            "refreshed", refreshed_expand.json()["item"]["collected_markdown"]
        )
        self.assertEqual(
            refreshed_expand.json()["item"]["runtime"]["update"]["status"],
            "completed",
        )

        disabled_expand = self.request(
            app,
            "PATCH",
            "/api/users/alice/expand/global/lights/enabled",
            json={"enabled": False},
        )
        self.assertEqual(disabled_expand.status_code, 200)
        self.assertEqual(disabled_expand.json()["whitelist"], ["__kemo_none__"])
        self.assertFalse(
            next(
                item
                for group in self.request(app, "GET", "/api/users/alice/expand").json()[
                    "expands"
                ]
                if group["scope"] == "global"
                for item in group["items"]
                if item["name"] == "lights"
            )["whitelisted"]
        )
        enabled_expand = self.request(
            app,
            "PATCH",
            "/api/users/alice/expand/global/lights/enabled",
            json={"enabled": True},
        )
        self.assertEqual(enabled_expand.status_code, 200)
        self.assertEqual(enabled_expand.json()["whitelist"], [])
        self.assertEqual(
            self.request(
                app,
                "PATCH",
                "/api/users/alice/expand/user/personal/enabled",
                json={"enabled": False},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.request(
                app, "DELETE", "/api/users/alice/expand/global/lights"
            ).status_code,
            400,
        )
        deleted_expand = self.request(
            app, "DELETE", "/api/users/alice/expand/user/personal"
        )
        self.assertEqual(deleted_expand.status_code, 200)
        self.assertTrue(deleted_expand.json()["deleted"])
        self.assertFalse((root / "users" / "alice" / "expand" / "personal").exists())

        sense = self.request(app, "GET", "/api/users/alice/sense")
        self.assertTrue(sense.json()["core_available"])
        self.assertEqual(
            [item["layer"] for item in sense.json()["sources"]],
            ["global", "global", "global"],
        )
        self.assertEqual(sense.json()["summary"]["global"], 3)
        self.assertEqual(sense.json()["summary"]["enabled"], 1)
        self.assertEqual(sense.json()["summary"]["healthy"], 1)
        self.assertEqual(sense.json()["summary"]["unhealthy"], 2)
        self.assertEqual(sense.json()["summary"]["invalid"], 1)
        self.assertEqual(sense.json()["core_files"], 2)
        self.assertEqual(sense.json()["summary"]["registered_data"], 2)
        self.assertEqual(sense.json()["summary"]["injected_data"], 1)
        self.assertTrue(sense.json()["injection"]["enabled"])
        self.assertEqual(sense.json()["injection"]["injected_items"], 1)
        self.assertGreater(sense.json()["injection"]["estimated_tokens"], 0)
        self.assertEqual(
            sense.json()["injection"]["source_files"],
            ["global_sense/runtime/sense.md"],
        )
        self.assertEqual(
            {item["id"]: item["status"] for item in sense.json()["sources"]},
            {"broken": "invalid", "network": "filtered", "runtime": "active"},
        )
        runtime_source = next(
            item for item in sense.json()["sources"] if item["id"] == "runtime"
        )
        self.assertEqual(runtime_source["display_name"], "runtime display")
        self.assertEqual(runtime_source["data_md"], "sense.md")
        self.assertEqual(runtime_source["recent_update"], "2026-07-19 12:00:00")
        self.assertEqual(runtime_source["health"], "正常")
        self.assertEqual(runtime_source["value_preview"], "runtime")
        self.assertEqual(runtime_source["collected_markdown"], "runtime")
        self.assertEqual(runtime_source["injected_markdown"], "[runtime]\nruntime")
        self.assertTrue(runtime_source["whitelisted"])
        self.assertEqual(runtime_source["update_interval"], "每 12 秒")
        self.assertEqual(runtime_source["update_interval_seconds"], 12)
        self.assertTrue(runtime_source["valid"])
        broken_source = next(
            item for item in sense.json()["sources"] if item["id"] == "broken"
        )
        self.assertFalse(broken_source["enabled"])
        self.assertFalse(broken_source["valid"])
        self.assertEqual(broken_source["health"], "异常")
        self.assertIn("sense.json", broken_source["error"])
        self.assertNotIn("must not be injected", sense.text)
        self.assertNotIn('"project"', sense.text)
        self.assertEqual(sense.json()["injection"]["content"], "[runtime]\nruntime")

        refreshed = self.request(app, "POST", "/api/users/alice/sense/runtime/refresh")
        self.assertEqual(refreshed.status_code, 200)
        self.assertTrue(refreshed.json()["updated"])
        self.assertEqual(
            refreshed.json()["source"]["collected_markdown"], "runtime refreshed"
        )

        disabled_runtime = self.request(
            app,
            "PATCH",
            "/api/users/alice/sense/runtime/enabled",
            json={"enabled": False},
        )
        self.assertEqual(disabled_runtime.status_code, 200)
        self.assertEqual(disabled_runtime.json()["whitelist"], ["__kemo_none__"])
        self.assertEqual(
            self.request(app, "GET", "/api/users/alice/sense").json()["summary"][
                "enabled"
            ],
            0,
        )
        reenabled_runtime = self.request(
            app,
            "PATCH",
            "/api/users/alice/sense/runtime/enabled",
            json={"enabled": True},
        )
        self.assertEqual(reenabled_runtime.status_code, 200)

        enabled_network = self.request(
            app,
            "PATCH",
            "/api/users/alice/sense/network/enabled",
            json={"enabled": True},
        )
        self.assertEqual(enabled_network.status_code, 200)
        self.assertTrue(enabled_network.json()["enabled"])
        self.assertEqual(
            self.request(app, "GET", "/api/users/alice/sense").json()["summary"][
                "enabled"
            ],
            2,
        )
        disabled_network = self.request(
            app,
            "PATCH",
            "/api/users/alice/sense/network/enabled",
            json={"enabled": False},
        )
        self.assertEqual(disabled_network.status_code, 200)
        deleted_network = self.request(app, "DELETE", "/api/users/alice/sense/network")
        self.assertEqual(deleted_network.status_code, 200)
        self.assertTrue(deleted_network.json()["deleted"])
        self.assertFalse((root / "global_sense" / "network").exists())

        prompt = self.request(app, "GET", "/api/users/alice/prompt/sections")
        self.assertEqual(len(prompt.json()["sections"]), len(PROMPT_SECTION_ORDER))
        self.assertNotIn("safe memory preview", prompt.text)
        self.assertIn("expand", prompt.json())
        memory = self.request(app, "GET", "/api/users/alice/memory/summary")
        self.assertEqual(memory.json()["summary"]["seven_days"], 1)
        self.assertEqual(memory.json()["items"][0]["weight"], 2)
        self.assertEqual(memory.json()["items"][0]["filename"], "safe-memory.md")

        settings = self.request(app, "GET", "/api/users/alice/settings")
        self.assertEqual(settings.json()["provider"]["model"], "test-model")
        self.assertEqual(settings.json()["provider"]["reasoning_effort"], "medium")
        self.assertEqual(settings.json()["provider"]["timeout"], 45.0)
        self.assertEqual(settings.json()["schema_version"], 9)
        self.assertEqual(
            settings.json()["schema_versions"],
            {
                "config_schema": 1,
                "history_schema": 3,
                "memory_storage_schema": 4,
            },
        )
        self.assertFalse(settings.json()["authentication"]["enabled"])
        self.assertTrue(settings.json()["features"]["expand_prompt_injection"])
        self.assertTrue(settings.json()["features"]["perception_prompt_injection"])
        self.assertEqual(
            settings.json()["source_policy"]["expand"]["injection_mode"],
            "round",
        )
        self.assertEqual(
            settings.json()["source_policy"]["perception"]["injection_mode"],
            "round",
        )
        self.assertNotIn("kemo_graph", settings.json()["source_policy"])
        self.assertNotIn("super-secret", settings.text)
        self.assertNotIn("api_key", settings.text)


if __name__ == "__main__":
    unittest.main()
