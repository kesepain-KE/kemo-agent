# task_plan 运行时适配 — 编程规划

> 目标：在 `generate_plan()` 中按顺序注入技能目录和知识库索引，同时增加编辑限制和 auto_accept 提醒。
>
> 输出给编程 agent。不修改代码，只输出结构化规划。

---

## 一、问题

| # | 问题 | 现状 |
|---|------|------|
| 1 | task_plan 只看到扁平工具列表，看不到指令型技能的 SKILL.md | `_tool_summary()` 只传 {name, description} |
| 2 | task_plan 只通过 `knowledge_search` 按需查知识库，没有索引总览 | 不知道知识库里有哪些文件可用 |
| 3 | 编辑模式没有状态限制 | 可以编辑 completed/cancelled 的计划 |
| 4 | auto_accept=false 时没有用户提示 | 不追加提醒文本 |
| 5 | 注入内容分散在多处，无明确的顺序和上限说明 | — |

---

## 二、方案

### 总体

修改 `run/task_plan_service.py` 的 `generate_plan()` 和 `edit_plan()`，按以下顺序构建 `input_data`：

```
1. available_tools     — 扁平工具列表（已有，不变）
2. plugin_skills       — 全部 plugins/*/SKILL.md 全文（新增）
3. shared_skills_text  — 全部 shared_skills/*/SKILL.md 全文（新增）
4. user_skills_text    — 全部 users/<name>/user_skills/*/SKILL.md 全文（新增）
5. global_knowledge_index — global_knowledge/data_structure.md 全文（新增）
6. shared_knowledge_index — shared_knowledge/data_structure.md 全文（新增）
7. user_knowledge_index   — users/<name>/knowledge/data_structure.md 全文（新增）
```

无字符限制。

---

## 三、详细规划

### 步骤 1：新增 `_collect_skills()` — 收集所有技能 SKILL.md

**文件**：`E:\code\kemo-agent\run\task_plan_service.py`

```python
def _collect_skills(root: Path, user: str) -> dict[str, str]:
    """Collect all SKILL.md contents from three skill directories.
    
    Returns a dict with keys: plugin_skills, shared_skills, user_skills.
    Each value is the concatenated Markdown of all SKILL.md files.
    """
    result = {"plugin_skills": "", "shared_skills": "", "user_skills": ""}
    
    # 1. plugins/
    plugins_dir = root / "plugins"
    if plugins_dir.is_dir():
        parts = []
        for skill_dir in sorted(plugins_dir.iterdir()):
            if skill_dir.is_dir():
                skill_md = skill_dir / "SKILL.md"
                if skill_md.is_file():
                    content = skill_md.read_text("utf-8")
                    parts.append(f"## {skill_dir.name}\n\n{content}")
        result["plugin_skills"] = "\n\n---\n\n".join(parts)
    
    # 2. shared_skills/
    shared_dir = root / "shared_skills"
    if shared_dir.is_dir():
        parts = []
        for skill_dir in sorted(shared_dir.iterdir()):
            if skill_dir.is_dir():
                for md in sorted(skill_dir.glob("*.md")):
                    content = md.read_text("utf-8")
                    parts.append(f"## {skill_dir.name}/{md.name}\n\n{content}")
        result["shared_skills"] = "\n\n---\n\n".join(parts)
    
    # 3. user_skills/
    user_skills_dir = root / "users" / user / "user_skills"
    if user_skills_dir.is_dir():
        parts = []
        for scope_dir in sorted(user_skills_dir.iterdir()):
            if scope_dir.is_dir():
                for skill_dir in sorted(scope_dir.iterdir()):
                    if skill_dir.is_dir():
                        skill_md = skill_dir / "SKILL.md"
                        if skill_md.is_file():
                            content = skill_md.read_text("utf-8")
                            parts.append(f"## {scope_dir.name}/{skill_dir.name}\n\n{content}")
        result["user_skills"] = "\n\n---\n\n".join(parts)
    
    return result
```

---

### 步骤 2：新增 `_collect_knowledge_indexes()` — 收集知识库索引

**文件**：`E:\code\kemo-agent\run\task_plan_service.py`

```python
def _collect_knowledge_indexes(root: Path, user: str) -> dict[str, str]:
    """Read data_structure.md from all three knowledge bases."""
    result = {
        "global_knowledge_index": "",
        "shared_knowledge_index": "",
        "user_knowledge_index": "",
    }
    
    # 1. global
    path = root / "global_knowledge" / "data_structure.md"
    if path.is_file():
        result["global_knowledge_index"] = path.read_text("utf-8")
    
    # 2. shared
    path = root / "shared_knowledge" / "data_structure.md"
    if path.is_file():
        result["shared_knowledge_index"] = path.read_text("utf-8")
    
    # 3. user
    path = root / "users" / user / "knowledge" / "data_structure.md"
    if path.is_file():
        result["user_knowledge_index"] = path.read_text("utf-8")
    
    return result
```

---

### 步骤 3：修改 `generate_plan()` — 组装注入内容

**文件**：`E:\code\kemo-agent\run\task_plan_service.py`

在 `input_data` 构建处追加技能和知识库内容：

```python
def generate_plan(...):
    # ... 现有逻辑 ...
    
    # 新增：收集技能和知识库
    skills = _collect_skills(root, user)
    knowledge = _collect_knowledge_indexes(root, user)
    
    input_data: dict[str, Any] = {
        "action": "edit" if existing_plan is not None else "create",
        "goal": goal,
        "available_tools": _tool_summary(tool_registry),
        # ↓ 新增字段（按顺序）
        "plugin_skills": skills["plugin_skills"],
        "shared_skills_text": skills["shared_skills"],
        "user_skills_text": skills["user_skills"],
        "global_knowledge_index": knowledge["global_knowledge_index"],
        "shared_knowledge_index": knowledge["shared_knowledge_index"],
        "user_knowledge_index": knowledge["user_knowledge_index"],
        # ↓ 已有字段
        "max_steps": max_steps,
        "auto_accept": auto_accept,
        "relevant_memory": memory_text,
    }
    # ... 其余不变 ...
```

同样修改 `edit_plan()`。

---

### 步骤 4：增加编辑模式的状态限制

**文件**：`E:\code\kemo-agent\run\task_plan_service.py`

在 `edit_plan()` 中，读取计划后检查状态：

```python
def edit_plan(...):
    # ... 读取 plan ...
    
    # 新增：状态限制
    editable_statuses = {"pending", "approved", "paused"}
    if plan["status"] not in editable_statuses:
        raise PlanGenerationError(
            f"计划 {plan.get('plan_id')} 当前状态为 {plan['status']!r}，"
            f"只能编辑 pending/approved/paused 状态的计划"
        )
    
    # 过滤掉已完成的步骤（不可修改）
    completed_steps = [s for s in plan.get("steps", []) if s.get("status") == "completed"]
    # 将这些步骤标记为不可修改，传入 input_data
    
    # ... 其余不变 ...
```

同时更新 `input_data`，传入已完成步骤列表供 task_plan 参考：

```python
input_data["completed_steps"] = [
    {"step_id": s["step_id"], "title": s.get("title", "")}
    for s in completed_steps
]
```

---

### 步骤 5：增加 auto_accept 提醒

**文件**：`E:\code\kemo-agent\run\task_plan_service.py` + `agents/task_plan/AGENT.md`

task_plan 的 AGENT.md 已包含 auto_accept 提醒规则。`generate_plan()` 在返回的 plan dict 中新增 `reminder` 字段：

```python
# generate_plan() 末尾：
if not auto_accept:
    plan["_reminder"] = (
        "当前任务计划已创建，请让用户点击批准后执行"
        if action == "create"
        else "当前任务计划已修改，请让用户点击批准后执行"
    )
```

主智能体在收到计划后，如果 `_reminder` 非空，追加到回复末尾。

**另一种方案**：不在 plan dict 中加字段，而是在 task_plan 子代理的输出中直接包含 `reminder` 字段。子代理在 AGENT.md 指导下自行生成。这个方案更简单——只需要在 `normalize_plan` 中保留 `reminder` 字段即可。

推荐方案 B（子代理自己生成 reminder），因为这样 reminder 会随 plan 一起序列化到磁盘，后续主智能体查阅计划时也能看到。

---

### 步骤 6：确认 `normalize_plan` 保留 `reminder` 字段

**文件**：`E:\code\kemo-agent\run\task_plan_store.py`

检查 `normalize_plan()` 是否允许额外字段。如果当前 schema 拒绝未知字段，需要添加 `reminder` 到白名单。

---

## 四、应达到的效果

1. **技能注入** — task_plan 能看到所有指令型技能的限制、适用场景和注意事项，不只是工具名
2. **知识库总览** — task_plan 知道知识库里有哪些文件可用，可据此判断"这个任务可能需要查哪个知识库文件"
3. **注入顺序** — 工具 → 插件技能 → 共享技能 → 用户技能 → 全局知识库 → 共享知识库 → 用户知识库
4. **编辑限制** — 只能编辑 pending/approved/paused 状态的计划，已完成步骤不可修改
5. **auto_accept 提醒** — false 时在计划输出中包含提醒文本，主智能体转发给用户
6. **上下文隔离** — task_plan 的工具调用与主会话隔离（现有行为，确认保持）
