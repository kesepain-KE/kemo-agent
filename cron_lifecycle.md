# Cron 定时任务生命周期

## 存储路径

```
users/<user>/task_cron/<task_id>.json
```

每个任务一个独立 JSON 文件，磁盘为唯一权威。

## 任务数据契约

```json
{
  "schema_version": 1,
  "task_id": "cron_a1b2c3d4",
  "title": "每日简报",
  "prompt": "整理今天的项目进展",
  "user": "kesepain",
  "source": "cli",
  "session_id": "cron",
  "schedule": {
    "type": "daily",
    "time": "09:00",
    "timezone": "Asia/Shanghai"
  },
  "status": "enabled",
  "next_run_at": "2026-07-18T01:00:00Z",
  "last_run_at": "",
  "last_result": null,
  "last_error": null,
  "run_count": 0,
  "revision": 1,
  "created_at": "2026-07-17T12:00:00Z",
  "updated_at": "2026-07-17T12:00:00Z"
}
```

### 字段说明

| 字段 | 说明 |
|------|------|
| `schema_version` | 契约版本，当前为 1 |
| `task_id` | 任务 ID，格式 `cron_<8hex>`，不可冲突 |
| `title` | 任务标题 |
| `prompt` | 执行时发给 Run 核心的提示词 |
| `user` | 所属用户 |
| `source` | 来源标识（cli / web / cron 等） |
| `session_id` | 执行时使用的会话 ID（默认 `cron`） |
| `schedule.type` | 调度类型：`once` / `daily` / `recurring` |
| `schedule.time` | 每日执行时间（HH:MM），仅 `daily` 使用 |
| `schedule.timezone` | 用户时区（IANA 名称，如 `Asia/Shanghai`） |
| `schedule.start_at` | 单次执行时间（ISO），仅 `once` 使用 |
| `schedule.interval_seconds` | 固定间隔秒数，仅 `recurring` 使用 |
| `status` | 任务状态 |
| `next_run_at` | 下一次执行时间（UTC ISO） |
| `last_run_at` | 上一次执行时间（UTC ISO） |
| `last_result` | 上一次执行结果摘要 |
| `last_error` | 上一次执行错误 |
| `run_count` | 累计执行次数 |
| `revision` | 修订号，每次原子写入递增 |
| `created_at` | 创建时间（UTC ISO） |
| `updated_at` | 最后修改时间（UTC ISO） |

## 调度类型

### once

单次执行。`schedule.start_at` 指定 UTC ISO 时间。
成功后状态转为 `completed`，不再计算 `next_run_at`。

### daily

每日固定时间执行。`schedule.time` 为 HH:MM，`schedule.timezone` 为 IANA 时区。
每次执行后计算下一个该时区的该时间点，转为 UTC ISO 存入 `next_run_at`。

### recurring

固定间隔重复执行。`schedule.interval_seconds` 为间隔秒数（最小 60）。
每次执行后 `next_run_at = last_run_at + interval_seconds`。

## 状态机

### 任务状态

| 状态 | 说明 |
|------|------|
| `enabled` | 已启用，等待调度 |
| `paused` | 已暂停，不参与调度 |
| `running` | 正在执行 |
| `completed` | 单次任务已完成（仅 `once` 类型） |
| `failed` | 执行失败且未恢复 |
| `cancelled` | 已取消 |

### 合法迁移

```
enabled → running → enabled（周期任务成功）
enabled → running → failed（执行失败）
enabled → running → completed（单次任务成功）
enabled → paused
paused → enabled
running → paused（恢复时回退）
failed → enabled（重试）
任意活跃状态 → cancelled
```

## 时区处理

- 所有存储时间统一使用 UTC ISO（带 Z 后缀）
- `schedule.timezone` 保存用户时区
- 时间计算时先转换到用户时区进行本地时间判断，再转回 UTC 存储
- 使用 Python 标准库 `zoneinfo`（3.9+）进行时区转换
- 无效本地时间（夏令时跳变）按 UTC 等价时间处理

## 过期任务补跑

- 服务重启后扫描所有 `enabled` 任务的 `next_run_at`
- 如果 `next_run_at < 当前UTC时间`，标记为过期
- 过期任务立即执行一次补跑
- 补跑后正常计算下一个 `next_run_at`
- 单次任务过期后仍执行一次，然后进入 `completed`

## 并发防重

- 调度器原子领取任务：先写入 `running` 状态再执行
- 同一任务同一时间只能被一个调度器线程执行
- 使用 per-(root, user, task_id) 文件锁，不依赖内存状态
- `running` 状态的任务不参与到期扫描

## 进程重启恢复

- 启动时扫描所有用户任务文件
- `running` 状态的任务：转为 `enabled`，`next_run_at` 设为当前时间（立即补跑）
- 不自动重放有副作用的执行
- 等待调度器自然扫描

## 与 time_plan 子代理的关系

- `time_plan` 子代理：只负责将自然语言要求解析为结构化定时任务草案（schedule + prompt）
- `cron/` 模块：负责确定性调度（时间计算、到期检测、原子领取）
- `run/` 核心引擎：负责实际执行（handle_request）
- 链路：用户要求 → time_plan 生成草案 → 存储层持久化 → 调度器到期触发 → Run 核心执行 → 结果写回

## Web 暂不接入

本阶段不修改 `web/`。
