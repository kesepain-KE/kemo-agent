"""Trusted tool-result handling and current-run media message projection."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from provider.adapters.image_content import prepare_image_blocks
from provider.protocol.models import MessageItem
from run.extensions.attachments import AttachmentError
from run.extensions.media_input_queue import MediaInputQueue
from run.extensions.media_input_request import request_media_attachment


class MediaInputHost:
    def __init__(
        self, *, root: Path, user: str, scope: tuple[str, ...],
        state: dict[str, Any], config: dict[str, Any],
        runtime_provider: dict[str, Any], provider: Any, session_id: str,
        known: list[dict[str, Any]], user_text: str, cancel_event: Any = None,
        input_limit: int | None = None, audit: list[dict[str, Any]] | None = None,
        visible_asset_ids: set[str] | None = None,
    ) -> None:
        scope = (str(root.resolve()), user, *scope)
        queue = state.get("media_input_queue")
        if queue is None:
            queue = MediaInputQueue(scope)
            state["media_input_queue"] = queue
        if not isinstance(queue, MediaInputQueue) or queue.scope != scope:
            raise AttachmentError("媒体队列不属于当前 Run/用户")
        self.queue = queue
        self.root, self.user, self.session_id = root, user, session_id
        self.config, self.runtime_provider, self.provider = config, runtime_provider, provider
        self.known, self.user_text, self.cancel_event = known, user_text, cancel_event
        self.input_limit = input_limit
        self.audit = audit if audit is not None else []
        self.visible_asset_ids = visible_asset_ids or set()
        self._sync()

    def _sync(self) -> None:
        ids = {str(item.get("asset_id")) for item in self.known}
        for entry in self.queue.entries.values():
            if entry.descriptor["asset_id"] not in ids:
                self.known.append(dict(entry.descriptor))
                ids.add(entry.descriptor["asset_id"])
        self.audit[:] = self.queue.snapshot(root=self.root, user=self.user)

    def accept(
        self, tool: Any, arguments: dict[str, Any], result: Any, *,
        call_id: str, tool_records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        expected = (self.root / "plugins" / "attach_media").resolve()
        if (
            tool.name != "attach_media"
            or tool.directory.resolve() != expected
            or tool.entrypoint != "tool.py:run"
        ):
            raise AttachmentError("只有内置 attach_media 入口可以装载请求媒体")
        if result != request_media_attachment(**arguments):
            raise AttachmentError("attach_media 返回了无效装载意图")
        try:
            return self.queue.enqueue(
                arguments, root=self.root, user=self.user, call_id=call_id,
                known=self.known, user_text=self.user_text, tool_records=tool_records,
                config=self.config, runtime_provider=self.runtime_provider,
                provider=self.provider, cancel_event=self.cancel_event,
                input_limit=self.input_limit,
            )
        finally:
            self._sync()

    def _prepared(self) -> list[dict[str, Any]]:
        try:
            return self.queue.prepare(
                root=self.root, user=self.user, config=self.config,
                runtime_provider=self.runtime_provider, provider=self.provider,
                session_id=self.session_id, cancel_event=self.cancel_event,
                visible_asset_ids=self.visible_asset_ids,
            )
        finally:
            self._sync()

    def refresh_messages(self, messages: list[dict[str, Any]]) -> None:
        # Guidance may have supplied a queued asset natively in the meantime.
        messages[:] = [
            item for item in messages
            if str(item.get("_media_input_id", "")).partition(":")[2]
            not in self.visible_asset_ids
        ]
        for prepared in self._prepared():
            asset_id = prepared["_media_input_id"].split(":", 1)[1]
            if asset_id in self.visible_asset_ids:
                continue
            existing = next(
                (item for item in messages
                 if item.get("_media_input_id") == prepared["_media_input_id"]),
                None,
            )
            if existing is None:
                messages.append(prepared)
            else:
                existing.update(prepared)

    def refresh_items(self, items: list[Any]) -> None:
        for prepared in self._prepared():
            item_id = "msg_media_" + hashlib.sha256(
                prepared["_media_input_id"].encode("utf-8")
            ).hexdigest()[:32]
            message = MessageItem.model_validate({
                "id": item_id, "role": "user",
                "content": prepare_image_blocks(
                    prepared["content"],
                    transport=str(self.runtime_provider.get("type") or ""),
                ),
            })
            index = next(
                (index for index, item in enumerate(items)
                 if isinstance(item, MessageItem) and item.id == item_id),
                None,
            )
            if index is None:
                items.append(message)
            else:
                items[index] = message

    def validate_request(self, messages: list[dict[str, Any]]) -> None:
        """Check the whole native input, including user and guidance attachments."""
        if not self.queue.entries:
            return
        self.queue.transport.validate_request(
            messages, config=self.config, runtime_provider=self.runtime_provider,
            provider=self.provider, cancel_event=self.cancel_event,
        )

    def validate_items(
        self, items: list[Any], system: str, tools: list[Any],
    ) -> None:
        if not self.queue.entries:
            return
        from run.context import estimate_messages_tokens, estimate_tools_tokens

        messages = [
            item.model_dump(mode="json", exclude_none=True) for item in items
        ]
        self.validate_request(messages)
        tokens = estimate_messages_tokens(
            [{"role": "system", "content": system}, *messages]
        ) + estimate_tools_tokens(
            [tool.model_dump(mode="json", exclude_none=True) for tool in tools]
        )
        if self.input_limit is not None and tokens >= self.input_limit:
            raise AttachmentError("含装载媒体的子代理请求超过上下文预算")

    def acknowledge(self) -> None:
        self.queue.acknowledge()
        self._sync()
