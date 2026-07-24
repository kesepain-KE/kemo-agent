---
type: module
project: kemo-agent
domain: infra
module: infra-repo-scripts
layer: L2
scope: project
status: active
summary: 仓库辅助脚本与基础设施 — 卫生检查、版本一致性、开发依赖、.gitignore
source: ".github/scripts/check_repository_hygiene.py, .github/scripts/check_versions.py, requirements-dev.txt, .gitignore"
updated: 2026-07-26
verified: true
tags: [kemo-agent, infra, 卫生, 版本, 开发依赖, gitignore]
---
# 仓库辅助脚本与基础设施

## .github/scripts/check_repository_hygiene.py — 仓库卫生检查

确保不会意外跟踪运行时数据或敏感文件。

### 检查规则

| 类别 | 拦截对象 |
|------|---------|
| 环境变量 | `.env`、所有 `.env.*`（除 `.env.example`） |
| 消息映射 | `config/message_config.json` |
| 私钥证书 | `.key`、`.p12`、`.pfx`、`.pem` |
| 备份目录 | `.backups/`、`.playwright-cli/`、`.pytest_cache/`、`.ruff_cache/` |
| 运行日志 | `cron/task_cron_system/log/` |
| 开发工作区 | `开发临时目录/` |
| 用户数据 | `users/`（除 `.gitkeep` 和 `_template/`） |
| 外部消息 | `message/out/`（除 `.gitkeep`） |
| 临时数据 | `tmp/`（除 `.gitignore` 和 `.gitkeep`） |

### 白名单

仅以下运行时文件允许被跟踪：
- `message/out/.gitkeep`
- `tmp/.gitignore`
- `tmp/.gitkeep`
- `users/.gitkeep`

### 工作方式

调用 `git ls-files` 列出所有被跟踪文件，逐条匹配阻断规则。有阻断项时返回 exit code 1 并列出原因。

## .github/scripts/check_versions.py — 版本一致性检查

确保 `version.json` 中定义的版本号与项目各处的版本声明一致。

### 校验点

| 检查项 | 说明 |
|--------|------|
| version.json 根版本 | 合法 SemVer |
| version.json components | 每个组件版本为合法 SemVer |
| web/frontend/package.json | 版本 == version.json 根版本 |
| web/frontend/package-lock.json | version + packages[""].version 均一致 |
| readme.md 版本徽章 | badge 指向当前版本 |
| readme.md 版本文本 | 文本声明匹配当前版本 |
| 发布标签（可选） | --tag 或 GITHUB_REF_NAME 与 v{version} 一致 |

### 工具链

- `version.json` 定义项目和各板块版本号
- CI 流水线中自动执行此脚本，阻止版本不一致的提交合并

## requirements-dev.txt — 开发依赖

```text
# Test-only dependencies. Runtime dependencies remain in requirements.txt.
-r requirements.txt

httpx>=0.27.0,<0.29.0
```

- 引用 `requirements.txt`（运行时依赖）
- 额外仅测试依赖：`httpx`

## requirements.txt — 运行时依赖

| 依赖 | 版本 | 用途 |
|------|------|------|
| fastapi | >=0.110.0,<0.140.0 | Web 后端框架 |
| itsdangerous | >=2.2.0,<3.0.0 | Web Session 签名 |
| PyYAML | >=6.0.0,<7.0.0 | YAML 配置解析 |
| python-multipart | >=0.0.20,<0.1.0 | 文件上传表单解析 |
| pydantic | >=2.0.0,<3.0.0 | 数据模型校验 |
| tzdata | >=2024.1,<2027.0 | 时区数据库（Windows 兼容） |
| uvicorn | >=0.27.0,<0.52.0 | ASGI 服务器 |
| tavily-python | >=0.7.0,<0.8.0 | Tavily 搜索 API |

> 2026-07-26 新增：`itsdangerous`、`PyYAML`、`tzdata`。

## .gitignore

### 变更（2026-07-26）

| 旧规则 | 新规则 | 原因 |
|--------|--------|------|
| `.env.local` | `.env.*`（排除 `.env.example`） | 统一屏蔽所有环境变量文件（`.env.dev`、`.env.prod` 等），仅允许示例模板 |
| 无 `config/message_config.json` | 新增排除项 | 外部消息身份映射为本地配置，不应纳入版本控制 |

### 变更记录

| 日期 | 变更 |
|------|------|
| 2026-07-26 | 仓库检查脚本从 `scripts/` 移至 `.github/scripts/`，与 CI 工作流就近存放；ci.yml 同步更新脚本路径 |
