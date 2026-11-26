"""Commit and control a handoff while the original Shell thread owns the process."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shlex
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from plugins.shell import tool as shell_plugin
from plugins.shell.tool import run as run_shell
from provider.protocol.models import ToolResultItem
from run.cards import CardConflictError, CardStore, IntegrationStore, protocol_items
from run.infra.process_execution import IsolatedProcessCall
from run.infra.process_identity import process_identity_matches, process_snapshot
import run.tools as tools
from run.tools import background_jobs as jobs
from run.tools.foreground_handoff import ForegroundHandoff, ShellHandoffUncertain
from web.errors import NotFoundError
from web.services.workspace_jobs import job_detail, list_jobs


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_COMMAND = """
import json
import os
from pathlib import Path
import subprocess
import sys
import time

root, tree, code, large = Path(sys.argv[1]), sys.argv[2] == "True", int(sys.argv[3]), sys.argv[4] == "True"
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]) if tree else None
with (root / "starts.txt").open("a") as handle:
    handle.write(f"{os.getpid()}\\n")
temp = root / "pids.tmp"
temp.write_text(json.dumps([os.getpid(), *([child.pid] if child else [])]))
temp.replace(root / "pids.json")
while not (root / "release").exists():
    time.sleep(.02)
if child:
    child.terminate()
    child.wait()
print(f"finished {os.getpid()}")
if large:
    print(('中文😀' + chr(34) + chr(92) + chr(1)) * 15000)
raise SystemExit(code)
"""


def _wait_until(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "Shell 未在预期时间到达该状态"
        time.sleep(.02)


def _alive(snapshot):
    return process_identity_matches(
        process_snapshot(snapshot["pid"]), process_started_at=snapshot["process_started_at"],
    ) is True


@pytest.fixture
def shell(tmp_path):
    (tmp_path / "users" / "alice").mkdir(parents=True)
    script = tmp_path / "command.py"
    script.write_text(_COMMAND, encoding="utf-8")
    tool = tools.discover_tools(PROJECT_ROOT, "alice").get("shell")
    store = CardStore(tmp_path, "alice")
    context = {"root": str(tmp_path), "user": "alice", "source": "web", "session_id": "handoff"}
    owners = []

    def start(*, tree=False, code=0, large=False):
        integration = IntegrationStore(store, "web", "handoff", "old-run")
        integration.start()
        runtime = tools.ToolRuntime(integration)
        argv = [sys.executable, str(script), str(tmp_path), str(tree), str(code), str(large)]
        if os.name == "nt":
            command = "& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in argv)
            command += "; exit $LASTEXITCODE"
            shell_type = "powershell"
        else:
            command, shell_type = shlex.join(argv) + "; exit $?", "sh"
        call = SimpleNamespace(name="shell", call_id="callid_handoff",
                               arguments={"command": command, "shell_type": shell_type})
        execution_id = runtime.start(call, tool, context=context, timeout=60)
        owner = runtime.shell_execution(execution_id)
        assert owner is not None
        owners.append(owner)
        _wait_until(lambda: owner._command_started is not None)
        with owner._changed:
            interpreter = owner._command_started["pid"]
        path = tmp_path / "pids.json"
        _wait_until(path.is_file)
        pids = json.loads(path.read_bytes())
        processes = [process_snapshot(pid) for pid in (interpreter, *pids)]
        assert all(record["identity_available"] and _alive(record) for record in processes)
        return SimpleNamespace(
            runtime=runtime, owner=owner, integration=integration, execution_id=execution_id,
            context=context, tool=tool, processes=processes, pid=pids[0],
            root=tmp_path, release=tmp_path / "release",
            backend=SimpleNamespace(root=tmp_path, require_user=lambda user: user),
        )

    yield start
    for owner in owners:
        owner.close()


def _records(case):
    return sorted((case.root / "users/alice/runtime/background_jobs").glob("job_*.json"))


def _handoff(case):
    receipt = case.runtime.handoff_shell(case.execution_id)
    assert receipt["ok"] is True and receipt["status"] == "handed_off"
    assert "exit_code" not in receipt
    return receipt


def _status(case, job_id, **kwargs):
    return run_shell(action="status", job_id=job_id, context=case.context, **kwargs)


def _cards(case):
    return [card for card in case.integration.store.raw_page("web", "handoff")
            if card.raw_kind == "tool_result"]


def _result(case):
    card, = _cards(case)
    event, = [event for event in case.runtime.events() if event.type == "tool_call_result"]
    assert card.seq == event.metadata["card_seq"]
    return card


def _foreground_result(case):
    case.release.touch()
    assert case.runtime.wait_for_initial_result(case.execution_id, timeout=15) == "completed"
    value = _result(case).raw_payload["result"]["result"]
    assert value["ok"] and value["exit_code"] == 0 and value["output"] == f"finished {case.pid}"


def _assert_invisible(case, job_id):
    for fn in (jobs.read_background_job, jobs.cancel_background_job, jobs.reconcile_background_job):
        with pytest.raises(KeyError, match="后台作业不存在"):
            fn(case.root, "alice", job_id)
    assert list_jobs(case.backend, "alice")["jobs"] == []
    with pytest.raises(NotFoundError):
        job_detail(case.backend, "alice", job_id)


def test_handoff_then_immediate_exit_keeps_one_receipt_and_bounded_result(shell, monkeypatch):
    case = shell(code=7, large=True)
    publish = case.runtime._publish_card

    def release_on_commit(card):
        if card.raw_payload["execution"]["status"] == "handed_off":
            case.release.touch()
        publish(card)

    monkeypatch.setattr(case.runtime, "_publish_card", release_on_commit)
    monkeypatch.setattr(jobs, "MAX_BACKGROUND_JOB_STORAGE_BYTES", 1024 * 1024)
    # Both requests compete for the same execution and must return the same receipt.
    with ThreadPoolExecutor(2) as pool:
        first, second = (pool.submit(_handoff, case) for _ in range(2))
        receipt = first.result(15)
        assert second.result(15) == receipt
    assert case.owner.subscribe().wait_reply(15)["exit_code"] == 7
    job_id = receipt["job_id"]
    final = _status(case, job_id)
    assert final["status"] == "failed" and final["exit_code"] == 7 and final["error"]["code"] == "shell_exit"
    assert final["pid"] == case.processes[0]["pid"]
    assert final["output"].startswith(f"finished {case.pid}")
    assert final["output_truncated"] is True
    assert len(json.dumps(final["output"], ensure_ascii=False).encode("utf-8")) <= 16 * 1024
    assert (case.root / "starts.txt").read_text().splitlines() == [str(case.pid)]
    assert all(not _alive(record) for record in case.processes)
    path, = _records(case)
    assert sorted(item.name for item in path.parent.iterdir()) == [path.name]
    assert path.stat().st_size < jobs.MAX_BACKGROUND_JOB_RECORD_BYTES
    assert job_detail(case.backend, "alice", job_id)["streams"] == {}
    assert _result(case).raw_payload["result"] == receipt
    _wait_until(lambda: case.runtime.shell_execution(case.execution_id) is None)
    assert _handoff(case) == receipt  # Durable idempotency even after the owner exits.


def test_quota_rejects_without_record_or_stopping_the_foreground(shell):
    case = shell()
    assert _records(case) == []
    for _ in range(jobs.MAX_ACTIVE_BACKGROUND_JOBS_PER_USER):
        jobs.prepare_background_job(case.root, "alice", source="web", session_id="handoff",
                                    working_dir=str(case.root), shell_type="auto", command_digest="test")
    before = _records(case)
    with pytest.raises(ValueError, match="活动作业数量"):
        case.runtime.handoff_shell(case.execution_id)
    assert len(before) == jobs.MAX_ACTIVE_BACKGROUND_JOBS_PER_USER
    assert _records(case) == before and all(_alive(record) for record in case.processes)
    _foreground_result(case)


def test_rejected_commit_rolls_back_an_invisible_preparation(shell, monkeypatch):
    case = shell()
    tool_result = case.integration.tool_result

    def reject(*args, **kwargs):
        if kwargs["status"] == "handed_off":
            path, = _records(case)
            raw = json.loads(path.read_bytes())
            assert raw["owner_kind"] == "foreground_handoff"
            _assert_invisible(case, path.stem)
            raise OSError("写入前失败")
        return tool_result(*args, **kwargs)

    monkeypatch.setattr(case.integration, "tool_result", reject)
    with pytest.raises(OSError, match="写入前失败"):
        case.runtime.handoff_shell(case.execution_id)
    assert _records(case) == [] and all(_alive(record) for record in case.processes)
    _foreground_result(case)


def test_terminal_claim_wins_during_preparation(shell, monkeypatch):
    case = shell()
    prepared, release_prepare = threading.Event(), threading.Event()
    cleaning, release_cleanup = threading.Event(), threading.Event()
    prepare = ForegroundHandoff.prepare
    wait = IsolatedProcessCall.wait

    def pause(*args, **kwargs):
        job = prepare(*args, **kwargs)
        prepared.set()
        assert release_prepare.wait(15)
        return job

    def pause_cleanup(call, timeout):
        if timeout == 1 and not cleaning.is_set():
            cleaning.set()
            assert release_cleanup.wait(15)
        return wait(call, timeout)

    monkeypatch.setattr(ForegroundHandoff, "prepare", pause)
    monkeypatch.setattr(IsolatedProcessCall, "wait", pause_cleanup)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(case.runtime.handoff_shell, case.execution_id)
        try:
            assert prepared.wait(15)
            path, = _records(case)
            _assert_invisible(case, path.stem)
            case.release.touch()
            assert cleaning.wait(15)
            with case.owner._changed:
                assert case.owner._terminal_claimed and case.owner._reply is None
            release_prepare.set()
            assert future.result(15) is None
            assert _records(case) == []
        finally:
            release_prepare.set()
            release_cleanup.set()
    assert case.runtime.wait_for_initial_result(case.execution_id, timeout=15) == "completed"
    assert _result(case).raw_payload["result"]["result"]["output"] == f"finished {case.pid}"


@pytest.mark.parametrize("verification_fails", [False, True])
def test_postcommit_error_is_routed_by_the_original_receipt(shell, monkeypatch, verification_fails):
    case = shell()
    db = case.integration.store.db

    @contextmanager
    def report_error_after_commit(*, write=False):
        with db(write=write) as connection:
            yield connection
        if write:
            raise OSError("提交已完成，但调用方收到错误")

    with monkeypatch.context() as patch:
        patch.setattr(case.integration.store, "db", report_error_after_commit)
        if verification_fails:
            def no_receipt_yet(job):
                raise OSError("回执暂时不可读")
            patch.setattr(ForegroundHandoff, "receipt", no_receipt_yet)
            with pytest.raises(ShellHandoffUncertain, match="无法核对"):
                case.runtime.handoff_shell(case.execution_id)
            path, = _records(case)
            job_id = path.stem
        else:
            job_id = _handoff(case)["job_id"]
    assert _status(case, job_id)["status"] == "running"
    case.release.touch()  # The original owner handles verification and settlement.
    assert case.owner.subscribe().wait_reply(15)["ok"]
    final = _status(case, job_id)
    assert final["status"] == "completed" and final["output"] == f"finished {case.pid}"
    assert _result(case).raw_payload["result"]["job_id"] == job_id


def test_closed_integration_rejects_commit_without_leaving_a_job(shell):
    case = shell()
    case.integration.finish("partial")
    with pytest.raises(CardConflictError, match="拒绝 Shell 移交"):
        case.runtime.handoff_shell(case.execution_id)
    assert _records(case) == [] and all(_alive(record) for record in case.processes)
    _foreground_result(case)


def test_reconcile_never_owns_the_process_and_receipt_releases_barrier(shell, monkeypatch):
    case = shell()
    with case.owner._changed:
        deadline = case.owner._command_started["deadline_at"]
    receipt = _handoff(case)
    job_id = receipt["job_id"]
    running = _status(case, job_id)
    assert running["status"] == "running" and "output" not in running
    assert running["deadline_at"] - time.time() == pytest.approx(deadline - time.monotonic(), abs=.3)
    assert running["completion_mode"] == "detached" and running["notification_enabled"] is False
    for field in ("execution_id", "job_id", "cardstream_generation", "session_id", "source"):
        raw = jobs.read_background_job(case.root, "alice", job_id)
        assert not jobs.background_job_visible(case.root, "alice", {**raw, field: raw[field] + "x"})
    for scope in ({"session_id": "other"}, {"source": "cli"}):
        for action in ("status", "cancel"):
            with pytest.raises(KeyError, match="后台作业不存在"):
                run_shell(action=action, job_id=job_id, context={**case.context, **scope})
    assert not _status(case, job_id)["cancel_requested"]

    jobs.update_background_job(case.root, "alice", job_id,
                               lambda record: {**record, "deadline_at": time.time() - 1})

    def no_pid_control(*args, **kwargs):
        pytest.fail("移交作业的对账不能检查或终止通知 PID")

    monkeypatch.setattr(jobs, "_identity_alive", no_pid_control)
    monkeypatch.setattr(jobs, "terminate_pid_tree", no_pid_control)
    assert jobs.reconcile_background_job(case.root, "alice", job_id)["status"] == "running"
    assert all(_alive(record) for record in case.processes)
    assert case.integration.live_executions() == []
    card = _result(case)
    snapshot = case.integration.store.snapshot("web", "handoff")
    result, = [item for item in protocol_items(snapshot.cards, native_items=True)
               if isinstance(item, ToolResultItem)]
    assert result.is_error is False and result.content[0].data == receipt
    with pytest.raises(CardConflictError, match="拒绝完成整合"):
        case.integration.finish("completed")
    case.integration.request_finished(accepted_seq=card.seq)
    case.integration.finish("completed")
    IntegrationStore(case.integration.store, "web", "handoff", "next-run").start()

    case.runtime.cancel_event.set()
    still_running = _status(case, job_id, wait=True, timeout=1)
    assert still_running["wait_outcome"] == "timeout" and still_running["status"] == "running"
    assert all(_alive(record) for record in case.processes)

    reconcile = shell_plugin.reconcile_background_job
    read_running = threading.Event()

    def observe_running(*args, **kwargs):
        current = reconcile(*args, **kwargs)
        if current["status"] == "running":
            read_running.set()
        return current

    monkeypatch.setattr(shell_plugin, "reconcile_background_job", observe_running)
    with ThreadPoolExecutor(1) as pool:
        resumed = pool.submit(_status, case, job_id, wait=True, timeout=3)
        assert read_running.wait(5)
        case.release.touch()
        final = resumed.result(8)
    assert final["wait_outcome"] == "finished" and final["exit_code"] == 0
    assert len(_cards(case)) == 1 and not case.runtime.events()


def test_external_cancel_is_consumed_by_the_original_execution_owner(shell, monkeypatch):
    case = shell(tree=True)
    job_id = _handoff(case)["job_id"]
    if sys.platform == "linux":
        group_records = [
            (p["pid"], int(p["process_started_at"]), os.getpgid(p["pid"])) for p in case.processes
        ]

    def no_pid_control(*args, **kwargs):
        pytest.fail("移交作业的取消只能写请求")

    monkeypatch.setattr(jobs, "_identity_alive", no_pid_control)
    monkeypatch.setattr(jobs, "terminate_pid_tree", no_pid_control)
    request = run_shell(action="cancel", job_id=f" {job_id} ", context=case.context)
    assert request["ok"] and request["cancel_requested"] and request["job_id"] == job_id
    result = _status(case, job_id, wait=True, timeout=8)
    assert result["wait_outcome"] == "finished" and result["status"] == "cancelled"
    _wait_until(lambda: all(not _alive(record) for record in case.processes))
    if sys.platform == "linux":
        from tests.plugins.test_shell_group_ownership import _assert_group_exited
        _assert_group_exited(group_records)
    assert _result(case).raw_payload["result"]["status"] == "handed_off"
