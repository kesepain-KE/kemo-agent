# skill_creater

安全列出、读取、校验、创建、更新或删除当前用户技能及共享技能，支持结构化参数和全文两种写入方式。

共享技能和用户技能只会注入 Prompt，不会注册成 Provider 可执行工具。结构化模式中的 `tool_schema` 只负责生成规范的 `## Tool` 文档；真正可执行的工具必须开发在 `plugins/` 下。

## 使用原则

- `agent_create`：智能体生成的当前用户技能；主智能体和 `self_improve` 可以使用。
- `user_create`：用户自建的当前用户技能；由主智能体使用。
- `shared`：跨用户共享技能；由主智能体使用。
- `self_improve` 只能访问 `agent_create`，不能访问 `user_create` 或 `shared`。
- 写入前会检查疑似敏感凭据；创建或更新后的格式校验失败时会自动回滚。

## 创建技能四步流程（必须遵守）

1. 判断是否真的需要技能。一次性任务直接执行；需要独立推理循环时使用子代理；对接外部系统时使用拓展模块。
2. 确认 scope：`agent_create`、`user_create` 或 `shared`。
3. 确认技能名称、标题、描述及指令正文；`instruction` 与 `tool_schema` 二选一。
4. 先调用 `list` 查重，向用户最终确认后调用 `create`，再报告创建结果。

## 技能目录不是固定模板

`skill_creater` 只负责创建或更新框架能够发现的 `SKILL.md`。技能目录是自由工作区，可以继续使用正常文件工具添加任意文件、任意层级资料、脚本、资源或完整配套工程；框架不会因模板未列出而拒绝这些内容，也不会自动把它们注入 Prompt、注册为工具或执行。

不要套用固定的 `references/scripts/assets` 目录树，应根据真实需求组织，并由 `SKILL.md` 使用相对路径说明何时读取。`update` 只替换 `SKILL.md` 并保留其他文件；`delete` 会删除整个技能目录，因此删除前必须确认所有配套内容。

## 参数说明

| 参数 | 适用 action | 说明 |
|---|---|---|
| `action` | 全部 | `list` / `get` / `validate` / `create` / `update` / `delete` |
| `scope` | 全部 | `agent_create` / `user_create` / `shared` |
| `name` | 除 `list` 外 | 技能目录名 |
| `content` | `create` / `update` | 完整 `SKILL.md`；提供后使用全文模式 |
| `title` | `create` / `update` | 结构化模式的一级标题 |
| `description` | `create` / `update` | 结构化模式的技能描述 |
| `instruction` | `create` / `update` | 指令正文，与 `tool_schema` 二选一 |
| `tool_schema` | `create` / `update` | 文档化 Tool JSON，与 `instruction` 二选一，不注册可执行工具 |

## Tool

```json
{
  "name": "skill_creater",
  "description": "列出、读取、校验、创建、更新或删除用户技能及共享技能。支持全文 content 或 title+description+instruction/tool_schema 结构化写入；创建前必须执行四步确认流程。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "enum": ["list", "get", "validate", "create", "update", "delete"],
        "description": "要执行的技能管理操作"
      },
      "scope": {
        "type": "string",
        "enum": ["agent_create", "user_create", "shared"],
        "description": "技能作用域"
      },
      "name": {
        "type": "string",
        "description": "技能目录名；list 以外的操作需要"
      },
      "content": {
        "type": "string",
        "description": "create/update 全文模式的完整 SKILL.md"
      },
      "title": {
        "type": "string",
        "description": "create/update 结构化模式的标题"
      },
      "description": {
        "type": "string",
        "description": "create/update 结构化模式的描述"
      },
      "instruction": {
        "type": "string",
        "description": "结构化模式的指令正文，与 tool_schema 二选一"
      },
      "tool_schema": {
        "type": "object",
        "description": "结构化模式的文档化 Tool JSON，与 instruction 二选一"
      }
    },
    "required": ["action", "scope"],
    "additionalProperties": false
  },
  "version": "1.1.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
