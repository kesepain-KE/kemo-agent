---
type: domain_overview
project: kemo-agent
domain: infra
module: infra-总览
layer: L1
scope: project
status: active
summary: infra — 项目基础设施（CI/CD、仓库卫生、版本一致性、开发依赖）
source: "infra/infra-总览.md"
updated: 2026-07-31
verified: true
tags: [kemo-agent, infra, CI/CD, 仓库卫生, 版本检查, 开发环境, 文档]
---
# infra — 项目基础设施

项目级基础设施工具、CI/CD 流水线与开发辅助脚本。

## 目录结构

```
.github/workflows/
  ci.yml            — CI 流水线（仓库检查 + 后端测试 + 前端构建）
  security.yml      — 安全扫描（Gitleaks 密钥泄露检测）
.github/scripts/
  check_repository_hygiene.py  — 仓库卫生检查（CI 守卫）
  check_versions.py            — 版本一致性检查
requirements-dev.txt           — 仅测试依赖
```

## 组成

| 模块 | 说明 |
|------|------|
| [[infra-ci-cd]] | CI/CD 流水线：CI 测试矩阵与 Gitleaks 安全扫描 |
| [[infra-repo-scripts]] | 仓库辅助脚本：卫生检查、版本一致性、开发依赖、.gitignore |

## 与已有测试体系的关系

源代码测试目录 `tests/`（39 个测试文件）由 CI 流水线自动触发执行，覆盖率已扩展至 80+ 项。详见 [[archive/tests-测试]]。

## 项目文档与资产

| 文件 | 说明 |
|------|------|
| `readme.md` | 中文版自述文件：Logo 优先展示，下方排语言切换栏、在线文档徽章与链接 |
| `README_EN.md` | 英文版自述文件：布局同中文版，Logo 优先展示后跟语言切换链接 |
| `kemo-agent.jpg` | 项目 Logo 图片（欢迎页 hero-logo 与 README 展示用） |
| `kemo-agent.ico` | 项目图标文件（可作为 favicon 等用途） |

## 相关笔记

- [[kemo-agent-模块索引]]
- [[cli-总览]]（setup.py / start_web.py / update.py 等入口）
- [[run-update]]（板块化更新系统）
- [[config-总览]]（version.json）
