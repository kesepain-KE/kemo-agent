# task_plan 插件 — 编程方案

> 目标：创建 `plugins/task_plan/`，将 `run/task_plan_store.py` 和 `run/task_plan_executor.py` 的运行态操作暴露为主智能体可调用的工具。
>
> 输出给编程 agent。不修改代码，只输出结构化方案。

---

## 一、问题

kemo-agent 的任务计划体系有三层，但**缺了暴露层**：

| 层 | 位置 | 能力 | 状态 |
|---|---|---|---|
| 子代理 | `agents/task_plan/` | 创建/编辑计划草案 | ✅ |
| 后端引擎 | `run/task_plan_executor.py` | 执行、批准、暂停、恢复、取消 | ✅ |
| 存储层 | `run/task_plan_store.py` | 原子读写、版本校验 | ✅ |
| **插件** | `plugins/task_plan/` | **把上述能力暴露为可调用工具** | ❌ **缺失** |

后果：主智能体执行完计划步骤后，无法标记 `step_done` 写入结果，也无法 `view`/`list`/`abort`/`approve`。`task_plan_executor.py` 的函数只被内部执行引擎调用，ToolRegistry 看不见。

对比 votx-agent 的 `plugins/task_plan/`（7 个工具：create / list / view / step_done / step_fail / abort / edit），kemo-agent 完全没有等效插件。

---

## 二、方案

在 `E:\code\kemo-agent\plugins\task_plan/` 创建插件，提供一个工具 `task_plan`，通过 `action` 参数路由到不同操作。直接对接 `PlanStore` 和 `task_plan_executor` 的现有函数，不重复实现存储逻辑。

### 工具清单

| action | 用途 | 后端调用 |
|--------|------|----------|
| `view` | 查看计划详情（步骤、状态、结果） | `PlanStore.read()` |
| `list` | 列出当前用户所有计划 | `PlanStore.list_plans()` |
| `step_done` | 标记步骤完成 + 写入 result | `PlanStore.update()` |
| `step_fail` | 标记步骤失败 + 自动暂停计划 | `PlanStore.update()` |
| `abort` | 中止计划，未完成步骤标为 skipped | `task_plan_executor.cancel_plan()` |
| `approve` | 批准 pending 计划 | `task_plan_executor.approve_plan()` |
| `pause` | 暂停 running/approved 计划 | `task_plan_executor.pause_plan()` |
| `resume` | 恢复 paused 计划 | `task_plan_executor.resume_plan()` |

> `create` 和 `edit` 不在此插件范围——它们走 `task_plan_service.py` → `agents/task_plan/` 子代理路径，已存在。

### 设计决策

- **复用 PlanStore**：不自己写 JSON 读写，直接 `from run.task_plan_store import PlanStore`
- **复用 executor 函数**：`approve_plan` / `pause_plan` / `resume_plan` / `cancel_plan` 已实现完整状态机
- **遵循 kemo-agent 插件规范**：单一入口 `tool.py:run()`，`context` 注入 `root` 和 `user`
- **`step_done` 自动推进**：标记完成后若所有步骤完成 → 计划状态自动变 `completed`
- **`step_fail` 自动暂停**：任何步骤失败 → 计划状态变 `paused`

---

## 三、详细规划

### 步骤 1：创建 `plugins/task_plan/SKILL.md`

**路径**：`E:\code\kemo-agent\plugins\task_plan\SKILL.md`

**内容要求**：

```markdown
# task_plan

管理任务计划的运行时状态：查看、标记步骤完成/失败、批准、暂停、恢复、中止。直接操作 `users/<name>/task_plan/` 中的计划文件。

## 使用原则

### 硬性规则

- 主智能体执行完计划中的一个步骤后，**必须立即调用 `step_done`** 标记完成并写入结果
- 步骤执行失败时调用 `step_fail`，计划会自动暂停
- `create` 和 `edit` 不在此工具范围——它们走 task_plan 子代理

### 必须调用的场景

| 场景 | action |
|------|--------|
| 想知道当前有哪些计划、各什么状态 | `list` |
| 查看某个计划的完整步骤、结果 | `view` |
| 刚执行完一个步骤 | `step_done` + result |
| 步骤执行失败了 | `step_fail` + error |
| 用户说"取消这个计划" | `abort` |
| 用户说"批准"/"开始执行" | `approve` |
| 用户说"暂停" | `pause` |
| 用户说"继续" | `resume` |

## 计划状态

| 状态 | 说明 |
|------|------|
| `pending` | 等待批准 |
| `approved` | 已批准，等待执行 |
| `running` | 正在执行 |
| `paused` | 已暂停（步骤失败或手动暂停） |
| `completed` | 全部步骤已完成 |
| `failed` | 无可执行步骤（依赖断裂） |
| `cancelled` | 已取消 |

## 步骤状态

| 状态 | 说明 |
|------|------|
| `pending` | 等待执行 |
| `running` | 正在执行 |
| `completed` | 已完成 |
| `failed` | 执行失败 |
| `skipped` | 已跳过（计划中止时） |
| `cancelled` | 已取消 |

## 参数说明

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `action` | string | 是 | `view` / `list` / `step_done` / `step_fail` / `abort` / `approve` / `pause` / `resume` |
| `plan_id` | string | 条件 | `view` / `step_done` / `step_fail` / `abort` / `approve` / `pause` / `resume` 需要。不传 `view` 或 `abort` 时自动使用活跃计划 |
| `step_id` | string | 条件 | `step_done` / `step_fail` 需要 |
| `result` | string | 否 | `step_done` 的执行结果摘要（建议 200 字以内） |
| `error` | string | 条件 | `step_fail` 的错误描述 |

## 返回字段

| 字段 | 说明 |
|------|------|
| `ok` | 操作是否成功 |
| `plan` | 操作后的完整计划（`view` / `step_done` / `step_fail` / `approve` / `pause` / `resume`） |
| `plans` | 计划列表（`list`） |
| `total` | 计划总数（`list`） |
| `plan_id` / `deleted` | 删除结果（预留） |
| `error` | `ok=false` 时的失败原因 |

## Tool

```json
{
  "name": "task_plan",
  "description": "管理任务计划的运行时状态：查看计划详情、列出所有计划、标记步骤完成/失败、批准/暂停/恢复/中止计划。主智能体执行完计划步骤后必须调用 step_done 或 step_fail。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "enum": ["view", "list", "step_done", "step_fail", "abort", "approve", "pause", "resume"],
        "description": "view=查看计划，list=列出全部，step_done=标记步骤完成，step_fail=标记步骤失败，abort=中止计划，approve=批准计划，pause=暂停计划，resume=恢复计划"
      },
      "plan_id": {
        "type": "string",
        "description": "计划 ID（如 plan_a8712fb9）。view/abort 不传时自动使用活跃计划。step_done/step_fail/approve/pause/resume 必传。"
      },
      "step_id": {
        "type": "string",
        "description": "步骤 ID（如 step_1）。step_done/step_fail 必传。"
      },
      "result": {
        "type": "string",
        "description": "步骤执行结果摘要（可选，建议 200 字以内）。仅 step_done 使用。"
      },
      "error": {
        "type": "string",
        "description": "错误描述。仅 step_fail 使用。"
      }
    },
    "required": ["action"],
    "additionalProperties": false
  },
  "version": "1.0.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```

### 步骤 2：创建 `plugins/task_plan/tool.py`

**路径**：`E:\code\kemo-agent\plugins\task_plan\tool.py`

**关键要求**：

1. 函数签名：`def run(*, action: str, plan_id: str = "", step_id: str = "", result: str = "", error: str = "", context: dict[str, Any]) -> dict[str, Any]`

   - `context["root"]` — 项目根目录（Path 字符串）
   - `context["user"]` — 当前用户名

2. 导入：
   ```python
   from pathlib import Path
   from typing import Any
   from run.task_plan_store import PlanStore, PlanNotFoundError, PlanError
   from run.task_plan_executor import (
       approve_plan, pause_plan, resume_plan, cancel_plan,
       get_plan, list_plans,
   )
   from run.users import validate_user_name
   ```

3. 核心逻辑：

   ```python
   def _find_active_plan(store: PlanStore) -> dict[str, Any] | None:
       """找到第一个非终态计划（pending/approved/running/paused）"""
       for plan in store.list_plans():
           if plan.get("status") in ("pending", "approved", "running", "paused"):
               return plan
       return None

   def _auto_complete_check(store: PlanStore, plan_id: str) -> dict[str, Any]:
       """检查计划是否全部步骤完成，若是则标记为 completed"""
       plan = store.read(plan_id)
       steps = plan.get("steps", [])
       terminal = {"completed", "failed", "skipped", "cancelled"}
       if all(s.get("status") in terminal for s in steps):
           all_completed = all(s.get("status") == "completed" for s in steps)
           if all_completed:
               return store.update(plan_id, lambda p: {**p, "status": "completed"})
       return plan
   ```

4. `step_done` 逻辑：
   ```python
   def _step_done(store, plan_id, step_id, result_text):
       plan = store.read(plan_id)
       # 找到步骤并标记
       found = False
       def mark(p):
           for s in p["steps"]:
               if s["step_id"] == step_id:
                   s["status"] = "completed"
                   if result_text:
                       s["result"] = result_text
                   s["finished_at"] = _now()
                   nonlocal found
                   found = True
                   break
           p["current_step"] = step_id
           return p
       plan = store.update(plan_id, mark)
       if not found:
           raise ValueError(f"步骤不存在: {step_id}")
       # auto-complete check
       return _auto_complete_check(store, plan_id)
   ```

5. `step_fail` 逻辑：
   ```python
   def _step_fail(store, plan_id, step_id, error_text):
       def mark(p):
           for s in p["steps"]:
               if s["step_id"] == step_id:
                   s["status"] = "failed"
                   s["error"] = error_text
                   s["finished_at"] = _now()
                   break
           p["status"] = "paused"
           p["current_step"] = step_id
           return p
       return store.update(plan_id, mark)
   ```

6. `_now()` 辅助函数：
   ```python
   from datetime import datetime, timezone
   def _now() -> str:
       return datetime.now(timezone.utc).isoformat()
   ```

7. `_result()` 辅助函数（与 `task_time/tool.py` 风格一致）：
   ```python
   def _result(ok: bool, **fields: Any) -> dict[str, Any]:
       return {"ok": ok, **fields}
   ```

8. 主 `run()` 函数路由结构：
   ```python
   def run(
       *,
       action: str,
       plan_id: str = "",
       step_id: str = "",
       result: str = "",
       error: str = "",
       context: dict[str, Any],
   ) -> dict[str, Any]:
       # 获取 root 和 user
       root = Path(str(context["root"])).resolve()
       user = validate_user_name(str(context["user"]))
       store = PlanStore(root, user)

       # list
       if action == "list":
           plans = store.list_plans()
           return _result(True, plans=plans, total=len(plans))

       # 需要 plan_id 的操作：先解析
       if not plan_id:
           active = _find_active_plan(store)
           if not active:
               return _result(False, error="没有活跃计划，请指定 plan_id")
           plan_id = active["plan_id"]

       # view
       if action == "view":
           try:
               plan = store.read(plan_id)
               return _result(True, plan=plan)
           except PlanNotFoundError:
               return _result(False, error=f"计划不存在: {plan_id}")

       # step_done
       if action == "step_done":
           if not step_id:
               return _result(False, error="step_done 需要 step_id")
           try:
               plan = _step_done(store, plan_id, step_id, result)
               return _result(True, plan=plan)
           except (PlanNotFoundError, PlanError, ValueError) as exc:
               return _result(False, error=str(exc))

       # step_fail
       if action == "step_fail":
           if not step_id or not error:
               return _result(False, error="step_fail 需要 step_id 和 error")
           try:
               plan = _step_fail(store, plan_id, step_id, error)
               return _result(True, plan=plan)
           except (PlanNotFoundError, PlanError) as exc:
               return _result(False, error=str(exc))

       # abort
       if action == "abort":
           try:
               plan = cancel_plan(root, user, plan_id)
               return _result(True, plan=plan)
           except Exception as exc:
               return _result(False, error=str(exc))

       # approve
       if action == "approve":
           try:
               plan = approve_plan(root, user, plan_id)
               return _result(True, plan=plan)
           except Exception as exc:
               return _result(False, error=str(exc))

       # pause
       if action == "pause":
           try:
               plan = pause_plan(root, user, plan_id)
               return _result(True, plan=plan)
           except Exception as exc:
               return _result(False, error=str(exc))

       # resume
       if action == "resume":
           try:
               plan = resume_plan(root, user, plan_id)
               return _result(True, plan=plan)
           except Exception as exc:
               return _result(False, error=str(exc))

       return _result(False, error=f"未知 action: {action}")
   ```

### 步骤 3：创建 `plugins/task_plan/version.json`

**路径**：`E:\code\kemo-agent\plugins\task_plan\version.json`

```json
{
  "version": "1.0.0"
}
```

与其他插件（如 `task_time` 有 `version.json`）保持一致。若大多数插件没有此文件则跳过此步——请编程 agent 先检查现有插件的实际惯例。

### 步骤 4：创建 `plugins/task_plan/__init__.py`

**路径**：`E:\code\kemo-agent\plugins\task_plan\__init__.py`

空文件，仅用于 Python 包识别。

---

## 四、应达到的效果

1. **主智能体可获得 `task_plan` 工具** — 插件被 `manifest.py` 自动发现并注册
2. **`step_done` 写入结果** — 执行 `task_plan step_done plan_id=plan_xxx step_id=step_1 result="已完成：xxx"` 后，JSON 文件中的步骤 status 变为 `completed`，result 字段被填充
3. **全部步骤完成后自动 `completed`** — 当所有步骤都是终态（completed/failed/skipped/cancelled）且全部为 completed 时，计划状态自动变为 `completed`
4. **`step_fail` 自动暂停** — 步骤失败后计划状态自动变为 `paused`
5. **`view` 返回完整计划** — 含所有步骤及其状态、结果、错误
6. **`list` 列出全部计划** — 不限状态，全部返回
7. **`approve`/`pause`/`resume`/`abort`** — 走 `task_plan_executor` 的已有状态机，行为一致
8. **不传 plan_id 时自动定位活跃计划** — `view` 和 `abort` 支持省略 plan_id

---

## 五、与现有模块的关系

| 模块 | 关系 | 说明 |
|------|------|------|
| `agents/task_plan/` | 互补 | 子代理负责 create/edit，插件负责运行时标记 |
| `run/task_plan_store.py` | 依赖 | 直接使用 PlanStore 做读写 |
| `run/task_plan_executor.py` | 依赖 | 复用 approve/pause/resume/cancel |
| `run/task_plan_service.py` | 无关 | create/edit 走子代理路径，不经过此插件 |
| `plugins/task_time/` | 平级 | 同为运行态管理插件，风格参考 |

---

## 六、注意事项

1. **不重复实现存储** — 禁止在 `tool.py` 中自己 `json.loads`/`json.dumps`，必须用 `PlanStore`
2. **不要覆盖 `create`/`edit`** — 这两个走子代理路径，插件不管
3. **`step_done` 要做 auto-complete** — 全步骤终态时自动标记计划为 `completed`
4. **异常要友好返回** — `PlanNotFoundError` → `{"ok": false, "error": "计划不存在: xxx"}`，不让异常抛到主智能体
5. **`context` 注入校验** — 与其他插件一致：`context["root"]` + `context["user"]` 必须存在
6. **`validate_user_name` 校验** — 与其他插件一致，防止路径穿越
