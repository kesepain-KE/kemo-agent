from __future__ import annotations

import unittest

from run.config import ConfigError
from run.source_policy import MainAgentSourcePolicy, NameFilter


class SourcePolicyTests(unittest.TestCase):
    def test_defaults_allow_main_sources_and_enable_all_knowledge_scopes(self) -> None:
        policy = MainAgentSourcePolicy.from_config({})
        self.assertEqual(policy.knowledge_scopes, ("user", "shared", "global"))
        self.assertEqual(policy.direct_knowledge_scopes(), policy.knowledge_scopes)
        self.assertTrue(policy.plugins.unrestricted)
        self.assertTrue(policy.shared_skills.unrestricted)
        self.assertTrue(policy.user_skills.unrestricted)
        self.assertTrue(policy.global_expand.unrestricted)
        self.assertTrue(policy.shared_expand.unrestricted)
        self.assertTrue(policy.global_perception.unrestricted)

    def test_knowledge_switches_compute_exact_main_scopes(self) -> None:
        policy = MainAgentSourcePolicy.from_config(
            {"knowledge": {"use_shared": False, "use_global": True}}
        )
        self.assertEqual(policy.knowledge_scopes, ("user", "global"))
        self.assertEqual(policy.direct_knowledge_scopes(), ("user", "global"))
        user_only = MainAgentSourcePolicy.from_config(
            {"knowledge": {"use_shared": False, "use_global": False}}
        )
        self.assertEqual(user_only.knowledge_scopes, ("user",))

    def test_legacy_graph_section_has_no_core_policy_effect(self) -> None:
        policy = MainAgentSourcePolicy.from_config({
            "kemo_graph": {
                "kemo_graph_global_knowledge": True,
                "kemo_graph_temporary_memory": True,
            }
        })
        self.assertEqual(policy.knowledge_scopes, ("user", "shared", "global"))
        summary = policy.public_summary()
        self.assertNotIn("kemo_graph", summary)
        self.assertEqual(
            summary["knowledge"],
            {
                "enabled": True,
                "configured_scopes": ["user", "shared", "global"],
                "effective_scopes": ["user", "shared", "global"],
            },
        )

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
            ({"skills": {"user_whitelist": []}}, "已移除"),
            ({"plugins": {"whitelist": ["*"]}}, "不支持"),
            ({"plugins": {"unknown": []}}, "未知"),
        )
        for config, message in cases:
            with self.subTest(config=config):
                with self.assertRaisesRegex(ConfigError, message):
                    MainAgentSourcePolicy.from_config(config)


if __name__ == "__main__":
    unittest.main()
