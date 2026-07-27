# 定时任务创建规则

定时任务存储在 `users/<user>/task_cron/cron_<8hex>.json`，由 `CronStore` 校验并由 RuntimeHost 调度。所有用户任务都以北京时间（Asia/Shanghai，`+08:00`）保存和计算。

## 正确创建管线

自然语言时间不能由主智能体直接猜测：

```text
用户描述“每天早上九点……”
  → 调用 time_plan 子智能体解析
  → 得到自包含 prompt、type 和调度字段
  → task_time(action="create")
  → CronStore 原子写入
```

修改自然语言任务时先 `task_time get`，再由 `time_plan` 解析修改，最后 `task_time update`。删除前也必须先读取并确认目标。只有调用方已经拥有完整、确定的结构化时间时，才可跳过 `time_plan`。

## 任务类型

| 类型 | 字段 | 规则 |
|------|------|------|
| `once` | `next_run_at` | 单次北京时间 ISO 8601；执行完成后终态为 `completed` |
| `daily` | `time` | 每天固定 `HH:MM`；`next_run_at` 保存最近计算出的下次时间 |
| `recurring` | `interval_seconds` | 用户工具要求至少 60 秒；系统内部任务可以使用更短受控间隔 |

`once` 不能包含 `time` 或 `interval_seconds`；`daily` 不能包含 `interval_seconds`；`recurring` 不能包含 `time`。

## 当前扁平 Schema

```json
{
  "task_id": "cron_a1b2c3d4",
  "title": "每日状态汇总",
  "prompt": "读取当前运行状态并向用户汇总异常；没有异常也要明确报告正常。",
  "user": "alice",
  "type": "daily",
  "time": "09:00",
  "next_run_at": "2026-07-24T09:00:00+08:00",
  "latest_run_at": "",
  "status": "enabled",
  "created_at": "2026-07-23T13:00:00+08:00",
  "exec_mode": "agent"
}
```

| 字段 | 说明 |
|------|------|
| `task_id` | 用户任务必须为 `cron_` + 8 位小写十六进制 |
| `title` | 非空标题 |
| `prompt` | 非空、自包含的执行提示词，不能依赖创建时对话上下文 |
| `user` | 现有内部用户名 |
| `type` | `once` / `daily` / `recurring` |
| `next_run_at` | 北京时间 ISO 8601；终态可为空 |
| `latest_run_at` | 最近运行的北京时间 ISO，未运行时为空 |
| `status` | 当前状态 |
| `created_at` | 北京时间 ISO 8601 |
| `exec_mode` | 普通用户任务使用 `agent`；其他模式属于框架内部编排 |

Schema 拒绝未知或废弃字段。不要重新引入旧式嵌套 `schedule` 对象；旧文件由存储层读取时迁移。

## 状态机

| 状态 | 含义 | 用户可直接设置 |
|------|------|----------------|
| `enabled` | 等待调度 | 是 |
| `paused` | 暂停 | 是 |
| `running` | 正在执行 | 否，调度器维护 |
| `completed` | 单次任务完成 | 否 |
| `failed` | 最近执行失败 | 否 |
| `cancelled` | 已取消的终态 | 否 |

RuntimeHost 重启时会把被中断的 `running` 用户任务恢复为 `enabled` 并重新安排。`cron.enabled=false` 或 `runtime_host.enable_background_scheduler=false` 时不会后台执行。

用户任务和系统任务的执行记录都写入结构化运行日志数据库 `runtime/logs.sqlite3`；每日系统任务 JSONL 文件仍保留作兼容审计。日志记录只保存受限结果摘要和错误信息，不保存完整提示词。

## 创建质量要求

- `prompt` 必须包含目标、输入来源、输出去向和失败时行为。
- 涉及外部发送时明确平台、目标身份和内容，不能依赖模糊的“发给他”。
- 一次任务的时间必须包含 `+08:00`；不能写无时区字符串。
- 重复任务避免过短周期和重复副作用，必要时设计幂等检查。
- 创建后用 `task_time get/list` 核对，不直接编辑磁盘 JSON。

## 系统任务边界

`cron/task_cron_system/` 属于框架维护，允许可读的系统任务 ID、`exec_mode=system` 和 `action` 字段。感知刷新、全局拓展刷新、记忆巡检等由 RuntimeHost 对账创建。用户任务不得伪装成系统任务。
