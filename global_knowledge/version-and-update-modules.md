# 版本号与更新模块对照

kemo-agent 共有 5 组版本号，分别对应框架不同层级的更新范围。

## 版本号总览

| 版本号 | 模块 | 更新范围 |
|--------|------|----------|
| `version` | all | 一键全量，等于 core + agents + plugins + web |
| `core` | 核心引擎 | 运行时、配置、消息路由、调度、全局注册模块、模板、测试、根目录散文件 |
| `agents` | 子代理系统 | 内置子代理智能合并，用户自建代理不动 |
| `plugins` | 插件生态 | 全部工具插件强制覆盖 |
| `web` | Web 服务 | 前端+后端强制覆盖，排除 node_modules 和 dist |

---

## 各模块详细范围

### core — 核心引擎

**强制覆盖（目录）：**

| 目录 | 说明 |
|------|------|
| `run/` | 运行时核心（对话引擎、工具调用、历史管理、Prompt 拼接） |
| `cron/` | 定时任务调度系统 |
| `template/` | 子代理/感知/拓展/用户等创建模板 |
| `tests/` | 测试目录 |
| `global_knowledge/` | 全局共享知识库正文 |
| `update/` | 更新模块自身 |

**强制覆盖（文件）：**

| 文件 | 说明 |
|------|------|
| `cli.py` | 命令行交互入口 |
| `events.py` | 事件系统 |
| `setup.py` | 项目安装脚本 |
| `update.py` | 更新调度器入口 |
| `requirements.txt` | Python 依赖清单 |
| `config/global_soul.md` | 全局基座人格配置 |
| `.env.example` | 环境变量模板 |
| `LICENSE` | 开源协议 |
| `README.md` | 项目说明 |
| `kemo-agent.jpg` | 项目图标 |
| `version.json` | 版本号文件 |
| `agents.md` | 智能体操作手册 |
| `user_create.py` | 用户创建模块 |

**询问覆盖：**

| 文件 | 说明 |
|------|------|
| `config/global_config.json` | 全局配置。schema 版本不同时提示差异，由用户选择覆盖/保留/查看差异 |

**排除保护：**

| 路径 | 说明 |
|------|------|
| `message/out/` | 消息路由外部平台推送队列，不覆盖不删除 |
| `message/` 其余内容 | 正常强制覆盖 |

**仅注册模块（只更新 register.py）：**

| 文件 | 说明 |
|------|------|
| `global_expand/register.py` | 全局拓展注册 |
| `global_sense/register.py` | 全局感知注册 |
| `shared_expand/register.py` | 共享拓展注册 |
| `shared_skills/register.py` | 共享技能注册 |

> 这些目录下的用户/共享数据（如 `global_sense/system_info/`）不受更新影响。

---

### agents — 子代理系统

**智能合并策略：**

1. `agents/_runtime/` 和 `agents/__init__.py` — 强制覆盖
2. 遍历远程每个子代理目录：
   - 本地不存在 → 新增
   - 本地存在且内容不同 → 覆盖更新
   - 本地存在且内容相同 → 跳过
3. 本地多出来的子代理目录（用户自建）— **不动**

**当前内置子代理：**

| 子代理 | 职责 |
|--------|------|
| `context_manage` | 上下文窗口管理与压缩 |
| `memory_temporary_important` | 临时重要记忆提取与维护 |
| `self_improve` | 记忆碎片提取、权重管理与晋升 |
| `task_plan` | 复杂任务分步计划生成与执行 |
| `time_plan` | 定时任务创建与管理 |

---

### plugins — 插件生态

**强制覆盖** `plugins/` 全部内容，含删除远程已移除的插件。

当前内置插件（共 9 个）：`audio_universal` `auto_improve` `download_anything` `file` `image_edit` `image_generation` `network` `speech_generation` `video_generation`

---

### web — Web 服务

**强制覆盖** `web/` 全部内容，但排除：

| 排除路径 | 原因 |
|----------|------|
| `web/node_modules/` | npm 依赖，由 `npm install` 管理 |
| `web/dist/` | 构建产物，更新后重新构建 |
| `web/frontend/node_modules/` | 前端 npm 依赖 |
| `web/frontend/dist/` | 前端构建产物 |

---

## 更新调度流程

```
update.py --module <core|agents|plugins|web|all>

克隆远程仓库 → 创建本地备份 → 按模块执行 → 迁移用户骨架/记忆
                                    → (web 更新时) 构建前端
                                    → (core 更新时) 刷新 pip 依赖
                                    → 打印汇总报告
```

## 版本文件位置

`version.json` 位于项目根目录，远程版本比对时读取 GitHub main 分支上的同名文件。
