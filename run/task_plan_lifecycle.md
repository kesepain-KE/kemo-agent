# 任务计划生命周期

## 存储路径

```
users/<user>/task_plan/task_plans.sqlite3
```

`task_plans` 保存计划元数据，`task_plan_steps` 保存有序步骤，`task_plan_dependencies` 保存依赖边。下方 JSON 只是 API/子代理数据契约，不是磁盘文件。

## 计划数据契约

```json
{
  "schema_version": 1,
  "plan_id": "plan_a1b2c3d4",
  "title": "任务标题",
  "description": "用户原始目标描述",
  "user": "kesepain",
  "source": "cli",
  "session_id": "default",
  "status": "pending",
  "auto_accept": false,
  "revision": 1,
  "created_at": "2026-07-17T12:00:00Z",
  "updated_at": "2026-07-17T12:00:00Z",
  "current_step": "step_1",
  "steps": [
    {
      "step_id": "step_1",
      "title": "步骤标题",
      "description": "步骤描述",
      "status": "pending",
      "depends_on": [],
      "tool_name": "get_current_time",
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

### 字段说明

| 字段 | 说明 |
|------|------|
| `schema_version` | 契约版本，当前为 1 |
| `plan_id` | 计划 ID，格式 `plan_<8hex>`，不可冲突 |
| `title` | 计划标题 |
| `description` | 用户原始目标 |
| `user` | 所属用户 |
| `source` | 来源标识（cli / web / cron 等） |
| `session_id` | 来源会话 |
| `status` | 计划状态 |
| `auto_accept` | 是否自动执行（由配置控制） |
| `revision` | 修订号，每次原子写入递增 |
| `created_at` | 创建时间（UTC ISO） |
| `updated_at` | 最后修改时间（UTC ISO） |
| `current_step` | 当前正在执行或将要执行的步骤 ID |
| `steps` | 步骤列表 |

### 步骤字段

| 字段 | 说明 |
|------|------|
| `step_id` | 步骤 ID，格式 `step_<n>` |
| `title` | 步骤标题 |
| `description` | 步骤描述 |
| `status` | 步骤状态 |
| `depends_on` | 依赖步骤 ID 列表 |
| `tool_name` | 要调用的工具名称 |
| `tool_arguments` | 工具参数 |
| `critical` | 关键步骤失败暂停计划；非关键步骤失败记录后继续 |
| `result` | 执行结果 |
| `error` | 错误信息 |
| `started_at` | 开始时间 |
| `finished_at` | 完成时间 |

## 状态机

### 计划状态

```
pending → approved → running → completed
                    ↓         ↑
                  paused →───┘
                    ↓
                  failed

任意状态 → cancelled
```

| 状态 | 说明 |
|------|------|
| `pending` | 已创建，等待批准 |
| `approved` | 已批准，即将执行 |
| `running` | 正在执行某一步骤 |
| `paused` | 已暂停（用户主动或步骤失败） |
| `completed` | 全部步骤完成 |
| `failed` | 仍有未完成步骤但依赖断裂，已无可运行步骤 |
| `cancelled` | 已取消 |

### 步骤状态

| 状态 | 说明 |
|------|------|
| `pending` | 等待执行 |
| `running` | 正在执行 |
| `completed` | 已完成 |
| `failed` | 执行失败 |
| `skipped` | 因依赖失败或取消而跳过 |
| `cancelled` | 已取消 |

### 合法迁移

**计划状态**:
- `pending → approved`（用户批准）
- `pending → cancelled`
- `approved → running`（RuntimeHost 的 TaskPlanScheduler 原子领取）
- `running → paused`（用户暂停或步骤失败）
- `running → completed`（全部步骤完成）
- `paused → running`（用户恢复）
- `paused → cancelled`
- `running → cancelled`（协作式取消）
- `approved → cancelled`

**步骤状态**:
- `pending → running`
- `running → completed`
- `running → failed`
- `pending → skipped`（依赖失败）
- `pending → cancelled`

## 进程重启恢复

- TaskPlanScheduler 按依赖逐步触发主智能体 Run；活跃计划由系统提示词自动注入。
- 每次 Run 只执行当前可运行步骤，步骤与计划状态由框架持久化，不能交给模型自行修改。
- 启动时按索引查询所有用户数据库中的运行步骤
- 步骤状态为 `running` 的计划：计划状态转为 `paused`，步骤状态恢复为 `pending`
- 不自动重放有副作用的步骤
- 等待用户决定恢复或取消
