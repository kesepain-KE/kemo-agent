# 版本号与更新模块功能分布

本说明对应 `version.json`、根目录 `update.py` 和 `update/` 包的当前实现。根目录 `update.py` 只是兼容入口；参数解析、版本检查、远程源码校验、板块调度、备份、互斥锁、恢复、依赖刷新和数据库初始化全部位于 `update/` 内。`python update.py` 与 `python -m update` 使用同一个实现。

## 版本结构

```json
{
  "name": "kemo-agent",
  "version": "1.2.3",
  "schema_version": 1,
  "components": {
    "core": {"version": "1.2.3"},
    "agents": {"version": "1.2.3"},
    "plugins": {"version": "1.2.3"},
    "web": {"version": "1.2.3"}
  }
}
```

`version` 是全量发布版本；四个 `components.*.version` 用于单独判断板块更新。版本号使用点分数字并由更新器比较。

组件版本不要求在后续版本中始终与根版本相同；仅有部分板块发生变化时，可以只推进对应组件版本。`1.0.0` 是主生态首次完整发布，`1.0.1` 是系统性稳定性复核版本。`1.0.2` 同时修改 core 文档与公共说明、内置 agents 记忆指引、plugins 搜索实现及 Web 公共包版本，因此根版本与四个组件统一推进。`1.0.3` 的行为修改集中于核心对话运行时；`1.0.4` 与 `1.0.5` 同时调整核心 Prompt/配置合同和 Web 前后端，均作为完整公开补丁统一推进根版本、四个组件版本、CLI 与前端公共包版本，避免部署端、更新器和发布检查器出现版本来源分歧。`1.1.0` 新增 `kemo_app` 全局拓展（Android App 桥接服务），同样作为完整公开版本统一推进根版本、四个组件版本、CLI 与前端公共包版本。`1.1.1` 仅修改核心会话/更新器、`kemo_app` 桥接和 Web 历史界面，因此根版本、`core`、`web`、CLI 与前端公共包推进到 `1.1.1`，未改动的 `agents`、`plugins` 保持 `1.1.0`。`1.1.2` 同时修改核心任务计划/工具看门狗、临时重要记忆子代理、插件生态、APP 拓展和 Web 前后端，因此根版本与四个组件再次统一推进到 `1.1.2`。`1.2.0` 的公开实现集中在核心长任务编排与 Web 前后端，故根版本、`core`、`web`、CLI 和前端公共包推进到 `1.2.0`；本轮未改变实现的 `agents`、`plugins` 及独立 `kemo_app` 桥接协议仍保持 `1.1.2`。 `1.2.1` 同时修改核心持久化、Cron、Provider 工具恢复、内置拓展分发和 Web 能力引用界面，因此根版本、`core`、`web`、CLI 与前端公共包推进到 `1.2.1`；`agents`、`plugins` 仍保持 `1.1.2`，独立 `kemo_app` 桥接协议则由 `1.1.3` 推进到 `1.1.4`。

`1.0.0` 完成了对话与工具循环、Chat/Kemo Provider、多模态引导、SQLite 历史与潮汐记忆、知识库、子代理、任务计划、定时任务、感知、三层拓展、外部消息、Web 管理界面和模块合同测试的主链路闭环。历史、记忆、任务计划和消息状态采用表结构与 WAL 事务；Kemo Graph 调整为按需使用、具备 Library ACL 的侧载文档站；Web 增加附件预览与持久缩略图；后台热路径减少重复扫盘与数据库建表检查；更新器保护运行时 Store 并处理旧图谱配置迁移。旧式历史和记忆 JSON 不再参与读取或自动导入。

`1.0.1` 在完整主生态之上进行框架级稳定性复核：统一检查全局/用户配置运行语义，强化工具调用次数、结果体积和 PowerShell 会话边界，补齐子代理超时存活期、感知兼容、知识图谱文件操作与全局知识说明，并将正式测试按领域目录重组。发布前验收同时覆盖开发期系统合同、正式 Python 测试、模板测试、Python 编译、Git 补丁检查、前端测试和生产构建。后续版本主要面向边缘生态接入、兼容性、性能、传输稳定性与长期维护，不再以补齐主框架模块为首要目标。

`1.0.2` 修复潮汐记忆的两条关键失效链路。临时重要热画像不再把 5000 字符 Prompt 注入预算误作输出硬上限，5000～20000 字符可以完整落盘；已有碎片搜索不再要求模型长句连续出现在标题或正文中，而是采用完整短语优先、带置信门槛的多关键词评分。`search_many` 每批只加载一次四层记忆，`self_improve` 在确认语义一致后复用已有文件名，使每日加权、到期晋升和长期收敛重新生效，同时避免单个公共词导致错误加权。

`1.0.3` 修复长工具循环中的动态状态陈旧问题，并补充缓存友好的用户级动态注入策略。人格、手册、知识、记忆和任务计划仍在一轮用户对话开始时构建一次；`[expand_data]` 与 `[perception]` 默认使用本轮开始时的固定快照。用户可以独立配置 `expand.realtime_injection=true` 和 `perception.realtime_injection=true`，让对应数据段随工具续轮、运行中引导和上下文恢复请求更新。后台调度器继续按配置频率独立采集，请求构建不会调用采集脚本；同一网络请求的传输重试和 SSE 续传复用同一正文。

`1.0.4` 增强运行连续性和多入口一致性：Provider 工具参数不完整且尚无可见输出或副作用时可按配置重新生成；Web 历史列表统一展示 Web、CLI 和外部消息归档，非 Web 来源保持只读并暴露记忆处理状态；生成媒体即使移动到下载区嵌套路径，也可按 SHA-256 与大小安全找回。前端同时修复技能 Markdown 编辑器高度和超长引导气泡横向滚动。CI 的公共版本检查新增全局知识文档一致性校验。

`1.0.5` 为 `[expand_data]` 与 `[perception]` 增加独立的用户级总注入闸门。每类数据都形成“不注入、按轮注入、实时注入”三态；关闭总闸门时对应 Prompt 段被完整省略，但后台采集和主动调用能力不受影响。Web 上下文窗口顶部新增两个只读策略气泡，配置页则提供总闸门与实时刷新开关。运行状态和 Prompt 分段接口会统一报告 `disabled`，不再把关闭状态误记为“空段”或“已注入”。生成媒体按校验和找回嵌套路径时采用有界 LRU、短期失败缓存及文件/哈希候选上限，避免损坏链接反复触发无界扫盘。

`1.1.0` 完成主生态的移动端闭环：新增 `kemo_app` 全局拓展，为 Android 客户端提供独立的 HTTP/SSE/WebSocket 桥接服务（两级认证、流式对话与运行中引导、任务与定时、文件传输上限 80 MiB、在线设备统计），kemo-agent 生态正式延伸至手机端。`.gitignore` 豁免该模块；`config.json`、`users.json`、`credential_registry.json` 等凭据与运行时文件仍只存在于本地部署副本，不进入仓库。公开源码清单固定为 `open_input=false`、无最近采集时间的未激活初始状态，克隆和更新都不会自动启动桥接进程。

`1.1.1` 将 Android App 从 Web 会话来源中彻底分离。`kemo_app` 固定向核心提交 `source=app`，并对历史列表、读取、关闭、压缩、删除和撤销操作使用同一来源；核心活动运行与客户端租约按 `source` 隔离；Web 历史抽屉明确显示“APP版”并保持只读。核心更新器新增 `kemo_app` 公开文件清单，更新时只覆盖源码和说明，保留本地 `config.json`、`users.json`、`credential_registry.json`、PID、日志、连接状态、采集摘要和激活状态。

`1.1.2` 修复多会话任务计划的起跑边界：创建计划后只终止当前 Run，计划 Prompt、主智能体管理工具与 Web/App 起跑均按 `source + session_id` 校验，不影响同一用户的其他对话。临时重要记忆注入预算提高到 20000 字符，并继续使用独立的 20000 字符输出防失控上限。插件生态新增 `wait_for_condition`，可在最长两小时内等待进程、路径和 TCP 条件，并用受限清理宽限返回正常超时结果。`kemo_app` 为显式激活部署增加被忽略的激活意愿文件和限流自动恢复；公开源码仍不携带凭据、激活状态或采集时间。Web 统一修复暗色主题固定浅色块、工具调用展开正文以及长步骤计划在输入框上方起跑时的双滚动溢出。

`1.2.0` 引入由用户显式授权的会话级长任务模式。开关和运行统计保存于 `history_sessions.record_json.long_task`，严格按 `(user, source, session_id)` 隔离，新会话默认关闭。当且仅当底层 Run 以 `status=limited`、`stop_reason=max_tool_iterations` 收束时，Web 编排层才完整提交当前 Run、生成下一 Run ID 并发送非终态 `long_task_update`；最终只发送一次 `done` 或 `error`。关闭开关不打断当前 Run，取消接口会取消整个逻辑长任务。续跑控制轮次使用 synthetic metadata，历史界面渲染为边界横条，记忆与历史摘要继续使用原始用户请求。Web 对话操作菜单提供独立开关，输入框上方状态气泡展示原始任务、累计耗时、Run/续跑次数、工具调用、Provider 请求和 Token 用量。自动、手动及 Provider 超限压缩通过非终态 `context_compression` 事件显示开始、摘要就绪或失败；队列模式下摘要就绪不等于裁剪轮次的记忆分析已经完成，后台游标追平才是完成判据，零新增候选允许正常提交。任务计划执行、上下文保护、Provider 失败及其他受控终态不会被自动续跑。完整客户端合同见 `global_knowledge/long-task-runtime.md`。

`1.2.1` 加固高频运行和持久化边界。历史 archive/runtime、轮次分区、上下文摘要、会话索引与活跃绑定统一在 SQLite 事务中提交，并用跨进程锁保护仍需兼容 JSON 的写路径；系统 Cron 通过操作系统文件锁选出单一领导实例，正常状态使用内存检查点并周期写回，成功执行按窗口聚合，错误与部分失败仍立即持久化。主智能体与 `AgentRunner` 子代理统一采用整批工具参数校验，损坏的并行调用不会先执行其中有效部分；没有媒体等不可安全重放副作用时，可以保留已流出的文本和思考并发起有界纠错。Web 能力引用抽屉统一覆盖拓展、技能和插件。`kemo_app` 1.1.4 在后台 Run 日志与恢复快照上增加跨进程生命周期锁、Windows 启动器交接宽限、同实例 PID 对账、未受管实例保护、端口冲突分类与可自动解锁的临时退避。持久化细节见 `global_knowledge/persistence-write-path.md`，工具恢复边界见 `global_knowledge/provider-tool-call-safety.md`。

## 1.2.2

`1.2.2` 是稳定性和维护更新。审计范围包含 **2026 年 8 月 22 日至 2026 年 8 月 23 日**的改动；`18bb0c5` 的 Git 时间为 **2026 年 8 月 23 日 01:56:44（+08:00）**，按当前日期属于本次发布检查范围。

本版做了这些事情：

- 将 `run/` 按职责拆成领域包，删除旧的 `run.*` 平铺导入路径；外部插件必须迁移到新的入口。
- 修复启动器的项目根目录判断和备用 Web 端口；本机 `kemo_app` 桥接跟随实际 Web 地址，桥接修复版本为 `1.1.5`。
- 任务计划支持 edit、retry、reset、revision 和安全 rollback；保存任务计划前对明显凭据形态做脱敏。
- 任务计划 Web edit/retry 必须携带计划所属 `session_id`；后端会拒绝跨对话空间修改。任务摘要、执行记录和 revision 列表在返回浏览器前再次递归脱敏，兼容清理旧数据库中的敏感内容。
- Windows 桌面网页端支持每用户独立的运行结束音效和运行失败音效；移动端不显示也不播放。
- 成功音效只接受显式 `status=completed` 的最终 `done`；失败音效只接受明确的最终 `status=failed/error`。暂停、拒绝、停止、取消、受限、缺失状态和长任务中间 Run 均不播放。
- 发送附件后立即清除引用；运行中引导上传使用 `purpose=input`。
- 增加包结构、项目路径、备用端口和用户模板合同测试。
- 更新器改为单入口：根 `update.py` 只调用 `update.cli.main`，实际实现按职责拆分在 `update/`。
- 更新器增加单实例锁、远程版本清单与克隆源码一致性校验、失败即停和源码自动恢复。
- 全量更新检查根版本和四个组件版本，任何组件降级都会被拒绝；更新日志、Git 错误、板块详情和配置差异输出前统一脱敏。
- core 更新保留 `cron/task_cron_system` 的本地调度状态；同 schema 的 `global_config.json` 只补远程新增默认值，不覆盖本地值，schema 不同时默认停止。

2026 年 8 月 23 日检查时的 42 项未提交内容包含 9 项本机运行态变化，不能全部加入发布：5 个 `cron/task_cron_system/*.json`，以及 `kemo_app`、`kemo_gateway_status`、`kemo_graph` 的 4 个清单/采集文件。其余内容由更新器源码、Web 源码、测试、文档和忽略规则组成。发布前要区分两类检查：`tests/`、`tests/template_tests/` 和前端测试会进入 GitHub CI；`开发临时目录/test_kemo` 与 `开发临时目录/release_check.py` 被 `.gitignore` 排除，只是本机发布前补强检查。任务计划 revision 目前没有自动保留上限，长期高频修改会增加 SQLite 大小；运行态 JSON、`runtime/` 和用户目录不属于版本文件。

## 1.2.3

`1.2.3` 是在 1.2.2 之后的稳定性补丁，重点收口工具调用、后台进程和重试边界：

- Kemo 网关先校验完整工具参数，再发布 `tool_call.completed`；并行调用按批次原子发布，非法调用统一进入 `response.incomplete`。
- 工具参数 Schema 增加递归、节点和数组安全上限，避免深层输入触发递归崩溃或绕过尾部校验。
- Shell 后台 Worker 强制执行 `deadline_at`，日志写入异常仍会 drain 输出；用户取消前复核 PID 身份，公开状态只返回项目根相对路径。
- Provider 诊断只保留有界、脱敏的信息；常见 Token/Key 别名、前缀 JSON、循环引用和深层结构都按安全规则处理。
- 对话流在取消、异常、缺失终态和参数重试时只归档一次当前正文/思考，避免跨 attempt 重复累加。

本版本不新增用户配置字段，不改变 Chat 协议固定思考档位，也不改变已有长任务、任务计划或记忆数据格式。版本检查、仓库卫生检查、Python 测试、前端测试和构建仍是发布门禁。

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

从 `0.1.x` 首次升级到 `0.2.x` 时，旧 core 清单尚不知道 `provider/` 和 `README_EN.md`。旧调度器默认执行全量更新时，会在 core 刷新更新模块后，由新版 web 板块补齐这两项兼容迁移，因此不需要再次使用 `--force` 更新。`0.2.x` 新调度器会显式关闭该桥接，单独更新 web 时不会越界修改 core。

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
