# task_time

管理北京时间 cron 定时任务，支持创建、列出、查询、修改和删除。任务使用扁平 schema。

## 使用原则

### 硬性规则：自然语言定时需求必须先走 time_plan

`task_time` 和 `time_plan` 子代理是搭档：

- `time_plan`：把“每天早上 9 点”“每隔 30 分钟”等自然语言解析为结构化草案。
- `task_time`：把经过解析或由程序确定的结构化参数持久化到 `CronStore`。

| 场景 | 正确路径 | 禁止 |
|---|---|---|
| 用户用自然语言创建定时任务 | `subagent_dispatch call time_plan` → `task_time create` | 直接猜测 `type`、`time`、`interval_seconds` 或 `next_run_at` |
| 用户用自然语言修改已有任务 | `task_time get` → `subagent_dispatch call time_plan (edit)` → `task_time update` | 主智能体自行计算新时间 |
| 删除已有任务 | `task_time get` → 确认目标 → `task_time delete` | 未核对任务便删除 |
| 内部程序已经确定完整参数 | 直接调用 `task_time create/update` | — |

`time_plan` 生成的 `prompt` 必须自包含，因为 cron 执行时没有创建任务时的对话上下文。

### 完整管道

```text
用户自然语言定时需求
  → 主智能体识别定时意图
  → subagent_dispatch call time_plan
  → 得到 title、prompt、type 和对应调度字段
  → task_time create/update
```

## 任务类型

| 类型 | 说明 | 关键字段 |
|---|---|---|
| `daily` | 每日固定时间执行 | `time`：北京时间 `HH:MM` |
| `once` | 单次执行 | `next_run_at`：北京时间 ISO 8601 |
| `recurring` | 固定间隔重复执行 | `interval_seconds`：至少 60 秒 |

## 任务状态

| 状态 | 说明 |
|---|---|
| `enabled` | 已启用，等待 cron 调度 |
| `paused` | 已暂停 |
| `running` | 正在执行，由 cron 管理 |
| `failed` | 最近执行失败，由 cron 管理 |
| `completed` | 单次任务已完成，由 cron 管理 |

主智能体通过 `update` 只能设置 `enabled` 或 `paused`；其他运行状态由 cron 模块管理。

## 参数说明

| 参数 | 适用 action | 说明 |
|---|---|---|
| `action` | 全部 | `list` / `get` / `create` / `update` / `delete` |
| `task_id` | `get` / `update` / `delete` | 任务 ID |
| `title` | `create` / `update` | 任务标题 |
| `prompt` | `create` / `update` | 执行时发送给智能体的自包含提示词 |
| `type` | `create` / `update` | `daily` / `once` / `recurring` |
| `time` | `create` / `update` | `daily` 的北京时间 `HH:MM` |
| `interval_seconds` | `create` / `update` | `recurring` 的间隔秒数，至少 60 |
| `next_run_at` | `create` / `update` | `once` 的北京时间 ISO 8601 |
| `status` | `update` | `enabled` / `paused` |
| `query` | `list` | 按标题子串过滤，大小写不敏感 |

## 返回字段

| 字段 | 适用 action | 说明 |
|---|---|---|
| `ok` | 全部 | 操作是否成功 |
| `tasks` | `list` | 过滤后的任务数组 |
| `total` | `list` | 过滤后的任务数量 |
| `active` | `list` | 过滤结果中状态为 `enabled` 或 `running` 的任务数量 |
| `task` | `get` / `create` / `update` | 单个任务详情 |
| `task_id`、`deleted` | `delete` | 删除结果 |
| `error` | 全部 | `ok=false` 时的失败原因 |

## Tool

```json
{
  "name": "task_time",
  "description": "管理北京时间 cron 定时任务，支持创建、列出、查询、修改和删除。用户自然语言定时需求必须先经过 time_plan 子代理解析，禁止主智能体直接猜测调度参数。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "enum": ["list", "get", "create", "update", "delete"],
        "description": "list=列出，get=查询单个，create=创建，update=修改，delete=删除"
      },
      "task_id": {
        "type": "string",
        "description": "任务 ID，get/update/delete 使用"
      },
      "title": {
        "type": "string",
        "description": "任务标题，create/update 使用"
      },
      "prompt": {
        "type": "string",
        "description": "自包含执行提示词，create/update 使用；用户自然语言需求应使用 time_plan 输出"
      },
      "type": {
        "type": "string",
        "enum": ["daily", "once", "recurring"],
        "description": "任务类型，用户自然语言需求应使用 time_plan 输出"
      },
      "time": {
        "type": "string",
        "description": "daily 的北京时间 HH:MM"
      },
      "interval_seconds": {
        "type": "integer",
        "minimum": 60,
        "description": "recurring 间隔秒数"
      },
      "next_run_at": {
        "type": "string",
        "description": "once 的北京时间 ISO 8601"
      },
      "status": {
        "type": "string",
        "enum": ["enabled", "paused"],
        "description": "update 可设置的任务状态"
      },
      "query": {
        "type": "string",
        "description": "list 按标题子串过滤，大小写不敏感"
      }
    },
    "required": ["action"],
    "additionalProperties": false
  },
  "version": "2.1.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
