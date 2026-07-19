# memory_manage

记忆管理插件。按当前用户隔离查询、删除、编辑和新增永久记忆、三层临时记忆及临时重要记忆热画像。

## Tool

```json
{
  "name": "memory_manage",
  "description": "按标题或内容查询记忆，并安全删除、编辑、新增记忆碎片；临时层会同步维护 data.json。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "enum": ["search_by_title", "search_by_content", "delete", "edit", "add"]
      },
      "tier": {
        "type": "string",
        "enum": ["seven_days", "one_month", "half_year", "permanent", "important"]
      },
      "query": {"type": "string"},
      "filename": {"type": "string"},
      "content": {"type": "string"},
      "new_filename": {"type": "string"}
    },
    "required": ["action", "tier"],
    "additionalProperties": false
  },
  "version": "1.0.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
