# task_time

管理北京时间 cron 任务。任务使用扁平 schema，不包含 schedule、timezone 或运行结果字段。

## Tool

```json
{
  "name": "task_time",
  "description": "创建、列出、修改或删除北京时间 cron 任务。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {"type": "string", "enum": ["create", "list", "update", "delete"]},
      "task_id": {"type": "string", "description": "update/delete 时的任务 ID"},
      "title": {"type": "string"},
      "prompt": {"type": "string", "description": "执行时发送给主智能体的自包含提示词"},
      "type": {"type": "string", "enum": ["daily", "once", "recurring"]},
      "time": {"type": "string", "description": "daily 的北京时间 HH:MM"},
      "interval_seconds": {"type": "integer", "minimum": 60, "description": "recurring 间隔"},
      "next_run_at": {"type": "string", "description": "once 的北京时间 ISO，必须包含 +08:00"},
      "status": {"type": "string", "enum": ["enabled", "paused"]}
    },
    "required": ["action"],
    "additionalProperties": false
  },
  "version": "2.0.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
