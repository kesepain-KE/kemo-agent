# task_plan

管理当前用户任务计划的运行时状态：查看、列出、修正计划、重试或重置失败步骤、标记步骤完成或失败，以及批准、暂停、恢复和中止计划。计划文件由 `PlanStore` 管理，位于 `users/<user>/task_plan/`。

## 创建计划

创建使用 `task_plan` 子智能体，不是本工具的 action。最小调用是：

```json
{"action":"call","agent":"task_plan","input":{"action":"create","goal":"用户想完成的目标","context":"相关背景与验收要求"}}
```

上例是 `subagent_dispatch` 的参数：外层 `action=call` 表示分派，内层 `input.action=create`
表示创建计划，不能省略。默认同步完成生成、校验与持久化，无需改成后台重试。
工具权限、技能和是否自动批准由框架注入，不需要手填。

## 使用原则

### 硬性规则

- 主智能体在“单轮连续执行”模式下执行完一个计划步骤后，必须立即调用 `step_done` 并写入结果摘要；根据返回的 `next_step` 继续执行，直到计划完成或暂停。
- 步骤执行失败时必须调用 `step_fail`；失败步骤会被记录，计划自动暂停。
- 当控制提示明确声明步骤状态由框架执行器维护（executor-managed）时，不得调用 `step_done` 或 `step_fail`，避免重复写入。
- `create` 不属于本工具；复杂的自然语言重编排仍优先走 `task_plan` 子代理。`edit` 只用于带 revision 的确定性字段修正。
- `edit`、`revise_remaining`、`retry_step`、`reset_step`、`skip_step` 必须携带最近一次读取到的 `revision`，版本变化时重新 `view` 后再操作。
- 已完成步骤不得编辑、重置或删除。`retry_step` 会把 failed/cancelled 步骤恢复为 pending；只有计划自身 `auto_accept=true` 或用户配置 `task_plan.auto_retry_on_fix=true` 时，paused/failed 计划才自动恢复为 approved。`reset_step` 只重置步骤，永远不改变计划状态。
- 普通 `edit` 在计划状态为 `running` 时会被拒绝；运行中需要调整未开始步骤时，使用 `revise_remaining`。
- 计划运行中可以调用 `revise_remaining` 调整尚未开始的剩余步骤；已完成、运行中和失败步骤不能删除或改字段，删除 pending 步骤采用物理删除。修改成功后会继续执行；`retry_failed=true` 才会把 failed 步骤重置为 pending。
- `skip_step` 只允许显式跳过 pending 的非关键步骤；它会持久化为 `skipped`，该状态满足后续步骤的依赖，但不会把步骤物理删除。关键步骤不得跳过；`revise_remaining` 删除 pending 步骤仍使用物理删除。
- `task_plan` 是管理工具，不能被写成计划中的执行步骤。

### 必须调用的场景

| 场景 | action |
|---|---|
| 查看当前计划或某个计划的完整信息 | `view` |
| 列出当前对话空间的计划 | `list` |
| 修正计划标题、描述或未完成步骤的执行字段 | `edit` |
| 在计划运行中重排、插入或删除剩余步骤 | `revise_remaining` |
| 修正失败原因后重试 failed/cancelled 步骤 | `retry_step` |
| 只重置 failed/cancelled 步骤但不激活计划 | `reset_step` |
| 刚执行完一个步骤 | `step_done` |
| 步骤执行失败 | `step_fail` |
| 用户批准计划 | `approve` |
| 用户暂停计划 | `pause` |
| 用户继续计划 | `resume` |
| 用户取消计划 | `abort` |

## 状态说明

计划状态包括 `pending`、`approved`、`running`、`paused`、`completed`、`failed`、`cancelled`。步骤状态包括 `pending`、`running`、`completed`、`failed`、`skipped`、`cancelled`。旧数据中的步骤级 `paused` 在读取时归一为 `pending` 并清空旧执行结果。执行器取消或生成器关闭时，计划由 finally 收束为 paused，当前 running 步骤恢复为 pending；resume 会继续领取它。计划 abort 时当前和未开始步骤均为 cancelled。`skipped` 步骤视为依赖已满足。

`resume` 将暂停计划恢复为 `approved`，再由后台调度器、CLI 或 Web/App 起跑入口中的唯一执行器原子领取为 `running`。`abort` 复用核心执行器的取消状态机：计划变为 `cancelled`，尚未执行的步骤也变为 `cancelled`。

## 返回字段

- 所有动作返回 `ok`。
- `view` 和状态变更动作成功时返回完整 `plan`。
- `list` 返回 `plans` 与 `total`。
- `edit`、`retry_step`、`reset_step` 返回 `activated` 与 `reason`；`activated=true` 只表示计划已恢复为 approved，仍由执行器通过 approved→running 的原子领取开始执行。
- 失败时返回 `error`，不会把普通计划状态错误抛给主智能体。
