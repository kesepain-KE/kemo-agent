"""按活跃 App 用户轮询上游状态并广播差异事件。"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from upstream import UpstreamClient


class EventHub:
    def __init__(self, upstream: UpstreamClient, poll_interval: float = 5.0, state_path: Path | None = None) -> None:
        self.upstream = upstream
        self.poll_interval = max(2.0, float(poll_interval))
        self.state_path = state_path
        self._queues: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._connections: dict[asyncio.Queue[dict[str, Any]], dict[str, Any]] = {}
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._task: asyncio.Task[None] | None = None
        self._write_connection_state()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="kemo-app-events")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._queues.clear()
        self._connections.clear()
        self._write_connection_state()

    def subscribe(self, username: str, device_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self._queues[username].add(queue)
        self._connections[queue] = {
            "user": username,
            "device_id": device_id or "unknown",
            "connected_at": int(time.time()),
            "capabilities": {},
        }
        self._write_connection_state()
        return queue

    def unsubscribe(self, username: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        queues = self._queues.get(username)
        if queues is not None:
            queues.discard(queue)
        if not queues:
            self._queues.pop(username, None)
        self._connections.pop(queue, None)
        self._write_connection_state()

    def connection_snapshot(self) -> dict[str, Any]:
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for item in self._connections.values():
            user = str(item.get("user") or "unknown")
            device_id = str(item.get("device_id") or "unknown")
            key = (user, device_id)
            connected_at = int(item.get("connected_at") or 0)
            current = grouped.get(key)
            if current is None:
                grouped[key] = {
                    "user": user,
                    "device_id": device_id,
                    "connections": 1,
                    "connected_at": connected_at,
                    "capabilities": item.get("capabilities") if isinstance(item.get("capabilities"), dict) else {},
                }
            else:
                current["connections"] += 1
                current["connected_at"] = min(int(current["connected_at"]), connected_at)
                capabilities = item.get("capabilities")
                if isinstance(capabilities, dict) and capabilities:
                    current["capabilities"] = capabilities
        devices = sorted(grouped.values(), key=lambda item: (item["user"].casefold(), item["device_id"].casefold()))
        return {
            "websocket_connections": len(self._connections),
            "connected_devices": len(devices),
            "devices": devices,
        }

    def update_capabilities(self, queue: asyncio.Queue[dict[str, Any]], value: Any) -> None:
        if queue not in self._connections or not isinstance(value, dict):
            return
        actions = value.get("actions") if isinstance(value.get("actions"), dict) else {}
        safe_actions = {
            str(name): {
                "available": bool(config.get("available")),
                "execution_mode": str(config.get("execution_mode") or "")[:40],
            }
            for name, config in actions.items()
            if isinstance(name, str) and isinstance(config, dict)
        }
        self._connections[queue]["capabilities"] = {
            "protocol_version": int(value.get("protocol_version") or 1),
            "device_name": str(value.get("device_name") or "")[:120],
            "android_api": int(value.get("android_api") or 0),
            "actions": safe_actions,
        }
        self._write_connection_state()

    def _write_connection_state(self) -> None:
        if self.state_path is None:
            return
        payload = {
            "schema_version": 1,
            "updated_at": int(time.time()),
            **self.connection_snapshot(),
        }
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, self.state_path)
            try:
                os.chmod(self.state_path, 0o600)
            except OSError:
                pass
        except OSError:
            return

    async def publish(self, username: str, event_type: str, data: Any) -> None:
        event = {"type": event_type, "ts": int(time.time()), "data": data}
        for queue in tuple(self._queues.get(username, ())):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)

    async def publish_to_device(self, username: str, device_id: str, event_type: str, data: Any) -> bool:
        event = {"type": event_type, "ts": int(time.time()), "data": data}
        delivered = False
        for queue in tuple(self._queues.get(username, ())):
            connection = self._connections.get(queue, {})
            if str(connection.get("device_id") or "") != device_id:
                continue
            delivered = True
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)
        return delivered

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.poll_interval)
            for username in tuple(self._queues):
                try:
                    current = await self._snapshot(username)
                    previous = self._snapshots.get(username)
                    self._snapshots[username] = current
                    if previous is not None:
                        await self._emit_diff(username, previous, current)
                except Exception as exc:
                    await self.publish(username, "system.warning", {"message": type(exc).__name__})

    async def _snapshot(self, username: str) -> dict[str, Any]:
        tasks, health, sessions = await asyncio.gather(
            self.upstream.request_json("GET", f"/api/users/{username}/tasks"),
            self.upstream.health(),
            self.upstream.request_json(
                "GET",
                f"/api/users/{username}/sessions",
                params={"source": "web", "limit": "50"},
            ),
        )
        return {"tasks": tasks, "health": health, "sessions": sessions}

    async def _emit_diff(self, username: str, previous: dict[str, Any], current: dict[str, Any]) -> None:
        if previous.get("health") != current.get("health"):
            await self.publish(username, "system.warning", current.get("health"))
        old_plans = _index_items(previous.get("tasks"), ("plans", "task_plans"))
        new_plans = _index_items(current.get("tasks"), ("plans", "task_plans"))
        for key, item in new_plans.items():
            old = old_plans.get(key, {})
            status = str(item.get("status", ""))
            if status != str(old.get("status", "")):
                mapped = {
                    "pending": "task_plan.awaiting_approval",
                    "approved": "task_plan.awaiting_approval",
                    "done": "task_plan.completed",
                    "completed": "task_plan.completed",
                    "failed": "task_plan.failed",
                }.get(status)
                if mapped:
                    await self.publish(username, mapped, item)
        old_crons = _index_items(previous.get("tasks"), ("cron_tasks", "crons", "cron", "scheduled"))
        new_crons = _index_items(current.get("tasks"), ("cron_tasks", "crons", "cron", "scheduled"))
        for key, item in new_crons.items():
            old = old_crons.get(key, {})
            latest_run = str(item.get("latest_run_at") or "")
            last_state = str(item.get("last_state") or item.get("status") or "")
            if latest_run and (
                latest_run != str(old.get("latest_run_at") or "")
                or last_state != str(old.get("last_state") or old.get("status") or "")
            ):
                await self.publish(username, "cron.result", item)

        old_sessions = _index_sessions(previous.get("sessions"))
        new_sessions = _index_sessions(current.get("sessions"))
        for session_id, item in new_sessions.items():
            if not session_id.startswith("app-"):
                continue
            old_rounds = int(old_sessions.get(session_id, {}).get("rounds") or 0)
            new_rounds = int(item.get("rounds") or 0)
            if new_rounds > old_rounds:
                await self.publish(username, "conversation.completed", item)


def _index_items(value: Any, keys: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    items: Any = []
    if isinstance(value, dict):
        for key in keys:
            if isinstance(value.get(key), list):
                items = value[key]
                break
        if not items:
            for nested in value.values():
                found = _index_items(nested, keys)
                if found:
                    return found
    elif isinstance(value, list):
        items = value
    result: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items if isinstance(items, list) else []):
        if isinstance(item, dict):
            identity = str(item.get("id") or item.get("plan_id") or item.get("task_id") or index)
            result[identity] = item
    return result


def _index_sessions(value: Any) -> dict[str, dict[str, Any]]:
    items = value.get("sessions", []) if isinstance(value, dict) else []
    return {
        str(item.get("session_id")): item
        for item in items
        if isinstance(item, dict) and str(item.get("session_id") or "")
    }
