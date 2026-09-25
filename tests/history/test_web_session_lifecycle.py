from contextlib import contextmanager
import asyncio
import json
from datetime import datetime, timezone
import copy
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from run.conversation import session_lock
from run.history import (cleanup_empty_web_sessions, inspect_stale_web_sessions,
                         touch_web_session_lease, reserve_session, find_record,
                         empty_window, commit_terminal_windows, runtime_window_path,
                         update_run_state)
from run.history import web_lifecycle
from run.history import store_core
from run.history.store import connection
from run.scheduler import MaintenanceScheduler
from web.errors import NotFoundError
from web.service import WebRunService
from web.app import create_app


class WebSessionLifecycleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'config').mkdir()
        (self.root / 'config/global_config.json').write_text(
            json.dumps({'memory': {'extraction_mode': 'compression_only'}}), encoding='utf-8'
        )
        for user in ('alice', 'bob'):
            (self.root / 'users' / user).mkdir(parents=True)
            (self.root / 'users' / user / 'user_config.json').write_text('{}', encoding='utf-8')
        self.now = time.time()

    def seed(self, sid='empty', user='alice', source='web'):
        reserve_session(self.root, user, source, sid, active_key=f'{source}:{user}:{sid}')
        self.age(sid, user=user, source=source)

    def age(self, sid, *, user='alice', source='web'):
        with connection(self.root, user, write=True) as db:
            timestamp = datetime.fromtimestamp(self.now - 200, timezone.utc).isoformat()
            row = db.execute(
                'SELECT record_json FROM history_sessions WHERE source=? AND session_id=?',
                (source, sid),
            ).fetchone()
            record = json.loads(row['record_json']) if row is not None else {}
            record['updated_at'] = timestamp
            db.execute('UPDATE history_sessions SET updated_at=?, record_json=? WHERE source=? AND session_id=?',
                       (timestamp, json.dumps(record, ensure_ascii=False), source, sid))

    def clean(self, **kwargs):
        return cleanup_empty_web_sessions(self.root, 'alice', now=self.now, **kwargs)

    def seed_data(self, sid='data', *, user='alice', processed=0, active=True):
        path = self.root / 'users' / user / 'history' / ('conv_' + sid)
        window = empty_window(user, 'web', sid)
        window['text']['messages'] = [
            {'role': 'user', 'content': '需要记忆的旧对话'},
            {'role': 'assistant', 'content': '已经完成一轮'},
        ]
        window['data'].update(rounds=1, memory_processed_round=processed,
                              memory_status='completed' if processed else 'deferred')
        commit_terminal_windows(
            path, window, runtime_window_path(path), copy.deepcopy(window),
            active_key=f'interactive:{user}:old_page' if active else None,
        )
        self.age(sid, user=user)
        return path

    def test_offline_empty_only_and_delete_fence(self):
        self.seed()
        self.seed('other-user', user='bob')
        self.seed('app', source='app')
        self.seed('cron', source='background:cron:job')
        reserve_session(self.root, 'alice', 'web', 'newborn')
        self.assertEqual(self.clean()['deleted_sessions'], ['empty'])
        self.assertIsNone(find_record(self.root, 'alice', 'web', 'empty'))
        self.assertIsNotNone(find_record(self.root, 'alice', 'web', 'newborn'))
        self.assertIsNotNone(find_record(self.root, 'bob', 'web', 'other-user'))
        self.assertIsNotNone(find_record(self.root, 'alice', 'app', 'app'))
        self.assertIsNotNone(find_record(self.root, 'alice', 'background:cron:job', 'cron'))
        self.assertFalse(touch_web_session_lease(self.root, 'alice', 'empty', 'late', now=self.now))
        with connection(self.root, 'alice') as db:
            self.assertIsNotNone(db.execute("SELECT 1 FROM history_deleted_sessions WHERE session_id='empty'").fetchone())
            self.assertIsNone(db.execute("SELECT 1 FROM history_active_sessions WHERE session_id='empty'").fetchone())

    def test_online_multi_tab_and_restart_checkpoint(self):
        self.seed()
        one, two = WebRunService(self.root), WebRunService(self.root)
        one.session_lease('alice', 'empty', 'client_one')
        two.session_lease('alice', 'empty', 'client_two')
        one.release_session_lease('alice', 'empty', 'client_one')
        self.assertEqual(self.clean()['deleted_sessions'], [])
        # A fresh service/process does not own the other tabs' memory map;
        # SQLite leases must still protect them.
        self.assertEqual(WebRunService(self.root).cleanup_empty_web_sessions(), 0)
        with patch.object(web_lifecycle.time, 'time', return_value=self.now + 100):
            self.assertEqual(WebRunService(self.root).cleanup_empty_web_sessions(), 1)
        with self.assertRaises(NotFoundError):
            two.session_lease('alice', 'empty', 'client_two')

    def test_checkpoint_coalesces_writes_and_errors_do_not_fake_presence(self):
        self.seed()
        with patch.object(web_lifecycle, 'connection', wraps=connection) as connect:
            touch_web_session_lease(self.root, 'alice', 'empty', 'one', now=self.now)
            touch_web_session_lease(self.root, 'alice', 'empty', 'one', now=self.now + 15)
            touch_web_session_lease(self.root, 'alice', 'empty', 'one', now=self.now + 30)
            self.assertEqual(sum(bool(call.kwargs.get('write')) for call in connect.call_args_list), 2)

    def test_closed_session_rejects_late_lease_and_cannot_be_reopened(self):
        self.seed_data('ended')
        from run.history import close_session, prepare_window
        close_session(self.root, 'alice', 'web', 'ended')
        self.assertFalse(touch_web_session_lease(self.root, 'alice', 'ended', 'late', now=self.now))
        with self.assertRaisesRegex(Exception, '已经结束'):
            prepare_window(self.root, 'alice', 'web', 'ended')

    def test_data_and_claims_protected_even_with_zero_registry_rounds(self):
        for sid, field in [('running', 'run_state'), ('memory', 'memory_status'), ('summary', 'summary_status')]:
            self.seed(sid)
            with connection(self.root, 'alice', write=True) as db:
                db.execute(f'UPDATE history_sessions SET {field}=? WHERE session_id=?',
                           ('running' if field == 'run_state' else 'processing', sid))
        for sid, rounds in [('partial', 0), ('completed', 1)]:
            window = empty_window('alice', 'web', sid)
            window['data']['rounds'] = rounds
            window['text']['messages'] = [{'role': 'user', 'content': 'keep me'}]
            path = self.root / 'users/alice/history' / ('conv_' + sid)
            commit_terminal_windows(path, window, runtime_window_path(path), copy.deepcopy(window))
            self.age(sid)
            # Even an inconsistent registry is not sufficient evidence to delete.
            with connection(self.root, 'alice', write=True) as db:
                db.execute('UPDATE history_sessions SET rounds=0 WHERE session_id=?', (sid,))
        self.assertEqual(self.clean()['deleted_sessions'], [])

    def test_empty_physical_windows_cleaned_and_busy_lock_skipped(self):
        sid = 'blank-window'
        path = self.root / 'users/alice/history/conv_blank'
        window = empty_window('alice', 'web', sid)
        commit_terminal_windows(path, window, runtime_window_path(path), copy.deepcopy(window))
        self.age(sid)
        held, release = threading.Event(), threading.Event()
        def hold():
            with session_lock(self.root, 'alice', 'web', sid):
                held.set()
                release.wait(3)
        thread = threading.Thread(target=hold)
        thread.start()
        try:
            self.assertTrue(held.wait(2))
            self.assertEqual(self.clean()['skipped_busy'], 1)
        finally:
            release.set(); thread.join(3)
        self.assertEqual(self.clean()['deleted_sessions'], [sid])
        with connection(self.root, 'alice') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM history_windows').fetchone()[0], 0)

    def test_transaction_rechecks_new_lease_and_rolls_back_on_failure(self):
        self.seed()
        original = connection
        renewed = False
        @contextmanager
        def renew_before_write(*args, **kwargs):
            nonlocal renewed
            if kwargs.get('write') and not renewed:
                renewed = True
                with original(self.root, 'alice', write=True) as db:
                    db.execute('INSERT INTO history_web_leases VALUES(?, ?, ?)', ('empty', 'racer', self.now + 90))
            with original(*args, **kwargs) as db:
                yield db
        with patch.object(web_lifecycle, 'connection', side_effect=renew_before_write):
            self.assertEqual(self.clean()['deleted_sessions'], [])
        with connection(self.root, 'alice', write=True) as db:
            db.execute('DELETE FROM history_web_leases')
        original_delete = web_lifecycle._delete_session_windows
        def fail(*args):
            original_delete(*args)
            raise RuntimeError('test rollback')
        with patch.object(web_lifecycle, '_delete_session_windows', side_effect=fail):
            with self.assertRaises(RuntimeError):
                self.clean()
        self.assertIsNotNone(find_record(self.root, 'alice', 'web', 'empty'))
        with connection(self.root, 'alice') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM history_deleted_sessions').fetchone()[0], 0)

    def test_no_database_creation_and_bounded_batch(self):
        self.assertEqual(self.clean()['deleted_sessions'], [])
        self.assertFalse((self.root / 'users/alice/history/history.sqlite3').exists())
        for index in range(4):
            self.seed(f'empty-{index}')
        self.assertEqual(len(self.clean(limit=2)['deleted_sessions']), 2)
        self.assertEqual(len(self.clean()['deleted_sessions']), 2)

    def test_new_page_reserves_independent_session_without_changing_original(self):
        service = WebRunService(self.root)
        first = service.create_session('alice', 'original_client')
        other = service.create_session('alice', 'newtab_client')
        self.assertNotEqual(first['session']['session_id'], other['session']['session_id'])
        self.assertEqual(service.active_session('alice', 'original_client')['session']['session_id'], first['session']['session_id'])

    def test_v5_migration_adds_leases_without_changing_existing_sessions(self):
        self.seed()
        with connection(self.root, 'alice', write=True) as db:
            db.execute('DROP TABLE history_web_leases')
            db.execute("UPDATE history_meta SET value='5' WHERE key='schema_version'")
        with patch.object(store_core, '_READY_DATABASES', set()):
            with connection(self.root, 'alice') as db:
                self.assertEqual(db.execute("SELECT value FROM history_meta WHERE key='schema_version'").fetchone()[0], '6')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM history_web_leases').fetchone()[0], 0)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM history_sessions').fetchone()[0], 1)

    def test_cleanup_worker_owned_by_app_lifespan(self):
        app = create_app(root=self.root)
        previous = set(threading.enumerate())
        async def check():
            async with app.router.lifespan_context(app):
                workers = [t for t in threading.enumerate() if t not in previous and t.name == 'web-empty-session-cleanup']
                self.assertEqual(len(workers), 1)
            self.assertFalse(workers[0].is_alive())
        asyncio.run(check())

    def test_startup_inspection_queues_data_closes_binding_and_deletes_empty(self):
        data_path = self.seed_data()
        self.seed('empty-startup')
        self.seed_data('already-remembered', processed=1)
        result = inspect_stale_web_sessions(self.root, 'alice', now=self.now, batch_size=1)
        self.assertEqual(result['deleted_sessions'], ['empty-startup'])
        self.assertEqual(result['queued_memory'], ['data'])
        self.assertEqual(result['errors'], [])
        record = find_record(self.root, 'alice', 'web', 'data')
        self.assertEqual(record['lifecycle'], 'closed')
        self.assertEqual(record['memory_status'], 'queued')
        self.assertEqual(record['memory_queue_reason'], 'startup_offline_session')
        self.assertEqual(record['memory_target_round'], 1)
        self.assertTrue(data_path)
        remembered = find_record(self.root, 'alice', 'web', 'already-remembered')
        self.assertEqual(remembered['lifecycle'], 'closed')
        self.assertEqual(remembered['memory_status'], 'completed')
        with connection(self.root, 'alice') as db:
            self.assertIsNone(db.execute("SELECT 1 FROM history_active_sessions WHERE session_id='data'").fetchone())
        self.assertFalse(touch_web_session_lease(self.root, 'alice', 'empty-startup', 'old_page', now=self.now))
        replacement = WebRunService(self.root).create_session('alice', 'new_page')['session']['session_id']
        self.assertNotEqual(replacement, 'empty-startup')

    def test_periodic_offline_inspection_uses_distinct_memory_reason(self):
        self.seed_data('periodic-offline')
        result = inspect_stale_web_sessions(
            self.root, 'alice', now=self.now,
            queue_reason='offline_session_expired',
        )
        self.assertEqual(result['queued_memory'], ['periodic-offline'])
        record = find_record(self.root, 'alice', 'web', 'periodic-offline')
        self.assertEqual(record['lifecycle'], 'closed')
        self.assertEqual(record['memory_queue_reason'], 'offline_session_expired')

    def test_startup_inspection_protects_online_new_busy_and_partial_data(self):
        self.seed_data('online')
        touch_web_session_lease(self.root, 'alice', 'online', 'live_page', now=self.now)
        self.seed_data('running')
        update_run_state(self.root, 'alice', 'web', 'running', run_state='running')
        reserve_session(self.root, 'alice', 'web', 'new-session')
        partial = empty_window('alice', 'web', 'partial-data')
        partial['text']['messages'] = [{'role': 'user', 'content': '未形成完整轮'}]
        path = self.root / 'users/alice/history/conv_partial'
        commit_terminal_windows(path, partial, runtime_window_path(path), copy.deepcopy(partial))
        self.age('partial-data')
        result = inspect_stale_web_sessions(self.root, 'alice', now=self.now)
        self.assertEqual(result['queued_memory'], [])
        self.assertEqual(result['deleted_sessions'], [])
        self.assertEqual(result['preserved_sessions'], [
            {'session_id': 'partial-data', 'reason': 'no_pending_rounds'},
        ])
        self.assertEqual(find_record(self.root, 'alice', 'web', 'partial-data')['lifecycle'], 'closed')
        self.assertEqual(find_record(self.root, 'alice', 'web', 'online')['lifecycle'], 'open')
        self.assertEqual(find_record(self.root, 'alice', 'web', 'running')['run_state'], 'running')
        self.assertEqual(find_record(self.root, 'alice', 'web', 'new-session')['lifecycle'], 'open')

    def test_startup_inspection_rechecks_racing_lease_and_reports_queue_failure(self):
        self.seed_data('race')
        self.seed_data('queue-failure')
        original = connection
        renewed = False
        @contextmanager
        def renew_before_write(*args, **kwargs):
            nonlocal renewed
            if kwargs.get('write') and not renewed:
                renewed = True
                with original(self.root, 'alice', write=True) as db:
                    db.execute('INSERT INTO history_web_leases VALUES(?, ?, ?)',
                               ('race', 'arriving_page', self.now + 90))
            with original(*args, **kwargs) as db:
                yield db
        failed_queue = Mock(side_effect=RuntimeError('must stay private'))
        with patch.object(web_lifecycle, 'connection', side_effect=renew_before_write):
            result = inspect_stale_web_sessions(
                self.root, 'alice', now=self.now, queue_memory=failed_queue,
            )
        self.assertEqual(find_record(self.root, 'alice', 'web', 'race')['lifecycle'], 'open')
        self.assertEqual(result['errors'], [
            {'session_id': 'queue-failure', 'exception_type': 'RuntimeError'},
        ])
        self.assertNotIn('must stay private', json.dumps(result))

    def test_service_startup_pass_all_users_wakes_memory_once(self):
        self.seed_data('alice-data')
        self.seed_data('bob-data', user='bob')
        wake = Mock()
        service = WebRunService(self.root, memory_waker=wake)
        result = service.inspect_conversation_spaces_on_startup()
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(result['queued_memory'], 2)
        self.assertEqual(set(result['users']), {'alice', 'bob'})
        wake.assert_called_once_with()
        # Durable queue registration is idempotent and does not wake twice.
        again = service.inspect_conversation_spaces_on_startup()
        self.assertEqual(again['queued_memory'], 0)
        wake.assert_called_once_with()

    def test_service_inspection_retires_offline_app_data_but_keeps_live_app(self):
        offline = empty_window('alice', 'app', 'app-offline')
        offline['text']['messages'] = [
            {'role': 'user', 'content': 'app fact'},
            {'role': 'assistant', 'content': 'saved'},
        ]
        offline['data'].update(rounds=1, memory_status='deferred', memory_processed_round=0)
        path = self.root / 'users/alice/history/conv_app_offline'
        commit_terminal_windows(path, offline, runtime_window_path(path), copy.deepcopy(offline))
        self.age('app-offline', source='app')

        service = WebRunService(self.root)
        reserve_session(
            self.root, 'alice', 'app', 'app-live',
            active_key='app:alice:app_live_client',
        )
        live = service.active_session('alice', 'app_live_client', source='app')
        live_id = live['session']['session_id']
        self.age(live_id, source='app')

        result = service.inspect_conversation_spaces_on_startup()
        self.assertEqual(result['queued_memory'], 1)
        self.assertEqual(find_record(self.root, 'alice', 'app', 'app-offline')['lifecycle'], 'closed')
        self.assertEqual(
            find_record(self.root, 'alice', 'app', 'app-offline')['memory_queue_reason'],
            'startup_offline_app_session',
        )
        self.assertEqual(find_record(self.root, 'alice', 'app', live_id)['lifecycle'], 'open')

    def test_app_lifespan_runs_one_delayed_startup_pass(self):
        service = WebRunService(self.root)
        service.startup_inspection_delay_seconds = 0
        inspected = threading.Event()
        service.inspect_conversation_spaces_on_startup = Mock(
            side_effect=lambda _stop: inspected.set() or {'state': 'completed'}
        )
        app = create_app(root=self.root, service=service)
        async def check():
            async with app.router.lifespan_context(app):
                self.assertTrue(await asyncio.to_thread(inspected.wait, 2))
                await asyncio.sleep(0.05)
                service.inspect_conversation_spaces_on_startup.assert_called_once()
        asyncio.run(check())

    def test_memory_queue_wake_interrupts_maintenance_sleep_without_running_agent(self):
        scheduler = MaintenanceScheduler(self.root, poll_interval=30)
        first, second = threading.Event(), threading.Event()
        calls: list[int] = []
        def scan_once():
            calls.append(len(calls) + 1)
            (first if len(calls) == 1 else second).set()
            return {}
        scheduler.scan_once = Mock(side_effect=scan_once)
        scheduler.start()
        try:
            self.assertTrue(first.wait(2))
            scheduler.wake()
            self.assertTrue(second.wait(2))
            self.assertEqual(calls, [1, 2])
        finally:
            scheduler.stop(timeout=2)

    def test_startup_inspection_does_not_create_missing_history_database(self):
        fresh = self.root / 'users' / 'alice' / 'history' / 'history.sqlite3'
        self.assertFalse(fresh.exists())
        result = inspect_stale_web_sessions(self.root, 'alice', now=self.now)
        self.assertEqual(result['inspected'], 0)
        self.assertFalse(fresh.exists())

    def test_startup_queue_respects_disabled_memory_and_bounds_result_samples(self):
        (self.root / 'config/global_config.json').write_text(
            json.dumps({'memory': {'extraction_mode': 'disabled'}}), encoding='utf-8'
        )
        self.seed_data('disabled-memory')
        for index in range(25):
            self.seed(f'empty-sample-{index:02d}')
        result = inspect_stale_web_sessions(self.root, 'alice', now=self.now)
        self.assertEqual(result['queued_memory_count'], 0)
        self.assertIn(
            {'session_id': 'disabled-memory', 'reason': 'memory_extraction_disabled'},
            result['preserved_sessions'],
        )
        self.assertEqual(result['deleted_count'], 25)
        self.assertEqual(len(result['deleted_sessions']), 20)
        self.assertEqual(
            find_record(self.root, 'alice', 'web', 'disabled-memory')['lifecycle'],
            'closed',
        )


if __name__ == '__main__':
    unittest.main()
