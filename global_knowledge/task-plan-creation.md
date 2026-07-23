# 任务计划创建规则

任务计划用于需要多个可验证步骤、依赖关系和中途控制的复杂任务。文件位于 `users/<user>/task_plan/plan_<8hex>.json`，由 `PlanStore` 管理。

简单的一次性操作不应创建计划。计划创建和编辑必须走 `task_plan` 子智能体；`task_plan` 工具只管理查看、批准、步骤结果、暂停、恢复和取消。

## 创建流程

```text
复杂用户需求
  → task_plan 子智能体读取真实工具/技能/知识索引
  → 生成或编辑结构化计划
  → PlanStore 校验并保存 pending/approved
  → 用户批准（或 auto_accept）
  → 前台连续执行或后台逐步执行
  → 每步写回结果，最终 completed/failed/paused/cancelled
```

全局 `task_plan.max_steps` 默认限制为 20。用户配置 `task_plan.auto_accept=false` 时，计划必须等待明确批准。

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

只允许编辑 `pending`、`approved` 或 `paused` 计划。每次更新都会增加 `revision`；界面保存前必须基于最新版本，收到“计划版本已变化”时重新读取并合并，而不是强制覆盖。运行结束或启动恢复时，未完成的运行步骤会回收到安全状态。

