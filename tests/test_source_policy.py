from __future__ import annotations

import unittest

from run.config import ConfigError
from run.source_policy import MainAgentSourcePolicy, NameFilter


class SourcePolicyTests(unittest.TestCase):
    def test_defaults_allow_main_sources_and_enable_all_knowledge_scopes(self) -> None:
        policy = MainAgentSourcePolicy.from_config({})
        self.assertEqual(policy.knowledge_scopes, ("user", "shared", "global"))
        self.assertTrue(policy.plugins.unrestricted)
        self.assertTrue(policy.shared_skills.unrestricted)
        self.assertTrue(policy.user_skills.unrestricted)
        self.assertTrue(policy.global_expand.unrestricted)
        self.assertTrue(policy.shared_expand.unrestricted)
        self.assertTrue(policy.global_perception.unrestricted)
        self.assertFalse(policy.kemo_graph_requested)

    def test_knowledge_switches_compute_exact_main_scopes(self) -> None:
        policy = MainAgentSourcePolicy.from_config(
            {"knowledge": {"use_shared": False, "use_global": True}}
        )
        self.assertEqual(policy.knowledge_scopes, ("user", "global"))
        user_only = MainAgentSourcePolicy.from_config(
            {"knowledge": {"use_shared": False, "use_global": False}}
        )
        self.assertEqual(user_only.knowledge_scopes, ("user",))

    def test_allowlist_deduplicates_and_normalizes_path_separators(self) -> None:
        selected = NameFilter.from_config(
            ["development\\python", "writing", "writing"],
            field="skills.shared_whitelist",
        )
        self.assertFalse(selected.unrestricted)
        self.assertEqual(selected.names, ("development/python", "writing"))
        self.assertTrue(selected.allows("writing"))
        self.assertFalse(selected.allows("other"))

    def test_invalid_policy_values_are_rejected(self) -> None:
        cases = (
            ({"knowledge": {"enabled": False}}, "已移除"),
            ({"skills": {"shared_whitelist": "all"}}, "skills.shared_whitelist"),
            ({"expand": {"global_whitelist": [""]}}, "expand.global_whitelist"),
            ({"perception": {"global_whitelist": [1]}}, "perception.global_whitelist"),
            ({"kemo_graph": {"enabled": 1}}, "kemo_graph.enabled"),
            ({"skills": {"user_whitelist": []}}, "已移除"),
            ({"plugins": {"whitelist": ["*"]}}, "不支持"),
            ({"plugins": {"unknown": []}}, "未知"),
        )
        for config, message in cases:
            with self.subTest(config=config):
                with self.assertRaisesRegex(ConfigError, message):
                    MainAgentSourcePolicy.from_config(config)

    def test_public_graph_status_never_claims_connection(self) -> None:
        disabled = MainAgentSourcePolicy.from_config({}).public_summary()["kemo_graph"]
        requested = MainAgentSourcePolicy.from_config(
            {"kemo_graph": {"enabled": True}}
        ).public_summary()["kemo_graph"]
        self.assertEqual(disabled["status"], "disabled")
        self.assertEqual(requested["status"], "not_connected")
        self.assertFalse(requested["connected"])
        self.assertFalse(requested["effective"])
        self.assertTrue(requested["replacement_active"])
        self.assertTrue(requested["replaces_knowledge"])
        self.assertTrue(requested["replaces_memory"])


if __name__ == "__main__":
    unittest.main()
