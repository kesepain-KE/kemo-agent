---
type: component
project: kemo-agent
domain: archive
module: tests-测试
layer: L2
scope: project
status: archived
summary: tests — 测试目录
source: "archive/tests-测试.md"
updated: 2026-07-18
verified: partial
tags: [kemo-agent, tests, task_plan, 计划测试]
created: 2026-07-15
---
# tests — 测试目录

**状态**：✅ 测试持续扩展中（第八轮）

## 文件结构

```
tests/
├── test_cli.py                   # CLI（8 项）
├── test_config_provider.py       # 配置 + Provider（9 项）
├── test_engine_cli.py            # 引擎 + CLI（4 项）
├── test_runtime_features.py      # 运行时特性（7 项）
├── test_context_lifecycle.py     # 上下文生命周期（7 项）
├── test_subagent_runtime.py      # 子代理运行时（8 项）
├── test_memory_engine.py         # 记忆引擎（~5 项）
├── test_memory_lifecycle.py      # 记忆生命周期（~10 项）
├── test_task_plan.py             # 任务计划（20 项，新增）
```

---

## test_task_plan.py（新增 20 项）

覆盖：
- 创建/读取/列出/删除
- 合法与非法状态迁移
- Markdown 包裹 JSON 提取
- 未知工具拒绝
- 循环依赖检测
- 审批门禁
- 关键与非关键失败
- 原子写入失败不破坏原文件
- 版本冲突 / revision 自增
- 多用户隔离
- 取消 / 编辑恢复
- 进程重启恢复
- 副作用步骤不重复执行

---

## 全量

| 文件 | 项数 |
|------|------|
| test_cli.py | 8 |
| test_config_provider.py | 9 |
| test_engine_cli.py | 4 |
| test_runtime_features.py | 7 |
| test_context_lifecycle.py | 7 |
| test_subagent_runtime.py | 8 |
| test_memory_engine.py | 5 |
| test_memory_lifecycle.py | 10 |
| test_task_plan.py | 20 |
| **合计** | **78** |

全部通过。`compileall` + `git diff --check` 通过。

---

## 后续变更（2026-07-26）

### 跨平台路径兼容性修复

- `test_extended_plugins.py`、`test_skill_creater_plugin.py`：`Path(...) == Path(...)` 改为 `Path(...).samefile(...)`，适配 Windows 大小写不敏感与符号链接场景。
- `test_task_time_plugin.py`：`AGENTS.md` 引用修正为 `agents.md`（文件名大小写修正）。

### CI 环境兼容

- `ci.yml` 后端测试矩阵新增 `PYTHONUTF8: "1"`，确保 Windows 下 UTF-8 编码一致性。

### Python 3.10 兼容

- `provider/protocol/enums.py` 新增 `StrEnum` 兼容 fallback，使 CI 的 Python 3.10 矩阵可运行。
