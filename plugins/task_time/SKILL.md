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
