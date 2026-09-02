# 更新模块功能说明

本说明对应 `version.json`、根目录 `update.py` 和 `update/` 包的当前实现。根目录 `update.py` 只是兼容入口；参数解析、版本检查、远程源码校验、板块调度、备份、互斥锁、恢复、依赖刷新和数据库初始化全部位于 `update/` 内。`python update.py` 与 `python -m update` 使用同一个实现。

本文只说明当前更新器行为，不保存发布历史、版本变更记录或单次审计结论；当前版本以根目录 `version.json` 为准。

## 版本结构

```json
{
  "name": "kemo-agent",
  "version": "1.2.5",
  "schema_version": 1,
  "components": {
    "core": {"version": "1.2.5"},
    "agents": {"version": "1.2.5"},
    "plugins": {"version": "1.2.5"},
    "web": {"version": "1.2.5"}
  }
}
```

`version` 是全量发布版本；四个 `components.*.version` 用于单独判断板块更新。版本号使用点分数字并由更新器比较。

组件版本不要求始终与根版本相同；只修改某个板块时，可以只推进对应组件版本。更新器以 `version.json` 中的根版本和所选组件版本为准，并拒绝降级。

## 命令

```powershell
python update.py --check
python update.py --module core
python update.py --module agents
python update.py --module plugins
python update.py --module web
python update.py --module all
python -m update --check
```

常用选项：

| 选项 | 说明 |
|------|------|
| `--check` | 只比较版本，不修改文件 |
| `--force` | 版本相同时仍重新安装；不能用于降级 |
| `--yes` / `-y` | 确认更新；全局配置仍按安全默认策略处理 |
| `--dry-run` | 展示将执行的操作，不写文件 |
| `--skip-web-build` | 仅用于 `--dry-run` 预览；正式更新不允许跳过 Web 构建 |
| `--skip-deps` | 仅用于 `--dry-run` 预览；正式更新不允许跳过 Python 依赖刷新 |
| `--replace-global-config` | 明确允许覆盖本地 `global_config.json`；默认不覆盖 |
| `--repo-url` / `--branch` | 覆盖远程仓库和分支 |

## 全量流程

```text
读取本地/远程 version.json
  → 校验根版本与四个组件版本
  → 拒绝根版本或任一所选组件降级
  → 用户确认
  → 浅克隆远程仓库并校验克隆中的 version.json 与检查结果完全一致
  → 取得全局更新锁并写入维护标记
  → 创建可恢复的源码备份
  → 从克隆源码加载最新 update/ 板块实现
  → 按 core → agents → plugins → web 执行选中板块；首个 failed/partial 立即停止
  → web：有 lockfile 时 npm ci，否则 npm install；随后 npm run build
  → core：刷新 pip 依赖
  → core：前面的源码、构建和依赖步骤成功后，再补齐用户骨架并初始化记忆、任务计划、消息幂等和运行日志数据库
  → 全部成功后原子提交 version.json
  → 输出逐板块汇总并释放更新锁
```

任一板块、迁移、前端构建、依赖刷新或版本提交失败时，本轮立即停止，保留旧版本号，并使用更新前备份自动恢复受版本管理的源码路径。`users/`、运行时数据库、Cron 运行状态、本地拓展凭据和采集数据不会因恢复而被覆盖；内置拓展的 `expand.json`、`input_data.md` 等运行状态也按部署机状态前向保留，失败后不保证回滚到旧采集快照。数据库迁移仍是向前操作，因此生产环境仍应独立备份用户数据。

## core — 核心与公共资源

### 完全同步目录

以下目录按远程内容同步，并删除远程已经移除的本地项：

| 目录 | 内容 |
|------|------|
| `run/` | 对话、历史、Prompt、记忆、工具与运行时核心 |
| `provider/` | Chat/Kemo Provider、协议适配与多模态 Asset 客户端 |
| `cron/` | 定时任务执行和调度代码；`task_cron_system/` 使用合并更新 |
| `template/` | 各类创建模板 |
| `tests/` | 后端测试 |
| `global_knowledge/` | 全局知识库文档 |
| `update/` | 更新器板块实现 |

因此，本地直接修改 `global_knowledge/` 或 `template/` 会在 core 更新时被远程版本覆盖；长期自定义内容应提交到自己的分支或放入不受 core 覆盖的用户/共享数据层。

### 覆盖的根文件

`cli.py`、`events.py`、`setup.py`、`update.py`、`requirements.txt`、`requirements-dev.txt`、`config/global_soul.md`、`.env.example`、`LICENSE`、`README.md`（兼容小写名）、`README_EN.md`、`kemo-agent.ico`、`kemo-agent.jpg`、`kemo-web-UI.png`、`agents.md`、`restart.py`、`start_web.py`、`user_create.py`。

`version.json` 不由 core 提前覆盖，而是由总调度器在所选板块、迁移、依赖刷新和 Web 构建全部成功后单独提交。

### 特殊处理

- `message/` 同步框架消息路由代码，但保留本地 `message/out/` 平台模块和运行数据。
- `cron/task_cron_system/*.json` 更新静态任务定义时保留部署机的 `next_run_at`、`latest_run_at` 和 `status`；本地独有系统任务及日志不删除。
- `config/global_config.json` 在 schema 相同时默认递归补入远程新增默认值，并完整保留本地已有值；schema 不同时停止更新，只有显式使用 `--replace-global-config` 才覆盖。
- 更新 `global_expand/register.py`、`global_sense/register.py`、`shared_expand/register.py`、`shared_skills/register.py`，不会删除这些资源根目录中的自定义模块。
- `global_expand/kemo_gateway_status/` 是内置例外：core 会同步其静态代码和说明，同时保留部署机的本地凭据、状态摘要、脱敏快照、图表和运行状态；存在本地配置时继续保持激活。
- core 源码同步成功后先执行 `pip install -r requirements.txt`；依赖刷新成功后才补齐现有用户骨架，并初始化缺失的记忆、历史、任务计划和运行日志数据库。这样，前端构建或依赖安装失败时不会先改用户数据库。初始化失败时自动进入恢复流程；更新器不扫描或导入其他存储格式。

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

同步范围包括 Web 应用装配、兼容服务门面、`web/routes/` 路由层、`web/services/` 领域服务层，以及 `web/schemas.py`、`web/errors.py`、`web/constants.py` 等公共契约文件。新增的分层目录会随 web 板块递归安装，不需要在部署机手动创建。

同步完成后默认在 `web/frontend/` 执行确定性安装：存在 `package-lock.json` 时使用 `npm ci`，否则使用 `npm install`，随后执行 `npm run build`。由于 Git 仓库不跟踪 `dist/`，没有 npm 时不能把旧构建产物视为更新成功；更新器会自动恢复源码并保留旧版本号。

## 版本提交规则

- 全量更新：所有步骤成功后写入远程完整版本文档，包括根版本和四个组件版本。
- 单板块更新：只推进对应 `components.<module>.version`，根版本及其他组件保持不变。
- `failed`、`partial`、用户骨架/数据库初始化失败、依赖安装失败、Web 构建失败或版本写入失败：不提交新版本号，并尝试自动恢复版本管理的源码路径。
- 使用同目录临时文件并通过原子替换写入，避免中断时留下半个 JSON 文件。

## 当前未纳入板块自动同步的路径

下列路径不在四个板块当前清单中，也不应被文档误认为会随 core 更新：

- `shared_knowledge/`
- 根目录 `.gitignore`
- `config/` 中除 `global_soul.md` 和交互处理的 `global_config.json` 之外的文件
- `global_expand/`、`global_sense/`、`shared_expand/`、`shared_skills/` 中除根 `register.py` 和内置 `global_expand/kemo_gateway_status/` 静态实现之外的模块数据
- `users/`、`tmp/`、`message/out/` 等运行数据

如这些框架路径发生版本变化，需要先扩展更新板块实现，或由维护者使用其他明确方式更新。不要假设 `--module all` 会覆盖未列出的路径。

## 备份规则

正式更新前在 `.backups/update-<时间>-<高精度后缀>/` 创建备份，成功完成后默认只保留最近 2 份。失败更新不会先删除旧备份。备份排除：`.git/`、虚拟环境、旧备份、`users/`、`runtime/`、`tmp/`、`message/out/`、Cron 运行状态、本地拓展凭据/采集数据、依赖目录和 Python 缓存。前端构建产物会纳入恢复范围，避免失败后只剩新源码配旧页面。

`users/` 不进入更新器备份并不表示它不重要；生产使用者必须建立独立的用户数据备份。

## 更新前检查

1. 提交或另行备份本地代码改动和自定义插件。
2. 单独备份 `users/`、`.env` 和 `message/out/`。
3. 先运行 `--check`，重大更新再运行 `--dry-run`。
4. 查看 `global_config.json` 差异，不盲目覆盖本地运行参数。
5. 更新后运行后端测试、前端测试和生产构建，并重启 RuntimeHost。
6. 暂存前检查 `git status --short`，排除 Cron 时间、拓展 `recent_update`、本地激活状态和采集正文。
