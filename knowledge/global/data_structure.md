# kemo-agent 架构与全局知识索引

本页只保留架构和文档入口，专题正文按需读取。实现细节、工程规则和更新说明写入对应正文，不在索引中重复。

## 架构

```text
kemo-agent/
├── web/                   # Web 入口
├── cli.py                 # 命令行入口
├── message/               # 外部消息入口
├── cron/                  # 定时任务入口
├── run/                   # 核心运行时
│   ├── conversation/      # 会话编排与执行循环
│   ├── cards/             # 上下文、压缩与请求组装
│   ├── tools/             # 工具发现与执行
│   └── agents/            # 子智能体运行
├── provider/              # 模型协议与适配
├── plugins/               # 工具插件
├── agents/                # 内置子智能体定义
├── skills/                # 可复用技能
├── expand/                # 拓展能力
├── sense/                 # 感知能力
├── config/                # 全局配置
├── knowledge/             # 知识库与文档
└── users/                 # 用户配置、人格、历史与记忆
```

仅列主要模块；详细架构见 `architecture-overview.md`。

## 专题文档

以下文件均位于 `knowledge/global/`。

| 分类 | 文档 | 查阅内容 |
|------|------|----------|
| 基础与总览 | `data_structure.md` | 架构与文档导航 |
| 基础与总览 | `project-introduction.md` | 项目定位、能力与使用入口 |
| 基础与总览 | `architecture-overview.md` | 请求流程、上下文与并发 |
| 基础与总览 | `open-source-license.md` | Apache-2.0 许可与分发 |
| 基础与总览 | `engineering-operations.md` | 工程操作、权限边界与维护规范 |
| 配置与知识 | `configuration-reference.md` | 配置字段、默认值与覆盖规则 |
| 配置与知识 | `prompt-authoring-standard.md` | 人格、手册、知识与能力说明的分工 |
| 配置与知识 | `knowledge-and-user-data.md` | 三层知识库、分类与用户数据目录 |
| 运行与调度 | `storage-and-persistence.md` | 历史与记忆、检索索引、数据持久化 |
| 运行与调度 | `provider-reliability.md` | 模型协议、重试、容量与缓存 |
| 运行与调度 | `task-automation.md` | 任务计划、定时任务与后台维护 |
| 运行与调度 | `long-task-runtime.md` | 持续执行、完成屏障与停止 |
| 运行与调度 | `agent-task-coordination.md` | 子智能体协作、状态与续跑 |
| 模块与集成 | `module-development.md` | 技能、拓展、感知与子智能体开发 |
| 模块与集成 | `plugin-development.md` | 工具插件、执行合同与媒体处理 |
| 模块与集成 | `builtin-expansions.md` | 内置 App、网关与图谱集成 |
| 模块与集成 | `external-message-route-creation.md` | 外部消息平台接入与路由 |
| 模块与集成 | `module-template-validation.md` | 六类模块的合同验收 |
| 网页与交互 | `frontend-conventions.md` | 网页布局、交互、多语言与验证 |
| 网页与交互 | `inline-widgets.md` | 正文内联组件与交互协议 |
| 部署与更新 | `deployment-and-release.md` | 安装、打包、发布与恢复 |
| 部署与更新 | `version-and-update-modules.md` | 版本兼容、更新入口与边界 |
