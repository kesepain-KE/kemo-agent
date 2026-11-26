"""Drive Web guidance, the real conversation barrier and isolated Shell together."""

from contextlib import contextmanager
import os
from pathlib import Path
import shlex
import shutil
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from events import RunEvent, TERMINAL_EVENTS
from plugins.shell.tool import run as run_shell
from provider.protocol.models import ToolCallItem, ToolResultItem
from provider.schema import ChatResponse, ToolCall
from run.agents import TaskCoordinator
from run.engine import iter_request_events
from run.infra.process_identity import process_identity_matches, process_snapshot
from run.tools import ToolRuntime, background_jobs
from run.tools.foreground_handoff import ForegroundHandoff
from run.tools.runtime import _ShellExecution
from tests.core.test_preview_runtime_safety import tool
from tests.media_input_support import MediaProvider, make_root
from web.service import WebRunService


STARTUP = 35
SESSION = "guidance-handoff"
RUN_ID = "run_guidance_handoff"


def eventually(predicate, timeout=STARTUP):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "等待引导或工具状态超时"
        time.sleep(.02)


def alive(record):
    return process_identity_matches(
        process_snapshot(record["pid"]), process_started_at=record["process_started_at"],
    ) is True


@pytest.fixture
def chat(tmp_path, monkeypatch):
    make_root(tmp_path)
    shutil.copytree(Path(__file__).resolve().parents[2] / "plugins/shell", tmp_path / "plugins/shell",
                    ignore=shutil.ignore_patterns("__pycache__"))
    script = tmp_path / "command.py"
    script.write_text(
        "import os, sys, time\n"
        "from pathlib import Path\n"
        "label = sys.argv[1]\n"
        "with Path(f'{label}.pid').open('a') as file:\n"
        "    file.write(f'{os.getpid()}\\n')\n"
        "while not Path(f'{label}.release').exists():\n"
        "    time.sleep(.02)\n"
        "print(f'finished {label} {os.getpid()}')\n",
        encoding="utf-8",
    )
    labels = []
    waiting = threading.Event()
    wakes, drains, attempts, events, errors = [], [], [], [], []
    cancel = threading.Event()
    context = {"root": str(tmp_path), "user": "alice", "source": "web", "session_id": SESSION}
    observe_attempt = None
    original_wait = TaskCoordinator.wait
    original_handoff = ToolRuntime.handoff_shell
    worker = None

    def wait(coordinator, *args, wake, **kwargs):
        def observed_wake():
            result = wake()
            wakes.append(result)
            if not result and not kwargs["allow_notifications"]:
                waiting.set()  # Already in the blocking wait, not the initial drain.
            return result
        return original_wait(coordinator, *args, wake=observed_wake, **kwargs)

    def handoff(runtime, execution_id):
        attempts.append(execution_id)
        if observe_attempt is not None:
            observe_attempt(runtime, execution_id)
        return original_handoff(runtime, execution_id)

    monkeypatch.setattr(TaskCoordinator, "wait", wait)
    monkeypatch.setattr(ToolRuntime, "handoff_shell", handoff)

    def shell(label):
        labels.append(label)
        argv = [sys.executable, str(script), label]
        if os.name == "nt":
            command = "& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in argv)
            shell_type = "powershell"
        else:
            command, shell_type = shlex.join(argv), "sh"
        return ToolCall(label, "shell", {"command": command, "shell_type": shell_type, "timeout": 60})

    def start(calls, *, before_handoff=None, before_first_response=None):
        nonlocal worker, observe_attempt
        observe_attempt = before_handoff

        class Provider(MediaProvider):
            def create(self, request):
                if not self.requests and before_first_response is not None:
                    before_first_response()
                return super().create(request)

        provider = Provider([ChatResponse(text="", tool_calls=calls)])

        def source(request, **kwargs):
            mailbox = request["_guidance_queue"]
            original_drain = mailbox.drain

            def drain():
                values = original_drain()
                drains.append(values)
                return values

            mailbox.drain = drain
            yield from iter_request_events({**request, "stream": False, "_auto_retry": False},
                                           provider_factory=lambda _: provider, **kwargs)

        service = WebRunService(tmp_path, event_source=source)
        stream = service.stream_chat("alice", SESSION, "start", cancel_event=cancel, run_id=RUN_ID)

        def collect():
            try:
                events.extend(stream)
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=collect, daemon=True)
        worker = thread
        thread.start()

        def offer(mode="interrupt"):
            receipt = service.submit_guidance(
                "alice", RUN_ID, "go on with my revision", session_id=SESSION,
                guidance_id="guidance-test", mode=mode,
            )
            assert receipt["status"] == "accepted_current_run"
            assert "job_id" not in receipt

        def done():
            thread.join(STARTUP)
            assert not thread.is_alive() and not errors
            assert events[-1].type == "done", events[-1].error
            return events[-1]

        return SimpleNamespace(service=service, provider=provider, offer=offer, done=done,
                               notices=lambda: [event for event in events if event.type == "guidance_handoff"])

    def process(label):
        path = tmp_path / f"{label}.pid"
        eventually(lambda: path.is_file() and path.stat().st_size > 0)
        pid, = path.read_text().splitlines()  # A second start would append a second PID.
        record = process_snapshot(int(pid))
        assert record["identity_available"] and alive(record)
        return record

    def release(label):
        (tmp_path / f"{label}.release").touch()

    def status(job_id):
        return run_shell(action="status", job_id=job_id, context=context)

    yield SimpleNamespace(root=tmp_path, shell=shell, start=start, waiting=waiting,
                          wakes=wakes, drains=drains, attempts=attempts, events=events,
                          release=release, process=process, status=status, cancel=cancel)
    for label in [*labels, "file"]:
        release(label)
    if worker is not None:
        cancel.set()
        worker.join(STARTUP)
        assert not worker.is_alive()


def applied(chat):
    return [event for event in chat.events if event.type == "guidance_applied"]


def test_ctrl_wakes_a_blocked_run_and_continues_with_the_paired_receipt(chat):
    run = chat.start([chat.shell("live")])
    assert chat.waiting.wait(STARTUP)
    child = chat.process("live")
    run.offer()
    terminal = run.done()  # The real subprocess has not been released yet.
    assert terminal.metadata["status"] == "completed" and alive(child)
    notice, = run.notices()
    assert notice.metadata["guidance_id"] == "guidance-test" and notice.metadata["status"] == "handed_off"
    assert RunEvent.from_dict(notice.to_dict()).type not in TERMINAL_EVENTS
    job_id = notice.metadata["job_id"]
    assert chat.status(job_id)["status"] == "running"
    receipt, = [event for event in chat.events if event.metadata.get("status") == "handed_off"
                and event.type == "tool_call_result"]
    assert receipt.result["job_id"] == job_id and receipt.metadata["execution_live"] is False
    ack, = applied(chat)
    assert chat.events.index(receipt) < chat.events.index(notice) < chat.events.index(ack)
    assert len(run.provider.requests) == 2
    followup = run.provider.requests[1]
    calls = [item.call_id for item in followup.input if isinstance(item, ToolCallItem)]
    result, = [item for item in followup.input if isinstance(item, ToolResultItem)]
    assert calls == [result.call_id] and result.is_error is False
    assert job_id in result.model_dump_json() and "go on with my revision" in followup.model_dump_json()
    cards = run.service.history("alice", SESSION)["cards"]
    assert [card["tool"]["status"] for card in cards if card.get("tool")][-1] == "handed_off"
    chat.release("live")
    eventually(lambda: chat.status(job_id)["status"] == "completed")
    assert chat.status(job_id)["output"] == f"finished live {child['pid']}"
    assert not alive(child) and len(chat.attempts) == 1


def test_ctrl_before_confirmation_waits_without_resubmitting(chat, monkeypatch):
    entered, confirm = threading.Event(), threading.Event()
    original = _ShellExecution.command_started

    def delay_notice(owner, payload):
        entered.set()
        assert confirm.wait(STARTUP)
        original(owner, payload)

    monkeypatch.setattr(_ShellExecution, "command_started", delay_notice)
    try:
        run = chat.start([chat.shell("early")])
        assert chat.waiting.wait(STARTUP) and entered.wait(STARTUP)
        run.offer()
        eventually(lambda: any(chat.drains))
        assert not chat.attempts and not run.notices() and len(run.provider.requests) == 1
    finally:
        confirm.set()
    assert run.done().metadata["status"] == "completed"
    notice, = run.notices()
    assert notice.metadata["status"] == "handed_off" and len(chat.attempts) == 1
    job_id = notice.metadata["job_id"]
    chat.release("early")
    eventually(lambda: chat.status(job_id)["status"] == "completed")


def test_ctrl_during_provider_inference_does_not_target_a_later_batch(chat):
    entered, reply = threading.Event(), threading.Event()

    def before_first_response():
        entered.set()
        assert reply.wait(STARTUP)

    try:
        run = chat.start([chat.shell("later")], before_first_response=before_first_response)
        assert entered.wait(STARTUP)
        run.offer()
    finally:
        reply.set()
    child = chat.process("later")
    assert chat.waiting.wait(STARTUP)
    assert not chat.attempts and not run.notices() and not applied(chat)
    chat.release("later")
    assert run.done().metadata["status"] == "completed"
    assert not alive(child) and len(applied(chat)) == 1


def test_ctrl_during_dispatch_leaves_the_later_call_in_the_same_batch_foreground(chat, monkeypatch):
    first, second = chat.shell("first"), chat.shell("second")
    dispatched, resume = threading.Event(), threading.Event()
    start = ToolRuntime.start

    def pause_after_first(runtime, call, *args, **kwargs):
        execution_id = start(runtime, call, *args, **kwargs)
        if call.arguments == first.arguments:
            dispatched.set()
            assert resume.wait(STARTUP)
        return execution_id

    monkeypatch.setattr(ToolRuntime, "start", pause_after_first)
    try:
        run = chat.start([first, second])
        assert dispatched.wait(STARTUP)
        child = chat.process("first")
        run.offer()
    finally:
        resume.set()
    later = chat.process("second")
    eventually(run.notices)
    notice, = run.notices()
    assert notice.metadata["status"] == "handed_off"
    assert len(chat.attempts) == 1 and len(run.provider.requests) == 1 and alive(later)
    chat.release("second")
    assert run.done().metadata["status"] == "completed"
    assert alive(child) and not alive(later) and len(applied(chat)) == 1
    chat.release("first")
    eventually(lambda: chat.status(notice.metadata["job_id"])["status"] == "completed")


@pytest.mark.parametrize(("failure", "stop_run"), [
    ("quota_full", False), ("registration_failed", False), ("quota_full", True),
])
def test_failed_handoff_keeps_the_foreground_and_the_guidance(chat, monkeypatch, failure, stop_run):
    if failure == "quota_full":
        monkeypatch.setattr(background_jobs, "MAX_ACTIVE_BACKGROUND_JOBS_PER_USER", 0)
    else:
        def fail(*args, **kwargs):
            raise OSError("test: unable to register the job")

        monkeypatch.setattr("run.tools.foreground_handoff.ForegroundHandoff.prepare", fail)
    run = chat.start([chat.shell("blocked")])
    assert chat.waiting.wait(STARTUP)
    child = chat.process("blocked")
    run.offer()
    eventually(run.notices)
    notice, = run.notices()
    assert notice.metadata["reason"] == failure and "命令仍在前台运行" in notice.content
    if failure == "quota_full":
        assert "名额已满" in notice.content
    assert alive(child) and len(run.provider.requests) == 1 and not applied(chat)
    assert not run.service.run_snapshot("alice", RUN_ID, session_id=SESSION)["finished"]
    assert not list((chat.root / "users/alice/runtime/background_jobs").glob("job_*.json"))
    if stop_run:
        chat.cancel.set()
        detail, = run.done().metadata["guidance_details"]
        assert detail["id"] == "guidance-test" and detail["status"] == "not_applied"
        assert not applied(chat) and len(run.provider.requests) == 1
        chat.release("blocked")
        eventually(lambda: not alive(child))
        return
    chat.release("blocked")
    assert run.done().metadata["status"] == "completed"
    assert not alive(child) and len(chat.attempts) == 1 and len(applied(chat)) == 1
    assert "go on with my revision" in run.provider.requests[1].model_dump_json()
    assert f"finished blocked {child['pid']}" in run.provider.requests[1].model_dump_json()


def test_untransferable_tool_waits_without_a_handoff_attempt(chat):
    tool(chat.root, "file")
    (chat.root / "plugins/file/tool.py").write_text(
        "from pathlib import Path\n"
        "import time\n"
        "def run(*, action, context):\n"
        "    root = Path(context['root'])\n"
        "    (root / 'file.started').touch()\n"
        "    while not (root / 'file.release').exists():\n"
        "        time.sleep(.02)\n"
        "    return {'ok': True, 'output': 'file finished'}\n",
        encoding="utf-8",
    )
    run = chat.start([ToolCall("file", "file", {"action": "read"})])
    assert chat.waiting.wait(STARTUP)
    eventually(lambda: (chat.root / "file.started").exists())
    run.offer()
    eventually(run.notices)
    notice, = run.notices()
    assert notice.tool_name == "file" and notice.metadata["reason"] == "not_transferable"
    assert not chat.attempts and len(run.provider.requests) == 1 and not applied(chat)
    chat.release("file")
    assert run.done().metadata["status"] == "completed" and len(applied(chat)) == 1


def test_finished_command_wins_without_a_failure_notice(chat):
    def finish_first(runtime, execution_id):
        chat.release("race")
        eventually(lambda: runtime.shell_handoff_state(execution_id) != "ready")

    run = chat.start([chat.shell("race")], before_handoff=finish_first)
    assert chat.waiting.wait(STARTUP)
    child = chat.process("race")
    run.offer()
    assert run.done().metadata["status"] == "completed"
    assert not alive(child) and not run.notices() and len(chat.attempts) == 1
    wire = run.provider.requests[1].model_dump_json()
    assert f"finished race {child['pid']}" in wire and "go on with my revision" in wire


def test_uncertain_commit_waits_for_the_original_receipt_before_guiding(chat, monkeypatch):
    readable = threading.Event()
    receipt = ForegroundHandoff.receipt

    def read_receipt(job):
        if not readable.is_set():
            raise OSError("test: receipt temporarily unreadable")
        return receipt(job)

    def lose_commit_ack(runtime, execution_id):
        store = runtime.integration.store
        db = store.db
        caller = threading.get_ident()
        raised = False

        @contextmanager
        def write(*, write=False):
            nonlocal raised
            with db(write=write) as connection:
                yield connection
            if write and threading.get_ident() == caller and not raised:
                raised = True
                assert runtime.integration.tool_execution_status(execution_id) == "handed_off"
                raise OSError("test: commit succeeded but did not report success")

        monkeypatch.setattr(store, "db", write)

    monkeypatch.setattr(ForegroundHandoff, "receipt", read_receipt)
    run = chat.start([chat.shell("uncertain")], before_handoff=lose_commit_ack)
    try:
        assert chat.waiting.wait(STARTUP)
        child = chat.process("uncertain")
        run.offer()
        eventually(run.notices)
        notice, = run.notices()
        assert notice.metadata["status"] == "unconfirmed" and "job_id" not in notice.metadata
        checks = len(chat.wakes)
        eventually(lambda: len(chat.wakes) > checks or len(run.provider.requests) > 1)
        assert alive(child) and not applied(chat) and len(run.provider.requests) == 1
    finally:
        readable.set()
        chat.release("uncertain")
    assert run.done().metadata["status"] == "completed"
    assert [event.metadata["status"] for event in run.notices()] == ["unconfirmed", "handed_off"]
    job_id = run.notices()[-1].metadata["job_id"]
    assert chat.status(job_id)["output"] == f"finished uncertain {child['pid']}"
    assert len(chat.attempts) == 1 and len(applied(chat)) == 1 and not alive(child)
    assert job_id in run.provider.requests[1].model_dump_json()


def test_mixed_batch_reports_each_call_without_releasing_other_blockers(chat, monkeypatch):
    monkeypatch.setattr(background_jobs, "MAX_ACTIVE_BACKGROUND_JOBS_PER_USER", 1)
    run = chat.start([chat.shell("first"), chat.shell("second")])
    assert chat.waiting.wait(STARTUP)
    children = {label: chat.process(label) for label in ("first", "second")}
    run.offer()
    eventually(lambda: len(run.notices()) == 2)
    notices = {notice.metadata["status"]: notice for notice in run.notices()}
    assert set(notices) == {"handed_off", "failed"}
    assert notices["failed"].metadata["reason"] == "quota_full"
    assert notices["failed"].tool_call_id != notices["handed_off"].tool_call_id
    assert {notice.metadata["guidance_id"] for notice in notices.values()} == {"guidance-test"}
    assert all(map(alive, children.values())) and len(run.provider.requests) == 1
    job_id = notices["handed_off"].metadata["job_id"]
    call_id = notices["failed"].tool_call_id
    failed_call, = [event for event in chat.events if event.type == "tool_call_start" and event.tool_call_id == call_id]
    foreground = "first" if "first" in failed_call.arguments["command"] else "second"
    background = "second" if foreground == "first" else "first"
    chat.release(foreground)
    assert run.done().metadata["status"] == "completed"
    assert not alive(children[foreground]) and alive(children[background])
    assert len(chat.attempts) == 2 and len(applied(chat)) == 1
    chat.release(background)
    eventually(lambda: chat.status(job_id)["status"] == "completed")


def test_alt_drains_once_and_does_not_spin_or_handoff(chat):
    run = chat.start([chat.shell("alt")])
    assert chat.waiting.wait(STARTUP)
    child = chat.process("alt")
    start = len(chat.wakes)
    run.offer("background")
    eventually(lambda: any(chat.drains))
    drained = len(chat.drains)
    eventually(lambda: len(chat.wakes) >= start + 3)
    assert len(chat.drains) == drained and sum(bool(values) for values in chat.drains) == 1
    assert not chat.attempts and not run.notices() and not applied(chat)
    assert len(run.provider.requests) == 1 and alive(child)
    chat.release("alt")
    assert run.done().metadata["status"] == "completed" and len(applied(chat)) == 1
    assert not alive(child)
