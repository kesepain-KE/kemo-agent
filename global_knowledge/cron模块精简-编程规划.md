# cron 模块精简 — 编程规划

> 目标：将 CronStore JSON 从 17 字段精简为 9 字段，全链路使用北京时间，统一执行路径为主智能体。
>
> 输出给编程 agent。不修改代码，只输出结构化规划。

---

## 一、问题

| # | 问题 | 现状 |
|---|------|------|
| 1 | cron 任务 JSON 字段过多（17 个），包含无用字段 | `source`、`session_id`、`exec_mode`、`system_key`、`last_result`、`last_error`、`run_count`、`revision` 均存在 |
| 2 | 时间格式混杂 UTC / 北京时间 | `next_run_at` 存 UTC，`daily` 用 `timezone` 字段指定时区 |
| 3 | 执行路径分三种（agent / subagent / function），system_key 判断系统任务 | 复杂度过高 |
| 4 | `cron/schedule.py` 的 `compute_next_run()` 和 `is_due()` 以 UTC 为基准 | 需改为北京时间 |

---

## 二、方案

### 精简后的 JSON schema（9 字段）

```json
{
  "task_id": "cron_a1b2c3d4",
  "title": "定时任务的名字",
  "prompt": "执行时发给智能体的提示词",
  "user": "kesepain",
  "type": "recurring",
  "interval_seconds": 30,
  "next_run_at": "2026-07-20T02:00:30+08:00",
  "latest_run_at": "2026-07-20T02:00:00+08:00",
  "status": "enabled",
  "created_at": "2026-07-19T22:00:00+08:00"
}
```

### 三种类型的差异字段

| type | 专有字段 | 示例 |
|------|----------|------|
| `recurring` | `interval_seconds` | `30` |
| `daily` | `time` | `"02:00"` |
| `once` | — | — |

### 时间统一

- 所有 `*_at` 字段使用北京时间 ISO（`+08:00`）
- `compute_next_run()` 输入输出均为北京时间
- `is_due()` 比较北京时间

### 执行统一

- 所有 cron 任务统一走 `handle_request()` → 主智能体
- 删除 `exec_mode` 和 `system_key`
- 系统任务通过 prompt 内容自描述，主智能体自行判断调用子代理

---

## 三、详细规划

### 步骤 1：重写 `run/cron_store.py` 的 `normalize_task()`

**文件**：`E:\code\kemo-agent\run\cron_store.py`

精简 `normalize_task()` 参数和输出：

```python
def normalize_task(
    *,
    task_id: str | None = None,
    title: str,
    prompt: str,
    user: str,
    type: str,                      # "recurring" | "daily" | "once"
    interval_seconds: int | None = None,  # recurring 专用
    time: str | None = None,        # daily 专用（HH:MM）
    next_run_at: str = "",
    status: str = "enabled",
) -> dict[str, Any]:
    tid = task_id or _generate_task_id()
    now_beijing = _now_beijing()
    
    task = {
        "task_id": tid,
        "title": title,
        "prompt": prompt,
        "user": user,
        "type": type,
        "next_run_at": next_run_at or now_beijing,
        "latest_run_at": "",
        "status": status,
        "created_at": now_beijing,
    }
    
    if type == "recurring":
        task["interval_seconds"] = interval_seconds or 60
    elif type == "daily":
        task["time"] = time or "00:00"
    
    _validate_task(task)
    return task
```

#### 1.1 新增 `_now_beijing()`

```python
from zoneinfo import ZoneInfo

BEIJING = ZoneInfo("Asia/Shanghai")

def _now_beijing() -> str:
    return datetime.now(BEIJING).isoformat()
```

#### 1.2 修改 `_validate_task()`

删除对 `source`、`session_id`、`exec_mode`、`system_key`、`last_result`、`last_error`、`run_count`、`revision` 的校验。

新增对 `type` 的校验：
- 必须是 `recurring` / `daily` / `once` 之一
- `recurring` 必须有 `interval_seconds` ≥ 60
- `daily` 必须有 `time`，格式 `HH:MM`
- `next_run_at` 必须为北京时间（包含 `+08:00`）

#### 1.3 修改 `_validate_schedule()`

删除旧的 `_validate_schedule()`，由 `type` 字段直接描述，不再嵌套 `schedule` 子对象。

---

### 步骤 2：修改 `cron/schedule.py` — 全链路北京时间

**文件**：`E:\code\kemo-agent\cron\schedule.py`

#### 2.1 `compute_next_run()` 改为以北京时间为基准

```python
BEIJING = ZoneInfo("Asia/Shanghai")

def _now_beijing() -> datetime:
    return datetime.now(BEIJING)

def compute_next_run(task: dict[str, Any], *, after: datetime | None = None) -> str:
    after = after or _now_beijing()
    
    ttype = task["type"]
    
    if ttype == "once":
        # next_run_at 已在创建时设定，直接返回
        return task["next_run_at"]
    
    if ttype == "daily":
        time_str = task["time"]
        hour, minute = map(int, time_str.split(":"))
        target = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= after:
            target += timedelta(days=1)
        return target.isoformat()
    
    if ttype == "recurring":
        interval = task["interval_seconds"]
        return (after + timedelta(seconds=interval)).isoformat()
```

#### 2.2 `is_due()` 改为北京时间比较

```python
def is_due(next_run_at: str, *, now: datetime | None = None) -> bool:
    if not next_run_at:
        return False
    now = now or _now_beijing()
    target = datetime.fromisoformat(next_run_at)
    return target <= now
```

---

### 步骤 3：修改 `cron/executor.py` — 统一执行路径

**文件**：`E:\code\kemo-agent\cron\executor.py`

#### 3.1 删除 `_parse_subagent_prompt()` 和 `_parse_function_prompt()`

不再需要区分执行模式。

#### 3.2 `execute_cron_task()` 简化为单一路径

```python
def execute_cron_task(...):
    # 1. 原子声称：enabled → running
    task = store.update(task_id, lambda t: {**t, "status": "running", "latest_run_at": _now_beijing()})
    
    # 2. 统一走主智能体
    request = {
        "user": task["user"],
        "prompt": task["prompt"],
        "source": "cron",
        "session_id": "cron",
    }
    
    try:
        result = handle_request(request, ...)
    except Exception as exc:
        # 失败
        store.update(task_id, lambda t: {**t, "status": "failed"})
        raise
    
    # 3. 成功 → 更新状态和 next_run_at
    def _finish(t):
        t["status"] = "completed" if t["type"] == "once" else "enabled"
        t["latest_run_at"] = _now_beijing()
        if t["type"] != "once":
            t["next_run_at"] = compute_next_run(t)
        return t
    
    return store.update(task_id, _finish)
```

---

### 步骤 4：修改 `cron/service.py` — 适配新 schema

**文件**：`E:\code\kemo-agent\cron\service.py`

#### 4.1 `generate_cron_task()` 适配

```python
# 旧: schedule = data.get("schedule")
# 新: task_type = data.get("type")

task = normalize_task(
    title=data["title"],
    prompt=data["prompt"],
    user=user,
    type=data["type"],
    interval_seconds=data.get("interval_seconds"),
    time=data.get("time"),
    next_run_at=compute_next_run(...),
)
```

#### 4.2 删除 `edit_cron_task()` 中旧字段引用

不再引用 `task.get("source", "cli")`、`task.get("session_id", "cron")`。

---

### 步骤 5：修改 `cron/scheduler.py` — 系统任务适配

**文件**：`E:\code\kemo-agent\cron\scheduler.py`

`ensure_memory_tasks()` 中的 `normalize_task()` 调用适配新参数：

```python
# 旧
normalize_task(
    title=..., prompt=..., user=...,
    source="system", session_id="memory_maintenance",
    exec_mode="subagent", system_key=...,
    schedule=...,
)

# 新
normalize_task(
    title=..., prompt=..., user=...,
    type="recurring",
    interval_seconds=review_hours * 3600,
    next_run_at=compute_next_run(...),
)
```

删除 `_memory_task_specs()` 中返回的 `system_key`、`exec_mode` 等字段。

---

### 步骤 6：修改 `plugins/task_time` — 适配新 schema

**文件**：`E:\code\kemo-agent\plugins\task_time/`

`task_time_create` 的输出适配新字段名：
- `schedule` → `type` + `interval_seconds`/`time`
- 新增 `type` 参数

---

### 步骤 7：清理 `time_plan` 子代理的输出字段

**文件**：`E:\code\kemo-agent\agents\time_plan/`

✅ AGENT.md 和 trigger.md 已更新为三种 type 和新输出格式。

---

## 四、应达到的效果

1. **9 字段 JSON** — task_id、title、prompt、user、type、[interval_seconds/time]、next_run_at、latest_run_at、status、created_at
2. **全链路北京时间** — `*_at` 均为 `+08:00`，`compute_next_run()` 和 `is_due()` 以北京时间为基准
3. **执行统一** — 所有 cron 任务走 `handle_request()` → 主智能体，删除 exec_mode/system_key
4. **三种类型** — recurring（interval_seconds）、daily（time）、once（仅 next_run_at）
5. **time_plan 适配** — 输出新字段：type + interval_seconds / time + next_run_at
6. **系统任务适配** — memory 维护和 review_due 使用新 schema
