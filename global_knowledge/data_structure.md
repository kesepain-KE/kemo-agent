# kemo-agent 框架骨架与全局知识索引

本文是框架目录导航，也是全局知识库的主索引。这里只保留稳定结构和文档入口；具体创建步骤、字段约束与示例请进入对应专题文档。

常驻手册检索：L1/L2/L3/L4 准入、能力地图、系统自动行为前提、机制/纪律/入口、
配置基线与有效值、规则标识、agents-manual.contract.json、AST 来源变化闸门、
check_agents_manual、内置能力可达性、路径模板/外部工程分类、反例验收及 Token 预算，
见 `prompt-authoring-standard.md` 的“常驻手册的准入、来源与契约”；
重复调用/大结果回执见 `plugin-development.md`，系统维护触发条件见 `task-automation.md`。

数据库迁移安全检索：schema 超前拒绝、非法版本、DDL 前检查、BEGIN IMMEDIATE、
迁移前备份、SQLite backup API、WAL 完整快照、只读第二连接、防自身写事务备份死等、
事务建表/补列/版本戳、失败回滚与可重试、当前版本缺表/列修复、.partial、
runtime/db_backups、manifest.json、更新前四库备份、不自动清理、源码回滚不等于数据回滚、
停止写进程与手工单库恢复，见 `storage-and-persistence.md` 的“SQLite 初始化、迁移与恢复安全”
及 `deployment-and-release.md` 的“旧源码更新器的数据库恢复边界”。

收尾边界检索：history 缺列/丢版本、compact 幂等迁移、混合分区拒绝、业务恢复演练、
来源隔离租约、低层末窗删除、读修复 CAS 三次重算，见 `storage-and-persistence.md`；
provider_options 全量白名单与原生本地 profile 见 `provider-reliability.md`；
免认证反代、可信代理及关键 JSON 响应模型/安全 500 见 `frontend-conventions.md`。

历史对话个人标记检索：置顶/取消置顶、收藏/取消收藏、history_session_marks、schema v7、
marks-v1 复合游标、favorites_only/favorites_count、服务端分页前筛选、只读来源个人标记、
右键菜单/触屏 ⋯、日期下方收藏气泡、搜索放大镜/Enter、侧栏同步、session-updated、
删除外键级联、普通 Run 不覆盖标记，见 `storage-and-persistence.md` 与 `frontend-conventions.md`。

工作面板视觉分组与实时请求池检索：多主任务独立容器、子智能体缩进与子卡片、提交顺序、
RuntimeCongestion、sections=congestion、1.5 秒局部刷新、Provider 占用/可用/排队、
工具执行不占模型槽位、停止后台轮询与取消在途请求，见 `frontend-conventions.md`。

对话空间公平与多窗口余量检索：可信 root/user/source/session 身份、同空间多窗口不重复配额、
单空间等待 ceil(Q/2)、默认总等待 50/单空间 25、新空间保护 5 个入队位置、
存活占用较少者优先/同占用轮转、空闲执行/API 容量可借用、非抢占、
取消超时清票据、resume 失败保留原结果及进程内边界，
见 `agent-task-coordination.md` 与 `provider-reliability.md`。

通用执行助手检索：general_worker、通用子智能体、文件处理/脚本/检索/批量/测试、
当前用户动态插件授权、allow=["*"]、共享技能白名单、tools.user_skills.allow、
user_knowledge、技能来源路径、拓展/感知不自动注入但按需使用、禁止递归调度、
外部桥接命令防绕过、非操作系统沙箱、独立上下文、后台必要依赖，
见 `module-development.md` 的“内置通用执行助手 general_worker”与 `agent-task-coordination.md`。

子智能体后台协作检索：main-task、root_task_id、attempt_id、必要依赖、wait=false、
有界并行、共享 Provider 请求池、max_concurrent_requests、串行维护通道、超时未退出占额、
submission_order、提交序号、同一时刻返回稳定排序、同批结果顺序、不阻塞可用结果、
完成屏障、waiting_children／merging／cancelling、结果通知／确认／去重、timed_out_running、
agent_tasks.sqlite3、workspace/agents、右侧任务树／详情标签、旧进度浮卡替代、关闭不取消、
进程创建时间与 interrupted、不重放副作用，见 `agent-task-coordination.md`。

子智能体超时续跑检索：超时停止／超时停止中、仅结论交付、不返回工具卡片、resume、
guidance、同 task_id、run_count、重新计时、私有 agent_checkpoints、未执行工具配对、
并发恢复裁决、轮次代际、恢复权限重新求交、部分结果不进入正常提交、32 轮安全上限，
见 `agent-task-coordination.md` 的“超时停止与原任务续跑”。

子智能体执行详情检索：时间右侧行为卡片、tool_call_id 合并、参数/返回结果展开、
主聊天式单行参数摘要/圆形状态图标、请求模型节点仅界面隐藏、渲染正文独立容器、
旧记录未保存详情、public_tool_data 脱敏与截断、Markdown 回复/表格行数组渲染、
Token 九语言、locale 数字格式、缓存百分比、input/prompt 与 cached 别名去重，
见 `agent-task-coordination.md` 与 `frontend-conventions.md` 的“子智能体执行详情”。

相关文件浮层定位检索：整体左移、右边框避让、未展开历史对话滚轮、32px 偏移、
8px 轨道间隔、窄屏头像安全留白、固定栏头/内部滚动、同会话后台 Shell/子智能体记录、
六条分批与服务端分页、打开轮询/关闭停止、点击工作面板详情，
见 `frontend-conventions.md` 的“当前对话文件历史”。

历史全部删除检索：只读对话、全部渠道、source=all、client_id、未加载分页、日期/搜索不缩小范围、
Web/APP/CLI/Cron/外部消息、同名 session_id 来源隔离、事务回滚、ActiveRun/执行锁/跨进程租约 409、
删除栅栏防复活、readonly-history 缓存、记忆/附件/任务定义保留，见 `storage-and-persistence.md`
的“网页全部删除历史（含只读对话）”与 `frontend-conventions.md`。

全局了解手册分类检索：基础与总览、配置与知识、运行与调度、模块与集成、网页与交互、
部署与更新。下方专题文档表按上述分类导航；网页分类标签使用
`global_knowledge/classify.sqlite3`，维护规则见 `knowledge-and-user-data.md` 的“全局了解手册分类”。

输入坞溢出检索：跟进／引导消息栏、长日志、计划标题和描述、步骤正文、
内部滚动、composer-auxiliary 辅助区限高、多卡片叠加、窄屏发送框可见，见 `frontend-conventions.md`；
正式 Vitest 回归、test_kemo 发布入口可达 CSS 与包内文档验收，见 `deployment-and-release.md`。

记忆搜索检索：输入丢字/焦点丢失、禁止逐字符重建 DOM、草稿与已提交条件分离、
右侧放大镜按钮、Enter 手动搜索、IME 组合输入保护、清除恢复、分页重置及刷新保留，
见 `frontend-conventions.md` 的“记忆详情”。

新建延迟检索：创建新对话空间、保存并新建、创建回执租约复用、并发 Promise 合并、
旧/无绑定 overview 避免重复刷新、ASGI 事件循环、关闭与摘要单行事务、
load_window_data、metadata_only 记忆入队，见 `frontend-conventions.md` 与 `storage-and-persistence.md`。

任务清理检索：任务中枢、清理已完成、计划/定时任务/执行记录、跨分页清单确认、ID/version
复核、并发跳过、task_hub_cleaned_records、执行记录持久列表清理与原始日志保留，见
`task-automation.md`、`storage-and-persistence.md`、`frontend-conventions.md`。

运行诊断检索：同屏系统提示词与组件拼接状态、小时 Token 刻度与极值、用户 API 脱敏模型路由配置、组件路由卡片、终端 OUT/ERR 与跟随滚动、消息日志输入输出同卡，见 `frontend-conventions.md`；消息轮次关联、合并后分页、旧库 schema v3、保留换行与绑定用户隔离，见 `storage-and-persistence.md`。

右侧工作面板检索：多标签、固定/关闭/调宽/最大化、默认白板、文件预览、ShellJobLink、
当前 Web 对话空间 Shell 后台任务、workspace/jobs、只读 GET、禁止全用户范围切换、stdout/stderr、
最近 64 KiB/1000 行、实时落盘、read1、本机编码、隐藏停止轮询及关闭不取消任务，
见 `frontend-conventions.md` 的“右侧多标签工作面板”与 `plugin-development.md`。

对话空间 Token 总命中率检索：当前 user/source/session 的持久化 usage、cache_hit_rate、
cached_prompt_tokens/cache_miss_tokens 回退计算、不复用用户每日 cache_rate、无可靠数据显示破折号，
见 `frontend-conventions.md` 的“对话空间 Token 命中率”。

缓存命中率检索：跨轮工具结果内容块、JSON 参数字段顺序、`provider_request_diagnostics`、SHA-256 累计前缀、Agent 映射前证据与网关/云端边界，见 `provider-reliability.md`。

Web 心跳超时检索：`/api/chat`、SSE 注释心跳、`sse_heartbeat_seconds`、反代静默超时、
单个 pending next、断开收尾、未消费即关闭、并发槽释放、任务计划最终化、原生 ASGI 认证、
BaseHTTPMiddleware 断开探测与排队 HTTP 503，见 `architecture-overview.md` 的“Web SSE 保活与断开收尾”
及 `configuration-reference.md` 的 `web` 配置。

工具异步返回值检索：同步/async 入口、Awaitable、`__await__`、`types.coroutine`、
`run_awaitable`、线程/隔离子进程共用桥接、Python 3.10–3.13、Future/Task 事件循环归属、
超时与取消边界，见 `plugin-development.md` 的“Python 入口”及“超时、取消与线程边界”。

多媒体方式三检索：`attach_media`、原生看图、媒体输入队列、next_provider_request、
ChatImageContent、Chat 20 MiB 原图、Kemo 1 MiB 内联边界、Base64 校验脱敏与 failed 状态、
asset_ids/paths、queued/prepared/included/failed、隔离进程装载意图、Run 重试去重、
Chat 图片与 Kemo Asset 音频/视频/文档、文件授权与父目录联接检查、上下文预算、
子代理模型 override/工具白名单及队列隔离，见 `plugin-development.md` 的
“三种多媒体输入方式与 attach_media”；领域接入边界见 `architecture-overview.md`。

原生媒体网页联动检索：attach_media、相关文件、工具装载的媒体、工具卡预览、
media_input_update、done.media_inputs、历史恢复、旧回执 paths、安全描述符、
download 缩略图、not_sent/unconfirmed、预览不调用模型/不复制原件，
见 `frontend-conventions.md` 的“原生媒体工具与相关文件联动”及 `plugin-development.md`。

DeepSeek 中转站检索：Chat adapter、官方主机与第三方 API 前缀、保留 /v1、
HTML 网页误命中、invalid_response、data 包装、reasoning_content、真实思考回传、
兼容占位、reasoning_synthetic、正文与工具消息合并、require_tool_reasoning 与工具续轮，
见 `provider-reliability.md`。

发布前检查检索：1.5.2、最新更新说明、release_check.py、test_kemo、
release_readiness、构建先于分发验收、安装树冒烟、Pyright 固定版本、release_ready、
quick/skip-frontend 局部检查边界和前提失败报告，见 `deployment-and-release.md`；
最新版更新说明见 `project-introduction.md`。

通用 Chat 兼容检索：最小请求字段、request_format none/fields/effort/openrouter、
enable_thinking、metadata_supported、effort_map、reasoning/reasoning_content/reasoning_details、
history_field、签名密文分片、chat-adapter opaque 状态、8192 字符持久化、
地址与模型绑定、占位不回传、单次 HTTP 请求、SSE data 包装与 JSON 降级，
见 `provider-reliability.md`。

网页会话恢复检索：任务切页、首轮 URL 绑定、sessionStorage 标签页身份、localStorage 最近空间、
显式链接优先、lease 验证、重启健康检查和聊天页回跳，见 `frontend-conventions.md`。

网页多语言检索：`web_ui.language`、九种语言、外观与主题、`user_config.json`、
`kemo-language:<user>`、用户隔离、保存回滚、中文回退、显式词条、阿拉伯语移除与旧配置回退、
用户内容不翻译，见 `configuration-reference.md` 与 `frontend-conventions.md` 的“网页多语言与用户隔离”。

网页认证排版检索：AuthLayout、AuthPage.module.css、Token/账号密码两阶段、步骤进度、
密码显隐、字段错误、请求锁定、阶段清理、加载与重连、手机与矮窗口滚动，
见 `frontend-conventions.md` 的“Web 访问认证布局”。

用户网页背景检索：`background.*`、`template/user/background.webp`、用户根目录、
UI 透明度、UI 毛玻璃、ui_opacity、ui_blur、聊天底部泛白、输入坞渐变遮挡、
图片 100 MB、视频 400 MB、视频不依赖系统动态偏好、
`web_ui.background`、图片/视频、静音循环、字节 Range、遮罩强度、模糊、关闭与确认清除、
新用户复制与旧用户不覆盖、目录联接拒绝、用户隔离、隐藏暂停/可见继续、播放失败主题回退，
见 `frontend-conventions.md` 的“用户网页背景”、`configuration-reference.md` 与
`knowledge-and-user-data.md`。

## 顶层骨架

记忆改造检索：schema v2、证据来源 user/assistant/manual/historical_migration、
不同日期出现次数、同轮用户背书与防自我强化、extraction/candidate/archive/skill 共用待办账本、
满页分页与历史游标、先归档后移除、expired_memory 普通 Markdown（不自动召回/注入）、
技能去重/质量门禁/source_memories/稳定来源传递/stale/失败续处理，见 `storage-and-persistence.md`；
用户骨架、三层知识库逻辑分类、`classify.sqlite3` 数据表、`未分类`默认规则与知识目录搬运边界见
`knowledge-and-user-data.md`；子代理各自 80 次、600 秒期限和技能合同见 `module-development.md`。

网页配置 UI 检索：SettingsModelSelect、模型搜索气泡、Chat/KEMO 目录隔离、草稿选择、
字号 14.5/15.5/17px、当前/可切换用户卡片、云端版本检查结果容器，见 `frontend-conventions.md`。
知识库 UI 检索：40px 等高工具栏、分类下拉筛选、已有分类计数、简化新建分类、详情三项归类操作、
全部层级分类来源标注、用户/共享/全局同名分类隔离、分层未分类、删除重复分类筛选、
文件列表等高截断与横向滚动，见 `frontend-conventions.md`。
知识库引用检索：聊天引用抽屉、知识文件标签/分类筛选、分类来源隔离与未分类、
名称/路径搜索叠加、匹配数、固定筛选区与独立列表滚动、长分类截断、窄屏引用按钮，
见 `frontend-conventions.md`。
永久记忆页面检索：无权重机制、隐藏权重数值/指示条与今日权重锁、详情无当前权重、
禁用权重排序与跨层切换实际排序回退，见 `frontend-conventions.md` 的“记忆详情”。
输入框模型选择检索：ComposerModelSelect、选项卡片 4px 间距、选中与悬停高亮，见 `frontend-conventions.md`。
全站控件检索：原生单选下拉主题、base-select、picker、明暗主题、长选项换行、
自定义选择弹层 4px 间距、思考档位、Provider/图片路由/模型、排序与菜单选项、
列表内部滚动、浏览器回退、搜索放大镜垂直居中、search-box、清除按钮，见 `frontend-conventions.md`。

```text
kemo-agent/
├── run/                    # 对话引擎、历史、Prompt、工具、记忆与运行时宿主
├── provider/               # Provider 协议、chat 兼容桥与 kemo 原生适配
├── web/                    # Web 后端与 React 前端
├── cron/                   # 定时任务调度器与系统维护任务
├── message/                # 平台中立消息路由；out/ 存放热插拔平台模块
├── agents/                 # 内置子智能体与子智能体运行时
├── plugins/                # Provider 可调用的工具插件
├── global_sense/           # 全局感知模块
├── global_expand/          # 全局拓展模块
├── shared_expand/          # 跨用户共享拓展模块
├── shared_skills/          # 跨用户共享技能
├── global_knowledge/       # 全局知识库（含 classify.sqlite3 分类表）
├── shared_knowledge/       # 共享知识库（含 classify.sqlite3 分类表）
├── users/<name>/           # 用户配置、人格、历史、记忆和私有资源
├── template/               # 用户、子智能体、技能、拓展等创建模板
├── update/                 # 源码更新调度、版本校验、备份、锁、恢复与四板块实现
├── deploy/                 # Windows/Linux/npm/Docker 独立部署、事务更新与发布打包
├── tests/                  # 后端测试
├── config/                 # 全局配置与全局人格
├── runtime/                # 结构化运行日志 SQLite（自动生成，不入 Git）
├── tmp/                    # 智能体中间文件，不作为用户交付目录
├── agents.md               # 智能体自身运行手册
├── .env / .env.example     # 本机环境变量与无密钥示例
├── cli.py / start_web.py   # CLI 与 Web 启动入口
├── user_create.py          # 用户创建入口
├── update.py               # 薄更新入口，调用 update.cli.main
├── version.json            # 总版本与板块版本
└── LICENSE                 # Apache License 2.0 正文
```

## 一次请求经过的主要层级

```text
入口（Web / CLI / 外部消息 / Cron）
  → run/engine.py 稳定公共门面
  → conversation_runtime / run_state / request_input / provider_events 领域运行模块
  → user + source + session_id 会话隔离
  → 全局配置与用户配置合并
  → 人格、子代理、插件、技能、知识索引、记忆、计划、拓展、感知拼接
  → Provider 与工具循环
  → 运行中引导在安全边界追加文本或多模态资产
  → 用户级 SQLite archive 完整归档 + 有界进程内 runtime 上下文工作区
  → 记忆、计划和后台维护管线
```

## 资源层级

| 资源 | 全局/共享层 | 用户层 | 核心区别 |
|------|-------------|--------|----------|
| 知识库 | `global_knowledge/`、`shared_knowledge/` | `users/<name>/knowledge/` | 只自动注入索引，正文按需读取；每层可用 `classify.sqlite3` 的 `classify` 表做逻辑分类 |
| 技能 | `shared_skills/` | `users/<name>/user_skills/` | 只提供指令，不注册可执行工具 |
| 拓展 | `global_expand/`、`shared_expand/` | `users/<name>/expand/` | 可提供状态注入和外部操控入口 |
| 感知 | `global_sense/` | 无 | 单向采集并注入，不提供操控 |
| 子智能体 | `agents/` | `users/<name>/agents/` | 独立 Prompt、权限与工具循环 |
| 工具插件 | `plugins/` | 无 | 唯一可注册 Provider function call 的目录 |

## 运行中引导的数据链路

网页对话执行期间的引导不是纯文本旁路。`web` 接口接收可选文本和最多 20 个当前用户上传文件路径，转换为带 `guidance_id` 的结构化消息后进入 `run/conversation/` 引导邮箱。该领域在下一个 Provider/工具边界重新验证资产，并把图片、音频、视频和普通文件分别按能力声明处理：Kemo 可通过 Asset API 直接接收已声明支持的模态；Chat 只沿既有视觉链路直传图片；其余资产继续向当前 Run 的 `multimodal` 或 `file` 工具开放。

引导附件与首次用户输入使用同一套用户目录隔离、类型识别、签名验证、大小限制和资产 ID 规则。当前 Run 未能接收时，文本与附件作为一个整体排入下一轮；已生效内容写入轮次的 `guidance_details`，其中只保留 UI 安全元数据，不保存绝对路径或内联数据。旧版纯文本 `guidance` 数据仍可读取。

## 专题文档

上下文压缩检索入口：轮末压缩、轮数上限、Token 硬上限与目标比例、手动轮数优先 /
Token 回退、最后一轮、摘要覆盖收敛和失败保留历史见 `architecture-overview.md`、
`configuration-reference.md`；API 输入超限、工具续轮恢复、两次内部恢复与输出截断区分
见 `provider-reliability.md`；摘要与裁剪事务、缓存恢复排除已摘要前缀见
`storage-and-persistence.md`。

| 分类 | 文件 | 内容 |
|------|------|------|
| 基础与总览 | `data_structure.md` | 全局了解手册主索引、六类文档导航、框架目录与按需查阅入口 |
| 基础与总览 | `project-introduction.md` | 项目定位、当前代码版本与发布状态、网关协议匹配、核心能力、部署和使用入口 |
| 基础与总览 | `architecture-overview.md` | 事件驱动架构、模块职责、请求生命周期、并发模型、子代理执行工作面板、模块注入预览片段来源、消息跟进队列与本轮引导/下一轮发送、Enter 跟进与 Ctrl+Enter 直接引导快捷键、暂停/停止后的 Run ID 状态收口与发送按钮恢复、文件空间排序及分页、新建此用户标签页、独立会话、离线清理后恢复、Web/App 存活租约、CLI 独立续接、closed Web 会话显式重开与禁止隐式复活、离线转记忆与入队失败补偿、会话生命周期兜底扫描 |
| 基础与总览 | `open-source-license.md` | Apache-2.0 使用、分发与声明要求 |
| 配置与知识 | `configuration-reference.md` | `.env`、全局配置和用户配置字段与优先级；中途上下文压缩开关、逐轮摘要与旧工具结果缩略的统一控制；`cron.history_retention_days`、`cron.session_idle_close_seconds` 及全局配置 API |
| 配置与知识 | `prompt-authoring-standard.md` | 提示词分层、全局/用户默认人格、操作手册、知识索引、插件提示词规范、技能/拓展/感知定义、按需读取来源、健康快照与数据/指令边界 |
| 配置与知识 | `knowledge-and-user-data.md` | 三层知识库、索引和用户目录骨架；逻辑分类与 `classify.sqlite3` 的 `classify` 表、`未分类`默认规则、路径稳定性和 Web 分类操作；`REQUIRED_USER_DIRS` 单一清单、`runtime/`、`tools/`、`expired_memory/`、运行期生成目录与数据库分类 |
| 运行与调度 | `storage-and-persistence.md` | 历史、记忆、运行状态、日志、高频写盘规则；archive 权威存储与 runtime 有界进程缓存（单项/全局/单用户三重容量）、跨进程版本校验和尾部重建；Web 历史默认时间倒序、复合游标分页与按上海自然日日期筛选；`history_search` 四模式：保留关键词搜索、SQL 元数据日期目录、物理归档清单、按 archive_id 读取 user/assistant 正文；updated_at 上海日期口径、旧 search 创建日期兼容、删除栅栏、条数+字符预算和长消息无损续读；结构化运行日志、终端长行 pending 缓冲上限；记忆按独立更新/失效/加权/检索边界拆分，碎片数量以独立事实为准，A 类仅同一稳定子主题可成簇、B 类保持最小事实，手动 add/edit 同样执行 memory_type 与长度硬边界；晋升时超限必拆（显式 `memory_type`、A 类 1000 字异常硬上限、B 类通常 100/绝不超过 150 字、宿主落盘复验、不设总纲、继承时效、权重归零、防震荡，挂 `memory_promotion`）；临时重要记忆生命周期；加权证据轮次 committed_at、Shanghai 原日期、memory_ref 检索绑定、create/reinforce/revise、creation 零分日锁、operation_id 幂等、weighted/daily_locked 可观测性；Cron 历史默认 7 天保留；Web/App 数据会话按可配置空闲阈值归档（默认 90 分钟），空 Web 会话保留独立 90 秒删除宽限，closed Web 会话支持显式重开但禁止迟到心跳、无参数入口和旧链接隐式复活；Web 启动及周期旧空间巡检、有数据入记忆队列、空空间离线清理、在线租约、CLI 独立绑定、closed 入队补偿、schema v6、显式新空间链接、事务删除栅栏；执行记录分类、多用户有界读缓存与持久化边界 |
| 运行与调度 | `provider-reliability.md` | Provider 工具调用完整性、网络恢复、错误序列内最多重试 5 次、接受完整响应即重置序列、单 Run 累计尝试不设上限、`retrying` 的序列序号与 Run 累计序号、SSE 续传和取消边界；含 Kemo 2.0 固定制品门禁，Chat 最小请求、显式厂商思考字段、真实签名状态续接、兼容占位、DeepSeek 专用分支与单次 HTTP 请求 |
| 运行与调度 | `task-automation.md` | 多步骤任务计划；Cron 的 once/daily/weekly/monthly/recurring、多时刻、生效区间、次数上限、失败终态、按任务索引的真实执行历史、历史只读访问；Web 创建/编辑字段校验、HH:MM 分钟精度、显式时区、enabled/paused 用户状态边界与数组归一化；任务计划/定时任务/执行记录三栏独立容器与各自 6 条分页，定时任务近期执行排序、用户执行记录倒序、`cron/task_cron_system/` 系统维护记录隔离、按需脱敏详情、网页管理与隔离边界；聊天开始页不展示全局或已清理会话的孤立活动计划 |
| 运行与调度 | `long-task-runtime.md` | 会话级长任务的隔离状态机、前台任务计划工具次数上限续跑、跨 Run 边界、HTTP/SSE 与客户端恢复合同 |
| 模块与集成 | `module-development.md` | 拓展、感知、技能、子智能体和外部智能体桥接的创建与运行合同；`module/panel.json` 用户配置组件面板、status/config/action、preset、masked 密钥与快速配置 |
| 模块与集成 | `plugin-development.md` | 插件发现、工具循环、执行规则与 SKILL.md 开发指南；三种多媒体输入、attach_media 原生请求队列与 Run/子代理隔离；受管理 Shell 后台作业、活动配额、启动宽限与失联 worker 对账 |
| 模块与集成 | `builtin-expansions.md` | 内置 kemo app 桥接激活意愿、端口/Token/用户绑定面板与异常诊断；Kemo 网关 IP/端口面板和状态拓展；Kemo Graph IP/端口、在线检测及图谱/实体/缓存/日志/维护/项目与文档组织边界 |
| 模块与集成 | `external-message-route-creation.md` | 外部消息平台模块合同 |
| 模块与集成 | `module-template-validation.md` | 六类模块创建后的独立合同验收、报告语义与维护边界 |
| 网页与交互 | `frontend-conventions.md` | Web 样例完整替换：reference 七份样式与生产别名桥接、14 路由与领域拆分、真实 API/Markdown/ModulePanel 宿主、React Root 与窗口/轮询清理、移动端导航覆盖层、任务六条分页与显式重开；高级白/高级黑两项主题偏好、输入框自动高度/useComposerAutosize/field-sizing fixed/空草稿回缩/宽度字号监听、CSS Module、变量链、投影裁切、右侧快捷轮次滚轮/弧线预览/百分比布局/轨道定位/触屏/历史增量加载、输入坞对话菜单溢出与视口内滚动、模型切换气泡/Chat 兼容 models API/KEMO 内置目录/服务端凭据/失败保留草稿、思考档位气泡/真实能力/PATCH 保存、图片文件名边框/简洁附件缩略图、当前会话文件历史隔离、清空对话自绘确认/取消/409/删除后新建重试、构建缓存、浏览器验证、知识库编辑/安全放大预览、DocumentMarkdown 全业务文档统一排版/只读渲染/任务 Markdown 与 JSON 代码块、文件空间右侧详情/FilePreview/媒体/PDF/Office/LibreOffice/原文件下载、外部消息双栏/无搜索框/六条分页/刷新保留选择 |
| 网页与交互 | `inline-widgets.md` | Web 智能体正文中的 `kemo-widget` 声明式卡片、图表、交互表格、标签页/折叠、差异、日程、看板、组合 UI、教育型轨道可视化、表单、建议追问、位置、影音、文件、商品、订单、投票、评分、有限点击动作与站内媒体；兼容历史 `follow-up.options`、旧表单字段/options/反馈动作归一化，未知名称通用渲染、流式闭合、Zod 校验、历史保存、复制降级、响应式与执行安全边界 |
| 部署与更新 | `deployment-and-release.md` | 一键部署；Windows/Linux/npm/Docker；Release ZIP/校验；安装渠道与安装根；启动/检查/停止后更新/恢复；预构建前端；打包/发布前提；用户数据保护与禁止混用更新器 |
| 部署与更新 | `version-and-update-modules.md` | 当前代码版本与发布状态、配套网关兼容基线、安装渠道更新入口，以及源码 core/agents/plugins/web 更新边界 |

## 维护原则

1. 代码、模板和配置文件是事实来源，文档不得发明未实现能力。
2. 新增、删除或移动全局知识文档后，同步更新本索引。
3. 凭据、个人隐私、Cookie、Token 和本机秘密不得进入全局知识库。
4. `.bak`、缓存、日志和运行产物不列入知识索引。
5. 目录中只有 `data_structure.md`、`index.md`、`索引.md`、`目录.md` 会作为知识索引自动注入；其他正文按需读取。
6. `project-introduction.md` 只保留最新版更新说明，不累积历史更新日志；
   其他知识文档不保存单次审计记录或工作区提交清单，长期有效的功能规则合并到对应专题文档。

## 模块目录的共同原则

感知、拓展、外部消息、技能、子代理和工具插件的模板都只展示框架可发现的最小合同，不定义模块内部架构。模块目录可以是极小实现，也可以容纳任意层级文件或完整工程；框架只读取清单、主文档和已声明入口，其他内部内容不会自动注册、注入或执行。具体合同与安全边界以对应专题文档为准，不能因为模板没有列出某个内部文件就判定其非法。

上述模块中的子代理、拓展、外部消息、感知、技能和用户包完成创建或实质修改后，应进入 `tests/template_tests/<kind>/` 运行对应的独立合同验收。具体映射、状态解释、沙箱边界和维护方法见 `module-template-validation.md`。

- **文件空间 / 复制路径 / 智能体工作区相对路径 / users 用户上传与产物 / tmp 临时区 / FilePreview**：见 `frontend-conventions.md` 的“文件空间预览”。
- **新建对话 / 清空新建 / 旧活跃会话恢复竞态 / 路由交接 / 日期浮层 Portal 测试**：见 `frontend-conventions.md`。
- **记忆页面 / 四层读写接口 / UTC 时间 / 临时重要记忆单栏只读预览 / 简要生命周期状态**：见 `frontend-conventions.md` 的“记忆详情”，来源失效规则见 `storage-and-persistence.md`。
- **子智能体页面 / 工具与技能页面 / 插件六条分页 / 右侧详情同宽 / 完整 SKILL.md 气泡预览 / 文档重新读取 / 定义与规则 Markdown 渲染 / 隐藏文件清单**：见 `frontend-conventions.md` 的“子智能体详情与列表”。
- **感知与拓展页面 / 左右等宽 / 六条分页 / 层级切换回首页 / 只读数据注入总览 / data_injected_markdown / injection.data_content / 模块详情 / 数据注入与用户配置标签 / 完整操作文档 / 创建用户拓展预置提示词 / 用户层拓展始终可用 / 标签右侧刷新采集 / 采集与实际注入并排渲染 / 配置组件加倍高度 / 操作与预览同高**：见 `frontend-conventions.md` 的“感知详情与注入预览”。
- **上帝模块 / 800 行 / 低耦合 / 高内聚 / 统一入口 / 页面组合根 / 模块尺寸合同**：见 `architecture-overview.md` 的“上帝模块边界与统一入口”，前端细则见 `frontend-conventions.md`。

## 审计修复检索（2026-10-25）

- RegistrySnapshot、baseline diff、同行 CAS、revision、墓碑/代际拒写、单事务删除、
  save_window_bundle、消息增量 upsert、processing 过期回收 → `storage-and-persistence.md`。
- allow_chat_images 移除、adapter-local Chat 20 MiB、Kemo 1 MiB、provider_options 白名单、
  Chat 自动有效档案、compat-default/assumed、精确型号与中转兼容标记、未知高级能力 fail-closed、合成 reasoning 不入协议 → `provider-reliability.md`。
- attempt_id、中断待确认、独立 Cron worker、watchdog、取消不计成功、系统 OS lease、
  超时不等于线程退出、完成屏障 cancellation_overdue → `task-automation.md`。
- ok=false、大结果 artifact 有界成功回执、禁止副作用盲重试、run_failures、同线程 RLock、
  实例管理员与本机无认证边界 → `architecture-overview.md`。
- response_model、六类 Body、Zod、AbortSignal、新版 reference 生产路由、旧 UI 移除与构建防回退、六条分页、草稿保护、
  UTC 转上海单次时刻 → `frontend-conventions.md`。
- 目录级包命名空间、.util 相对导入、无全局 sys.path 污染、导入探针对拍 → `external-message-route-creation.md`。
- 宿主异常穿透、MemoryError、host_exception、业务/传输重试区分、动作级复用、
  动态状态刷新与成功副作用防重 → `provider-reliability.md`、`plugin-development.md`。
- API 错误回执类型校验、语音错误后重启、迟到识别回调、共享会话命名函数 →
  `frontend-conventions.md`。
