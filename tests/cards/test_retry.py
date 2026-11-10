from tests.type_assertions import require_present
import sqlite3
import threading

import pytest

from provider.retry import RetryCancelled, RetryManager, transient
from provider.adapters.reliability import KemoNetworkRetryPolicy
from provider.schema import ProviderError


def test_default_means_five_additional_retries_not_five_total_attempts():
    attempts = []

    def fail():
        attempts.append(True)
        raise TimeoutError("temporary")

    with pytest.raises(TimeoutError):
        RetryManager(base_delay=0).call(fail, replay_safe=True)
    assert len(attempts) == 6


def test_unknown_side_effect_execution_is_not_blindly_retried():
    attempts = []

    def unknown():
        attempts.append(True)
        raise TimeoutError("may already have executed")

    with pytest.raises(TimeoutError):
        RetryManager(base_delay=0).call(unknown, replay_safe=False)
    assert len(attempts) == 1


@pytest.mark.parametrize("status,expected", [(400, False), (401, False), (403, False),
                                          (408, True), (429, True), (500, True)])
def test_retry_classification_cannot_be_expanded_by_provider_claims(status, expected):
    assert transient(ProviderError("failure", status_code=status, retryable=True)) is expected


def test_busy_is_transient_but_corruption_and_internal_errors_are_not():
    assert transient(sqlite3.OperationalError("database is locked"))
    assert not transient(sqlite3.OperationalError("database disk image is malformed"))
    assert not transient(TypeError("internal bug"))


def test_kemo_transport_and_runtime_retries_do_not_multiply():
    from run.conversation.provider_exchange import ProviderResponseError
    from events import error_event

    calls = []
    transport = KemoNetworkRetryPolicy(base_seconds=0, cap_seconds=0)

    def network():
        calls.append(True)
        raise ProviderError("disconnected", category="connection_error", retryable=True)

    def exchange():
        try:
            return transport.run(network, cancel_event=None)
        except ProviderError as error:
            raise ProviderResponseError(require_present(error_event(error).error)) from error

    with pytest.raises(ProviderError) as caught:
        RetryManager(base_delay=0).call(exchange, replay_safe=True)
    assert len(calls) == 6
    assert caught.value.retry_budget_exhausted
    assert caught.value.attempt_count == 6


def test_cancellation_and_retry_limit_validation():
    event = threading.Event()
    event.set()
    called = []
    with pytest.raises(RetryCancelled):
        RetryManager().call(lambda: called.append(True), replay_safe=True, cancel_event=event)
    assert not called
    for invalid in (-1, 6, True):
        with pytest.raises(ValueError):
            RetryManager(max_retries=invalid)


@pytest.mark.parametrize("retry_after_ms,expected", [(30_000, 30), (999_999, 120)])
def test_retry_after_is_not_shortened_to_the_local_backoff_cap(monkeypatch, retry_after_ms, expected):
    attempts = []
    waits = []

    def operation():
        attempts.append(True)
        if len(attempts) == 1:
            raise ProviderError("upstream cooling down", category="upstream_error",
                                retryable=True, retry_after_ms=retry_after_ms)
        return "recovered"

    monkeypatch.setattr("provider.retry.time.sleep", waits.append)
    result = RetryManager(max_retries=1).call(operation, replay_safe=True)
    assert result == "recovered"
    assert waits == [expected]
    assert len(attempts) == 2
    assert RetryManager().delay(20) <= 8


def test_retry_after_wait_stops_at_the_request_deadline(monkeypatch):
    clock = [100.0]
    waits = []
    attempts = []

    def wait(seconds):
        waits.append(seconds)
        clock[0] += seconds

    def fail():
        attempts.append(True)
        raise ProviderError("upstream cooling down", category="upstream_error",
                            retryable=True, retry_after_ms=30_000)

    monkeypatch.setattr("provider.retry.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("provider.retry.time.sleep", wait)
    with pytest.raises(TimeoutError, match="deadline exhausted"):
        RetryManager().call(fail, replay_safe=True, deadline=105)
    assert waits == [5]
    assert len(attempts) == 1


def test_retry_after_wait_remains_cancellable():
    event = threading.Event()
    attempts = []

    def fail():
        attempts.append(True)
        event.set()
        raise ProviderError("upstream cooling down", category="upstream_error",
                            retryable=True, retry_after_ms=30_000)

    with pytest.raises(RetryCancelled):
        RetryManager().call(fail, replay_safe=True, cancel_event=event)
    assert len(attempts) == 1


@pytest.mark.parametrize("retry_after_ms,expected", [(30_000, 30), (999_999, 120)])
def test_kemo_http_retry_after_is_not_shortened_to_the_local_cap(monkeypatch, retry_after_ms, expected):
    attempts = []
    waits = []

    def operation():
        attempts.append(True)
        if len(attempts) == 1:
            raise ProviderError("gateway unavailable", category="gateway_error", status_code=503,
                                retryable=True, retry_after_ms=retry_after_ms)
        return "recovered"

    monkeypatch.setattr("provider.adapters.reliability.time.sleep", waits.append)
    policy = KemoNetworkRetryPolicy(max_attempts=2)
    assert policy.run(operation, cancel_event=None) == "recovered"
    assert len(attempts) == 2
    assert waits == [expected]
