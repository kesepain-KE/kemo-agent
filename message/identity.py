"""外部身份绑定和传输级工具权限。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from message.schema import MessageEnvelope
from run.tools import ToolRegistry
from run.config import user_dir


class IdentityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class IdentityBinding:
    platform: str
    external_user_id: str
    internal_user: str
    chat_type: str | None = None
    external_chat_id: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "IdentityBinding":
        if not isinstance(value, dict):
            raise IdentityError("身份绑定必须是对象")
        required = ("platform", "external_user_id", "internal_user")
        missing = [name for name in required if not str(value.get(name) or "").strip()]
        if missing:
            raise IdentityError(f"身份绑定缺少字段：{', '.join(missing)}")
        chat_type = str(value.get("chat_type") or "").strip() or None
        if chat_type is not None and chat_type not in {"private", "group", "channel"}:
            raise IdentityError(f"身份绑定 chat_type 无效：{chat_type!r}")
        return cls(
            platform=str(value["platform"]).strip().lower(),
            external_user_id=str(value["external_user_id"]).strip(),
            internal_user=str(value["internal_user"]).strip(),
            chat_type=chat_type,
            external_chat_id=str(value.get("external_chat_id") or "").strip() or None,
        )

    def match_score(self, envelope: MessageEnvelope) -> int | None:
        if self.platform != envelope.platform:
            return None
        if self.external_user_id != envelope.external_user_id:
            return None
        score = 2
        if self.chat_type is not None:
            if self.chat_type != envelope.chat_type:
                return None
            score += 1
        if self.external_chat_id is not None:
            if self.external_chat_id != envelope.external_chat_id:
                return None
            score += 2
        return score


class IdentityResolver:
    def __init__(self, root: Path, bindings: list[IdentityBinding]) -> None:
        self.root = root.resolve()
        self.bindings = list(bindings)

    @classmethod
    def from_config(cls, root: Path, config: dict[str, Any]) -> "IdentityResolver":
        message_config = config.get("message") if "message" in config else config
        message_config = message_config or {}
        if not isinstance(message_config, dict):
            raise IdentityError("消息配置必须是对象")
        raw_bindings = message_config.get("bindings") or []
        if not isinstance(raw_bindings, list):
            raise IdentityError("message.bindings 必须是数组")
        return cls(root, [IdentityBinding.from_dict(item) for item in raw_bindings])

    def resolve(self, envelope: MessageEnvelope) -> str:
        matches: list[tuple[int, IdentityBinding]] = []
        for binding in self.bindings:
            score = binding.match_score(envelope)
            if score is not None:
                matches.append((score, binding))
        if not matches:
            raise IdentityError(
                f"外部身份未绑定：{envelope.platform}:{envelope.external_user_id}"
            )
        max_score = max(score for score, _ in matches)
        best = [binding for score, binding in matches if score == max_score]
        internal_users = {binding.internal_user for binding in best}
        if len(internal_users) != 1:
            raise IdentityError("身份绑定冲突：相同精确度指向多个内部用户")
        user = best[0].internal_user
        user_dir(user, self.root)
        return user


def filter_tool_registry(
    registry: ToolRegistry,
    allowed_tools: frozenset[str] | None,
) -> ToolRegistry:
    """Apply transport permission as an intersection over enabled user tools."""
    if allowed_tools is None:
        return registry
    return registry.selected(set(allowed_tools))
