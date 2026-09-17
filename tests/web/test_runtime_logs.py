from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import httpx

from run.infra import LogStore
from run.infra import runtime_diagnostics as diagnostics
from run.tools import ToolDefinition, execute_tool
from web.app import create_app
from web.errors import NotFoundError
from web.service import WebRunService


class RuntimeLogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for user in ('alice', 'bob'):
            (self.root / 'users' / user).mkdir(parents=True)
        self.backend = WebRunService(self.root)
        self.store = LogStore(self.root)
        self.now = datetime.now(timezone.utc).isoformat()
        self.addCleanup(diagnostics.invalidate_runtime_log_cache, self.root)

    def cron(self, user='alice', task_id='job'):
        self.store.append_cron({'user': user, 'task_id': task_id, 'executed_at': self.now,
                                'status': 'success', 'result': {'private': 'secret-output'},
                                'error': {'message': 'secret-error'}})

    def message(self):
        self.store.append_message_entries([{
            'user': 'alice', 'machine_id': 'private-machine', 'platform': 'test',
            'occurred_at': self.now, 'direction': 'receive', 'kind': 'text',
            'chat_id': 'private-chat', 'content': 'secret-message', 'file_path': 'private-file',
        }])

    def test_categories_scope_projection_and_validation(self):
        self.cron(); self.cron('bob', 'bob-only'); self.cron('__system__', 'global-job'); self.message()
        diagnostics.record_terminal_output(self.root, 'Web 后端 → http://127.0.0.1:1357')
        diagnostics.record_terminal_output(self.root / 'other', 'other-root terminal output')
        result = self.backend.runtime_logs('alice')
        serialized = json.dumps(result)
        for secret in ('secret-output', 'secret-error', 'secret-message', 'private-chat', 'private-file', 'private-machine', 'bob-only', 'other-root'):
            self.assertNotIn(secret, serialized)
        self.assertIn('global-job', serialized)
        for category in ('backend', 'threads', 'terminal', 'message'):
            view = self.backend.runtime_logs('alice', category=category)
            self.assertTrue(view['entries'])
            self.assertTrue(all(row['category'] == category for row in view['entries']))
        with self.assertRaises(NotFoundError):
            self.backend.runtime_logs('missing')
        with self.assertRaises(ValueError):
            self.backend.runtime_logs('alice', category='bad')

    def test_cache_hit_refresh_write_delete_invalidation(self):
        self.message()
        with patch.object(LogStore, 'runtime_log_records', wraps=self.store.runtime_log_records) as read:
            first = self.backend.runtime_logs('alice', category='message')
            self.assertFalse(first['cache']['hit'])
            first['entries'][0]['title'] = 'mutated'
            second = self.backend.runtime_logs('alice', category='message')
            self.assertTrue(second['cache']['hit'])
            self.assertNotEqual(second['entries'][0]['title'], 'mutated')
            self.backend.runtime_logs('alice', category='terminal', page=2)
            self.assertEqual(read.call_count, 1)
            self.backend.runtime_logs('alice', refresh=True)
            self.assertEqual(read.call_count, 2)
            self.cron()
            self.assertFalse(self.backend.runtime_logs('alice')['cache']['hit'])
            self.store.delete_message_logs('private-machine', user='alice')
            self.assertEqual(self.backend.runtime_logs('alice', category='message')['entries'], [])

    def test_cache_ttl_bound_and_singleflight(self):
        loader = Mock(return_value=[{'id': 'x'}])
        with patch.object(diagnostics.time, 'monotonic', return_value=100):
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda _: diagnostics.cached_runtime_log_records(self.root, 'alice', loader), range(8)))
            self.assertEqual(loader.call_count, 1)
            self.assertEqual(sum(hit for _, hit in results), 7)
        with patch.object(diagnostics.time, 'monotonic', return_value=106):
            self.assertFalse(diagnostics.cached_runtime_log_records(self.root, 'alice', loader)[1])
            for index in range(diagnostics.MAX_CACHE_ENTRIES + 1):
                diagnostics.cached_runtime_log_records(self.root, f'user{index}', loader)
            self.assertLessEqual(len(diagnostics._cache), diagnostics.MAX_CACHE_ENTRIES)
            self.assertFalse(diagnostics.cached_runtime_log_records(self.root, 'alice', loader)[1])

    def test_memory_bound_expiry_and_no_disk_write(self):
        with patch.object(diagnostics.time, 'monotonic', return_value=100):
            for index in range(diagnostics.MAX_EVENTS + 3):
                diagnostics.record_terminal_output(self.root, f'启动终端第 {index} 行')
            rows = diagnostics.runtime_event_snapshot(self.root, 'alice')
            self.assertEqual(len(rows), diagnostics.MAX_EVENTS)
            self.assertFalse((self.root / 'runtime').exists())
            rows[0]['title'] = 'mutated'
            self.assertNotEqual(diagnostics.runtime_event_snapshot(self.root, 'alice')[0]['title'], 'mutated')
        with patch.object(diagnostics.time, 'monotonic', return_value=3701):
            self.assertEqual(diagnostics.runtime_event_snapshot(self.root, 'alice'), [])

    def test_terminal_logs_capture_startup_console_not_shell_and_redact_secrets(self):
        diagnostics.record_terminal_output(
            self.root,
            'Web 后端 → http://127.0.0.1:1357?token=super-secret',
        )
        diagnostics.record_terminal_output(
            self.root,
            'ERROR: Authorization Bearer very-private-value',
            stream='stderr',
        )
        result = self.backend.runtime_logs('alice', category='terminal', refresh=True)
        bob_result = self.backend.runtime_logs('bob', category='terminal', refresh=True)
        self.assertEqual(len(result['entries']), 2)
        stderr_entry = next(entry for entry in result['entries'] if entry['detail'] == '启动终端 · 标准错误')
        stdout_entry = next(entry for entry in result['entries'] if entry['detail'] == '启动终端 · 标准输出')
        self.assertEqual(stderr_entry['category'], 'terminal')
        self.assertEqual(stderr_entry['source'], 'memory')
        self.assertEqual(stderr_entry['status'], 'error')
        self.assertEqual(stderr_entry['stream'], 'stderr')
        self.assertEqual(stdout_entry['stream'], 'stdout')
        self.assertEqual(result['terminal']['stdout_items'], 1)
        self.assertEqual(result['terminal']['stderr_items'], 1)
        self.assertEqual(result['terminal']['loaded_items'], 2)
        self.assertEqual(result['terminal']['window_size'], 300)
        self.assertTrue(result['terminal']['process_local'])
        self.assertNotIn('super-secret', json.dumps(result))
        self.assertNotIn('very-private-value', json.dumps(result))
        self.assertEqual(result['entries'], bob_result['entries'])

    def test_terminal_capture_mirrors_visible_start_web_output(self):
        original_stdout, original_stderr = __import__('sys').stdout, __import__('sys').stderr
        diagnostics.restore_terminal_capture()
        try:
            self.assertTrue(diagnostics.install_terminal_capture(self.root))
            print('RuntimeHost 已启动 | transports=无')
            __import__('sys').stdout.flush()
        finally:
            diagnostics.restore_terminal_capture()
        self.assertIs(__import__('sys').stdout, original_stdout)
        self.assertIs(__import__('sys').stderr, original_stderr)
        terminal = self.backend.runtime_logs('alice', category='terminal', refresh=True)
        self.assertIn('RuntimeHost 已启动 | transports=无', [entry['title'] for entry in terminal['entries']])

    def test_terminal_omits_decorative_startup_banner_borders(self):
        for text in ('┌────────────┐', '│  kemo-agent  1.2.8  │', '└────────────┘'):
            diagnostics.record_terminal_output(self.root, text)
        terminal = self.backend.runtime_logs('alice', category='terminal', refresh=True)
        self.assertEqual([entry['title'] for entry in terminal['entries']], ['kemo-agent  1.2.8'])

    def test_terminal_is_chronological_unpaginated_output(self):
        for text in ('first terminal line', 'second terminal line', 'third terminal line'):
            diagnostics.record_terminal_output(self.root, text)
        result = self.backend.runtime_logs('alice', category='terminal', page=99, page_size=1, refresh=True)
        self.assertEqual([entry['title'] for entry in result['entries']], [
            'first terminal line', 'second terminal line', 'third terminal line',
        ])
        self.assertEqual(result['pagination'], {
            'page': 1, 'page_size': 3, 'total_items': 3, 'total_pages': 1,
            'has_previous': False, 'has_next': False,
        })
        self.assertEqual(result['terminal']['stdout_items'], 3)
        self.assertEqual(result['terminal']['stderr_items'], 0)

    def test_terminal_returns_only_bounded_live_tail(self):
        from web.services.runtime_logs import TERMINAL_LIVE_WINDOW
        for index in range(TERMINAL_LIVE_WINDOW + 5):
            diagnostics.record_terminal_output(self.root, f'terminal line {index}')
        result = self.backend.runtime_logs('alice', category='terminal', refresh=True)
        self.assertEqual(result['pagination']['total_items'], TERMINAL_LIVE_WINDOW + 5)
        self.assertEqual(result['pagination']['page_size'], TERMINAL_LIVE_WINDOW)
        self.assertEqual(len(result['entries']), TERMINAL_LIVE_WINDOW)
        self.assertEqual(result['entries'][0]['title'], 'terminal line 5')
        self.assertEqual(result['entries'][-1]['title'], f'terminal line {TERMINAL_LIVE_WINDOW + 4}')

    def test_source_failure_is_visible_and_not_cached(self):
        with patch.object(LogStore, 'runtime_log_records', side_effect=RuntimeError('private-db-path')) as read:
            for _ in range(2):
                result = self.backend.runtime_logs('alice')
                self.assertTrue(result['source_errors'])
                self.assertNotIn('private-db-path', json.dumps(result))
            self.assertEqual(read.call_count, 2)

    def test_pagination_and_thread_snapshot_do_not_expose_names(self):
        for index in range(30):
            self.cron(task_id=f'job{index}')
        result = self.backend.runtime_logs('alice', category='backend', page=2, page_size=25)
        self.assertEqual(len(result['entries']), 5)
        self.assertTrue(result['pagination']['has_previous'])
        self.assertFalse(result['pagination']['has_next'])
        self.assertEqual(self.backend.runtime_logs('alice', category='backend', page=999)['pagination']['page'], 2)
        fake_thread = Mock(ident=99, name='private-user-thread', daemon=True)
        fake_thread.is_alive.return_value = True
        with patch('web.services.runtime_logs.threading.enumerate', return_value=[fake_thread]):
            result = self.backend.runtime_logs('alice', category='threads')
        self.assertNotIn('private-user-thread', json.dumps(result))
        self.assertEqual(result['entries'][0]['source'], 'snapshot')

    def test_tool_result_and_exception_preserved_without_output_leak(self):
        tool = ToolDefinition(name='shell', description='', input_schema={'type': 'object'}, version='1',
                              enabled=True, entrypoint='', source='test', directory=self.root)
        context = {'root': self.root, 'user': 'alice'}
        for returned, status in (({'ok': True, 'output': 'private-output', 'exit_code': 0}, 'success'),
                                  ({'ok': True, 'status': 'running'}, 'running'),
                                  ({'ok': True, 'status': 'interrupted'}, 'failed'),
                                  ({'ok': False, 'exit_code': 1}, 'failed')):
            with self.subTest(status=status):
                tool._callable = lambda: returned
                self.assertIs(execute_tool(tool, {}, context=context, timeout=1), returned)
                row = diagnostics.runtime_event_snapshot(self.root, 'alice')[-1]
                self.assertEqual(row['category'], 'backend')
                self.assertEqual(row['status'], status)
                self.assertNotIn('private-output', json.dumps(row))
        failure = ValueError('private-exception')
        tool._callable = Mock(side_effect=failure)
        with self.assertRaises(ValueError) as raised:
            execute_tool(tool, {}, context=context, timeout=1)
        self.assertIs(raised.exception, failure)
        row = diagnostics.runtime_event_snapshot(self.root, 'alice')[-1]
        self.assertIn('ValueError', row['detail'])
        self.assertNotIn('private-exception', json.dumps(row))
        tool._callable = lambda: {'ok': True}
        with patch('run.infra.runtime_diagnostics.record_runtime_event', side_effect=RuntimeError):
            self.assertEqual(execute_tool(tool, {}, context=context, timeout=1), {'ok': True})

    def test_route_validates_filters_and_pagination(self):
        async def check():
            app = create_app(service=self.backend)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                response = await client.get('/api/users/alice/runtime/logs?category=threads')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['category'], 'threads')
                for query in ('category=bad', 'page=0', 'page_size=101'):
                    response = await client.get('/api/users/alice/runtime/logs?' + query)
                    self.assertEqual(response.status_code, 400)
        asyncio.run(check())

    def test_host_lifecycle_records_changes_without_private_names(self):
        from run.scheduler import RuntimeHost
        host = RuntimeHost.__new__(RuntimeHost)
        host.root = self.root
        host._lock = threading.RLock()
        host._components = {}
        host._set_component('transport:private-account', 'running')
        host._set_component('transport:private-account', 'running')
        host._set_component('transport:private-account', 'failed', ValueError('private-failure'))
        rows = diagnostics.runtime_event_snapshot(self.root, 'alice')
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['title'], 'runtime_transport')
        self.assertNotIn('private-account', json.dumps(rows))
        self.assertNotIn('private-failure', json.dumps(rows))


if __name__ == '__main__':
    unittest.main()
