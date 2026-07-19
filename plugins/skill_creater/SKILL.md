# skill_creater

安全创建、更新或删除当前用户技能及共享技能。`self_improve` 子代理只能写入 `agent_create`。

## Tool

```json
{
  "name": "skill_creater",
  "description": "创建、更新或删除技能目录及 SKILL.md；支持 agent_create、user_create 和 shared 作用域。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "enum": ["create", "update", "delete"]
      },
      "scope": {
        "type": "string",
        "enum": ["agent_create", "user_create", "shared"]
      },
      "name": {"type": "string"},
      "content": {"type": "string"}
    },
    "required": ["action", "scope", "name"],
    "additionalProperties": false
  },
  "version": "1.0.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
