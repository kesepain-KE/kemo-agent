from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from provider.adapters.compat import chat_request_to_kemo, kemo_request_to_chat
from provider.schema import ChatRequest
from run.config import PromptBundle, PromptSection
from run.context import ContextPolicy, build_round_groups, estimate_messages_tokens
from run.conversation.request_diagnostics import provider_request_diagnostic
from run.history import append_round_items, commit_window, empty_window, load_window
from run.tools import MAX_TOOL_RESULT_CHARS


def protocol(messages: list[dict]):
    return chat_request_to_kemo(ChatRequest(model="offline", messages=messages))


class HistoryProviderPrefixTests(unittest.TestCase):
    def make_round(self):
        arguments = {"z": 1, "action": "read", "nested": {"z": 2, "a": 3}}
        result = {"ok": True, "result": {"z": ["第一行", "第二行"], "a": 2}}
        call = {
            "id": "callid_read", "name": "file", "arguments": arguments,
            "result": result, "iteration": 1, "status": "completed",
        }
        messages = [
            {"role": "user", "content": "inspect"},
            {
                "role": "assistant", "content": None, "reasoning_content": "checking",
                "tool_calls": [{
                    "id": call["id"], "type": "function",
                    "function": {
                        "name": "file",
                        "arguments": json.dumps(arguments, ensure_ascii=False),
                    },
                }],
            },
            {
                "role": "tool", "tool_call_id": call["id"], "name": "file",
                "content": json.dumps(result, ensure_ascii=False),
            },
            {"role": "assistant", "content": "done"},
        ]
        window = empty_window("alice", "web", "prefix")
        append_round_items(
            window, round_number=1,
            user_content=[{"type": "text", "text": "inspect"}],
            reasoning="checking", text="done", tool_records=[call],
            provider_responses=[
                {
                    "id": "resp_tool", "_iteration": 1,
                    "output": [
                        {
                            "id": "rs_check", "type": "reasoning", "content": "checking",
                        },
                        {
                            "id": "call_read", "type": "tool_call",
                            "call_id": call["id"], "name": "file", "arguments": arguments,
                        },
                    ],
                },
                {
                    "id": "resp_answer", "_iteration": 2,
                    "output": [{
                        "id": "msg_answer", "type": "message", "role": "assistant",
                        "content": [{"type": "text", "text": "done"}],
                    }],
                },
            ],
        )
        window["data"]["rounds"] = 1
        window["text"]["messages"] = [messages[0], messages[-1]]
        window["tool"]["rounds"] = [{"round": 1, "calls": [call]}]
        return messages, window

    def test_live_tool_prefix_survives_sqlite_commit_and_next_round(self):
        live, window = self.make_round()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "users" / "alice" / "history" / "prefix"
            path.parent.mkdir(parents=True)
            commit_window(path, window)
            saved = load_window(path)
        snapshot = copy.deepcopy(saved)
        for enabled in (False, True):
            with self.subTest(compression=enabled):
                messages = build_round_groups(
                    saved, ContextPolicy(mid_context_compression_enabled=enabled)
                )[0].messages
                messages.append({"role": "user", "content": "next"})
                original = kemo_request_to_chat(protocol(live)).messages
                replay = kemo_request_to_chat(protocol(messages)).messages
                self.assertEqual(replay[:-1], original)
                self.assertIsInstance(json.loads(replay[2]["content"]), dict)
                self.assertEqual(
                    replay[1]["tool_calls"][0]["function"]["arguments"],
                    live[1]["tool_calls"][0]["function"]["arguments"],
                )
                bundle = PromptBundle("", (), (), {})
                before = provider_request_diagnostic(
                    protocol(live), bundle, iteration=2, attempt=1
                )
                after = provider_request_diagnostic(
                    protocol(messages), bundle, iteration=1, attempt=1
                )
                self.assertEqual(before["inputs"], after["inputs"][:-1])
        self.assertEqual(saved, snapshot)

    def test_legacy_history_preserves_json_order(self):
        live, window = self.make_round()
        window.pop("items")
        messages = build_round_groups(
            window, ContextPolicy(mid_context_compression_enabled=False)
        )[0].messages
        # Legacy history does not retain per-iteration reasoning.
        original = copy.deepcopy(live)
        original[1].pop("reasoning_content")
        self.assertEqual(
            kemo_request_to_chat(protocol(messages)).messages,
            kemo_request_to_chat(protocol(original)).messages,
        )

    def test_native_mixed_blocks_and_error_flag_are_preserved(self):
        _, window = self.make_round()
        result = next(
            item for item in window["items"]["items"] if item["type"] == "tool_result"
        )
        result["is_error"] = True
        result["content"] = [
            {"type": "text", "text": '{"looks": "like JSON"}'},
            {"type": "json", "data": {"z": 1, "a": 2}},
            {"type": "text", "text": "last\nline"},
        ]
        messages = build_round_groups(
            window, ContextPolicy(mid_context_compression_enabled=False)
        )[0].messages
        request = protocol(messages)
        native_result = next(item for item in request.input if item.type == "tool_result")
        self.assertTrue(native_result.is_error)
        self.assertEqual(
            [block.model_dump(mode="json", exclude_none=True) for block in native_result.content],
            result["content"],
        )
        self.assertEqual(
            kemo_request_to_chat(request).messages[2]["content"],
            '{"looks": "like JSON"}\n{"z": 1, "a": 2}\nlast\nline',
        )

    def test_native_result_copy_does_not_double_the_context_budget(self):
        _, window = self.make_round()
        messages = build_round_groups(
            window, ContextPolicy(mid_context_compression_enabled=False)
        )[0].messages
        tool = next(message for message in messages if message["role"] == "tool")
        plain = {key: value for key, value in tool.items() if key != "_kemo_tool_result"}
        self.assertEqual(estimate_messages_tokens([tool]), estimate_messages_tokens([plain]))

    def test_json_string_results_use_their_encoded_length_for_the_hard_limit(self):
        for value in ("line\ntext", "\n" * (MAX_TOOL_RESULT_CHARS // 2 + 1)):
            with self.subTest(oversized=len(value) > 100):
                _, window = self.make_round()
                result = next(
                    item for item in window["items"]["items"]
                    if item["type"] == "tool_result"
                )
                result["content"] = [{"type": "json", "data": value}]
                messages = build_round_groups(
                    window, ContextPolicy(mid_context_compression_enabled=False)
                )[0].messages
                sent = kemo_request_to_chat(protocol(messages)).messages[2]["content"]
                if len(value) > 100:
                    self.assertEqual(
                        json.loads(sent)["error"]["exception_type"], "ToolResultTooLargeError"
                    )
                else:
                    self.assertEqual(sent, json.dumps(value, ensure_ascii=False))

    def test_compaction_and_hard_limit_cannot_replay_native_full_result(self):
        for oversized in (False, True):
            with self.subTest(oversized=oversized):
                _, window = self.make_round()
                result = next(
                    item for item in window["items"]["items"]
                    if item["type"] == "tool_result"
                )
                result["content"][0]["data"] = {
                    "ok": True,
                    "result": "X" * (MAX_TOOL_RESULT_CHARS + 1 if oversized else 500),
                }
                messages = build_round_groups(
                    window, ContextPolicy(recent_tool_rounds=0)
                )[0].messages
                tool = next(message for message in messages if message["role"] == "tool")
                self.assertNotIn("_kemo_tool_result", tool)
                sent = kemo_request_to_chat(protocol(messages)).messages[2]["content"]
                self.assertNotIn("X" * 500, sent)
                payload = json.loads(sent)
                if oversized:
                    self.assertEqual(payload["error"]["exception_type"], "ToolResultTooLargeError")
                else:
                    self.assertTrue(payload["compressed"])


class RequestFingerprintTests(unittest.TestCase):
    def diagnostic(self, messages, *, system="system"):
        request = protocol([{"role": "system", "content": system}, *messages])
        bundle = PromptBundle(system, (PromptSection("test", system),), (), {})
        return provider_request_diagnostic(request, bundle, iteration=1, attempt=1)

    def test_new_request_ids_do_not_change_existing_prefix(self):
        messages = [{"role": "user", "content": "one"}]
        first = self.diagnostic(messages)
        following = self.diagnostic(messages + [{"role": "assistant", "content": "two"}])
        self.assertNotEqual(first["request_id"], following["request_id"])
        self.assertEqual(first["inputs"], following["inputs"][:1])

    def test_changed_system_and_json_order_are_detected(self):
        messages = [
            {
                "role": "assistant", "content": None,
                "tool_calls": [{
                    "id": "callid_read", "type": "function",
                    "function": {"name": "file", "arguments": '{"z": 1, "a": 2}'},
                }],
            },
            {
                "role": "tool", "tool_call_id": "callid_read", "name": "file",
                "content": '{"z": 1, "a": 2}',
            },
        ]
        first = self.diagnostic(messages)
        changed = copy.deepcopy(messages)
        changed[-1]["content"] = '{"a": 2, "z": 1}'
        second = self.diagnostic(changed)
        self.assertEqual(first["inputs"][0], second["inputs"][0])
        self.assertNotEqual(first["inputs"][1]["sha256"], second["inputs"][1]["sha256"])
        third = self.diagnostic(messages, system="changed")
        self.assertNotEqual(first["system_prompt_sha256"], third["system_prompt_sha256"])
        self.assertNotEqual(first["sections"], third["sections"])
        self.assertNotEqual(first["inputs"][0]["prefix_sha256"], third["inputs"][0]["prefix_sha256"])

    def test_fingerprints_never_store_content_or_credentials(self):
        secret = "secret-api-key-and-user-body"
        diagnostic = self.diagnostic([{"role": "user", "content": secret}], system=secret)
        self.assertNotIn(secret, json.dumps(diagnostic))
        self.assertEqual(diagnostic["system_prompt_bytes"], len(secret.encode("utf-8")))
        self.assertEqual(diagnostic["scope"], "agent_before_provider_mapping")
        self.assertEqual(len(diagnostic["system_prompt_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
