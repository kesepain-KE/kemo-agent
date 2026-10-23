from __future__ import annotations

from tests.type_assertions import require_present

import asyncio
from pathlib import Path
import tempfile
import unittest

import httpx

from run.memory import MemoryStore
from web.app import create_app
from web.service import WebRunService


class MemoryApiContractTests(unittest.TestCase):
    def test_four_tiers_read_update_delete_and_user_isolation(self) -> None:
        async def exercise(root: Path) -> None:
            (root / "config").mkdir()
            (root / "config/global_config.json").write_text('{"schema_version": 1}', "utf-8")
            for user in ("alice", "bob"):
                (root / "users" / user).mkdir(parents=True)
            store = MemoryStore(root, "alice", {})
            tiers = ("seven_days", "one_month", "half_year", "permanent")
            for tier in tiers:
                store.create_fragment(tier, f"合同 {tier}.md", f"Original {tier}")
            app = create_app(service=WebRunService(root))
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as client:
                summary = await client.get("/api/users/alice/memory/summary")
                self.assertEqual(summary.status_code, 200, summary.text)
                self.assertEqual(summary.json()["summary"]["total"], 4)
                self.assertEqual({item["tier"] for item in summary.json()["items"]}, set(tiers))
                for tier in tiers:
                    with self.subTest(tier=tier):
                        filename = f"合同 {tier}.md"
                        params = {"tier": tier, "filename": filename}
                        before = store.get_entry(tier, filename)
                        endpoint = "/api/users/alice/memory/item"
                        detail = await client.get(endpoint, params=params)
                        self.assertEqual(detail.status_code, 200, detail.text)
                        self.assertEqual(detail.json()["memory_ref"], f"{tier}:{filename}")
                        for field in ("weighted_today", "weight_locked_today", "weight_day", "weight_timezone"):
                            self.assertIn(field, detail.json())
                        await client.get(endpoint, params=params)
                        self.assertEqual(store.get_entry(tier, filename), before)
                        other = await client.get("/api/users/bob/memory/item", params=params)
                        self.assertEqual(other.status_code, 404, other.text)
                        response = await client.put(
                            endpoint, params={"filename": filename},
                            json={"tier": tier, "content": f"Updated {tier}"},
                        )
                        self.assertEqual(response.status_code, 200, response.text)
                        self.assertEqual(response.json()["tier"], tier)
                        self.assertEqual(response.json()["content"], f"Updated {tier}")
                        self.assertEqual(require_present(store.get_entry(tier, filename))["content"], f"Updated {tier}")
                        blank = await client.put(
                            endpoint, params={"filename": filename}, json={"tier": tier, "content": "  "},
                        )
                        self.assertEqual(blank.status_code, 400, blank.text)
                        deleted = await client.delete(endpoint, params=params)
                        self.assertEqual(deleted.status_code, 200, deleted.text)
                        self.assertTrue(deleted.json()["deleted"])
                        self.assertEqual((await client.get(endpoint, params=params)).status_code, 404)
                self.assertEqual((await client.get("/api/users/alice/memory/summary")).json()["summary"]["total"], 0)
                created = await client.put(
                    "/api/users/alice/memory/item", params={"filename": "长期事实.md"},
                    json={"tier": "permanent", "content": "Explicit long term fact"},
                )
                self.assertEqual(created.status_code, 200, created.text)
                self.assertEqual(created.json()["tier"], "permanent")
                downgraded = await client.put(
                    "/api/users/alice/memory/item", params={"filename": "长期事实.md"},
                    json={"tier": "seven_days", "content": "Changed fact"},
                )
                self.assertEqual(downgraded.status_code, 400, downgraded.text)
                self.assertEqual(require_present(store.get_entry("permanent", "长期事实.md"))["content"], "Explicit long term fact")

        with tempfile.TemporaryDirectory(prefix="kemo-memory-contract-") as directory:
            asyncio.run(exercise(Path(directory)))
