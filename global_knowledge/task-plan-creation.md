# 任务计划创建规则

任务计划用于需要多个可验证步骤、依赖关系和中途控制的复杂任务。权威数据位于 `users/<user>/task_plan/task_plans.sqlite3`，由 `PlanStore` 管理；模板 JSON 只用于说明输入结构。

简单的一次性操作不应创建计划。计划创建和复杂重编排必须走 `task_plan` 子智能体；运行中的
`task_plan` 工具还可以查看、列出、修正计划、重试或重置失败步骤、记录步骤结果、批准、暂停、
恢复和取消。所有修改都经过 `PlanStore` 的 revision 校验，不能直接改 SQLite。

## 创建流程

```text
复杂用户需求
  → task_plan 子智能体读取真实工具/技能/知识索引
  → 生成或编辑结构化计划
  → PlanStore 校验并保存 pending/approved
  → 创建计划的当前主智能体 Run 在本会话边界强制收束
  → 用户批准（或 auto_accept）
  → 前台连续执行或后台逐步执行
  → 每步写回结果，最终 completed/failed/paused/cancelled
```

全局 `task_plan.max_steps` 默认限制为 20。用户配置 `task_plan.auto_accept=false` 时，计划必须等待明确批准。
`auto_accept=true` 时，新计划直接保存为 `approved` 并由正式计划执行链路领取；无论开关状态如何，创建计划的原始主智能体 Run 都不得继续自由执行普通工具，以避免绕过计划状态机或重复执行。

创建成功后的强制收束只作用于当前 `user + source + session_id + run_id`：同一 Provider 响应中位于计划创建之后的工具统一记录为 `not_executed`，不再发起下一次 Provider 请求。本机制不写入用户级暂停标志，也不会停止同一用户的其他对话空间。

任务计划数据库按用户集中保存，便于任务页统一管理；系统提示词注入则按 `source + session_id` 过滤。A 对话只能看到 A 对话所属的未完成计划，B 对话不会因 A 创建计划而获得其内容或被迫停止。
主智能体调用 `task_plan` 工具时同样执行会话归属校验；B 对话即使显式提交 A 的 `plan_id`，也不能查看、批准、暂停、恢复或取消 A 的计划。统一任务页、CLI 管理命令和后台调度器不经过这一模型工具边界，仍可在用户明确操作或系统调度下管理集中存储的计划。
Web/App 发起计划执行时，后端会在把计划迁移到 `running` 之前原子校验请求的 `source + session_id` 与计划归属完全一致；错误客户端不能把 A 的计划挂到 B 对话执行。任务页可以统一查看计划，但执行流仍回到计划原始对话空间。

## 完整 Schema 示例

```json
{
  "schema_version": 1,
  "plan_id": "plan_a1b2c3d4",
  "title": "检查并修复服务启动问题",
  "description": "先收集状态，再定位原因，最后进行受控修复与验证。",
  "user": "alice",
  "source": "web",
  "session_id": "conv_example",
  "status": "pending",
  "auto_accept": false,
  "reminder": "修复涉及重启时先提醒用户",
  "revision": 1,
  "created_at": "2026-07-23T05:00:00+00:00",
  "updated_at": "2026-07-23T05:00:00+00:00",
  "current_step": "step_1",
  "steps": [
    {
      "step_id": "step_1",
      "title": "收集状态",
      "description": "读取服务状态和最近错误，不修改系统。",
      "status": "pending",
      "depends_on": [],
      "tool_name": null,
      "tool_arguments": {},
      "critical": true,
      "result": null,
      "error": null,
      "started_at": "",
      "finished_at": ""
    },
    {
      "step_id": "step_2",
      "title": "执行修复并验证",
      "description": "根据第一步证据实施最小修复并重新验证。",
      "status": "pending",
      "depends_on": ["step_1"],
      "tool_name": null,
      "tool_arguments": {},
      "critical": true,
      "result": null,
      "error": null,
      "started_at": "",
      "finished_at": ""
    }
  ]
}
```

## 字段约束

- `plan_id` 必须为 `plan_` + 8 位小写十六进制。
- `step_id` 必须为 `step_<数字>`，在计划内唯一。
- `title` 与 `description` 必须非空。
- `depends_on` 只能引用同一计划中已存在的步骤，禁止循环依赖。
- `critical` 必须是布尔值。
- `tool_name` 可为空；非空时必须是实际可用工具，且不能是 `task_plan` 或 `task_plan_*` 管理工具。
- `tool_arguments` 必须是对象。
- `tool_arguments` 不得包含密码、Cookie、Authorization、API Key、访问/刷新 Token、私钥或其他 `_token`/`_secret` 字段；计划是持久化、可回溯数据，凭据必须改为环境变量名或受控安全引用。`token_limit` 等非凭据配置不会被误判。
- `revision`、`updated_at` 和运行时间由存储层维护，不应由 UI 用旧副本覆盖。
- 已完成步骤在编辑时受保护，不能通过重写计划抹掉结果。

## 状态

计划状态：`pending`、`approved`、`running`、`paused`、`completed`、`failed`、`cancelled`。

步骤状态：`pending`、`running`、`completed`、`failed`、`skipped`、`cancelled`。

计划批准后有两种执行形态：

1. Web 前台执行：在当前对话中连续运行；每步完成后主智能体调用 `task_plan step_done`，读取返回的 `progress` 和 `next_step`，同一轮继续下一步，用户可以看到工具与文本输出。
2. 后台执行器：一次只运行一个步骤，步骤状态由框架维护；控制提示会明确禁止再次调用 `step_done/step_fail`。

两种形态不能混用步骤写入责任。运行中暂停表示在安全边界停止；取消则进入不可继续的 `cancelled` 终态。

## 步骤设计原则

- 每一步只产生一个可验证结果，避免“分析、修改、发布、通知”混在一步。
- 依赖关系表达真实前置条件，不仅是视觉排序。
- `tool_name` 只是建议，执行时仍可根据实际环境修正；不要把不存在的工具写入计划。
- 高风险操作单独成步，并在描述中注明确认点和回滚方式。
- 结果摘要应说明做了什么、证据是什么、还剩几步，不只写“完成”。

## 编辑与并发

只允许编辑 `pending`、`approved`、`paused` 或等待修正的 `failed` 计划；`running`、`completed` 和
`cancelled` 计划不能编辑。`retry_step`/`reset_step` 只能处理 `failed` 或 `cancelled` 步骤，已完成
步骤永远不能修改、删除或重置。每次更新都会增加 `revision`；界面保存前必须基于最新版本，收到
“计划版本已变化”时重新读取并合并，而不是强制覆盖。`retry_step` 可以把暂停计划恢复为
`approved`；`reset_step` 只清理步骤，不改变计划状态。若 `auto_accept=true` 或用户配置
`task_plan.auto_retry_on_fix=true`，修正后才会自动等待执行器领取，否则仍需用户批准。运行结束或启动恢复时，
未完成的运行步骤会回收到安全状态。

网页任务计划的 edit/retry 请求必须带当前计划的 `session_id`。后端会拒绝用其他对话空间的
`session_id` 修改或重试计划；任务总览可以跨来源只读查看，但不能借此改变计划归属。

任务总览、执行记录和 revision 列表属于浏览器输出边界。服务层会对旧数据库中的工具参数、结果、
错误和修订备注再次递归脱敏；Authorization、API Key、访问 Token、Bearer、`sk-` 密钥和私钥块
不能通过只读接口返回。这个输出脱敏是持久化前校验的第二道保护，不能用来允许新计划保存凭据。

数据库把计划元数据、步骤和依赖分别保存在 `task_plans`、`task_plan_steps`、`task_plan_dependencies`，并用 `task_plan_revisions` 保存不可改写的修订历史。大型参数、结果和错误通过 `task_plan_revision_blobs` 按计划内 SHA-256 去重，读取时透明还原；旧版明文 JSON 和压缩快照继续兼容。创建、修改、revision 与大型字段引用在同一事务提交，任一步失败都会整体回滚。启动恢复只把 `running` 步骤退回 `pending` 并暂停对应计划。计划运行时没有文件式旁路。
