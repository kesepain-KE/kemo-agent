from __future__ import annotations

import json

from provider.schema import ChatResponse, ToolCall
from run.agents import AgentRunner
from run.agents.public_trace import public_tool_data
from tests.agents.test_general_worker import OUTPUT, workspace  # noqa: F401 (pytest fixture)
from tests.agents.test_subagent_hotplug import ScriptedProvider


def test_public_tool_projection_preserves_useful_arguments_and_masks_credentials():
    result = public_tool_data({
        "command": "python report.py --date 2026-10-25",
        "max_tokens": 80, "prompt": "Describe this public file",
        "apiKey": "credential-value", "headers": {"Authorization": "Bearer top-secret"},
        "reasoning": "private reasoning", "system_prompt": "private system",
        "metadata": {"provider": "private transport"},
        "items": [{"password": "hidden", "name": "report.md"}],
        "stdout": 'password="supersecret"; url=https://name:pass@example.test\n',
    })
    assert result["command"].startswith("python report.py")
    assert result["max_tokens"] == 80 and result["prompt"] == "Describe this public file"
    assert result["apiKey"] == "[redacted]"
    assert result["headers"]["Authorization"] == "[redacted]"
    assert result["items"][0] == {"password": "[redacted]", "name": "report.md"}
    assert not {"reasoning", "system_prompt", "metadata"} & set(result)
    assert "supersecret" not in str(result) and "name:pass" not in str(result)
    assert public_tool_data(result) == result
    nested = public_tool_data('{"answer":"ok","reasoning":"hidden","api_key":"hidden"}')
    assert json.loads(nested) == {"answer": "ok", "api_key": "[redacted]"}
    assert "sk-" not in public_tool_data("sk-" + "a" * 32)


def test_public_tool_projection_is_bounded_and_marks_truncation():
    assert "[truncated]" in public_tool_data("x" * 100000)
    assert len(json.dumps(public_tool_data({"huge": "x" * 100000}))) < 17000
    values = public_tool_data(list(range(1000)))
    assert len(values) == 81 and values[-1] == "[truncated]"
    recursive = {}
    recursive["self"] = recursive
    assert "[truncated]" in str(public_tool_data(recursive))
    assert public_tool_data({str(i): i for i in range(1000)})["truncated"] is True


def test_model_loop_emits_linked_redacted_arguments_and_result(workspace):
    root, config = workspace
    directory = root / "plugins/inspect"
    directory.mkdir()
    manifest = {
        "name": "inspect", "description": "test", "version": "1", "enabled": True,
        "entrypoint": "tool.py:run",
        "input_schema": {"type": "object", "additionalProperties": True},
    }
    (directory / "SKILL.md").write_text(
        "# inspect\nTest\n\n## Tool\n```json\n" + json.dumps(manifest) + "\n```\n", "utf-8",
    )
    (directory / "tool.py").write_text(
        "def run(query, api_key, *, context):\n"
        "    return {'answer': query, 'access_token': 'hidden-result'}\n", "utf-8",
    )
    provider = ScriptedProvider([
        ChatResponse(text="", tool_calls=[
            ToolCall("inspect-1", "inspect", {"query": "public request", "api_key": "hidden-input"}),
        ]),
        ChatResponse(text=json.dumps(OUTPUT), model="mock"),
    ])
    events = []
    AgentRunner(root, "alice", config=config, provider_factory=lambda _: provider).run(
        "general_worker", {"task": "inspect"}, event_callback=events.append,
    )
    tools = [event.metadata for event in events
             if event.metadata.get("status") in {"tool_running", "tool_finished"}]
    assert len(tools) == 2
    assert tools[0]["tool_call_id"] == tools[1]["tool_call_id"]
    assert tools[0]["arguments"] == {"query": "public request", "api_key": "[redacted]"}
    assert tools[1]["result"]["result"] == {
        "answer": "public request", "access_token": "[redacted]",
    }
    assert "hidden-input" not in str(tools) and "hidden-result" not in str(tools)
