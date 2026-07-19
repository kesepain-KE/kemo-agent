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
        "description": "create 时使用的数据型用户代理定义；生成四字段 agent.json、六字段 agent-config.json 和 trigger.md",
        "properties": {
          "name": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,63}$"},
          "description": {"type": "string"},
          "instruction": {"type": "string", "description": "写入 AGENT.md 的完整代理指令"},
          "trigger_condition": {"type": "string", "description": "写入 trigger.md 注册信息的触发条件"},
          "version": {"type": "string", "default": "1.0.0"},
          "input_schema": {"type": "object", "description": "可选，仅作为 trigger.md 中的输入参考"},
          "output_schema": {"type": "object", "description": "可选，仅作为 trigger.md 中的输出参考"},
          "agent_config": {
            "type": "object",
            "description": "可选运行时授权；未提供时创建可由 main_agent 调用、无工具和知识权限的代理",
            "properties": {
              "schema_version": {"type": "integer", "enum": [1]},
              "internal_mode": {"type": "boolean"},
              "allowed_callers": {"type": "array", "items": {"type": "string"}},
              "tools": {
                "type": "object",
                "properties": {
                  "plugins": {
                    "type": "object",
                    "properties": {
                      "allow": {"type": "array", "items": {"type": "string"}}
                    }
                  },
                  "shared_skills": {
                    "type": "object",
                    "properties": {
                      "allow": {"type": "array", "items": {"type": "string"}}
                    }
                  },
                  "max_iterations": {"type": "integer", "minimum": 1}
                }
              },
              "global_knowledge": {"type": "boolean"},
              "shared_knowledge": {"type": "boolean"},
              "inherit_main_history": {"type": "boolean"}
            },
            "required": ["schema_version", "internal_mode", "allowed_callers", "tools", "global_knowledge", "shared_knowledge", "inherit_main_history"],
            "additionalProperties": false
          }
        },
        "required": ["name", "description", "instruction"],
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
