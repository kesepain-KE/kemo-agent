# task_time

管理北京时间 cron 定时任务，支持创建、列出、查询、执行历史、修改和删除。任务使用扁平 schema。

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
| `weekly` | 每周指定星期执行 | `weekdays` + `time` / `times` |
| `monthly` | 每月指定日期执行 | `month_days` + `time` / `times` |
| `once` | 单次执行 | `next_run_at`：北京时间 ISO 8601 |
| `recurring` | 固定间隔重复执行 | `interval_seconds`：至少 60 秒 |

`daily` / `weekly` / `monthly` 可用 `times` 设置每日多个时刻；所有重复任务都可用 `start_date`、`end_date` 设置首尾包含的北京时间生效日期，并用 `max_runs` 限制成功执行次数。

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
| `action` | 全部 | `list` / `get` / `history` / `create` / `update` / `delete` |
| `task_id` | `get` / `history` / `update` / `delete` | 任务 ID |
| `title` | `create` / `update` | 任务标题 |
| `prompt` | `create` / `update` | 执行时发送给智能体的自包含提示词 |
| `type` | `create` / `update` | `daily` / `weekly` / `monthly` / `once` / `recurring` |
| `time` / `times` | `create` / `update` | 日历任务的一个或多个北京时间 `HH:MM` |
| `weekdays` | `create` / `update` | weekly 的 ISO 星期数组，1=周一、7=周日 |
| `month_days` | `create` / `update` | monthly 的日期数组，1–31；不存在的日期跳过 |
| `interval_seconds` | `create` / `update` | `recurring` 的间隔秒数，至少 60 |
| `next_run_at` | `create` / `update` | `once` 的北京时间 ISO 8601 |
| `start_date` / `end_date` | `create` / `update` | 重复任务首尾包含的生效日期，`YYYY-MM-DD` |
| `max_runs` | `create` / `update` | 最大成功执行次数 |
| `status` | `update` | `enabled` / `paused` |
| `query` | `list` | 按标题子串过滤，大小写不敏感 |
| `history_limit` | `get` / `history` | 最近执行记录数量，最多 50 |

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
        "enum": ["list", "get", "history", "create", "update", "delete"],
        "description": "list=列出，get=查询单个，history=执行历史，create=创建，update=修改，delete=删除"
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
        "enum": ["daily", "weekly", "monthly", "once", "recurring"],
        "description": "任务类型，用户自然语言需求应使用 time_plan 输出"
      },
      "time": {
        "type": "string",
        "description": "daily 的北京时间 HH:MM"
      },
      "times": {
        "type": "array",
        "items": {"type": "string"},
        "maxItems": 24,
        "description": "daily/weekly/monthly 的多个北京时间 HH:MM"
      },
      "weekdays": {
        "type": "array",
        "items": {"type": "integer", "minimum": 1, "maximum": 7},
        "description": "weekly 的 ISO 星期，1=周一、7=周日"
      },
      "month_days": {
        "type": "array",
        "items": {"type": "integer", "minimum": 1, "maximum": 31},
        "description": "monthly 的日期；不存在的日期跳过"
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
      "start_date": {"type": "string", "description": "生效开始日期 YYYY-MM-DD"},
      "end_date": {"type": "string", "description": "生效结束日期 YYYY-MM-DD，首尾包含"},
      "max_runs": {"type": "integer", "minimum": 1, "description": "最大成功执行次数"},
      "clear_max_runs": {"type": "boolean", "description": "update 时清除最大成功次数限制"},
      "status": {
        "type": "string",
        "enum": ["enabled", "paused"],
        "description": "update 可设置的任务状态"
      },
      "query": {
        "type": "string",
        "description": "list 按标题子串过滤，大小写不敏感"
      },
      "history_limit": {"type": "integer", "minimum": 0, "maximum": 50, "description": "get/history 返回的最近执行记录数量"}
    },
    "required": ["action"],
    "additionalProperties": false
  },
  "version": "2.2.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
