---
type: domain_overview
project: kemo-agent
domain: config
module: config-总览
layer: L1
scope: project
updated: 2026-08-04
summary: config — 全局配置
source: "config/config-总览.md"
updated: 2026-07-26
verified: true
tags: [kemo-agent, config, 全局配置]
---
# config — 全局配置

`E:\code\kemo-agent\config\global_config.json`

## 配置段
```json
{\"type\": \"kemo\", \"base_url\": \"http://127.0.0.1:8741/v1\",
 \"api_key_env\": \"KEMO_API_KEY\", \"model\": \"deepseek-deepseek-v4-pro\",
 \"timeout\": 120, \"stream\": false, \"input_modalities\": [\"text\"]}

```json
{"type": "kemo", "base_url": "http://127.0.0.1:8741/v1",
 "api_key_env": "KEMO_API_KEY", "model": "deepseek-deepseek-v4-pro",
 "timeout": 120, "stream": false}
```

### tools

```json
{"enabled": true, "timeout": 240, "max_iterations": 80, "consecutive_identical_call_limit": 8}
```

> 2026-07-22 变更：`max_per_round` 字段已移除。`max_iterations` 从 8 上调到 80。`timeout` 从 60 上调到 240。
> 2026-07-26 变更：新增 `consecutive_identical_call_limit=8`，阻止相同签名连续调用。

### history

```json
{"recent_full_rounds": 3, "older_tool_log_max_chars": 200}
```

### prompt

```json
{"include_global_soul": true, "include_user_soul": true, "include_agents_manual": true}
```

### memory

```json
  "storage_schema_version\": 3,
  "extraction_mode\": \"compression_only\",
  \"recovery_max_rounds_per_scan\": 10,
  \"extraction_batch_rounds\": 5,
  \"extraction_max_candidates_per_batch\": 10,
  \"temporary_injection_limits\": {\"half_year\": 100, \"one_month\": 200, \"seven_days\": 300},
  \"important_memory_max_chars\": 5000,
  \"tiers\": {\"seven_days\": {\"days\": 7, \"upgrade_threshold\": 3, \"next\": \"one_month\"}, ...}
}
```

> 2026-07-23 变更：`storage_schema_version` 从 2 升级到 3。`auto_extract_on_commit` 布尔值替换为 `extraction_mode` 四模式枚举。详见 [[run-memory]]。
> 2026-08-04 v0.2.0：`recovery_max_rounds_per_scan` 2→10。新增 `extraction_batch_rounds`(5) 和 `extraction_max_candidates_per_batch`(10)。
### agent_models

```json
{"default": {}, "cheap": {}, "reasoning": {}}
```

空对象继承主 Provider。

### provider_runtime（新增 2026-07-22）

```json
{"max_concurrent_requests": 10, "request_semaphore_timeout": 300.0}
```

进程级 Provider 并发总闸，所有来源共享。详见 [[provider-factory]]。

### web（新增 2026-07-22）

```json
{"max_concurrent_chats": 3, "max_pending_chats": 5, "pending_chat_timeout": 30.0}
```

单用户 Web Chat 并发与等待区控制。详见 [[web-service]]。

### message（新增字段 2026-07-22）

```json
{"max_workers": 8, "max_queued_messages": 20}
```

新增 `max_queued_messages`，控制消息路由有界等待队列。详见 [[message-router]]。

### agent_runtime（更新 2026-07-22）

```json
{"queue_maxsize": 50, "default_timeout": 600}
```

`queue_maxsize` 从 0 改为 50（有界）。`default_timeout` 从 120 改为 600。

### cron（新增字段 2026-07-22）

```json
{"enabled": true, "poll_interval": 30, "avoid_congestion": true, "congestion_threshold_ratio": 0.2}
```

新增 `avoid_congestion` 和 `congestion_threshold_ratio`，控制 Provider 高负载时 Cron 退避。

### task_cron_system

```json
{"sense_update_rate": 5, "expand_update_rate": 5, "module_update_timeout": 120}
```

控制全局感知与拓展数据刷新间隔（秒）。`module_update_timeout` 是每个采集脚本的独立子进程超时（默认 120 秒，上限 3600）。

### task_plan

```json
{\"auto_accept\": false, \"max_steps\": 20}
```

```json
{
  \"n1_recent_tool_rounds\": 3,
  \"n2_max_rounds\": 80,
  \"n3_rounds_after_compression\": 20,
  \"n4_token_limit\": 1000000,
  \"n5_token_compression_ratio\": 0.3,
  \"n6_important_memory_review_hours\": 3,
  \"n7_daily_memory_review_time\": \"02:00\",
  \"n8_task_plan_max_steps\": 20
}
```
  "n1_recent_rounds_before_tool_compression": 3,

## 加载链

```
config/config_core.json  (全局默认)
  ← 深合并
users/<user>/config.json  (用户覆盖)
  ← .env 密钥注入
provider_runtime_config()
```
