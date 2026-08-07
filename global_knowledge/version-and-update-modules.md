# 版本号与更新模块功能分布

本说明对应 `version.json`、`update.py` 和 `update/` 当前实现。更新器支持 Windows 与 Linux，按四个板块拉取远程 `main` 分支并更新本地文件。

## 版本结构

```json
{
  "name": "kemo-agent",
  "version": "1.0.3",
  "schema_version": 1,
  "components": {
    "core": {"version": "1.0.3"},
    "agents": {"version": "1.0.3"},
    "plugins": {"version": "1.0.3"},
    "web": {"version": "1.0.3"}
  }
}
```

`version` 是全量发布版本；四个 `components.*.version` 用于单独判断板块更新。版本号使用点分数字并由更新器比较。

组件版本不要求在后续版本中始终与根版本相同；仅有部分板块发生变化时，可以只推进对应组件版本。`1.0.0` 是主生态首次完整发布，`1.0.1` 是系统性稳定性复核版本。`1.0.2` 同时修改 core 文档与公共说明、内置 agents 记忆指引、plugins 搜索实现及 Web 公共包版本，因此根版本与四个组件统一推进。`1.0.3` 的行为修改集中于核心对话运行时，但作为完整公开补丁发布，根版本、四个组件版本、CLI 与前端公共包版本仍统一推进，避免部署端、更新器和发布检查器出现版本来源分歧。

`1.0.0` 完成了对话与工具循环、Chat/Kemo Provider、多模态引导、SQLite 历史与潮汐记忆、知识库、子代理、任务计划、定时任务、感知、三层拓展、外部消息、Web 管理界面和模块合同测试的主链路闭环。历史、记忆、任务计划和消息状态采用表结构与 WAL 事务；Kemo Graph 调整为按需使用、具备 Library ACL 的侧载文档站；Web 增加附件预览与持久缩略图；后台热路径减少重复扫盘与数据库建表检查；更新器保护运行时 Store 并处理旧图谱配置迁移。旧式历史和记忆 JSON 不再参与读取或自动导入。

`1.0.1` 在完整主生态之上进行框架级稳定性复核：统一检查全局/用户配置运行语义，强化工具调用次数、结果体积和 PowerShell 会话边界，补齐子代理超时存活期、感知兼容、知识图谱文件操作与全局知识说明，并将正式测试按领域目录重组。发布前验收同时覆盖开发期系统合同、正式 Python 测试、模板测试、Python 编译、Git 补丁检查、前端测试和生产构建。后续版本主要面向边缘生态接入、兼容性、性能、传输稳定性与长期维护，不再以补齐主框架模块为首要目标。

`1.0.2` 修复潮汐记忆的两条关键失效链路。临时重要热画像不再把 5000 字符 Prompt 注入预算误作输出硬上限，5000～20000 字符可以完整落盘；已有碎片搜索不再要求模型长句连续出现在标题或正文中，而是采用完整短语优先、带置信门槛的多关键词评分。`search_many` 每批只加载一次四层记忆，`self_improve` 在确认语义一致后复用已有文件名，使每日加权、到期晋升和长期收敛重新生效，同时避免单个公共词导致错误加权。

`1.0.3` 修复长工具循环中的动态状态陈旧问题。人格、手册、知识、记忆和任务计划仍在一轮用户对话开始时构建一次；`[expand_data]` 与 `[perception]` 改为在每一次逻辑 Provider 请求前从磁盘重新读取。后台调度器继续按配置频率独立执行采集，请求构建不会调用 `data_update.py`；同一网络请求的传输重试和 SSE 续传复用同一正文。工具续轮、运行中引导续轮以及上下文超限恢复后的请求因此能够使用当时最新已发布的拓展与感知快照。

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
  → 校验根版本与四个组件版本
  → 用户确认
  → 浅克隆远程仓库
  → 创建本地备份
  → 从克隆源码加载最新 update/ 板块实现
  → 按 core → agents → plugins → web 执行选中板块
  → core：补齐用户骨架，初始化记忆库，并事务迁移任务计划、消息幂等、上下文摘要和路由状态，再刷新 pip 依赖
  → web：npm install + npm run build
  → 全部成功后原子提交 version.json
  → 输出逐板块汇总
```

任一板块返回 `failed` 或 `partial` 时，本轮更新结束为失败，保留旧版本号并显示更新前备份位置。已经复制的代码不会被自动回滚，可在排除问题后直接重新执行更新，或使用备份手动恢复。

## core — 核心与公共资源

### 完全同步目录

以下目录按远程内容同步，并删除远程已经移除的本地项：

| 目录 | 内容 |
|------|------|
| `run/` | 对话、历史、Prompt、记忆、工具与运行时核心 |
| `provider/` | Chat/Kemo Provider、协议适配与多模态 Asset 客户端 |
| `cron/` | 定时任务执行和调度 |
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
- `config/global_config.json` 内容不同时询问覆盖、保留或查看差异；schema 不同时额外显示顶层字段差异。
- 更新 `global_expand/register.py`、`global_sense/register.py`、`shared_expand/register.py`、`shared_skills/register.py`，不会删除这些资源根目录中的自定义模块。
- `global_expand/kemo_gateway_status/` 是内置例外：core 会同步其静态代码和说明，同时保留部署机的本地凭据、状态摘要、脱敏快照、图表和运行状态；存在本地配置时继续保持激活。
- core 完成后补齐现有用户骨架，并初始化缺失的记忆、历史、任务计划和运行日志数据库。初始化失败时版本号不提交；更新器不扫描或导入其他存储格式。全部完成后才执行 `pip install -r requirements.txt`（除非跳过）。

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

同步完成后默认在 `web/frontend/` 执行 `npm install` 和 `npm run build`。由于 Git 仓库不跟踪 `dist/`，没有 npm 时不能把旧构建产物视为更新成功；更新器会保留旧版本号并提示安装 Node.js。

从 `0.1.x` 首次升级到 `0.2.x` 时，旧 core 清单尚不知道 `provider/` 和 `README_EN.md`。旧调度器默认执行全量更新时，会在 core 刷新更新模块后，由新版 web 板块补齐这两项兼容迁移，因此不需要再次使用 `--force` 更新。`0.2.x` 新调度器会显式关闭该桥接，单独更新 web 时不会越界修改 core。

## 版本提交规则

- 全量更新：所有步骤成功后写入远程完整版本文档，包括根版本和四个组件版本。
- 单板块更新：只推进对应 `components.<module>.version`，根版本及其他组件保持不变。
- `failed`、`partial`、依赖安装失败、Web 构建失败或版本写入失败：不提交新版本号。
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

更新前在 `.backups/update-<时间>/` 创建备份，默认只保留最近 2 份。备份排除：`.git/`、虚拟环境、旧备份、`users/`、`tmp/`、依赖目录、构建产物和 Python 缓存。

`users/` 不进入更新器备份并不表示它不重要；生产使用者必须建立独立的用户数据备份。

## 更新前检查

1. 提交或另行备份本地代码改动和自定义插件。
2. 单独备份 `users/`、`.env` 和 `message/out/`。
3. 先运行 `--check`，重大更新再运行 `--dry-run`。
4. 查看 `global_config.json` 差异，不盲目覆盖本地运行参数。
5. 更新后运行后端测试、前端测试和生产构建，并重启 RuntimeHost。
