import json
import tempfile
from pathlib import Path

from tests.template_tests.message.validator import validate


ROOT = Path(__file__).resolve().parents[3]


def test_reference_message_contract() -> None:
    report = validate(
        ROOT / "template" / "message",
        repository_root=ROOT,
        template_mode=True,
        timeout=8,
    )
    assert report.ok, report.render_text()


def test_dependency_free_message_plugin_completes_discovery() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        target = Path(temporary) / "local_message"
        target.mkdir()
        (target / "message.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "machine_id": "local-contract-1",
                    "platform": "local_contract",
                    "display_name": "Local Contract",
                    "bound_user": "alice",
                    "modules": {
                        "input": "input.py",
                        "output": "output.py",
                        "detect": "detect.py",
                    },
                    "capabilities": ["receive_text", "send_text"],
                    "allowed_tools": [],
                    "message_buffer": "message.md",
                    "files_dir": "files",
                    "log_dir": "log",
                },
                ensure_ascii=False,
            ),
            "utf-8",
        )
        (target / "state.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "health": "unknown",
                    "messages_received_today": 0,
                    "messages_sent_today": 0,
                    "input_status": "unknown",
                    "input_restart_count": 0,
                }
            ),
            "utf-8",
        )
        (target / "message.md").write_text("", "utf-8")
        (target / "input.py").write_text(
            "def start(config, buffer_path, files_path, state_path):\n    return None\n\n"
            "def stop():\n    return None\n",
            "utf-8",
        )
        (target / "output.py").write_text(
            "def send(payload):\n    return True\n",
            "utf-8",
        )
        (target / "detect.py").write_text(
            "def check(config, state):\n    return dict(state)\n",
            "utf-8",
        )
        report = validate(target, repository_root=ROOT, timeout=8)
        assert report.complete, report.render_text()

