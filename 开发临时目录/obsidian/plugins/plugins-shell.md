---
type: component
project: kemo-agent
domain: plugins
module: plugins-shell
layer: L2
scope: project
status: active
summary: plugins/shell/ — Shell 命令执行工具（六项优化：去 action、内置命令、shell_type、locale、chain_timeout_mode）
source: plugins/shell/tool.py
updated: 2026-07-26
verified: true
tags: [kemo-agent, plugins, tool, shell, shell_type, locale, chain_timeout_mode]
---
# plugins/shell/ — Shell 命令执行工具

`E:\code\kemo-agent\plugins\shell\`

## 功能

执行系统 shell 命令，返回标准输出和标准_error。

- command: 要执行的命令
- timeout: 超时秒数
- working_dir: 工作目录

## 工具定义

- name: `shell`
- 作为受控基础工具，仅在没有等价 Skill/工具时使用
- entrypoint: `tool.py:run`

## 六项优化详情（2026-07-21）

1. **去掉 action 冗余** — 简化参数模型
2. **新增内置命令** — 支持更多内置命令操作
3. **shell_type** — 可指定 shell 类型（pwsh/bash 等）
4. **locale 编码** — 支持指定输出编码
5. **chain_timeout_mode** — 命令链（&&/||/;）的超时模式
6. **指令型 SKILL.md** — 从参数型 SKILL.md 改为指令型引导

## 取消感知命令执行（2026-07-26）

Shell 插件新增完整的取消感知（cancel-aware）子进程管理：

1. 当 `context` 中包含 `cancel_event` 时，使用 `cancellable_subprocess_kwargs()` 将子进程放入独立进程组
2. 使用 `Popen` + 轮询模式替代 `subprocess.run`，在命令执行期间实时检查取消事件
3. 取消时调用 `terminate_process_tree(process)` 终止整个进程树
4. 命令链（`&&`/`||`/`;`）中每段命令前检查取消事件
5. 返回结果包含 `cancelled: true` 标识

依赖 `run/process_utils.py` 的 `cancellable_subprocess_kwargs`、`hidden_subprocess_kwargs`、`terminate_process_tree`。

## 相关笔记

- [[plugins-manifest]]
- [[run-tools]]
- [[run-process_utils]]