"""Deterministic request fairness and trusted scope propagation, offline only."""
from __future__ import annotations

import os
import threading
from unittest.mock import patch

import pytest

from events import RunEvent
import provider.factory as factory
from provider.request_pool import FairRequestPool
from provider.request_scope import current_request_scope, provider_request_scope, UNSCOPED
from run.tools import ToolDefinition, execute_tool
from tests.agents.test_parallel_scheduler import eventually


@pytest.fixture(autouse=True)
def isolate_pool(monkeypatch):
    monkeypatch.setattr(factory, "_provider_semaphore", None)
    monkeypatch.setattr(factory, "_provider_semaphore_limit", 0)


def test_api_requests_rotate_spaces_instead_of_draining_first_space(tmp_path):
    config = {"provider_runtime": {"max_concurrent_requests": 1}}
    names = ("a1", "a2", "b1", "b2", "c1", "c2")
    entered = []
    errors = []
    release = {name: threading.Event() for name in names}

    def request(name):
        try:
            with provider_request_scope(tmp_path, "alice", "web", name[0]):
                with factory.provider_request_slot(config):
                    entered.append(name)
                    assert release[name].wait(8)
        except BaseException as exc:
            errors.append(exc)

    threads = []
    try:
        with provider_request_scope(tmp_path, "alice", "web", "a"):
            with factory.provider_request_slot(config):
                for index, name in enumerate(names):
                    worker = threading.Thread(target=request, args=(name,))
                    threads.append(worker)
                    worker.start()
                    eventually(lambda: factory.provider_semaphore_status()["waiting_estimate"] == index + 1)
                # No extra API pool is created while waiters/requests exist.
                assert factory.get_provider_semaphore(7).limit == 1
        for index, name in enumerate(("b1", "c1", "a1", "b2", "c2", "a2")):
            eventually(lambda: len(entered) == index + 1)
            assert entered[-1] == name
            assert factory.provider_semaphore_status()["active_requests"] == 1
            release[name].set()
    finally:
        for event in release.values():
            event.set()
        for worker in threads:
            worker.join(4)
    assert not errors
    assert all(not worker.is_alive() for worker in threads)
    assert factory.provider_semaphore_status()["waiting_estimate"] == 0
    assert factory.provider_semaphore_status()["active_requests"] == 0
    assert factory.get_provider_semaphore(7).limit == 7


def test_one_space_borrows_all_idle_api_slots_and_late_space_gets_next(tmp_path):
    config = {"provider_runtime": {"max_concurrent_requests": 3}}
    gates = {name: threading.Event() for name in ("a1", "a2", "a3", "a4", "b1")}
    entered, errors = [], []

    def request(name):
        try:
            with provider_request_scope(tmp_path, "alice", "web", name[0]):
                with factory.provider_request_slot(config):
                    entered.append(name)
                    assert gates[name].wait(8)
        except BaseException as exc:
            errors.append(exc)

    threads = []
    try:
        for name in ("a1", "a2", "a3"):
            worker = threading.Thread(target=request, args=(name,))
            threads.append(worker)
            worker.start()
        eventually(lambda: len(entered) == 3)
        assert factory.provider_semaphore_status()["available_requests"] == 0
        for index, name in enumerate(("a4", "b1")):
            worker = threading.Thread(target=request, args=(name,))
            threads.append(worker)
            worker.start()
            eventually(lambda: factory.provider_semaphore_status()["waiting_estimate"] == index + 1)
        gates["a1"].set()
        eventually(lambda: len(entered) == 4)
        assert entered[-1] == "b1"
    finally:
        for event in gates.values():
            event.set()
        for worker in threads:
            worker.join(4)
    assert not errors
    assert factory.provider_semaphore_status()["active_requests"] == 0


def test_cancelled_request_cannot_consume_an_idle_slot(tmp_path):
    cancelled = threading.Event()
    cancelled.set()
    with provider_request_scope(tmp_path, "alice", "web", "a"):
        with pytest.raises(factory.ProviderCongestionError, match="已取消"):
            with factory.provider_request_slot({}, cancel_event=cancelled):
                pytest.fail("Cancelled request entered the API pool")
    assert factory.provider_semaphore_status()["waiting_estimate"] == 0
    assert factory.provider_semaphore_status()["active_requests"] == 0


def test_registered_but_not_yet_waiting_request_prevents_idle_pool_replacement():
    pool = factory.get_provider_semaphore(1)
    ticket = pool.register(("space",))
    assert factory.get_provider_semaphore(4) is pool
    assert pool.wait(ticket, 0) is True
    pool.release(("space",))
    assert factory.get_provider_semaphore(4) is not pool


def test_cancelled_space_waiter_does_not_block_another_space_or_leak_occupancy(tmp_path):
    config = {"provider_runtime": {"max_concurrent_requests": 1}}
    cancelled = threading.Event()
    errors, entered = [], []

    def request(space, cancel=None):
        try:
            with provider_request_scope(tmp_path, "alice", "web", space):
                with factory.provider_request_slot(config, cancel_event=cancel):
                    entered.append(space)
        except factory.ProviderCongestionError as exc:
            errors.append(exc)

    threads = []
    try:
        with provider_request_scope(tmp_path, "alice", "web", "a"):
            with factory.provider_request_slot(config):
                for index, (space, cancel) in enumerate((("b", cancelled), ("c", None))):
                    worker = threading.Thread(target=request, args=(space, cancel))
                    threads.append(worker)
                    worker.start()
                    eventually(lambda: factory.provider_semaphore_status()["waiting_estimate"] == index + 1)
                cancelled.set()
                threads[0].join(2)
                assert not threads[0].is_alive()
                assert len(errors) == 1
                assert factory.provider_semaphore_status()["waiting_estimate"] == 1
        threads[1].join(2)
    finally:
        cancelled.set()
        for worker in threads:
            worker.join(3)
    assert entered == ["c"]
    assert factory.provider_semaphore_status()["active_requests"] == 0
    assert factory.provider_semaphore_status()["waiting_estimate"] == 0


def test_legacy_semaphore_api_and_release_validation():
    pool = FairRequestPool(1)
    assert pool.acquire(blocking=False)
    assert not pool.acquire(blocking=False)
    pool.release()
    with pytest.raises(ValueError, match="重复释放"):
        pool.release()


def test_scope_is_per_installation_user_source_session_not_per_thread_or_window(tmp_path):
    assert current_request_scope() == UNSCOPED
    keys = []
    for root, user, source, session in (
        (tmp_path, "alice", "web", "a"), (tmp_path / ".", "alice", "web", "a"),
        (tmp_path, "alice", "web", "b"), (tmp_path, "alice", "app", "a"),
        (tmp_path, "bob", "web", "a"), (tmp_path / "other", "alice", "web", "a"),
    ):
        with provider_request_scope(root, user, source, session):
            keys.append(current_request_scope())
    assert keys[0] == keys[1]
    assert len(set(keys)) == 5
    assert current_request_scope() == UNSCOPED
    with pytest.raises(RuntimeError):
        with provider_request_scope(tmp_path, "alice", "web", "a"):
            raise RuntimeError("scope restoration")
    assert current_request_scope() == UNSCOPED


@pytest.mark.parametrize("asynchronous", [False, True])
def test_real_tool_worker_binds_and_restores_trusted_scope(tmp_path, asynchronous):
    def sync_entry(*, context):
        return list(current_request_scope())

    async def async_entry(*, context):
        return list(current_request_scope())

    tool = ToolDefinition(
        name="scope_echo", description="Offline scope probe", input_schema={"type": "object"},
        version="1", enabled=True, entrypoint="tool.py:run", source="user", directory=tmp_path,
        _callable=async_entry if asynchronous else sync_entry,
    )
    expected = [os.path.normcase(str(tmp_path.resolve())), "alice", "web", "a"]
    assert execute_tool(tool, {}, timeout=5, context={
        "root": tmp_path, "user": "alice", "source": "web", "session_id": "a",
    }) == expected
    assert current_request_scope() == UNSCOPED


def test_interleaved_main_generators_do_not_leak_space_across_yields(tmp_path):
    from run.conversation.runtime import iter_request_events

    seen = []

    def events(request, **kwargs):
        try:
            for _ in range(2):
                seen.append((request["session_id"], current_request_scope()))
                yield RunEvent(type="text_delta", content="offline")
        finally:
            seen.append((request["session_id"], current_request_scope()))

    with patch("run.conversation.runtime._iter_request_events_unlocked", events):
        first = iter_request_events({"user": "alice", "source": "web", "session_id": "a"}, root=tmp_path)
        second = iter_request_events({"user": "alice", "source": "web", "session_id": "b"}, root=tmp_path)
        try:
            for iterator in (first, second, first, second):
                assert next(iterator).content == "offline"
                assert current_request_scope() == UNSCOPED
        finally:
            first.close()
            second.close()
    assert len(seen) == 6
    assert all(key[-1] == session for session, key in seen)
    assert current_request_scope() == UNSCOPED
