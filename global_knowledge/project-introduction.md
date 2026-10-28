# kemo-agent 项目介绍

## 项目定位

kemo-agent 是面向个人智能基础设施的本地优先、多用户 Agent Runtime。它不是只保存当前窗口
上下文的聊天壳，而是把对话历史、长期记忆、知识、工具、子代理、任务计划、定时调度、感知、
拓展和多入口交互组织成持续存在的用户工作空间。

当前稳定版本为 `1.5.2`（2026-10-26 正式定档），兼容基线保持 `kemo-adapter-api 1.0.0`、
`kemo-graph 1.6.0` 和 Kemo 2.0。版本号、各组件版本和兼容网关基线以根目录 `version.json`
为准。本文只保留最新版更新说明；长期有效的实现合同请阅读对应专题文档。

项目的核心目标是：

- 让对话在不同时间和入口之间保持连续；
- 让数据、授权和运行状态由部署者掌管；
- 让复杂任务可规划、可暂停、可继续、可回溯；
- 让能力通过明确合同扩展，而不是把所有逻辑堆入对话主循环。

## 最新版更新说明

### 1.5.2（2026-10-26）

- 2026-10-28 同版本修复整体上下文压缩：手动、轮数、本地 Token、API 实际输入量
  和上下文拒绝共享轮数优先、Token 后备管线；轮数已解决上限不继续追逐比例。
  补齐实际输入量轮末/跨轮检查、摘要完整请求预算、超大单轮无丢字分片及有界尺寸恢复；
  HTTP 413 独立分类且要求内容尺寸下降，失败保留原历史和旧摘要，不重复执行业务工具。
  本项为源码修复，不表示部署版或远程产物已经更新。
- 2026-10-27 同版本补齐页面租约与并行派发：正常刷新/退出有序释放自身持久租约，
  防止迟到心跳复活或旧释放抹掉新激活，真实多窗口保护及异常退出 90 秒回退不变。
  普通本地非串行 LLM 子代理 call/resume 默认后台提交，显式同步及特殊代理合同不变；
  卡片区分等待提交/排队/运行，仍共享 API 池、空间公平调度和必要依赖完成屏障。
  history schema 7→8 采用备份后事务迁移，应用版本与当前 reference UI 不变。
- 2026-10-27 同版本预览完善运行安全：宿主控制/资源异常不进入业务重试，
  主子智能体共用动作级复用规则，动态查询实时刷新、成功副作用防重。
  补强 Chat 未知高级能力拒绝、独立导入、网页请求错误和语音识别生命周期回归；
  当前 UI 入口及版本保持不变，不表示部署版或远程渠道已经更新。
- 2026-10-27 同版本完善子代理分段预算：主智能体可指定每段工具额度（0 为不限次数）
  和运行时长；额度耗尽后保留现场、结论与用量，原任务续跑获新段额度并保留累计次数。
  增加预算/权限披露、软预警、运行中 guide、显式 split、结构化停止原因与 Shell 归属接管，
  不自动无限重派、不回灌工具轨迹。
- 子代理协作加入 `general_worker` 通用执行助手、后台任务账本与主任务完成屏障。
  工作面板按主任务分组展示子任务，工具参数和结果可展开，回复渲染为独立正文容器；
  向主智能体默认只交付结论、必要证据与未完成事项，不回灌工具轨迹。
- 本地 LLM 业务子任务支持超时收尾、保存已有结论和原任务显式续跑；尚未真实退出的任务保留
  执行占用与屏障。多空间调度和共享 API 请求池按空间公平轮转，同空间多窗口不重复配额，
  空闲执行容量可借用；默认等待区总量 50、单空间 25，并保护 5 个其他空间首次入队位置。
- 新增 `attach_media`，将已取得媒体载入当前 Run 的下一次原生模型请求，并联动工具卡、
  相关文件和历史展示。Chat 保留最大 20 MiB 原图，Kemo 仍遵守 1 MiB 内联边界，大图走 Asset；
  装载成功、网页预览与模型实际收到媒体分别记录，不混为“已看过”。
- Chat 基础工具与兼容档案由框架自动选择，用户无需自行检测；完善工具续接、结构化输出、
  思考参数与厂商白名单。保留 DeepSeek 专用适配，高级未知媒体能力继续 fail-closed，
  Kemo 2.0 与网关兼容基线不变。
- 网页右侧增加多标签工作面板，支持文件预览、当前空间后台 Shell 和子代理详情；默认草稿本，
  支持本机保存、放入聊天框和复用既有发送/排队。终端独立卡片展示任务归属、完成时间与耗时；
  关闭标签不取消任务。相关文件浮层支持内部滚动，空记录隐藏，复制路径以框架工作区为基准；
  状态面板展示真实 API 池占用及当前对话空间 Token 缓存命中率。
- 后台 Shell 结束后向所属主/子智能体交付可确认的短通知，仅含状态、时间、退出码及有界五行尾部，
  完整日志仍按需查询；有限任务默认 required 完成屏障，服务显式 detached，不擅自启动新模型请求。
- 历史对话支持置顶、收藏和右键操作，查询工具增加按日归档目录与归档正文分页读取；
  全部删除包含只读来源，并保护活跃任务与迟到提交。知识引用支持分类筛选，
  记忆搜索改为放大镜/Enter 显式执行，永久记忆不展示权重输入。
- 主题页提供九种语言、用户 `background.*` 图片/视频、UI 透明度和毛玻璃设置；
  背景图片上限 100 MB、视频 400 MB，视频不依赖系统减少动态效果偏好。
  优化 Token/账号认证排版、正文与工具卡；保留引导和任务计划内部滚动及当前新版 UI 入口。
- 修复 Web SSE 心跳和断开收尾，完善 SQLite 迁移前备份、版本检查、事务/CAS、
  更新前用户库备份、会话租约及定时/后台任务终态。操作手册增加来源契约，
  正式测试、开发期 `test_kemo` 与构建后分发验收共同防止旧构建和缺失模块进入交付。

本次定档是源码版本更新。远程 Release、npm 与 GHCR 产物仍须单独构建、验收和发布，
本地检查通过不表示所有第三方模型、远程渠道或目标设备已经完成实机联调。

## 核心能力

### 潮汐式生命周期记忆

Kemo Tidal Engram 记忆系统把记忆分为七天、一个月、半年和永久四档。用户历史中的可回溯
证据通过完整短语优先、带覆盖率门槛的多关键词搜索定位已有碎片；确认语义一致后复用稳定
文件名，使临时记忆每日最多加权一次。达到阈值后晋升，未达到阈值的过期内容淡出。

`memory_temporary_important.md` 是从临时三层单向提炼的可重建热视图，不是第五个记忆层，
也不能反向作为源记忆加权证据。热视图只强化高价值事实，不替换或阻止七天、月、半年三层
权威碎片按各自上限正常注入。其 Prompt 注入预算已提升至 20000 字符；语义独立的 20000
字符输出上限只用于阻止模型失控重复。

### 长对话与上下文压缩

完整历史保存于 archive 窗口，Provider 使用可裁剪 runtime 窗口。达到轮次或 Token 条件时，
框架通过 `context_manage` 生成摘要并事务化裁剪 runtime；完整归档不会因压缩而被删除。
1.5.2 的整体压缩入口统一先轮数、再 Token；API 实际输入用量支持轮末和跨轮校准。
摘要请求有独立完整预算，超大单轮内部安全分片，失败不丢历史。

Web 只加载最近消息，需要时用游标继续读取更早正文，避免历史增多后每次启动全量扫盘。

删除会话会同时清理归档、运行时窗口、正文、活跃绑定和后台副本，并在历史库中留下很小的删除栅栏。
如果旧 Run 在删除之后才结束，它的迟到提交会被丢弃，不会让已删除会话重新出现。知识库索引同样会
拒绝符号链接和 Windows junction，避免把项目目录外的文件带入 Prompt。

### 会话级长任务

长任务模式把“单个 Run 的工具调用上限”保留为稳定保护边界，同时允许用户授权当前对话在命中该边界后
开启下一 Run。中间 Run 不向客户端发送终态 `done`，而是发送 `long_task_update`；最终 Run 才结束整个
逻辑任务。Web 输入框上方展示原始请求、累计耗时、Run/续跑次数、工具调用、Provider 请求和 Token
用量，并提供会话级取消。内部续跑控制消息带 synthetic metadata，历史界面只显示续跑边界，记忆和
摘要仍使用原始用户请求，避免控制提示污染长期数据。

### 三层知识库

知识分为：

- `global_knowledge/`：框架级公共知识；
- `shared_knowledge/`：跨用户共享资料；
- `users/<user>/knowledge/`：用户私有资料。

系统 Prompt 只自动注入各层索引，正文按需读取。Kemo Graph 是可选的侧载超级文档站，只有
用户明确要求时才查询、同步或整理，不接管本地知识和记忆。

每层知识目录使用自己的 `classify.sqlite3` 分类表绑定相对文件路径与分类名；分类不移动
知识正文，数据库随目录一起搬运。网页支持分类筛选和归类操作，全部层级视图会标注分类来源。

### 工具与技能

`plugins/` 是唯一能注册 Provider function call 的目录。每个插件使用 `SKILL.md` 描述操作规则
和 Tool Schema，并由本地 Python 入口执行。

长时间后台任务应由专门工具先启动，再使用 `wait_for_condition` 等待可靠的 PID、路径或端口
条件；最长等待 7200 秒，条件满足立即返回。等待超时只代表条件尚未满足，不等同于后台任务
失败。插件可声明最多 30 秒的外层看门狗清理宽限，但不得借此增加实际业务期限。

技能只提供可复用指令，不注册工具。共享技能位于 `shared_skills/`，用户技能位于
`users/<user>/user_skills/`。Web 支持安全上传技能 ZIP 并递归发现 `SKILL.md`。

### 子代理

内置子代理包括：

- `context_manage`：上下文摘要和压缩；
- `general_worker`：在当前用户授权范围内承接通用执行，禁止再次委派；
- `history_summary`：后台历史标题/摘要；
- `memory_temporary_important`：临时重要热视图维护；
- `self_improve`：记忆候选提取和晋升决策；
- `task_plan`：任务计划生成；
- `time_plan`：定时任务参数规划。

用户可以在 `users/<user>/agents/` 创建自己的子代理。每个子代理拥有独立 Prompt、模型档位、
工具白名单、知识权限、超时和取消信号，不继承主智能体全部权限。
并行协作、仅结论交付、超时续跑和对话空间公平调度见 `agent-task-coordination.md`；
网页观察详情不会增加主模型上下文中的工具轨迹。

### 任务计划与定时任务

任务计划创建后，创建它的当前主智能体 Run 会在同一会话边界强制收束；同一 Provider 响应中
排在创建工具之后的调用不会执行。计划存储仍按用户集中管理，但 Prompt 注入、主智能体管理
工具和 Web/App 起跑都会校验 `source + session_id`，因此 A 对话不会把计划或暂停状态传播给 B 对话。

任务计划支持创建、审批、逐步执行、暂停、继续、失败和取消。计划、步骤与依赖存入用户级
SQLite。

定时任务使用北京时间，支持一次性和周期性规则。RuntimeHost 负责 Cron 扫描、系统维护、
历史摘要、任务计划调度和消息路由。

### 感知、拓展和外部消息

- 感知：从已授权数据源单向采集状态并注入 Prompt；
- 拓展：提供状态摘要和显式操控入口；
- 外部消息：把 Telegram 等平台适配为统一消息传输合同；
- Kemo 网关状态拓展：只读查看已配置网关运行状态；
- Kemo Graph 拓展：连接用户注册的内置或绝对路径 Library。

这些模块采用“最小框架合同 + 内部自由工作区”。模块可以很小，也可以包含完整工程，但必须
遵守清单、入口、权限、超时和输出协议。

### 多模态

首次输入和运行中引导都可携带图片、音频、视频和普通文件。Kemo 协议根据模型能力和 Asset
API 处理已支持模态；Chat 兼容协议保留图片直传链路，其余资产通过专用多模态或文件工具处理。
`attach_media` 可在工具取得文件后将其排入下一次原生请求；它与委托专用模型分析的
`multimodal` 是不同入口。装载队列、授权范围与实际发送状态见 `plugin-development.md`。

### Web 正文内联组件

Web 回复可以在自然语言段落之间直接镶嵌 `kemo-widget` 声明式组件。内置目录覆盖卡片、指标、图表、
表格、时间线、标签页、折叠、差异、日程、看板，以及市场常见的组合 UI、位置示意、影音播放器、
文件列表、商品网格、订单摘要、投票和评分。组合 UI 进一步提供卡片/列表/行列布局、Markdown、图标、
图片、按钮、日期、选择、输入、复选、单选、滑杆和开关节点。

组件交互只改变当前组件本地状态；只有用户明确点击后，按钮、表单、投票或评分才能产生回填输入框、
发送纯文本或复制文本三种有限动作。媒体和文件只引用站内根相对 URL，位置组件不加载第三方地图瓦片，
订单组件不支付或调用商户 API。完整协议与兼容规则见 `inline-widgets.md`。

### Provider 双模式

Kemo 协议是第一优先级的原生链路，提供 Asset API、SSE 续传、统一 Usage 和幂等取消。Chat
Completions 兼容模式面向第三方 OpenAI 兼容服务，默认净化未声明的思考控制与 metadata，
并支持显式厂商字段映射、思考别名和真实签名状态续接；DeepSeek 使用 adapter 内的专用策略。
流式工具调用支持宽容聚合，截断调用不可执行。传输层每次只发起一次 HTTP 交换，
由统一运行级规则决定重试；工具不支持时明确报告。两种模式在一次 Run 内固定，
不自动互相回退。细节见 `provider-reliability.md`。

### 多入口

同一用户可以通过：

- Web；
- `cli.py`；
- 外部消息平台；
- Cron/任务计划；
- 框架内部维护任务

访问同一历史、记忆和已授权资源。入口不同，不代表创建新的用户身份。

App 桥接在重启恢复时可以按设备 ID 查询该设备的全部活动 Run，再按明确的 `session_id` 读取快照和
继续事件流；没有设备或会话范围的查询会被拒绝，避免把一个用户的所有运行无范围暴露给客户端。

## 本地数据与隐私边界

kemo-agent 本地管理对话、记忆、知识、任务和用户文件。不同用户使用独立工作空间，主配置和
模块白名单决定可使用的资源。

本地优先不等于模型请求永不离开机器。发送给外部 Provider 的 Prompt、历史片段、附件和工具
结果取决于用户选择的服务、模型能力和授权范围。部署者应同时审查 kemo-agent 配置和上游服务
隐私政策。

凭据通过 `.env`、用户配置或运行环境提供，不应写入知识库、记忆、日志、回复或 Git。

## 环境要求

按安装渠道区分：

- Windows/Linux Release 部署：Python 3.10+；Linux 需要可用的 venv/ensurepip，应用依赖仍需安装；不要求 Git/Node.js。
- npm：Node.js 18+、npm、Python 3.10+ 与可用的 Python 虚拟环境。
- Docker：Docker Engine 与 Docker Compose，宿主无需安装 Python/Node.js。
- 源码部署：Python 3.10+、Git、Node.js/npm；只有源码前端构建才要求 Node.js。

Release 带预构建前端。完整渠道选择、日常操作和发布合同见 `deployment-and-release.md`。

## 获取与首次部署

### 一键部署

当前源码已正式定档为 1.5.2；下列一键命令只有在对应远程产物实际发布后才可直接使用。原生安装依赖 Release 的
`kemo-agent-release-<version>.zip` 与校验文件；npm/Docker 各自需要发布包/镜像，推送代码本身不等于发布。

Windows PowerShell：

```powershell
irm https://raw.githubusercontent.com/kesepain-KE/kemo-agent/main/deploy/windows/install.ps1 | iex
python "$env:USERPROFILE\.kemo-agent\deploy\deploy.py" start
```

Linux：

```sh
curl -fsSL https://raw.githubusercontent.com/kesepain-KE/kemo-agent/main/deploy/linux/install.sh | sh
python3 "$HOME/.kemo-agent/deploy/deploy.py" start
```

npm：

```sh
npm install -g https://github.com/kesepain-KE/kemo-agent/releases/latest/download/kemo-agent-npm.tgz
kemo
```

Docker（在固定专用目录中执行，不覆盖已有 Compose 文件）：

```sh
curl -fsSL https://raw.githubusercontent.com/kesepain-KE/kemo-agent/main/deploy/docker/docker-compose.yml -o docker-compose.yml && docker compose up -d
```

原生脚本只安装，首次 `start` 才调用原初始化向导；可以先下载脚本审查再执行。
安装根默认位于用户主目录 `.kemo-agent`（Docker 为 `/data`），不能接管开发仓库或其他渠道安装。

### 源码部署（开发者）

```bash
git clone https://github.com/kesepain-KE/kemo-agent.git
cd kemo-agent
python setup.py
```

`setup.py` 引导依赖安装、环境配置、前端构建和用户创建。非交互默认部署：

```bash
python setup.py --yes
```

可选参数包括：

- `--skip-deps`：跳过 Python 依赖安装；
- `--skip-web`：跳过前端构建。

跳过步骤后，部署者需要自行完成相应依赖或构建。

## 用户创建

setup 可在首次部署中引导创建用户，也可以独立运行：

```bash
python user_create.py
```

用户目录包含配置、人格、历史、记忆、知识、技能、拓展、子代理、上传和下载空间。完整结构见
`knowledge-and-user-data.md`。

## 启动 Web

下面是源码安装入口。Release 安装应使用安装根内 `deploy/deploy.py start`，npm 使用 `kemo`，
Docker 使用 `docker compose up -d`，不要绕过各渠道的运行锁与初始化入口。

```bash
python start_web.py
```

默认监听端口为 1357：

```text
http://127.0.0.1:1357
```

如果默认端口被占用，启动器会在有限范围内尝试后续端口。也可以显式指定：

```bash
python start_web.py --host 0.0.0.0 --port 1357
```

开放局域网或公网监听前必须配置认证、TLS 反向代理、防火墙和可信代理边界。

### Web 认证

Web 认证支持：

- `WEB_ACCESS_TOKEN`；
- `WEB_USERNAME` + `WEB_PASSWORD`；
- `WEB_SESSION_SECRET`；
- 登录失败限流和可信反向代理配置。

用户名和密码必须同时配置或同时留空。详细字段、优先级和安全规则见 `configuration-reference.md`。

## 命令行入口

```bash
python cli.py
```

CLI 复用核心对话引擎和用户数据，不是另一套独立历史系统。需要后台 Cron、消息路由和维护任务
时，应通过 RuntimeHost 入口运行，而不是假定一次性 CLI 进程会永久驻留。

## 更新与重启

先识别安装渠道，停止应用后再更新；不能交替使用两个更新器管理同一个安装：

- Windows/Linux：使用安装根内 `deploy/deploy.py check` / `update --yes`，再 `start`。
- npm：先 `npm install -g https://github.com/kesepain-KE/kemo-agent/releases/latest/download/kemo-agent-npm.tgz`，再 `kemo`；`kemo update` 只使用本机分发包版本。
- Docker：在同一项目中 `docker compose stop` → `docker compose pull` → `docker compose up -d`；禁止以 `down -v` 更新。
- 源码：使用下面原业务更新器，仍支持 core/agents/plugins/web 板块。

安装根、完整可复制命令、恢复与数据边界见 `deployment-and-release.md`。

**以下仅描述源码更新器：**

```bash
python update.py
```

根目录 `update.py` 是兼容入口，实际逻辑集中在 `update/` 包。更新器支持按 core、agents、
plugins、web 四个板块同步，使用单实例锁、远程源码一致性校验和失败恢复，并保护用户数据、
配置、Cron 运行状态、运行时数据库和模块派生存储。全量更新会同时检查根版本和四个组件版本，
不允许隐式降级；输出到终端或 CI 的仓库 URL、配置差异和错误信息会先脱敏。详细规则见
`version-and-update-modules.md`。

`restart.py` 是 Web 受控重启辅助入口，一般由 Web API 调用，不建议把它当作常规手工启动命令。
重启后新进程会重新读取 `.env` 和磁盘配置。

## 常用目录

| 路径 | 内容 |
|------|------|
| `run/` | 12 个领域子包；`engine.py` 为总门面，`__init__.py` 保持懒加载 |
| `provider/` | Kemo 与 Chat Provider 实现 |
| `web/` | FastAPI 后端和 React 前端 |
| `agents/` | 内置子代理 |
| `plugins/` | Provider 可调用工具 |
| `global_sense/` | 全局感知 |
| `global_expand/` | 全局拓展 |
| `shared_skills/` | 共享技能 |
| `global_knowledge/` | 框架级知识库和主索引 |
| `users/<name>/` | 用户私有工作空间 |
| `template/` | 模块和用户创建骨架 |
| `deploy/` | 四渠道 Release 部署、事务更新、恢复、打包和渠道入口 |
| `tests/` | 会上传的正式发布红线，按领域及 contracts/runtime/storage 职责组织 |
| `tests/deploy/` | 独立部署器、Release 归档、四渠道命令与文档一致性的正式测试 |
| `tests/template_tests/` | 六类模块创建后合同验收 |
| `开发临时目录/test_kemo/` | `.gitignore` 排除的本机系统/集成补强；其中 deployment_distribution 负责真实打包和隔离安装，不与正式测试重复断言 |

`run/` 的公开调用只允许从 `run.<领域>` 入口导入；顶层只保留懒加载的 `run/__init__.py` 和
`run/engine.py` 总门面，不再维护旧平铺模块或平行兼容层。完整导航见 `data_structure.md`。

## 关键入口

| 文件 | 用途 |
|------|------|
| `start_web.py` | 启动 RuntimeHost、FastAPI 和前端 |
| `cli.py` | 命令行对话 |
| `setup.py` | 首次部署 |
| `user_create.py` | 用户创建与管理 |
| `update.py` | 源码安装的更新薄入口；实际实现位于 `update/` |
| `deploy/deploy.py` | Release 安装的统一 install/update/check/recover/init/start 入口 |
| `deploy/pack.py` | 从主框架版本生成 Release ZIP、npm 暂存目录和 Docker 构建上下文 |
| `restart.py` | Web 受控重启辅助进程 |
| `version.json` | core/agents/plugins/web/all 版本 |

## 扩展开发入口

| 需求 | 文档 |
|------|------|
| 开发工具插件 | `plugin-development.md` |
| 创建技能、子代理、感知、拓展或外部代理 | `module-development.md` |
| 创建消息平台适配 | `external-message-route-creation.md` |
| 理解整体运行方式 | `architecture-overview.md` |

## 相关项目

- `kemo-adapter-api 1.0.0`：当前网关兼容基线，使用精确 Kemo 2.0 线路协议；
- `kemo-graph`：可选知识图谱和大型文档 Library 服务；
- `kemo-agent-doc`：面向用户的独立文档站；
- `votx-agent`：独立维护的 Agent 项目，与 kemo-agent 不存在继承关系。

这些项目可以协作使用，但 kemo-agent 的本地历史、记忆、知识和主对话能力不应因可选项目未启动
而整体失效。

## 开源协议

kemo-agent 使用 Apache License 2.0。使用、修改、分发和 NOTICE/声明要求见
`open-source-license.md` 与仓库根 `LICENSE`。
