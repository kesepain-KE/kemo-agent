---
type: component
project: kemo-agent
domain: cli
module: setup-wizard
layer: L2
scope: project
status: active
summary: setup.py — 首次部署引导脚本（Python 检查/依赖安装/.env 配置/前端构建/用户创建）
source: "cli/setup-wizard.md"
updated: 2026-07-22
verified: true
tags: [kemo-agent, cli, 部署, setup, 引导, 首次运行]
---
# setup.py — 首次部署引导脚本

`E:\code\kemo-agent\setup.py`

## 概览

首次部署向导，引导用户完成 kemo-agent 的初始安装。6 步流程，支持交互式和 `--yes` 静默模式。

## 步骤

| 步骤 | 说明 |
|------|------|
| 1. Python 环境 | 检查 Python >= 3.10 |
| 2. 安装依赖 | `pip install -r requirements.txt`（可 `--skip-deps` 跳过） |
| 3. 环境变量 | 从 `.env.example` 创建 `.env`，可选引导填写 API Key |
| 4. 前端构建 | `npm install && npm run build`（可 `--skip-web` 跳过） |
| 5. 创建用户 | 检查已有用户，无则引导创建 |
| 6. 目录检查 | 确保 `tmp/` 和 `users/` 存在 |

## 命令行参数

| 参数 | 说明 |
|------|------|
| `--yes` / `-y` | 跳过交互，全部使用默认值 |
| `--skip-deps` | 跳过 pip install |
| `--skip-web` | 跳过前端构建 |

## 环境变量引导

- KEMO_API_KEY（getpass 不回显）
- KEMO_BASE_URL（默认 127.0.0.1:8741）
- WEB_USERNAME / WEB_PASSWORD

## 与其他入口的关系

- `python setup.py` → 首次部署
- `python start_web.py` → 启动 Web 服务
- `python user_create.py` → 用户管理
- `python update.py --check` → 检查更新

## 相关笔记

- [[cli-总览]]
- [[user-create]]
- [[run-update]]
