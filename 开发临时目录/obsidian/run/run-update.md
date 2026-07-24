---
type: component
project: kemo-agent
domain: run
module: run-update
layer: L2
scope: project
status: active
summary: update.py + update/ — 模块化板块更新系统（全平台）
source: "run/run-update.md"
updated: 2026-07-22
verified: true
tags: [kemo-agent, run, 更新, 部署, 板块化, 模块化]
---

# update.py + update/ — 模块化板块更新系统

`E:\code\kemo-agent\update.py` + `E:\code\kemo-agent\update\`

## 概览

全平台更新脚本重构为模块化板块架构。`update.py` 作为调度器，按板块（core/agents/plugins/web）分别执行独立更新模块。

## 板块定义

```python
MODULES = {
    "core": ("核心引擎", "update.core"),
    "agents": ("智能体系统", "update.agents"),
    "plugins": ("插件生态", "update.plugins"),
    "web": ("Web 服务", "update.web"),
}
```

## 核心流程

1. 加载本地和远程 version.json
2. 按模块比对版本号（支持 `--module` 指定单板块）
3. 克隆远程仓库（`--depth 1`）
4. 创建备份到 `.backups/update-<timestamp>/`
5. `run_modules()` 逐板块执行 `update()` 函数
6. 汇总打印每个板块状态（ok/skipped/partial/failed）
7. `core` 板块更新后执行用户骨架迁移和依赖刷新
8. `web` 板块更新后执行前端构建

## 子模块

| 文件 | 职责 |
|------|------|
| `update/__init__.py` | 模块包声明，导出 core/agents/plugins/web |
| `update/_utils.py` | 共享工具函数（UpdateError, run, sync_directory, compare_versions 等） |
| `update/core.py` | 核心引擎同步（框架文件、config、global_knowledge） |
| `update/agents.py` | 智能体系统同步 |
| `update/plugins.py` | 插件生态同步 |
| `update/web.py` | Web 服务同步 |

## 命令行参数

| 参数 | 说明 |
|------|------|
| `--module` | 更新板块（all/core/agents/plugins/web，默认 all） |
| `--check` | 仅检查版本 |
| `--force` | 版本相同时强制重装 |
| `--yes` / `-y` | 默认确认所有提示 |
| `--dry-run` | 仅展示计划操作 |
| `--skip-web-build` | 跳过前端构建 |
| `--skip-deps` | 跳过 pip install |
| `--repo-url` | 自定义 Git 仓库 |
| `--branch` | 自定义 Git 分支 |
| `--remote-version-url` | 自定义远程版本检查 URL |

## 版本号体系

`version.json` 简化为 4 个板块版本号：

```json
{
  "version": "0.1.0",
  "components": {
    "core": {"version": "0.1.0", "description": "核心引擎"},
    "agents": {"version": "0.1.0", "description": "子代理系统"},
    "plugins": {"version": "0.1.0", "description": "工具插件生态"},
    "web": {"version": "0.1.0", "description": "Web 前端+后端"}
  }
}
```

`version_for_module(document, module)` 读取对应板块版本号，`module="all"` 读顶层 `version`。

## 变更记录

| 旧版 | 新版 |
|------|------|
| 单文件全量更新 | 模块化板块更新 |
| 全量同步所有文件 | 按 `--module` 选择板块 |
| version.json 含 7+ components + shared_components | 简化为 4 个核心板块 |
| 工具函数内联在 update.py | 提取到 update/_utils.py |
| config/global_knowledge 交互式选择 | 移至 update/core.py |
| 无板块汇总 | `print_module_summary()` 输出每板块状态 |
| `--check` 只比较总版本号 | `print_version_report()` 按板块比较 |

## 相关笔记

- [[run-总览]]
- [[cli-总览]]
- [[config-总览]]（version.json）
