# task_time

定时任务管理。创建/查看/修改/删除 daily、once、recurring 定时任务。

## Tool

```json
{
  "name": "task_time",
  "description": "管理 cron 定时任务 — 创建/查看/修改/删除每日、单次或重复定时任务，由后台调度器自动执行。通过 action 参数选择操作。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "enum": ["create", "list", "update", "delete"],
        "description": "操作: create=创建, list=列出, update=修改, delete=删除"
      },
      "task_id": {"type": "string", "description": "update/delete 时必填的任务 ID"},
      "title": {"type": "string", "description": "create/update 任务标题"},
      "prompt": {"type": "string", "description": "create/update 任务命令/prompt，cron 执行时发送给 AI 的消息内容"},
      "type": {"type": "string", "enum": ["daily", "once", "recurring"], "description": "create/update 任务类型"},
      "time": {"type": "string", "description": "daily 类型时的时间，HH:MM 格式（如 09:00）"},
      "interval_seconds": {"type": "integer", "description": "recurring 类型的执行间隔秒数，>=60"},
      "start_at": {"type": "string", "description": "once 类型时的执行时间（UTC ISO）"},
      "timezone": {"type": "string", "description": "daily 类型时的 IANA 时区，默认 UTC"},
      "status": {"type": "string", "enum": ["enabled", "paused"], "description": "update 时可启用或暂停任务"}
    },
    "required": ["action"],
    "additionalProperties": false
  },
  "version": "1.0.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
