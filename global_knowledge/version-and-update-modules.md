# 版本号与更新模块功能分布

本说明对应 `version.json`、`update.py` 和 `update/` 当前实现。更新器支持 Windows 与 Linux，按四个板块拉取远程 `main` 分支并更新本地文件。

## 版本结构

```json
{
  "name": "kemo-agent",
  "version": "0.1.0",
  "schema_version": 1,
  "components": {
    "core": {"version": "0.1.0"},
    "agents": {"version": "0.1.0"},
    "plugins": {"version": "0.1.0"},
    "web": {"version": "0.1.0"}
  }
}
```

`version` 是全量发布版本；四个 `components.*.version` 用于单独判断板块更新。版本号使用点分数字并由更新器比较。

## 命令

```powershell
python update.py --check
python update.py --module core
python update.py --module agents
python update.py --module plugins
python update.py --module web
python update.py --module all
```

常用选项：

| 选项 | 说明 |
|------|------|
| `--check` | 只比较版本，不修改文件 |
| `--force` | 版本相同时仍重新安装 |
| `--yes` / `-y` | 使用默认答案处理确认提示 |
| `--dry-run` | 展示将执行的操作，不写文件 |
| `--skip-web-build` | 更新 Web 后不执行前端构建 |
| `--skip-deps` | 更新 core 后不刷新 Python 依赖 |
| `--repo-url` / `--branch` | 覆盖远程仓库和分支 |

## 全量流程

```text
读取本地/远程 version.json
  → 用户确认
  → 浅克隆远程仓库
  → 创建本地备份
  → 按 core → agents → plugins → web 执行选中板块
  → core：尝试用户骨架与旧记忆迁移、刷新 pip 依赖
  → web：npm install + npm run build
  → 输出逐板块汇总
```

任一板块返回 `failed` 时，全量更新结束为失败。`partial` 会保留警告并继续汇总。

## core — 核心与公共资源

### 完全同步目录

以下目录按远程内容同步，并删除远程已经移除的本地项：

| 目录 | 内容 |
|------|------|
| `run/` | 对话、历史、Prompt、记忆、工具与运行时核心 |
| `cron/` | 定时任务执行和调度 |
| `template/` | 各类创建模板 |
| `tests/` | 后端测试 |
| `global_knowledge/` | 全局知识库文档 |
| `update/` | 更新器板块实现 |

因此，本地直接修改 `global_knowledge/` 或 `template/` 会在 core 更新时被远程版本覆盖；长期自定义内容应提交到自己的分支或放入不受 core 覆盖的用户/共享数据层。

### 覆盖的根文件

`cli.py`、`events.py`、`setup.py`、`update.py`、`requirements.txt`、`config/global_soul.md`、`.env.example`、`LICENSE`、`README.md`（兼容小写名）、`kemo-agent.jpg`、`version.json`、`agents.md`、`user_create.py`。

### 特殊处理

- `message/` 同步框架消息路由代码，但保留本地 `message/out/` 平台模块和运行数据。
- `config/global_config.json` 内容不同时询问覆盖、保留或查看差异；schema 不同时额外显示顶层字段差异。
- 只更新 `global_expand/register.py`、`global_sense/register.py`、`shared_expand/register.py`、`shared_skills/register.py`，不会删除这些资源根目录中的自定义模块。
- core 完成后尝试补齐现有用户骨架和迁移旧记忆，再执行 `pip install -r requirements.txt`（除非跳过）。

## agents — 内置子智能体

- `agents/_runtime/` 完全覆盖。
- `agents/__init__.py` 覆盖。
- 远程存在的每个内置代理目录：本地不存在则新增，内容不同则完整更新。
- 本地独有的代理目录不会删除。
- `users/<name>/agents/` 完全不在此板块范围内。

内置代理如果与远程同名，会按远程版本更新；本地长期自建代理应放在用户层，避免与框架内置名称冲突。

## plugins — 可执行工具生态

`plugins/` 与远程完全同步，包括删除远程已经移除的插件。该板块不做逐插件智能合并。

本地自建但未进入远程仓库的可执行插件会被删除，更新前应备份或维护在自己的分支。共享技能和用户技能不属于 plugins 板块。

## web — Web 前端与后端

`web/` 与远程同步，但保留以下依赖和构建目录：

- `web/node_modules/`
- `web/dist/`
- `web/frontend/node_modules/`
- `web/frontend/dist/`

同步完成后默认在 `web/frontend/` 执行 `npm install` 和 `npm run build`。没有 npm 时，只有已有非空 `dist/` 才允许跳过构建继续使用。

## 当前未纳入板块自动同步的路径

下列路径不在四个板块当前清单中，也不应被文档误认为会随 core 更新：

- `provider/`
- `shared_knowledge/`
- 根目录 `start_web.py`、`restart.py`、`.gitignore`
- `config/` 中除 `global_soul.md` 和交互处理的 `global_config.json` 之外的文件
- `global_expand/`、`global_sense/`、`shared_expand/`、`shared_skills/` 中除根 `register.py` 之外的模块数据
- `users/`、`tmp/`、`message/out/` 等运行数据

如这些框架路径发生版本变化，需要先扩展更新板块实现，或由维护者使用其他明确方式更新。不要假设 `--module all` 会覆盖未列出的路径。

## 备份规则

更新前在 `.backups/update-<时间>/` 创建备份，默认只保留最近 2 份。备份排除：`.git/`、虚拟环境、旧备份、`users/`、`tmp/`、依赖目录、构建产物和 Python 缓存。

`users/` 不进入更新器备份并不表示它不重要；生产使用者必须建立独立的用户数据备份。

## 更新前检查

1. 提交或另行备份本地代码改动和自定义插件。
2. 单独备份 `users/`、`.env` 和 `message/out/`。
3. 先运行 `--check`，重大更新再运行 `--dry-run`。
4. 查看 `global_config.json` 差异，不盲目覆盖本地运行参数。
5. 更新后运行后端测试、前端测试和生产构建，并重启 RuntimeHost。
