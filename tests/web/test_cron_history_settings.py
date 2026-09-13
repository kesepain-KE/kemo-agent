from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import unittest

import httpx

from web.app import create_app
from web.service import WebRunService


class CronHistorySettingsApiTests(unittest.TestCase):
    def test_global_api_defaults_validates_and_persists_retention(self) -> None:
        async def exercise(root: Path) -> None:
            (root / 'config').mkdir()
            config_path = root / 'config/global_config.json'
            config_path.write_text('{}\n', encoding='utf-8')
            (root / 'users/alice').mkdir(parents=True)
            app = create_app(service=WebRunService(root))
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url='http://test'
            ) as client:
                response = await client.get('/api/global-config')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['config']['cron']['history_retention_days'], 7)

                response = await client.patch(
                    '/api/global-config', json={'changes': {'cron': {'history_retention_days': 14}}}
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['config']['cron']['history_retention_days'], 14)
                self.assertEqual(json.loads(config_path.read_text('utf-8'))['cron']['history_retention_days'], 14)

                for invalid in (-1, 3651, 1.5, True, '7'):
                    response = await client.patch(
                        '/api/global-config', json={'changes': {'cron': {'history_retention_days': invalid}}}
                    )
                    self.assertEqual(response.status_code, 400, invalid)
                self.assertEqual(json.loads(config_path.read_text('utf-8'))['cron']['history_retention_days'], 14)

                response = await client.patch(
                    '/api/global-config', json={'changes': {'cron': {'history_retention_days': None}}}
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['config'].get('cron', {}).get('history_retention_days'), None)
                self.assertEqual((await client.get('/api/global-config')).json()['config']['cron']['history_retention_days'], 7)

                response = await client.patch(
                    '/api/users/alice/config', json={'changes': {'cron': {'history_retention_days': 1}}}
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn('仅支持全局配置', response.json()['error']['message'])
                self.assertFalse((root / 'users/alice/user_config.json').exists())

        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(exercise(Path(directory)))


if __name__ == '__main__':
    unittest.main()
