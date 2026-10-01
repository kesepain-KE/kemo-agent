from __future__ import annotations

from run.history.session_payload import session_payload


def test_session_payload_keeps_channel_default_and_normalizes_lifecycle_fields() -> None:
    record = {
        "lifecycle": "closed",
        "run_state": "idle",
        "memory_status": "queued",
        "memory_processed_round": -3,
        "memory_target_round": 4,
        "summary_last_error": {"message": "temporary"},
        "rounds": -1,
    }

    payload = session_payload(record, chain_default="interactive")

    assert payload["state"] == "closed"
    assert payload["chain"] == "interactive"
    assert payload["memory_processed_round"] == 0
    assert payload["memory_target_round"] == 4
    assert payload["rounds"] == 0
    assert payload["summary_last_error"] == {"message": "temporary"}

    record["summary_last_error"]["message"] = "mutated"
    assert payload["summary_last_error"] == {"message": "temporary"}


def test_session_payload_prefers_persisted_chain_over_transport_default() -> None:
    payload = session_payload({"chain": "message"}, chain_default="interactive")

    assert payload["chain"] == "message"


def test_session_payload_tolerates_malformed_legacy_numeric_metadata() -> None:
    payload = session_payload(
        {
            "summary_target_round": "not-a-number",
            "summary_completed_round": None,
            "summary_retry_count": "-2",
            "summary_attempt_count": object(),
            "summary_consecutive_failures": "3",
            "summary_max_attempts": "4",
            "memory_processed_round": "broken",
            "rounds": "9",
        }
    )

    assert payload["summary_target_round"] == 0
    assert payload["summary_completed_round"] == 0
    assert payload["summary_retry_count"] == 0
    assert payload["summary_attempt_count"] == 0
    assert payload["summary_consecutive_failures"] == 3
    assert payload["summary_max_attempts"] == 4
    assert payload["memory_processed_round"] == 0
    assert payload["rounds"] == 9
