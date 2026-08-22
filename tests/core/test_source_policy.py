from __future__ import annotations

import unittest

from run.config import ConfigError
from run.config import MainAgentSourcePolicy, NameFilter


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
        self.assertTrue(policy.expand_prompt_injection)
        self.assertFalse(policy.expand_realtime_injection)
        self.assertEqual(policy.expand_injection_mode, "round")
        self.assertTrue(policy.global_perception.unrestricted)
        self.assertTrue(policy.perception_prompt_injection)
        self.assertFalse(policy.perception_realtime_injection)
        self.assertEqual(policy.perception_injection_mode, "round")

    def test_perception_realtime_injection_is_user_controlled_and_defaults_off(
        self,
    ) -> None:
        policy = MainAgentSourcePolicy.from_config(
            {
                "perception": {
                    "global_whitelist": ["screen"],
                    "realtime_injection": True,
                }
            }
        )
        self.assertTrue(policy.perception_realtime_injection)
        self.assertEqual(policy.global_perception.names, ("screen",))
        self.assertTrue(policy.public_summary()["perception"]["realtime_injection"])

    def test_expand_realtime_injection_is_user_controlled_and_defaults_off(
        self,
    ) -> None:
        policy = MainAgentSourcePolicy.from_config(
            {
                "expand": {
                    "global_whitelist": ["weather"],
                    "shared_whitelist": [],
                    "realtime_injection": True,
                }
            }
        )
        self.assertTrue(policy.expand_realtime_injection)
        self.assertEqual(policy.global_expand.names, ("weather",))
        self.assertTrue(policy.public_summary()["expand"]["realtime_injection"])

    def test_prompt_injection_master_switches_override_realtime_mode(self) -> None:
        policy = MainAgentSourcePolicy.from_config(
            {
                "expand": {
                    "prompt_injection": False,
                    "realtime_injection": True,
                },
                "perception": {
                    "prompt_injection": True,
                    "realtime_injection": True,
                },
            }
        )
        self.assertFalse(policy.expand_prompt_injection)
        self.assertEqual(policy.expand_injection_mode, "disabled")
        self.assertTrue(policy.perception_prompt_injection)
        self.assertEqual(policy.perception_injection_mode, "realtime")
        summary = policy.public_summary()
        self.assertEqual(summary["expand"]["injection_mode"], "disabled")
        self.assertEqual(summary["perception"]["injection_mode"], "realtime")

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
            ({"expand": {"prompt_injection": 1}}, "必须是布尔值"),
            ({"expand": {"realtime_injection": 1}}, "必须是布尔值"),
            ({"perception": {"global_whitelist": [1]}}, "perception.global_whitelist"),
            ({"perception": {"prompt_injection": "no"}}, "必须是布尔值"),
            ({"perception": {"realtime_injection": "yes"}}, "必须是布尔值"),
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
