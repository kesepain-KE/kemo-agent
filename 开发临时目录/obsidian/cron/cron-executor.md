---
type: component
project: kemo-agent
domain: cron
module: cron-executor
layer: L2
scope: project
status: active
summary: cron/executor.py — 执行适配器（子进程隔离 + 安全校验 + 健康状态写回）
source: "cron/cron-executor.md"
updated: 2026-07-26
verified: true
tags: [kemo-agent, cron, 执行器, 原子领取, 系统任务, 子进程, 安全]
---
# cron/executor.py — 执行适配器

`E:\code\kemo-agent\cron\executor.py`

## 概览

Cron → Run 直接调用适配器。**不经过 `cli.py`**。支持普通用户任务和系统任务两种执行路径。

## 函数

### execute_cron_task

```python
def execute_cron_task(
    *,
    root, user, task_id,
    config=None, provider_factory, tool_registry_factory,
    cancel_event=None,
    system_task: dict | None = None,
) -> dict
```

**流程**：
- 如果 `system_task` 非空 → 走 `_execute_system_task()`
- 否则 → 原子领取用户任务 → `handle_request()` → 持久化结果

### _execute_system_task

系统任务分发器，根据 `action` 路由：

| action | 执行函数 | 说明 |
|--------|---------|------|
| memory_promotion | `_execute_memory_promotion()` | 调用 `cron.review_due.scan_and_promote` |
| periodic_scan | `_execute_memory_review(trigger="periodic_scan")` | 调用 `memory_temporary_important` 子代理 |
| daily_consolidate | `_execute_memory_review(trigger="daily_consolidate")` | 调用 `memory_temporary_important` 子代理 |
| perception_update | `_execute_perception_update()` | 全局感知模块刷新 |
| expand_update | `_execute_expand_update()` | 全局+共享+用户三层拓展刷新 |

## 重要变更（2026-07-26）

### 1. 模块更新从进程内改为子进程隔离

旧版：通过 `importlib.util.spec_from_file_location` 在当前进程动态加载更新脚本
新版：在独立 Python 子进程中执行更新脚本

```python
def _run_module_updater(
    update_path: Path, module_root: Path, *, timeout: float
) -> dict[str, Any]
```

- 通过 `subprocess.run([sys.executable, "-I", "-c", _MODULE_UPDATE_RUNNER, ...])` 启动
- `_MODULE_UPDATE_RUNNER` 是嵌入的 Python 代码字符串，在子进程中执行
- 子进程使用隔离模式（`-I`），与主进程环境隔离
- 输出通过 `__KEMO_MODULE_UPDATE_RESULT__=` 前缀协议传回
- 失败时返回 `{ok: false, reason, exception_type}`
- 超时由 `task_cron_system.module_update_timeout` 控制（默认 120s）

### 2. 新增 `_MODULE_UPDATE_RUNNER` 嵌入式 runner

独立的 Python 代码段，在子进程中执行：

1. 通过 `importlib.util.spec_from_file_location` 加载目标更新脚本
2. 优先调用 `update()`，兼容 `main()`
3. 返回 `False`、`{ok: false}`、或失败状态均视为失败
4. 异常时通过 `__KEMO_MODULE_UPDATE_RESULT__=` 前缀协议返回错误信息
5. 成功时返回 `{ok: true}`

### 3. 符号链接/目录联接安全检查

```python
def _is_link_or_junction(path: Path) -> bool
```

- 检查路径是否为符号链接或 Windows 目录联接
- 应用在：模块根目录、manifest 文件、update 路径的每个组件
- 拒绝任何经过符号链接的路径，防止路径穿越攻击

### 4. 健康状态原子写回

```python
def _record_module_health(manifest_path, category, *, healthy: bool) -> None
```

- 更新 `sense.json` 的 `health`（或 `expand.json` 的 `input_health`）为 `正常`/`异常`
- 成功时同步更新 `recent_update` 为北京时间
- 使用 `_atomic_write_json` 原子写入（先写 `.tmp` 再 `os.replace`）

### 5. 拓展更新从全局扩展到三层

```python
def _execute_expand_update(root, user, *, config, cancel_event) -> dict
```

- `user == "__system__"`：先刷新 `global_expand/`（前缀 `global/`），再刷新 `shared_expand/`（前缀 `shared/`），合并为 `scope=global_shared`
- 其他用户：只刷新 `users/<user>/expand/`，`scope=user`
- 用户目录安全检查：拒绝符号链接、拒绝路径越界 `users/`

### 6. 取消支持

- `cancel_event` 传入 `_update_modules`，在每轮模块迭代开始时检查
- 取消时返回 `{status: "cancelled", updated, failed, errors}`
- 子进程不会主动 kill，依赖 `subprocess.run` 的 timeout 机制

### 7. 权限与超时配置

新增配置字段：

| 字段 | 位置 | 默认值 | 说明 |
|------|------|--------|------|
| `module_update_timeout` | `task_cron_system` | 120 | 每个采集脚本的独立子进程超时（秒），上限 3600 |

## _update_modules 返回值

```python
{
    "status": "completed" | "partial" | "failed" | "skipped" | "cancelled",
    "category": "sense" | "expand",
    "updated": ["module1", "module2"],
    "failed": ["module3"],
    "errors": [
        {"module": "module3", "reason": "...", "exception_type": "..."}
    ]
}
```

- `status="cancelled"`：用户取消，保留已更新列表
- `status="partial"`：部分更新成功、部分失败
- `status="skipped"`：模块目录不存在或无合法模块

## 系统任务路由总表

| action | 执行函数 | 执行身份 | 说明 |
|--------|---------|---------|------|
| memory_promotion | `_execute_memory_promotion()` | per-user | 记忆到期晋升 |
| periodic_scan | `_execute_memory_review(trigger="periodic_scan")` | per-user | 临时重要记忆巡检 |
| daily_consolidate | `_execute_memory_review(trigger="daily_consolidate")` | per-user | 每日记忆整理 |
| perception_update | `_execute_perception_update()` | `__system__` | 全局感知模块数据采集 |
| expand_update | `_execute_expand_update()` | `__system__` + per-user | 三层拓展刷新（先全局+共享，再按用户隔离） |

## 变更记录

| 旧版 | 新版 |
|------|------|
| 进程内 `importlib` 动态加载更新脚本 | 独立子进程隔离执行，嵌入 runner 代码 |
| 无符号链接检查 | `_is_link_or_junction` 全路径安全检查 |
| 无健康状态写回 | `_record_module_health` 原子写回 `正常`/`异常` |
| 只刷新 `global_expand/` | 先 `global_expand/` 再 `shared_expand/`，再按用户隔离刷新 |
| 无取消支持 | `cancel_event` 检查 + cancelled 状态 |
| 普通的文件写入 | 原子写入（`.tmp` → `os.replace`） |
| 无超时配置 | `module_update_timeout` 可配置，默认 120s |
