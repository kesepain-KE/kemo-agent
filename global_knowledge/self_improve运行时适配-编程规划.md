# self_improve 运行时适配 — 编程规划

> 目标：将 self_improve 从"逐轮异步提取"重构为"context_manage 批处理 + cron 晋升检查"双模式。
>
> 输出给编程 agent。不修改代码，只输出结构化规划。

---

## 一、问题

| # | 问题 | 现状 |
|---|------|------|
| 1 | self_improve 接收两条路径的输入（逐轮 + 压缩），分别由 `submit_memory_extraction` 和 `extract_compressed_round_memory` 调用 | 需统一为 context_manage 批处理路径 |
| 2 | `review_due()` 在 `MemoryStore` 中，由 `MaintenanceScheduler` 调用，不经过 cron 模块 | 需迁移到 cron，30 秒轮询检查 |
| 3 | 碎片晋升只在 `review_due` 中做简单移动，不做碎片融合、工作记忆判断、技能创建 | 需交给 self_improve 处理 |
| 4 | 180d→permanent 的工作记忆→技能创建功能不存在 | 需新建 `skill_creater` 插件 |
| 5 | self_improve 当前无工具权限（纯 LLM 推理） | 需接入 memory_manage + skill_creater |
| 6 | `submit_memory_extraction()` 每轮调用 reasoning 模型，高成本 | 废弃，改为 context_manage 批处理时一次性调 |

---

## 二、方案

### 总体架构

```
context_manage 裁剪 N 轮
  └─ subagent_dispatch → self_improve (trigger = "context_compression")
       ├─ memory_manage: 搜索全量已有碎片 → 命中/未命中
       ├─ memory_manage: 创建/更新碎片 + data.json
       └─ 返回 candidates[]

cron review_due 任务（30s 轮询）
  └─ 检查各层 data.json → 发现到期+权重达标碎片
       └─ AgentRunner.run("self_improve", {trigger: "memory_promotion", ...})
            ├─ memory_manage: 读取下一层碎片 → 融合/移动
            ├─ skill_creater: 180d→permanent 工作记忆 → 创建技能
            └─ 返回 promotions[]
```

### 关键决策

| 决策 | 说明 |
|------|------|
| 唯一输入路径 | context_manage 裁剪时批量传入，废弃 `submit_memory_extraction` |
| 晋升触发 | 由 cron 任务（30s 轮询）发现达标碎片后唤起 self_improve |
| 工作记忆判断 | 写在 self_improve 的 AGENT.md prompt 中，LLM 自行判断 |
| 碎片融合 | 晋升时 self_improve 通过 memory_manage 读下一层全量碎片，相似则融合 |
| skill_creater | 新建插件，self_improve 只写 `agent_create` 目录 |

---

## 三、详细规划

### 步骤 1：废弃 `memory_pipeline.py` 中的 `submit_memory_extraction`

**文件**：`E:\code\kemo-agent\run\memory_pipeline.py`

- 删除 `submit_memory_extraction()` 函数（约 30 行）
- 删除 `EXISTING_CANDIDATE_LIMIT` 常量
- 删除 `_existing_candidates()` 函数
- 保留 `extract_compressed_round_memory()`（管线 2，context_manage 调用）

**注意**：需要检查 `engine.py` 中是否有调用 `submit_memory_extraction` 的地方，同步移除。

---

### 步骤 2：修改 `extract_compressed_round_memory` — 改为传入完整轮次

**文件**：`E:\code\kemo-agent\run\memory_pipeline.py`

当前函数把多轮对话的 user_text/assistant_text 扁平拼接后传给 self_improve。改为传入完整轮次结构：

```python
def extract_compressed_round_memory(...):
    # ...
    result = agent_runner.run(
        "self_improve",
        {
            "trigger": "context_compression",
            "rounds": rounds,  # 保留完整轮次结构，不再扁平拼接
        },
        cancel_event=cancel_event,
    )
    # ... 写入 MemoryStore ...
```

---

### 步骤 3：新建 `cron/review_due.py` — 记忆碎片到期检查

**新文件**：`E:\code\kemo-agent\cron\review_due.py`

```python
def scan_and_promote(root: Path, user: str, config: dict, provider_factory, cancel_event):
    """
    遍历三层临时记忆的 data.json，查找到期+权重达标的碎片。
    发现后调 self_improve 处理晋升。
    每 30 秒由 cron 任务触发一次。
    """
    store = MemoryStore(root, user, config)
    current = utc_now()
    rules = tier_rules(config)
    
    due_promotions = []
    for tier in TEMPORARY_TIERS:
        rule = rules[tier]
        for filename, meta in store.load_index(tier).items():
            expires_at = parse_time(meta.get("expires_at"))
            if expires_at is None or expires_at > current:
                continue
            weight = int(meta.get("weight", 0))
            threshold = int(rule.upgrade_threshold or 0)
            if weight >= threshold and rule.next:
                due_promotions.append({
                    "from_tier": tier,
                    "to_tier": rule.next,
                    "filename": filename,
                    "weight": weight,
                })
            else:
                # 权重不达标，直接删除
                location = MemoryLocation(tier, filename, store.fragment_path(tier, filename), True)
                store._delete_location(location)
    
    if not due_promotions:
        return {"promotions": 0}
    
    # 调 self_improve 处理晋升
    runner = AgentRunner(root, user, config=config, provider_factory=provider_factory)
    result = runner.run("self_improve", {
        "trigger": "memory_promotion",
        "promotions": due_promotions,
    }, cancel_event=cancel_event)
    
    # 执行返回的 promotions
    for promo in result.data.get("promotions", []):
        # 已有 MemoryStore._promote_location，需要增强支持融合和技能创建
        ...
    
    return {"promotions": len(due_promotions)}
```

#### 3.1 注册为 cron 任务

在 `RuntimeHost.start()` 或初始化时注册：

```python
normalize_task(
    title="记忆碎片到期晋升检查",
    prompt=json.dumps({
        "exec_mode": "function",
        "function": "cron.review_due.scan_and_promote",
    }),
    user=user,
    schedule={"type": "recurring", "interval_seconds": 30},
    source="system",
    session_id="memory_review",
)
```

**注意**：cron executor 需新增 `exec_mode: "function"` 支持（不同于 `"agent"` 和 `"subagent"`），或直接在 `scan_and_promote` 内部处理。

---

### 步骤 4：增强 `MemoryStore._promote_location` — 支持融合

**文件**：`E:\code\kemo-agent\run\memory.py`

当前 `_promote_location` 不允许目标路径已存在（会抛错）。改为支持融合模式：

```python
def _promote_location(
    self, location: MemoryLocation, target_tier: str, 
    current: datetime, *, merged_content: str | None = None
) -> None:
    if location.tier == "permanent" or target_tier not in TIERS:
        raise MemoryError(f"无效晋升：{location.tier}→{target_tier}")
    
    target_path = self.fragment_path(target_tier, location.filename)
    
    if merged_content is not None:
        # 融合模式：覆盖写入目标
        _atomic_text(target_path, merged_content)
        # 删除源
        self._delete_location(location)
        # 更新目标索引
        if target_tier != "permanent":
            target_index = self.load_index(target_tier)
            if location.filename in target_index:
                target_index[location.filename]["weight"] = 0
                target_index[location.filename]["updated_at"] = iso(current)
                self.write_index(target_tier, target_index)
        return
    
    # 原有逻辑...
    if target_path.exists():
        raise MemoryError(f"晋升目标已存在同名记忆：{target_path}")
    # ... 移动文件 ...
```

---

### 步骤 5：新建 `plugins/skill_creater` 插件

**目录**：`E:\code\kemo-agent\plugins\skill_creater/`

#### 5.1 工具定义（SKILL.md）

```json
{
  "name": "skill_creater",
  "description": "创建或更新技能文件。支持 agent_create（智能体自创技能）和 user_create（用户创建技能）两个目录。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "enum": ["create", "update", "delete"],
        "description": "操作类型"
      },
      "scope": {
        "type": "string",
        "enum": ["agent_create", "user_create", "shared"],
        "description": "技能作用域"
      },
      "name": {
        "type": "string",
        "description": "技能名称（用作目录名）"
      },
      "content": {
        "type": "string",
        "description": "技能 SKILL.md 的 Markdown 内容（create/update 时必填）"
      }
    },
    "required": ["action", "scope", "name"],
    "additionalProperties": false
  },
  "version": "1.0.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```

#### 5.2 实现要点

```python
def run(action, scope, name, *, context, content=None):
    root = Path(context["root"])
    user = context["user"]
    
    if scope == "agent_create":
        base = root / "users" / user / "user_skills" / "agent_create"
    elif scope == "user_create":
        base = root / "users" / user / "user_skills" / "user_create"
    elif scope == "shared":
        base = root / "shared_skills"
    
    skill_dir = base / name
    
    if action == "delete":
        if skill_dir.exists():
            shutil.rmtree(skill_dir)
        return {"deleted": name}
    
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(content, "utf-8")
    
    return {"action": action, "path": str(skill_dir)}
```

---

### 步骤 6：修改 `run/maintenance.py` — 移除 review_due 调用

**文件**：`E:\code\kemo-agent\run\maintenance.py`

- 第 192 行：`store.review_due(now=current)` 保留不动（`MemoryStore.review_due()` 仍可被独立调用），但 MaintenanceScheduler 不再触发它
- 第 210-218 行的 `daily_review_time` 相关逻辑移到 cron 模块
- 或者：直接删除 `_scan_user` 中所有 review_due 调用，只保留 context_review

**建议**：保留 `MemoryStore.review_due()` 方法但标记 `@deprecated`，cron 模块使用新的 `scan_and_promote()` 替代。

---

### 步骤 7：更新 `agents/self_improve/` 配置

✅ 已完成（AGENT.md、trigger.md、agent-config.json）

---

### 步骤 8：更新 `预留开发点.txt`

在 `E:\code\kemo-agent\开发临时目录\预留开发点.txt` 追加 skill_creater 接口信息。

---

## 四、应达到的效果

1. **self_improve 单一路径** — 只从 context_manage 接收批量轮次（`trigger: "context_compression"`），`submit_memory_extraction` 已移除
2. **碎片自动提取** — 每次上下文压缩后，新碎片写入 seven_days，命中碎片增量权重（每天最多+1）
3. **晋升由 cron 驱动** — 每 30 秒扫描 data.json，发现到期+达标碎片后唤起 self_improve 处理晋升
4. **碎片融合** — 7d→30d 和 30d→180d 晋升时，自动检查并融合相似碎片
5. **工作记忆→技能** — 180d→permanent 时，工作记忆自动调用 skill_creater 创建技能到 `agent_create`
6. **永久记忆不自动修改** — 仅 explicit=true 或 180d 晋升可写入，权重更新不触及 permanent
7. **skill_creater 可用** — 支持 create/update/delete，scope 区分 agent_create/user_create/shared
8. **maintenance.py 瘦身** — review_due 调用移出，MaintenanceScheduler 只保留 context_review
