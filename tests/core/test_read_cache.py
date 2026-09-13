from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from run.infra import BoundedReadCache, cached_read_text, invalidate_source_cache
from run.infra import read_cache
from run.config import read_json_object
from run.scheduler import CronStore, normalize_task
from run.scheduler.runtime_state import update_cron_runtime, clear_cron_runtime


class ReadCacheTests(unittest.TestCase):
    def test_singleflight_and_unrelated_owner_does_not_wait(self):
        cache = BoundedReadCache()
        started, release = threading.Event(), threading.Event()
        def slow():
            started.set()
            self.assertTrue(release.wait(3))
            return {"value": [1]}
        loader = Mock(side_effect=slow)
        with ThreadPoolExecutor(max_workers=4) as pool:
            first = pool.submit(cache.get_or_load, 'alice', loader, owner='alice', ttl=10)
            self.assertTrue(started.wait(2))
            second = pool.submit(cache.get_or_load, 'alice', loader, owner='alice', ttl=10)
            try:
                other = pool.submit(cache.get_or_load, 'bob', lambda: 2, owner='bob', ttl=10)
                self.assertEqual(other.result(timeout=1)[0], 2)
            finally:
                release.set()
            a, b = first.result(), second.result()
        self.assertEqual(loader.call_count, 1)
        a[0]['value'].append(9)
        self.assertEqual(b[0], {'value': [1]})

    def test_invalidation_fences_inflight_without_tombstones(self):
        cache = BoundedReadCache()
        started, release = threading.Event(), threading.Event()
        def slow():
            started.set()
            self.assertTrue(release.wait(3))
            return 'old'
        with ThreadPoolExecutor() as pool:
            future = pool.submit(cache.get_or_load, 'key', slow, owner='u', ttl=10)
            self.assertTrue(started.wait(2))
            cache.clear()
            release.set()
            self.assertEqual(future.result()[0], 'old')
        self.assertEqual(len(cache), 0)
        self.assertEqual(cache.get_or_load('key', lambda: 'new', owner='u', ttl=10), ('new', False))
        for i in range(1000):
            cache.invalidate(lambda key: key == i)
        self.assertEqual(cache.stats()['in_flight'], 0)

    def test_limits_ttl_version_failure_and_copies(self):
        cache = BoundedReadCache(max_entries=3, per_owner=2)
        cache.get_or_load(('root', 'bob'), lambda: [], owner='bob', ttl=5)
        for i in range(8):
            cache.get_or_load(i, lambda: [i], owner='alice', ttl=5)
        self.assertEqual(len(cache), 3)
        self.assertTrue(cache.get_or_load(('root', 'bob'), lambda: None, owner='bob', ttl=5)[1])
        value, _ = cache.get_or_load(7, lambda: [8], owner='alice', ttl=5, version=2)
        value.append(9)
        self.assertEqual(cache.get_or_load(7, lambda: None, owner='alice', ttl=5, version=2)[0], [8])
        with patch.object(read_cache.time, 'monotonic', return_value=10**12):
            self.assertFalse(cache.get_or_load(7, lambda: [], owner='alice', ttl=5, version=2)[1])
        fail = Mock(side_effect=ValueError('failure'))
        for _ in range(2):
            with self.assertRaises(ValueError):
                cache.get_or_load('fail', fail, owner='u', ttl=5)
        self.assertEqual(fail.call_count, 2)
        self.assertEqual(cache.stats()['in_flight'], 0)
        tiny = BoundedReadCache(max_bytes=100)
        tiny.get_or_load('large', lambda: 'x' * 200, owner='u', ttl=5)
        self.assertEqual(len(tiny), 0)
        disabled = BoundedReadCache(max_entries=0)
        self.assertEqual(disabled.get_or_load('x', lambda: 1, owner='u', ttl=5), (1, False))

    def test_source_read_reuse_edit_delete_and_config_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'
            path.write_text('{"nested": [1]}', encoding='utf-8')
            original = Path.read_text
            with patch.object(Path, 'read_text', autospec=True, side_effect=original) as read:
                self.assertEqual(read_json_object(path), {'nested': [1]})
                first = read_json_object(path)
                first['nested'].append(2)
                self.assertEqual(read_json_object(path), {'nested': [1]})
                self.assertEqual(read.call_count, 1)
                old = path.stat()
                replacement = path.with_suffix('.tmp')
                replacement.write_text('{"nested": [9]}', encoding='utf-8')
                os.utime(replacement, ns=(old.st_atime_ns, old.st_mtime_ns))
                os.replace(replacement, path)
                self.assertEqual(read_json_object(path), {'nested': [9]})
                path.unlink()
                with self.assertRaises(FileNotFoundError):
                    cached_read_text(path)
                path.write_text('new', encoding='utf-8')
                self.assertEqual(cached_read_text(path), 'new')
                invalidate_source_cache(path)
                before = read.call_count
                self.assertEqual(cached_read_text(path), 'new')
                self.assertEqual(read.call_count, before + 1)
            invalidate_source_cache(path)

    def test_env_is_not_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '.env'
            path.write_text('EXAMPLE=1', encoding='utf-8')
            with patch.object(Path, 'read_text', autospec=True, side_effect=Path.read_text) as read:
                cached_read_text(path); cached_read_text(path)
                self.assertEqual(read.call_count, 2)

    def test_cron_cache_keeps_only_durable_definitions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = CronStore(root, 'alice')
            task = normalize_task(title='test', prompt='never run', user='alice', type='recurring')
            store.create(task)
            update_cron_runtime(root, 'alice', False, task['task_id'], latest_run_at='', next_run_at=task['next_run_at'], status='running')
            self.addCleanup(clear_cron_runtime, root, 'alice', False, task['task_id'])
            with patch.object(store, '_load', wraps=store._load) as load:
                self.assertEqual(store.list_tasks()[0]['status'], 'running')
                clear_cron_runtime(root, 'alice', False, task['task_id'])
                self.assertEqual(store.list_tasks()[0]['status'], 'enabled')
                self.assertEqual(load.call_count, 1)
            store.update(task['task_id'], lambda item: {**item, 'title': 'changed'})
            self.assertEqual(store.list_tasks()[0]['title'], 'changed')
            store.delete(task['task_id'])
            self.assertEqual(store.list_tasks(), [])


if __name__ == '__main__':
    unittest.main()
