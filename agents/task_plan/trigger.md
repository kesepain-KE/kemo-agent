# 注册信息

- **名称**: task_plan
- **触发**: 主智能体判断用户请求需要多步骤分解时调用（`allowed_callers: ["main_agent"]`）
- **职责**: 根据上下文+目标+工具/技能注入，创建或编辑结构化任务计划草案
- **模型**: reasoning
- **工具**: 无；仅使用调用方注入的技能全文
- **输入**: `input.action=create/edit` 必填；创建时提供 `goal` 与相关 `context`，默认同步生成并持久化

# 操作信息

## 调用方式

主智能体通过 `subagent_dispatch` 调用，外层 `action=call` 与内层计划动作不同：

```json
{"action":"call","agent":"task_plan","input":{"action":"create","goal":"用户的任务目标","context":"相关背景"}}
```

输入契约在同目录 `schema.json`，通过 `subagent_dispatch(action=list)` 也可读取。
缺少动作会在分派前返回 `invalid_input`，不启动子任务。内部服务由 `AgentRunner.run` 执行。

## 输入

注入内容（无字符限制）：

### 工具与技能

| 序号 | 字段 | 来源 | 说明 |
|------|------|------|------|
| 1 | `available_tools` | 主智能体的 ToolRegistry | 扁平工具：{name, description} |
| 2 | `plugin_skills` | `plugins/*/SKILL.md` | 全部插件的完整 SKILL.md |
| 3 | `shared_skills_text` | `skills/**/SKILL.md` | 全部共享技能的完整 SKILL.md |
| 4 | `user_skills_text` | `users/<name>/user_skills/**/SKILL.md` | 全部用户技能的完整 SKILL.md |

### 任务数据

| 字段 | 说明 |
|------|------|
| `action` | `create` 或 `edit` |
| `goal` | 任务目标描述 |
| `context` | 主会话上下文的压缩摘要（来自 temp 层） |
| `max_steps` | 最大步骤数（来自 `global_config → task_plan.max_steps`，默认 20） |
| `auto_accept` | 是否自动执行（来自 `user_config → task_plan.auto_accept`） |
| `existing_plan` | 编辑模式时的现有计划（可选） |
| `edit_request` | 编辑模式时的修改要求（可选） |
| `completed_steps` | 编辑模式下不可修改的已完成步骤 ID 与标题 |

通过 `subagent_dispatch` 调用时，框架会忽略调用方提交的同名权限字段，并根据当前用户配置强制注入真实的 `available_tools`、技能、`max_steps` 与 `auto_accept`。主智能体只需提供任务数据，不得手工伪造工具清单。任务计划必须同步调用，以便输出立即完成校验和持久化。

## 上下文隔离

- `inherit_main_history: true` — 首次传入时获得主会话上下文
- 子代理运行上下文与主智能体完全隔离
- 子代理不会看到任务执行过程中用户的新消息

## 编辑限制

只能编辑以下状态的计划：`pending` / `approved` / `paused` / `failed`

不能编辑：`running` / `completed` / `cancelled`。计划运行中若需要重排、
插入或删除尚未开始的步骤，必须使用带 revision 的 `revise_remaining`，
不得使用 `edit` 修改计划元数据或步骤字段。

已完成步骤（`status=completed`）不可修改。

## 输出

```json
{
  "action": "create | edit | skip",
  "title": "string",
  "description": "string",
  "steps": [
    {
      "step_id": "step_1",
      "title": "string",
      "description": "string",
      "depends_on": [],
      "tool_name": "string",
      "tool_arguments": {},
      "critical": false
    }
  ],
  "message": "skip/error 说明",
  "reminder": "auto_accept=false 时的用户提示"
}
```

## 注意事项

- 所有阈值从 global_config.yaml 读取
- 不直接执行工具或写文件
- 不编造不存在的工具名
- `depends_on` 不得形成循环
- 禁止 `task_plan_` 前缀的工具名
- `critical=true` 的步骤失败时暂停计划
- auto_accept=false 时追加提醒文本
- reminder 随标准化计划持久化，后续读取仍可见
- **reminder 由 executor 硬编码覆盖**：无论 LLM 输出什么 reminder 文本，executor 都会根据 auto_accept 和 action 类型强制覆盖为规范文本。LLM 不需要自行生成 reminder，输出空字符串即可
- 注入内容由调用方组装，子代理不控制注入过程
