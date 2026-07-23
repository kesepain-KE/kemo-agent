# 注册信息

- **名称**: time_plan
- **触发**: 主智能体判断用户请求涉及定时任务时调用（`allowed_callers: ["main_agent"]`）
- **职责**: 将自然语言定时要求解析为结构化定时任务草案（recurring / daily / once）
- **模型**: default
- **工具**: get_current_time（获取北京时间）

# 操作信息

## 调用方式

由主智能体通过 `AgentRunner.run("time_plan", input_data)` 调用。

## 三种任务类型

### recurring — 重复间隔

```json
{ "type": "recurring", "interval_seconds": 3600 }
```

- `interval_seconds` ≥ 60
- 用于"每隔 N 秒/分钟/小时"

### daily — 每日固定时间

```json
{ "type": "daily", "time": "02:00" }
```

- `time` 格式 `HH:MM`，时区固定 `Asia/Shanghai`
- 用于"每天 N 点"

### once — 单次执行

```json
{ "type": "once" }
```

- `next_run_at` 由 `compute_next_run()` 根据当前时间 + 用户表述计算
- 用于"在 xxx 时间执行一次"

## 输入

| 字段 | 说明 |
|------|------|
| `action` | `create` / `edit` / `delete` |
| `user_request` | 用户自然语言 |
| `current_time_beijing` | 框架在每次调用前强制注入的当前北京时间 ISO；主智能体无需提供，伪造值会被覆盖 |
| `existing_task` | 编辑/删除时的现有任务 |
| `edit_request` | 编辑时的修改要求 |

## 输出

```json
{
  "action": "create | edit | delete | skip",
  "title": "任务标题",
  "prompt": "自包含执行提示词",
  "type": "recurring | daily | once",
  "interval_seconds": 3600,
  "time": "02:00",
  "next_run_at": "2026-07-21T02:00:00+08:00",
  "message": "skip 原因"
}
```

## 注意事项

- 所有时间使用北京时间（`Asia/Shanghai`）
- `subagent_dispatch` 在运行时强制注入真实 `current_time_beijing`，不依赖主智能体先调用时间工具
- 不直接执行、写文件或调用 CLI
- 执行 prompt 必须自包含（cron 执行时无上下文）
- `next_run_at` 由 `compute_next_run()` 确定性覆盖，子代理输出为参考
- `interval_seconds` 最小 60 秒
- 无法解析返回 `action=skip`
- 继承主会话上下文

## 主智能体硬性调用规则

`task_time` 插件与 `time_plan` 子代理遵循以下双向约定：

- **time_plan 的职责**：接收自然语言，输出 `title`、自包含 `prompt`、`type` 及对应调度参数组成的结构化草案。
- **task_time 的职责**：接收由 `time_plan` 解析或由程序确定的结构化参数，并持久化到 `CronStore`。
- **主智能体的职责**：识别需求类型；用户自然语言创建或修改定时任务时先调用 `time_plan`，再把输出交给 `task_time`；只有程序已经确定完整参数时才可直接调用 `task_time`。

编辑已有任务时，应先用 `task_time get` 读取当前任务，并把现有任务和修改要求交给 `time_plan`。主智能体不得绕过 `time_plan`，自行猜测 `type`、`time`、`interval_seconds` 或 `next_run_at`。
