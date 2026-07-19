# memory_temporary_important 运行时适配 — 编程规划

> 目标：将临时重要记忆子代理的两种操作迁移到 cron 模块调度，同时新建记忆管理插件供子代理和主智能体使用。
>
> 输出给编程 agent。不修改代码，只输出结构化规划。

---

## 一、问题

| # | 问题 | 现状 |
|---|------|------|
| 1 | 调度在 `run/maintenance.py` 的 `MaintenanceScheduler` 中，不经过 `cron/` 模块 | 用户要求所有时间相关任务统一走 cron 模块 |
| 2 | 子代理无法删除已提取的源碎片，因为它没有文件/记忆操作工具 | `agent-config.json` 当前 `plugins.allow: []` |
| 3 | 操作 A（定时巡检）和操作 B（每日整理）没有区分 trigger 标记 | maintenance.py 只传一种输入格式 |
| 4 | kemo-agent 没有通用的记忆 CRUD 插件 | 主智能体和子代理都无法在运行时增删改记忆碎片 |

---

## 二、方案

### 总体架构

```
cron/ 模块（统一调度）
  ├─ 任务 A（recurring，N 小时）→ CronScheduler → AgentRunner.run("memory_temporary_important", {trigger: "periodic_scan", ...})
  │     └─ 子代理调用 memory_manage 插件 → 删碎片 + 更新 data.json
  │
  └─ 任务 B（daily，02:00）→ CronScheduler → AgentRunner.run("memory_temporary_important", {trigger: "daily_consolidate", ...})
        └─ 子代理做整合/压缩

主智能体对话中
  └─ 用户说"删掉那条记忆" → 主智能体直接调 memory_manage 插件
```

### 关键决策

| 决策 | 说明 |
|------|------|
| cron 执行方式 | 直接调 `AgentRunner.run()`，不经过主智能体的 `handle_request`。在 `cron/executor.py` 中新增 `exec_mode: "subagent"` 支持 |
| 不影响主线程 | cron 任务跑在 `cron-scheduler` 守护线程，与用户对话线程完全隔离 |
| 新建插件 | `plugins/memory_manage`，提供 5 个工具：按标题搜索、按内容搜索、删除碎片、编辑碎片、新增碎片 |
| 删除永久记忆 | 插件允许删除 permanent 层碎片（仅删除 .md 文件，永久层无 data.json） |
| 删除临时记忆 | 同时删除 .md 文件 + 更新该层级 data.json（移除对应权重/到期条目） |
| maintenance.py | `_review_important_memory()` 方法标记废弃，保留但不调用 |

---

## 三、详细规划

### 步骤 1：新建 `plugins/memory_manage` 插件

**目录**：`E:\code\kemo-agent\plugins\memory_manage/`

#### 1.1 目录结构

```
plugins/memory_manage/
├── SKILL.md          # 插件描述与工具定义
├── tool.py           # 入口函数
└── memory_ops.py     # 纯函数：搜索、删除、编辑、新增
```

#### 1.2 工具定义（SKILL.md）

```markdown
# memory_manage

记忆管理插件 — 查询、删除、编辑、新增记忆碎片。支持永久记忆和临时分层记忆。

## Tool

```json
{
  "name": "memory_manage",
  "description": "记忆管理：按标题/内容搜索、删除、编辑、新增记忆碎片。支持永久记忆和临时分层记忆（删除临时碎片时自动更新 data.json）。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "enum": ["search_by_title", "search_by_content", "delete", "edit", "add"],
        "description": "操作类型"
      },
      "tier": {
        "type": "string",
        "enum": ["seven_days", "one_month", "half_year", "permanent", "important"],
        "description": "目标记忆层级。important=临时重要记忆文件"
      },
      "query": {
        "type": "string",
        "description": "搜索关键词（search_by_title 和 search_by_content 时必填）"
      },
      "filename": {
        "type": "string",
        "description": "目标文件名（delete 时必填，不含 .md 扩展名）"
      },
      "content": {
        "type": "string",
        "description": "新增或编辑后的 Markdown 内容（add 和 edit 时必填）"
      },
      "new_filename": {
        "type": "string",
        "description": "编辑后的新文件名（edit 时可选）"
      }
    },
    "required": ["action", "tier"],
    "additionalProperties": false
  },
  "version": "1.0.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
```

#### 1.3 两段检索设计

| 检索方式 | `action` | 说明 |
|----------|----------|------|
| 按标题搜索 | `search_by_title` | 暴力遍历指定层级所有 .md 文件名，按关键词匹配。返回文件名列表。速度快，适合作初步筛选 |
| 按内容搜索 | `search_by_content` | 暴力读取指定层级所有 .md 文件全文，按关键词匹配。返回文件名 + 匹配片段。速度慢但精确 |

#### 1.4 删除逻辑（`memory_ops.py`）

```python
def delete_fragment(root: Path, user: str, tier: str, filename: str) -> dict:
    """删除记忆碎片，临时层同步更新 data.json"""
    if tier == "important":
        # 删除 memory_temporary_important.md 中的对应条目（纯文本编辑）
        important_path = root / "users" / user / "memory_temporary_important.md"
        # ... 正则匹配删除对应 section
    elif tier == "permanent":
        # 删除 permanent/{filename}.md
        fragment_path = root / "users" / user / "improve" / "permanent" / f"{filename}.md"
        fragment_path.unlink(missing_ok=True)
    else:
        # 临时层：删除 .md + 更新 data.json
        tier_dir = root / "users" / user / "improve" / tier
        fragment_path = tier_dir / f"{filename}.md"
        fragment_path.unlink(missing_ok=True)
        
        # 更新 data.json
        data_path = tier_dir / "data.json"
        if data_path.exists():
            data = json.loads(data_path.read_text("utf-8"))
            data.pop(filename, None)
            _atomic_write(data_path, data)
    
    return {"deleted": filename, "tier": tier}
```

#### 1.5 tool.py 入口

```python
def run(action, tier, *, context, query=None, filename=None, content=None, new_filename=None):
    root = Path(context["root"])
    user = context["user"]
    
    if action == "search_by_title":
        return search_by_title(root, user, tier, query)
    elif action == "search_by_content":
        return search_by_content(root, user, tier, query)
    elif action == "delete":
        return delete_fragment(root, user, tier, filename)
    elif action == "edit":
        return edit_fragment(root, user, tier, filename, content, new_filename)
    elif action == "add":
        return add_fragment(root, user, tier, filename, content)
```

---

### 步骤 2：修改 `cron/executor.py` — 支持子代理直调

**文件**：`E:\code\kemo-agent\cron\executor.py`

#### 2.1 新增 `exec_mode` 字段

在 cron 任务 schema 中新增 `exec_mode` 字段：

```python
# 在 normalize_task 中新增:
"exec_mode": "agent"  # 默认值，兼容现有任务

# 可选值: "agent"（走 handle_request）| "subagent"（走 AgentRunner.run）
```

#### 2.2 新增 `execute_subagent_task()` 函数

```python
def execute_subagent_task(
    *,
    root: Path,
    user: str,
    task_id: str,
    subagent_name: str,
    input_data: dict[str, Any],
    config: dict[str, Any] | None = None,
    provider_factory=...,
    cancel_event=None,
) -> dict[str, Any]:
    """直接唤起子代理，不经过主智能体"""
    cfg = config or load_config(user, root)
    store = CronStore(root, user)
    
    # 1. 原子声称任务
    # （与 execute_cron_task 相同的 claim 逻辑）
    
    # 2. 直接调子代理
    runner = AgentRunner(root, user, config=cfg, provider_factory=provider_factory)
    result = runner.run(subagent_name, input_data, cancel_event=cancel_event)
    
    # 3. 持久化结果
    # ...
```

#### 2.3 修改 `execute_cron_task()` 路由

```python
def execute_cron_task(...):
    # ... claim 逻辑 ...
    
    exec_mode = task.get("exec_mode", "agent")
    
    if exec_mode == "subagent":
        # prompt 字段存储 JSON: {"subagent": "memory_temporary_important", "input": {...}}
        meta = json.loads(prompt)
        return execute_subagent_task(
            root=root, user=user, task_id=task_id,
            subagent_name=meta["subagent"],
            input_data=meta["input"],
            ...
        )
    
    # 原有 agent 模式
    # ... handle_request ...
```

---

### 步骤 3：创建两个 cron 任务

**新文件或修改**：启动时由 `RuntimeHost` 或初始化脚本在 `CronStore` 中注册两个任务。

#### 3.1 任务 A：定时巡检

```python
task_a = normalize_task(
    title="临时重要记忆定时巡检",
    prompt=json.dumps({
        "subagent": "memory_temporary_important",
        "input": {
            "trigger": "periodic_scan",
            "temporary_memories": [],   # 由 handler 在运行时填充
            "permanent_memories": [],   # 由 handler 在运行时填充
        }
    }),
    user=user,
    schedule={
        "type": "recurring",
        "interval_seconds": important_memory_review_hours * 3600,
    },
    source="system",
    session_id="memory_maintenance",
    exec_mode="subagent",  # ← 新增字段
)
```

**注意**：`temporary_memories` 和 `permanent_memories` 不能在注册时确定（随时间变化），需要 executor 在执行前动态填充。方案：

- 在 `execute_subagent_task()` 中，检测到 `memory_temporary_important` + `periodic_scan` 时，自动从 `MemoryStore` 加载当前全量数据
- 或：子代理内部通过 `memory_manage` 插件自己读取（需要给子代理加 `memory_manage` 工具）

**推荐方案**：子代理内部通过 `memory_manage` 插件自己读取全量数据，不依赖外部填充。这样 prompt 可以极简：

```json
{"subagent": "memory_temporary_important", "input": {"trigger": "periodic_scan"}}
```

子代理收到后，使用 `memory_manage` 的 `search_by_content` 遍历三层临时记忆，用 `search_by_title` 查永久记忆去重。

#### 3.2 任务 B：每日整理

```python
task_b = normalize_task(
    title="临时重要记忆每日整理",
    prompt=json.dumps({
        "subagent": "memory_temporary_important",
        "input": {"trigger": "daily_consolidate"}
    }),
    user=user,
    schedule={
        "type": "daily",
        "time": daily_memory_review_time,    # 如 "02:00"
        "timezone": "Asia/Shanghai",
    },
    source="system",
    session_id="memory_maintenance",
    exec_mode="subagent",
)
```

---

### 步骤 4：更新 `memory_temporary_important` 的 agent-config.json

**文件**：`E:\code\kemo-agent\agents\memory_temporary_important\agent-config.json`

工具白名单加入 `memory_manage`：

```json
{
  "schema_version": 1,
  "internal_mode": true,
  "allowed_callers": ["scheduler"],
  "tools": {
    "plugins": {"allow": ["memory_manage"]},
    "shared_skills": {"allow": []},
    "max_iterations": 20
  },
  "global_knowledge": false,
  "shared_knowledge": false,
  "inherit_main_history": false
}
```

子代理通过 `memory_manage` 插件自行读取全量临时记忆、查永久记忆去重、删除已提取碎片。

---

### 步骤 5：修改 `run/maintenance.py` — 标记废弃

**文件**：`E:\code\kemo-agent\run\maintenance.py`

- `_review_important_memory()` 方法保留但不再被 `_scan_user()` 调用
- 移除第 195-208 行的 `important_memory_review_hours` 检查逻辑
- 移除第 210-218 行的 `daily_memory_review_time` 检查逻辑
- `MaintenanceScheduler` 保持运行（还有其他职责如 `memory_lifecycle` 和 `context_review`），但不再负责重要记忆调度

---

### 步骤 6：启动时自动注册 cron 任务

**位置**：`RuntimeHost.start()` 或 `cron/scheduler.py` 的 `recover_all()` 中

启动时检查两个系统任务是否存在，不存在则创建：

```python
def ensure_memory_maintenance_tasks(root: Path, user: str, config: dict):
    store = CronStore(root, user)
    agents = config.get("agents") or {}
    memory_config = config.get("memory") or {}
    
    review_hours = agents.get("important_memory_review_hours", 3)
    daily_time = agents.get("daily_memory_review_time", "02:00")
    
    existing = {t["title"] for t in store.list_tasks()}
    
    if "临时重要记忆定时巡检" not in existing:
        store.create(normalize_task(
            title="临时重要记忆定时巡检",
            prompt=json.dumps({"subagent": "memory_temporary_important", "input": {"trigger": "periodic_scan"}}),
            user=user,
            schedule={"type": "recurring", "interval_seconds": review_hours * 3600},
            source="system", session_id="memory_maintenance",
        ))
    
    if "临时重要记忆每日整理" not in existing:
        store.create(normalize_task(
            title="临时重要记忆每日整理",
            prompt=json.dumps({"subagent": "memory_temporary_important", "input": {"trigger": "daily_consolidate"}}),
            user=user,
            schedule={"type": "daily", "time": daily_time, "timezone": "Asia/Shanghai"},
            source="system", session_id="memory_maintenance",
        ))
```

**注意**：`exec_mode` 字段需先加入 cron store 的 schema（步骤 2），否则 `normalize_task` 可能拒绝该字段。

---

## 四、应达到的效果

1. **cron 统一调度** — 两个任务在 `cron/` 模块中注册和执行，不经过 `maintenance.py`
2. **不干扰主对话** — cron 任务跑在 `cron-scheduler` 守护线程，用户正常对话不受影响
3. **子代理直调** — `exec_mode: "subagent"` 使 executor 直接调 `AgentRunner.run()`，不浪费主智能体 Token
4. **定时巡检自动化** — 每隔 N 小时自动全量扫描三层临时记忆，提取重要碎片 → 去重永久 → 写入热画像 → 删除源碎片 + 更新 data.json
5. **每日整理自动化** — 每天凌晨 2 点整合优化热画像，超限时压缩或删除最不重要条目
6. **memory_manage 插件可用** — 主智能体和子代理均可通过该插件查询/删除/编辑/新增记忆碎片
7. **两段检索** — 按标题快速匹配（轻量）+ 按内容全文搜索（精确）
8. **删除联动 data.json** — 删除临时层碎片时同步更新对应层级的 JSON 权重文件
9. **maintenance.py 瘦身** — `_review_important_memory` 不再被调用，相关时间检查逻辑移除
