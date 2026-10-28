"""Transient record access must not become a false missing/corrupt job."""
import json
from unittest.mock import Mock

import pytest

from run.tools import background_jobs as jobs


def test_record_read_retries_brief_access_denial_without_changing_data(tmp_path, monkeypatch):
    payload = {"schema_version": jobs.JOB_SCHEMA_VERSION, "status": "running"}
    reader = Mock(side_effect=[PermissionError("sharing violation"), json.dumps(payload)])
    sleep = Mock()
    monkeypatch.setattr(type(tmp_path), "read_text", reader)
    monkeypatch.setattr(jobs.time, "sleep", sleep)
    assert jobs._read_record(tmp_path / "job.json") == payload
    assert reader.call_count == 2
    sleep.assert_called_once_with(jobs._RECORD_READ_RETRY_DELAYS[0])


def test_persistent_access_denial_is_bounded_and_remains_an_error(tmp_path, monkeypatch):
    reader = Mock(side_effect=PermissionError("persistent ACL denial"))
    sleep = Mock()
    monkeypatch.setattr(type(tmp_path), "read_text", reader)
    monkeypatch.setattr(jobs.time, "sleep", sleep)
    with pytest.raises(RuntimeError, match="损坏或不可读") as raised:
        jobs._read_record(tmp_path / "job.json")
    assert isinstance(raised.value.__cause__, PermissionError)
    assert reader.call_count == len(jobs._RECORD_READ_RETRY_DELAYS) + 1
    assert [call.args[0] for call in sleep.call_args_list] == list(jobs._RECORD_READ_RETRY_DELAYS)


@pytest.mark.parametrize("error, expected, message", [
    (FileNotFoundError(), KeyError, "不存在"),
    (OSError("disk failure"), RuntimeError, "损坏或不可读"),
])
def test_missing_and_non_access_io_errors_are_not_retried(
    tmp_path, monkeypatch, error, expected, message,
):
    reader = Mock(side_effect=error)
    sleep = Mock()
    monkeypatch.setattr(type(tmp_path), "read_text", reader)
    monkeypatch.setattr(jobs.time, "sleep", sleep)
    with pytest.raises(expected, match=message):
        jobs._read_record(tmp_path / "job.json")
    assert reader.call_count == 1
    sleep.assert_not_called()


def test_corrupt_json_is_not_retried_or_mislabeled_as_completion(tmp_path, monkeypatch):
    reader = Mock(return_value="{invalid")
    sleep = Mock()
    monkeypatch.setattr(type(tmp_path), "read_text", reader)
    monkeypatch.setattr(jobs.time, "sleep", sleep)
    with pytest.raises(RuntimeError, match="损坏或不可读"):
        jobs._read_record(tmp_path / "job.json")
    assert reader.call_count == 1
    sleep.assert_not_called()
