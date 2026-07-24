---
type: module
project: kemo-agent
domain: infra
module: infra-ci-cd
layer: L2
scope: project
status: active
summary: CI/CD 流水线 — 自动测试、安全扫描、版本校验
source: ".github/workflows/ci.yml, .github/workflows/security.yml"
updated: 2026-07-26
verified: true
tags: [kemo-agent, infra, CI, CD, GitHub Actions, 测试, 安全]
---
# CI/CD 流水线

GitHub Actions 工作流，位于 `.github/workflows/`。

## ci.yml — 持续集成

### 触发条件

`push`、`pull_request`、`workflow_dispatch` 手动触发。

### 权限

`contents: read`（只读检出）。

### 并发控制

按 `workflow + ref` 分组，新运行自动取消同一组进行中的运行。

### 任务一览

#### 1. Repository checks（仓库检查）

| 属性 | 值 |
|------|-----|
| 运行环境 | ubuntu-latest |
| 超时 | 5 分钟 |
| 步骤 | 检出 → Python 3.13 → 版本一致性检查 → 仓库卫生检查 |

**检查内容**：
- `check_versions.py`：校验 `version.json`、`package.json`、`package-lock.json`、`readme.md` 中的版本号是否一致
- `check_repository_hygiene.py`：确保没有环境凭据、用户数据、运行日志、私钥等敏感文件被 Git 跟踪

#### 2. Backend tests（后端测试矩阵）

| 属性 | 值 |
|------|-----|
| 运行环境 | ubuntu-latest + windows-latest |
| Python 版本 | 3.10、3.13 |
| 超时 | 20 分钟 |
| 缓存 | pip 缓存，依赖 `requirements.txt` + `requirements-dev.txt` |
| 步骤 | 检出 → Python 多版本 → pip install -r requirements-dev.txt → unittest discover |

#### 3. Frontend tests & build（前端测试与构建）

| 属性 | 值 |
|------|-----|
| 运行环境 | ubuntu-latest |
| Node 版本 | 22 |
| 超时 | 15 分钟 |
| 工作目录 | `web/frontend` |
| 缓存 | npm 缓存 |
| 步骤 | 检出 → Node.js → npm ci → npm test → npm run build |

### 架构图

```
push / PR / manual
       │
       ▼
  ┌────────────────┐
  │ Repository     │  Python 3.13, 5 min
  │ checks         │  check_versions + check_repository_hygiene
  └───────┬────────┘
          │
  ┌───────┴───────────────────────┐
  │ Backend tests (matrix)        │
  │ ubuntu + windows, 3.10 + 3.13 │  20 min, pip cache
  └───────┬───────────────────────┘
          │
  ┌───────┴──────────┐
  │ Frontend tests   │
  │ npm ci → test →  │  Node 22, 15 min, npm cache
  │ npm run build    │
  └──────────────────┘
```

## security.yml — 安全扫描

### 触发条件

`push`、`pull_request`、`workflow_dispatch`、每周一 `02:17 UTC` 定时调度。

### 权限

`contents: read`。

### 任务

#### Secret scan

| 属性 | 值 |
|------|-----|
| 运行环境 | ubuntu-latest |
| 超时 | 10 分钟 |
| 检出深度 | `fetch-depth: 0`（全历史检出，扫描历史提交中泄露的密钥） |
| 工具 | gitleaks-action |
| 配置 | 禁用自动评论、禁用上传构件、启用摘要显示 |

### 变更记录

| 旧版 | 新版 |
|------|------|
| 无 CI/CD 流水线 | 新增 ci.yml（矩阵测试 + 仓库检查 + 前端构建） |
| 无安全扫描 | 新增 security.yml（Gitleaks 密钥扫描） |
| ci.yml 中脚本路径指向 `scripts/` | 脚本路径改为 `.github/scripts/`，与 CI 脚本就近存放 |
| actions/checkout v4.4.0 | actions/checkout v5.1.0（ci.yml + security.yml 统一升级） |
| actions/setup-python v5.6.0 | actions/setup-python v6.3.0（ci.yml 后端步骤升级） |
| actions/setup-node v4.4.0 | actions/setup-node v5.0.0（ci.yml 前端步骤升级） |
| 后端测试无 PYTHONUTF8 | 后端测试矩阵新增 `PYTHONUTF8: "1"` 环境变量，确保 Windows 下 UTF-8 编码兼容 |
