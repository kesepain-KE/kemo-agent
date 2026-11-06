from __future__ import annotations

from run.history.session_payload import session_payload


def test_session_payload_keeps_channel_default_and_normalizes_lifecycle_fields() -> None:
    record = {
        "lifecycle": "closed",
        "run_state": "idle",
        "memory_status": "queued",
        "memory_processed_seq": 3,
        "memory_target_seq": 4,
        "summary_last_error": {"message": "temporary"},
            "summary_attempt_count": 2,
            "summary_max_attempts": 5,
            "title_source": "summary",
            "last_seq": 9,
            "integration_count": 2,
    }

    payload = session_payload(record, chain_default="interactive")

    assert payload["state"] == "closed"
    assert payload["chain"] == "interactive"
    assert payload["memory_processed_seq"] == 3
    assert payload["memory_target_seq"] == 4
    assert payload["last_seq"] == 9
    assert payload["integration_count"] == 2
    assert payload["summary_last_error"] == {"message": "temporary"}
    assert payload["summary_attempt_count"] == 2
    assert payload["summary_max_attempts"] == 5
    assert payload["title"] == ""
    assert payload["title_source"] == "summary"

    record["summary_last_error"]["message"] = "mutated"
    assert payload["summary_last_error"] == {"message": "temporary"}


def test_session_payload_prefers_persisted_chain_over_transport_default() -> None:
    payload = session_payload(
        {"chain": "message", "lifecycle": "open", "run_state": "idle"},
        chain_default="interactive",
    )

    assert payload["chain"] == "message"


def test_session_payload_exposes_only_cardstream_metadata() -> None:
    payload = session_payload(
        {
            "lifecycle": "closed",
            "run_state": "idle",
            "summary_target_seq": 9,
            "summary_completed_seq": 6,
            "summary_attempt_count": 3,
            "summary_max_attempts": 5,
            "memory_processed_seq": 3,
            "last_seq": 9,
            "active_tokens": 123,
            "rounds": 100,
            "memory_processed_round": 100,
            "summary_target_round": 100,
            "summary_claim_id": "private-claim",
        }
    )

    assert payload["summary_target_seq"] == 9
    assert payload["summary_completed_seq"] == 6
    assert payload["summary_attempt_count"] == 3
    assert payload["summary_max_attempts"] == 5
    assert payload["memory_processed_seq"] == 3
    assert payload["last_seq"] == 9
    assert payload["active_tokens"] == 123
    assert payload["engine"] == "cardstream"
    assert not {"rounds", "summary_target_round", "memory_processed_round",
                "summary_claim_id"} & payload.keys()
