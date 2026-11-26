"""Fixed protocol capabilities. Instance configuration cannot extend them."""

DEVICE_CAPABILITIES: frozenset[str] = frozenset({
    "chat", "run_status", "status", "cron", "task_plan", "token_usage",
    "config", "reasoning", "file_upload", "file_list", "skill_ref",
    "knowledge_ref", "expand_read", "device_action",
})
AGENT_CAPABILITIES: frozenset[str] = frozenset({
    "chat", "status", "artifact_download", "artifact_upload",
})


def capabilities_for(channel: str) -> frozenset[str]:
    if channel == "device":
        return DEVICE_CAPABILITIES
    if channel == "agent":
        return AGENT_CAPABILITIES
    return frozenset()
