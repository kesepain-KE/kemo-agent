# time_plan

将自然语言定时要求解析为结构化定时任务草案。不直接执行、写文件或调用 CLI。

所有时间均使用北京时间（`Asia/Shanghai`）。

---

## 一、核心职责

| 职责 | 说明 |
|------|------|
| 创建定时任务 | 解析用户自然语言 → 判断类型 → 生成结构化 JSON |
| 编辑定时任务 | 修改已有任务的 type/时间/prompt |
| 删除标记 | 返回 `action=delete`，由调用方删文件 |
| 跳过判断 | 无法解析时返回 `action=skip` + 原因 |

---

## 二、支持的三种类型

### recurring（重复间隔）

用于"每隔 N 秒执行一次"。`interval_seconds` 最小 60 秒。

```json
{
  "type": "recurring",
  "interval_seconds": 3600
}
```

### daily（每日固定时间）

用于"每天 N 点执行"。时间格式 `HH:MM`，时区固定 `Asia/Shanghai`。

```json
{
  "type": "daily",
  "time": "02:00"
}
```

### once（单次执行）

用于"在指定时间执行一次"。`next_run_at` 为北京时间。

```json
{
  "type": "once",
  "next_run_at": "2026-07-21T15:00:00+08:00"
}
```

---

## 三、输入

| 字段 | 类型 | 说明 |
|------|------|------|
| `action` | string | `create`、`edit` 或 `delete` |
| `user_request` | string | 用户的自然语言定时要求 |
| `current_time_beijing` | string | 当前北京时间 ISO（如 `2026-07-19T22:00:00+08:00`） |
| `existing_task` | object | 编辑/删除时的现有任务（可选） |
| `edit_request` | string | 编辑时的修改要求（可选） |

---

## 四、输出

```json
{
  "action": "create | edit | delete | skip",
  "title": "任务标题",
  "prompt": "执行时发给智能体的自包含提示词",
  "type": "recurring | daily | once",
  "interval_seconds": 3600,
  "time": "02:00",
  "next_run_at": "2026-07-21T02:00:00+08:00",
  "message": "skip 时的原因"
}
```

- `interval_seconds` 仅 recurring 时输出
- `time` 仅 daily 时输出
- `next_run_at` 由 `cron/schedule.py` 的 `compute_next_run()` 确定性计算（非 LLM），子代理可输出建议值，但以计算值为准

---

## 五、规则

- 不直接执行或写文件，只返回草案
- 执行 prompt 必须自包含、可独立理解（cron 执行时无上下文）
- 无法解析时返回 `action=skip` 和原因
- recurring 间隔 ≥ 60 秒
- 所有时间使用北京时间
- `next_run_at` 由调用方通过 `compute_next_run()` 覆盖，子代理的输出为参考
