# subagent_dispatch
发现并调用当前用户可用的公开子代理；支持列出、同步调用、后台提交、状态查询和取消。

## Tool

```json
{
  "name": "subagent_dispatch",
  "description": "发现和调度当前用户可用的公开子代理。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {"type": "string", "enum": ["list", "create", "call", "status", "cancel"]},
      "agent": {"type": "string", "description": "call 时使用的公开子代理名称"},
      "input": {"type": "object", "description": "call 时传给子代理的结构化输入"},
      "definition": {
        "type": "object",
        "description": "create 时使用的数据型用户代理定义；未提供 agent_config 时使用全拒绝默认授权",
        "properties": {
          "name": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,63}$"},
          "description": {"type": "string"},
          "instruction": {"type": "string", "description": "写入 AGENT.md 的完整代理指令"},
          "version": {"type": "string", "default": "1.0.0"},
          "model_profile": {"type": "string", "default": "default"},
          "timeout": {"type": "number", "exclusiveMinimum": 0, "default": 120},
          "execution": {"type": "string", "enum": ["sync", "background_serial"], "default": "sync"},
          "write_policy": {"type": "string", "enum": ["none", "derived_cache", "user_memory", "user_task"], "default": "none"},
          "input_schema": {"type": "object", "description": "必须以 type=object 开头的 JSON Schema"},
          "output_schema": {"type": "object", "description": "必须以 type=object 开头的 JSON Schema"},
          "agent_config": {
            "type": "object",
            "description": "可选运行时授权；schema_version 固定为 1",
            "properties": {
              "schema_version": {"type": "integer", "enum": [1]},
              "exposure": {
                "type": "object",
                "properties": {
                  "mode": {"type": "string", "enum": ["internal", "tool"]},
                  "allowed_callers": {"type": "array", "items": {"type": "string"}}
                }
              },
              "tools": {
                "type": "object",
                "properties": {
                  "plugins": {
                    "type": "object",
                    "properties": {
                      "allow": {"type": "array", "items": {"type": "string"}}
                    }
                  },
                  "max_iterations": {"type": "integer", "minimum": 1}
                }
              },
              "prompt_sources": {
                "type": "object",
                "description": "skills.shared/user 与 expand.global/shared/user 均使用名称白名单；* 表示该层全部"
              },
              "knowledge": {
                "type": "object",
                "properties": {
                  "scopes": {"type": "array", "items": {"type": "string", "enum": ["global", "shared", "user"]}},
                  "index_enabled": {"type": "boolean"},
                  "body_access": {"type": "string", "enum": ["none", "search_tool"]},
                  "max_index_chars": {"type": "integer", "minimum": 0}
                }
              },
              "context": {
                "type": "object",
                "properties": {
                  "inherit_main_history": {"type": "boolean"},
                  "inherit_current_request": {"type": "boolean"}
                }
              }
            },
            "required": ["schema_version"]
          }
        },
        "required": ["name", "description", "instruction", "input_schema", "output_schema"],
        "additionalProperties": false
      },
      "wait": {"type": "boolean", "description": "call 时是否同步等待；默认 true", "default": true},
      "task_id": {"type": "string", "description": "status 或 cancel 时使用的后台任务 ID"}
    },
    "required": ["action"],
    "additionalProperties": false
  },
  "version": "1.0.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
