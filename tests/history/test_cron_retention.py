from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from run.config import ConfigError, cron_history_retention_days, merge_user_config
from run.conversation import session_lock
from run.history import (
    cleanup_cron_history, commit_terminal_windows, empty_window, find_record,
    runtime_window_path, update_run_state, window_exists,
)
from run.history.store import connection, upsert_registry_record
from run.scheduler import MaintenanceScheduler


class CronHistoryRetentionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'config').mkdir()
        (self.root / 'config/global_config.json').write_text('{}', encoding='utf-8')
        for user in ('alice', 'bob'):
            (self.root / 'users' / user).mkdir(parents=True)
        self.now = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)

    def seed(self, session='old', *, source='background:cron:job', days=8, user='alice'):
        archive = self.root / 'users' / user / 'history' / ('conv_' + session)
        window = empty_window(user, source, session)
        window['text']['messages'] = [{'role': 'user', 'content': 'fixture'}, {'role': 'assistant', 'content': 'done'}]
        window['data'].update(rounds=1, memory_status='completed', memory_processed_round=1)
        runtime = copy.deepcopy(window)
        with patch('run.history.commit_ops._now', return_value=(self.now - timedelta(days=days)).isoformat()):
            commit_terminal_windows(archive, window, runtime_window_path(archive), runtime,
                                    active_key=source, summary_cache={'summary': {'text': 'summary'}})
        return archive, window, runtime

    def update(self, session='old', *, user='alice', source='background:cron:job', **fields):
        record = find_record(self.root, user, source, session)
        record.update(fields)
        upsert_registry_record(self.root, user, record)

    def test_default_zero_validation_and_global_only(self):
        self.assertEqual(cron_history_retention_days({}), 7)
        self.assertEqual(cron_history_retention_days({'cron': {'history_retention_days': 0}}), 0)
        merged = merge_user_config({'cron': {'history_retention_days': 14}}, {'cron': {'history_retention_days': 1, 'enabled': False}})
        self.assertEqual(merged['cron']['history_retention_days'], 14)
        self.assertFalse(merged['cron']['enabled'])
        for value in (-1, 3651, 1.5, True, None, '7'):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                cron_history_retention_days({'cron': {'history_retention_days': value}})

    def test_only_expired_cron_and_all_sqlite_partitions_removed(self):
        old, window, runtime = self.seed()
        recent, _, _ = self.seed('recent', days=6)
        bob, _, _ = self.seed(user='bob')
        others = [self.seed('other' + str(i), source=source)[0] for i, source in enumerate(
            ('web', 'message:telegram', 'background:plan:job', 'background:agent:job', 'background:cron:', 'BACKGROUND:CRON:job'))]
        protected_file = self.root / 'users/alice/download/keep.txt'
        protected_file.parent.mkdir(); protected_file.write_text('keep', encoding='utf-8')
        result = cleanup_cron_history(self.root, 'alice', now=self.now)
        self.assertEqual(result['deleted_sessions'], 1)
        self.assertEqual(result['deleted_windows'], 1)
        self.assertFalse(window_exists(old))
        self.assertTrue(all(window_exists(path) for path in [recent, bob, *others]))
        self.assertTrue(protected_file.exists())
        with connection(self.root, 'alice') as database:
            for table in ('history_sessions', 'history_active_sessions', 'history_windows', 'history_messages', 'history_context_summaries'):
                self.assertEqual(database.execute(f'SELECT COUNT(*) FROM {table} WHERE session_id=?', ('old',)).fetchone()[0], 0)
            self.assertEqual(database.execute('SELECT COUNT(*) FROM history_rounds WHERE window_name=?', (old.name,)).fetchone()[0], 0)
            self.assertEqual(database.execute('SELECT COUNT(*) FROM history_deleted_windows WHERE session_id=?', ('old',)).fetchone()[0], 1)
        # Delayed terminal writers must not recreate expired sessions/windows.
        commit_terminal_windows(old, window, runtime_window_path(old), runtime)
        self.assertFalse(window_exists(old))
        self.assertIsNone(find_record(self.root, 'alice', 'background:cron:job', 'old'))
        # A recurring task with a fixed logical session id can start a fresh
        # generation after expiration; the old physical window stays fenced.
        new_archive = self.root / 'users/alice/history/conv_reused'
        new_window = empty_window('alice', 'background:cron:job', 'old')
        update_run_state(self.root, 'alice', 'background:cron:job', 'old', run_state='running', directory=new_archive)
        commit_terminal_windows(new_archive, new_window, runtime_window_path(new_archive), copy.deepcopy(new_window))
        self.assertTrue(window_exists(new_archive))
        self.assertFalse(window_exists(old))

    def test_boundary_legacy_and_metadata_do_not_extend_new_lifetime(self):
        boundary, _, _ = self.seed('boundary', days=7)
        metadata, _, _ = self.seed('metadata')
        self.update('metadata', updated_at=self.now.isoformat(), title='renamed')
        legacy, _, _ = self.seed('legacy')
        record = find_record(self.root, 'alice', 'background:cron:job', 'legacy')
        record.pop('cron_finished_at')
        upsert_registry_record(self.root, 'alice', record)
        unknown, _, _ = self.seed('unknown')
        self.update('unknown', cron_finished_at='invalid-time')
        self.assertEqual(cleanup_cron_history(self.root, 'alice', now=self.now)['deleted_sessions'], 3)
        self.assertFalse(any(window_exists(path) for path in [boundary, metadata, legacy]))
        self.assertTrue(window_exists(unknown))

    def test_busy_sessions_and_new_completion_are_kept(self):
        for session, fields in [('running', {'run_state': 'running'}), ('memory', {'memory_status': 'processing'}), ('summary', {'summary_status': 'processing'})]:
            self.seed(session); self.update(session, **fields)
        archive, window, runtime = self.seed('renewed')
        with patch('run.history.commit_ops._now', return_value=self.now.isoformat()):
            commit_terminal_windows(archive, window, runtime_window_path(archive), runtime)
        self.seed('failed-run'); self.update('failed-run', run_state='running')
        with patch('run.history.index_core._now', return_value=self.now.isoformat()):
            update_run_state(self.root, 'alice', 'background:cron:job', 'failed-run', run_state='idle')
        self.assertEqual(cleanup_cron_history(self.root, 'alice', now=self.now)['deleted_sessions'], 0)
        self.assertEqual(find_record(self.root, 'alice', 'background:cron:job', 'failed-run')['cron_finished_at'], self.now.isoformat())

    def test_active_session_lock_skipped_without_blocking(self):
        archive, _, _ = self.seed()
        ready, release = threading.Event(), threading.Event()
        def hold():
            with session_lock(self.root, 'alice', 'background:cron:job', 'old'):
                ready.set(); release.wait(5)
        thread = threading.Thread(target=hold)
        thread.start()
        try:
            self.assertTrue(ready.wait(2))
            result = cleanup_cron_history(self.root, 'alice', now=self.now)
            self.assertEqual(result['skipped_busy'], 1)
            self.assertTrue(window_exists(archive))
        finally:
            release.set(); thread.join(2)

    def test_zero_and_batch_limit(self):
        for index in range(3):
            self.seed(str(index))
        self.assertEqual(cleanup_cron_history(self.root, 'alice', retention_days=0, now=self.now)['deleted_sessions'], 0)
        self.assertEqual(cleanup_cron_history(self.root, 'alice', limit=2, now=self.now)['deleted_sessions'], 2)
        self.assertEqual(cleanup_cron_history(self.root, 'alice', now=self.now)['deleted_sessions'], 1)

    def test_deletion_rolls_back_on_failure(self):
        from run.history.retention import _delete_session_windows
        archive, _, _ = self.seed()
        def fail(database, source, session):
            _delete_session_windows(database, source, session)
            raise RuntimeError('fixture failure')
        with patch('run.history.retention._delete_session_windows', side_effect=fail), self.assertRaises(RuntimeError):
            cleanup_cron_history(self.root, 'alice', now=self.now)
        self.assertTrue(window_exists(archive))
        self.assertIsNotNone(find_record(self.root, 'alice', 'background:cron:job', 'old'))
        with connection(self.root, 'alice') as database:
            self.assertEqual(database.execute('SELECT COUNT(*) FROM history_deleted_sessions').fetchone()[0], 0)

    def test_maintenance_throttle_and_hot_global_setting(self):
        archive, _, _ = self.seed()
        config_path = self.root / 'config/global_config.json'
        config_path.write_text('{"cron":{"history_retention_days":0}}', encoding='utf-8')
        scheduler = MaintenanceScheduler(self.root, history_summary_enabled=False)
        with patch.object(scheduler, '_recover_pending_memory', return_value={}):
            scheduler.scan_once(now=self.now)
            self.assertTrue(window_exists(archive))
            config_path.write_text('{"cron":{"history_retention_days":7}}', encoding='utf-8')
            scheduler.scan_once(now=self.now + timedelta(minutes=1))
            self.assertTrue(window_exists(archive))
            result = scheduler.scan_once(now=self.now + timedelta(minutes=5))
            self.assertEqual(result['alice']['cron_history_cleanup']['deleted_sessions'], 1)
            self.assertFalse(window_exists(archive))


if __name__ == '__main__':
    unittest.main()
