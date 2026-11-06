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
            raise ProviderResponseError(error_event(error).error) from error

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
