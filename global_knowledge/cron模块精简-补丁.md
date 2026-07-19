# cron 模块精简 — 补丁

> 前一个编程文档过度删除了 `exec_mode` 和 `system_key`。本文档为补丁，仅覆盖需回退的部分。

---

## 一、回退项

### 1. `exec_mode` — 保留，默认 `"agent"`

三种执行路径共存：

| exec_mode | 执行方式 | 使用者 |
|-----------|----------|--------|
| `"agent"`（默认） | `handle_request()` → 主智能体 | 用户创建的定时任务 |
| `"subagent"` | `AgentRunner.run(name, input)` → 直接调子代理 | memory_temporary_important 巡检 |
| `"function"` | 内部函数注册表 → Python 直调 | review_due 30s 扫描 |

### 2. `system_key` — 保留

系统任务用 `system_key` 去重和更新。用户任务为空字符串。

### 3. 仍删除的字段

`last_result`、`last_error`、`run_count`、`revision`、`source`、`session_id` → 保持删除。

---

## 二、修正后的 JSON schema（11 字段）

```json
{
  "task_id": "cron_a1b2c3d4",
  "title": "定时任务的名字",
  "prompt": "执行时发给智能体的提示词",
  "user": "kesepain",
  "type": "recurring",
  "interval_seconds": 30,
  "exec_mode": "agent",
  "system_key": "",
  "next_run_at": "2026-07-20T02:00:30+08:00",
  "latest_run_at": "2026-07-20T02:00:00+08:00",
  "status": "enabled",
  "created_at": "2026-07-19T22:00:00+08:00"
}
```

---

## 三、需修正的代码位置

### 3.1 `run/cron_store.py` → `normalize_task()`

恢复参数：

```python
def normalize_task(
    *,
    task_id: str | None = None,
    title: str,
    prompt: str,
    user: str,
    type: str,                      # "recurring" | "daily" | "once"
    interval_seconds: int | None = None,
    time: str | None = None,
    next_run_at: str = "",
    status: str = "enabled",
    exec_mode: str = "agent",       # ← 恢复，默认 "agent"
    system_key: str = "",           # ← 恢复，默认空
) -> dict[str, Any]:
```

### 3.2 `cron/executor.py` → `execute_cron_task()`

恢复 `exec_mode` 路由逻辑：

```python
exec_mode = task.get("exec_mode", "agent")

if exec_mode == "subagent":
    subagent_name, input_data = _parse_subagent_prompt(task["prompt"])
    return execute_subagent_task(...)
elif exec_mode == "function":
    func_name = _parse_function_prompt(task["prompt"])
    return execute_function_task(...)

# 默认: agent 模式
request = {"user": task["user"], "prompt": task["prompt"], "source": "cron", "session_id": "cron"}
result = handle_request(request, ...)
```

### 3.3 `cron/scheduler.py` → 系统任务创建

恢复 `exec_mode` 和 `system_key` 的赋值：

```python
normalize_task(
    title="临时重要记忆定时巡检",
    prompt='{"subagent":"memory_temporary_important","input":{"trigger":"periodic_scan"}}',
    type="recurring", interval_seconds=review_hours * 3600,
    exec_mode="subagent",    # ← 恢复
    system_key="memory_scan", # ← 恢复
    ...
)

normalize_task(
    title="记忆碎片到期晋升检查",
    prompt='{"function":"cron.review_due.scan_and_promote"}',
    type="recurring", interval_seconds=30,
    exec_mode="function",     # ← 恢复
    system_key="memory_promotion", # ← 恢复
    ...
)
```

### 3.4 `cron/service.py` → `generate_cron_task()`

用户创建的定时任务不设 `exec_mode`（走默认 `"agent"`），不设 `system_key`（默认空）。

```python
task = normalize_task(
    title=data["title"],
    prompt=data["prompt"],
    user=user,
    type=data["type"],
    interval_seconds=data.get("interval_seconds"),
    time=data.get("time"),
    # exec_mode 默认 "agent"，不传
    # system_key 默认 ""，不传
)
```

---

## 四、应达到的效果

1. 用户通过对话创建的定时任务 → `exec_mode: "agent"` → 走主智能体
2. memory_temporary_important 巡检 → `exec_mode: "subagent"` → 直接调子代理，不浪费 Token
3. review_due 30s 扫描 → `exec_mode: "function"` → Python 直调，零 LLM 消耗
4. `system_key` 保留，系统任务去重更新不受影响
5. 北京时间和 9（→11）字段仍按原方案执行
