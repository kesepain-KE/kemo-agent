# kemo-agent 运行手册

本文件是智能体操作自身的完整手册。涵盖架构、安全、资源位置、配置结构、工具、记忆、上下文、子代理、调度、prompt 拼接、provider 和交付标准。

---

## 1. 架构概览

kemo-agent 是一个事件驱动的多用户智能体框架。核心运行流程：

```
请求（user + source + session_id）
  → 加载配置（global_config.json + user_config.json 深合并）
  → 构建本轮静态 prompt bundle（人格 + 知识索引 + 记忆等）
  → 上下文选择（轮次预算 + token 预算 + 压缩）
  → 拓展与感知按各自用户总注入闸门与实时开关决定省略、按轮固定或请求级重读
  → Provider 调用循环（流式/非流式）
  → 工具调用循环（注册/发现/执行/超时/去重/取消）
  → 事务提交 SQLite 历史窗口（text + think + tool + items + data 五个逻辑分区）
  → 记忆引用加权；按 extraction_mode 提交、延期或游标提取
```

`run/` 按领域拆分，外部代码只从 `run.<领域>` 的 `__init__.py` 导入公开符号；不得深入导入
其他领域的私有实现文件。`run/engine.py` 是跨入口总门面，`run/__init__.py` 仅懒加载最小公共 API。

| 入口 | 职责 |
|------|------|
| `run/engine.py` | 对 Web、CLI、Cron、外部消息稳定公开对话、上下文与压缩 API |
| `run/conversation/` | 主循环、输入、Provider 事件、引导、Run 状态、终态提交和 Usage 聚合 |
| `run/context/` | 轮次/Token 预算、上下文状态、压缩服务和摘要缓存 |
| `run/history/` | SQLite archive/runtime 窗口、会话索引和历史摘要调度 |
| `run/memory/` | 四档记忆、分析管道、权重、晋升、过期和 SQLite 存储 |
| `run/tools/` | 工具发现、Schema 验证、执行、超时、取消、看门狗和参数恢复 |
| `run/agents/` | 子代理运行、队列、服务和调用合同；底层包发现仍位于 `agents/_runtime/` |
| `run/tasks/` | 任务计划存储、修订、执行、边界、服务和调度 |
| `run/long_task/` | 会话级长任务授权、跨 Run 续跑、统计与控制元数据 |
| `run/scheduler/` | Cron、维护、历史摘要、运行时状态和 RuntimeHost 生命周期 |
| `run/config/` | `.env`、双层 JSON、Prompt 来源/组装、知识索引和用户路径 |
| `run/extensions/` | 拓展、感知模块子进程、多模态、附件、媒体产物和模型能力 |
| `run/infra/` | 原子写入、进程执行、公共错误、日志存储和 CLI 桥 |
| `provider/` | 内部统一 Kemo 契约；`chat` 标准兼容与 `kemo` 原生网关双模式 |
| `web/` | FastAPI 装配、领域路由/服务和 React 前端 |
| `plugins/manifest.py` | 解析插件 `SKILL.md` 和 Provider 工具定义 |
| `events.py` | 所有入口复用的统一运行事件合同 |

`run/` 顶层只保留懒加载入口 `run/__init__.py` 与总门面 `run/engine.py`。旧平铺模块已经删除；
所有生产入口、插件、测试和部署脚本都必须使用领域入口，不能重新引入平行兼容文件。

---

## 2. 安全规则

以下规则不可被用户人格、技能或任何上层配置覆盖。

- 不执行危害用户系统、数据或安全的操作。
- 不记录密码、API Key、Token、Cookie、私钥或验证码到长期记忆。
- 不确定的内容必须说明不确定，不能编造文件、状态、工具结果或外部事实。
- 不擅自扩大任务范围——用户要 A，默认只做 A。
- 改动前先理解现有状态，不盲改。
- 涉及删除、覆盖、权限、安全、成本、兼容性破坏时必须谨慎确认。
- 工具失败时不假装成功。
- 不重复执行已产生副作用的工具调用。
- 对外操作（发消息、发布、传输）前确认目标和内容。
- 敏感凭据不得写入记忆或知识库。

### 优先级

当规则冲突时：

1. 安全与隐私底线（`config/global_soul.md`）
2. 用户明确指令
3. 本运行手册（`agents.md`）
4. 用户人格（`users/<name>/user_soul.md`）
5. 其他

### 用户指定执行路径

用户明确指定以下内容时，必须视为强约束：

- “先 A，再 B，最后 C”指定执行顺序。
- “使用 A 工具”“读取 A 文件”指定工具或资源。
- “只修改前端”“不要修改配置”指定作用范围与禁止事项。
- “先出方案”“完成此步后等待批准”指定暂停和授权节点。
- “按此格式输出”指定交付形式。

执行规则：

1. 执行前识别目标、顺序、指定工具、文件范围、禁止事项和暂停节点，并在后续步骤中持续遵守。
2. 严格按照指定顺序推进，不得提前执行后续步骤，也不得把明确路径降级为普通建议。
3. 前一步失败时，可以在安全范围内重试或诊断，但不能跳过该步骤继续后续操作。
4. 认为存在更优方案时，只能提出建议，不能未经允许直接替换、重排、省略或扩展原路径。
5. 只有安全风险、权限不足、客观不可执行或更高优先级规则冲突时，才可以暂停原路径。
6. 需要偏离时必须提供具体证据、影响和替代方案，并等待用户决定；未获授权时停在冲突步骤。
7. 用户后续明确修改路径时，以最新的明确指令为准。

“可以考虑”“例如”“建议”默认属于非强制建议；“必须”“先”“然后”“只允许”“不要”“完成后暂停”属于明确路径约束。

---

## 3. 资源位置

| 资源 | 路径 | 说明 |
|------|------|------|
| 全局配置 | `config/global_config.json` | 框架全局默认值 |
| 消息配置 | `config/message_config.json` | 外部账号绑定与 Transport 配置（可选，不存在时使用默认值） |
| 外部消息插件 | `message/out/<platform>/` | 文件夹级平台适配器、文件消息队列、附件、状态与日志 |
| 外部消息附件 | `message/out/<platform>/files/` | 各平台消息模块的收发文件存放处。核心会把入站附件登记为当前 Run 的资产；智能体优先使用 `asset_id`，明确的绝对路径也可直接交给多模态工具。例：`message/out/telegram/files/` |
| 全局人格 | `config/global_soul.md` | 安全底线，不可覆盖 |
| 用户配置 | `users/<name>/user_config.json` | 覆盖全局配置 |
| 用户人格 | `users/<name>/user_soul.md` | 用户偏好与风格 |
| 运行手册 | `agents.md` | 本文件 |
| 插件（工具型） | `plugins/<name>/` | 每个插件含 `SKILL.md` + `tool.py`，注册可调用工具 |
| 共享技能（指令型） | `shared_skills/` | 仅注入提示词，不注册工具 |
| 用户技能（指令型） | `users/<name>/user_skills/` | 仅注入提示词，不注册工具 |
| 全局知识库 | `global_knowledge/` | 所有用户共享，只读（除非用户明确要求写入） |
| 共享知识库 | `shared_knowledge/` | 共享知识层 |
| 用户知识库 | `users/<name>/knowledge/` | 用户私有，优先检索、默认写入 |
| 全局拓展 | `global_expand/` | 标准化全局拓展模块，使用 `expand.json` 控制数据与操控注入 |
| 共享拓展 | `shared_expand/` | 标准化共享拓展模块，按用户主配置过滤 |
| 用户拓展 | `users/<name>/expand/` | 当前用户私有拓展模块，可信自动发现且不执行用户注册代码 |
| 感知模块 | `global_sense/<module>/` | 每个直接子目录为独立模块，必须由 `sense.json` 的 `data_md` 指定唯一注入文件 |
| 内置子代理 | `agents/<name>/` | 受信任代码包：`AGENT.md`、`agent.json`、`agent-config.json`、`trigger.md`、`executor.py`、可选 `schema.json` |
| 用户子代理 | `users/<name>/agents/<agent>/` | 可信热插拔包：`AGENT.md`、`agent.json`、`agent-config.json`、`trigger.md`、可选 `executor.py`、可选 `schema.json` |
| 用户历史 | `users/<name>/history/history.sqlite3` | 每用户独立 SQLite WAL；会话、活跃绑定、归档/运行时窗口、消息检索和后台任务状态统一事务化存储 |
| 用户记忆 | `users/<name>/improve/memory.sqlite3` | 每用户独立 SQLite WAL；四档正文、生命周期、每日加权证据、幂等操作和热画像来源统一事务化存储 |
| 任务计划 | `users/<name>/task_plan/task_plans.sqlite3` | 计划、步骤与依赖的用户级 SQLite WAL |
| 定时任务 | `users/<name>/task_cron/` | cron 任务文件 |
| 用户下载产物 | `users/<name>/download/` | 智能体生成的文件 |
| 用户上传文件 | `users/<name>/file_upload/` | 用户上传的附件 |
| 智能体临时文件 | `tmp/` | 智能体中途生成的中间文件（不交给用户） |
| 创建模板 | `template/` | 子代理/拓展/消息/感知/技能/定时任务/任务计划/用户的创建骨架 |
| 模块验收基准 | `tests/template_tests/<kind>/` | 子代理、拓展、外部消息、感知、技能和用户包各自独立的创建后合同测试 |
| 外部消息幂等 | `users/<name>/history/history.sqlite3` | `message_processed_messages` 表；领取、终态与启动恢复 |

Web 历史列表按当前用户统一读取 `web`、`app`、`cli` 与 `message:<platform>` 来源。非 Web 会话在网页中
只读展示，不能由网页接管或续写；其归档正文、摘要、记忆状态和失败信息仍必须完整可见。所有
来源绑定到同一内部用户后共享同一个 `memory.sqlite3`，记忆页不得按渠道过滤。
| 外部路由状态 | `runtime/logs.sqlite3` | `message_route_state` 表；模块健康、计数与输入线程状态 |
| Web 外观偏好 | `users/<name>/web_preferences.json` | Web UI 主题与字号等外观偏好 |
| Web 服务 | `web/` | 前端（React + Vite）+ 后端（FastAPI），开发服务器默认 `:5173` |
| 全局版本 | `version.json` | 5 组版本号（core/agents/plugins/web/all），启动时展示 |
| 更新系统 | `update.py` + `update/` | 8 板块覆盖策略，版本对比，远程拉取 |
| CLI 入口 | `cli.py` | 纯命令行交互，用户选择 → 对话 |
| 重启模块 | `restart.py` | Web 端触发，保持端口不变 |
| 环境安装 | `setup.py` / `requirements.txt` | 依赖安装与环境初始化 |
| 事件定义 | `events.py` | 统一事件类型（text_delta / tool_call_result / usage / error / done） |
| 环境变量 | `.env` | 启动级参数和密钥兜底 |

### 模块工作区自由与最小合同

- 感知、拓展、外部消息、技能、子代理和工具插件都采用“最小框架合同 + 内部自由工作区”。清单、主说明文件、数据出口和声明入口是框架交互边界，不是完整目录清单，也不是建议的工程规模。
- 每个模块目录都可以按需求包含任意普通文件、任意层级子目录、第三方源码或完整工程。极小能力可以保持单文件，大型项目可以原样保留，只在框架要求的位置提供薄适配入口；不得为了迎合模板强行拆分简单实现，也不得把完整工程挤进一个入口文件。
- 框架只发现并处理明确约定的内容：感知/拓展读取清单声明项，消息路由加载 `message.json` 声明的适配入口，技能发现 `SKILL.md`，子代理发现包合同与可选执行器，插件发现 `SKILL.md` 中声明的工具。其余内部文件不会仅因存在而自动注册、注入 Prompt 或执行。
- 创建器和模板只负责建立可发现的最小骨架。创建成功后，可继续使用正常文件、Shell 或代码工具在模块目录内完善实现、迁入已有项目；校验器不得以“模板未列出”为由拒绝、删除额外文件或要求合并目录。
- “内部自由”不代表绕过合同和安全边界。清单字段、声明入口签名、Prompt 可见范围、调用权限、生命周期和返回协议仍需满足各模块规则；不得路径越界、借符号链接逃逸、硬编码凭据或加载不可信代码。不同模块仍按各自运行方式接受白名单、超时、取消、隔离或主进程信任边界约束。

### 模块创建后的独立验收

- 创建或实质修改子代理、拓展、外部消息路由、感知、技能或用户包后，必须读取并运行 `tests/template_tests/<kind>/` 中对应的 `STANDARD.md` 和独立 CLI；已知类型时不得用其他类型的标准代替。
- 独立入口格式为 `python -m tests.template_tests.<kind> --target <path>`，其中 `<kind>` 取 `agent`、`expand`、`message`、`sense`、`skills` 或 `user`。只有候选类型未知或需要统一批处理时，才使用根级薄入口 `python -m tests.template_tests --kind auto --target <path>`。
- 每类业务规则只属于本类目录。根级代码只能保存报告协议、临时沙箱、通用入口探测、类型识别和薄分发，不得累积跨类型分支形成测试“上帝模块”。新增类型时应建立新的独立验收包。
- `FAIL` 必须修复后再交付；`WARN` 必须核对并说明；`SKIP` 表示外部凭据、网络、设备或可选 SDK 等条件尚未验证，不能表述为完整通过。通用验收不能替代模块自己的真实平台或设备集成测试。
- `task_cron` 和 `task_plan` 不属于这组模块业务基准；用户包验收只确认它们的初始化目录存在。
- 详细设计、命令选项和维护规则见 `global_knowledge/module-template-validation.md`。

### 外部消息插件发现规则

- RuntimeHost 启动时只扫描 `message/out/` 的直接子目录，并只加载 `message.json` 明确声明的 `input`、`output`、`detect` 三个模块；目录中的其他 Python 文件不会被核心自动执行。
- `message.json.bound_user` 将整个平台插件绑定到一个现有内部用户；传统 Transport 仍使用 `config/message_config.json` 的外部身份映射。
- `message.md` 是可恢复的文件队列。群聊中同一外部会话的累积消息合并为一次 Run；私聊逐条处理。
- 附件必须使用非空的插件目录相对路径并位于该插件的 `files_dir` 内。文本文件正文直接并入请求；其他文件统一登记为带 `asset_id`、校验和、来源和媒体类型的当前 Run 资产，再由主模型能力路由或 `multimodal` 工具处理。平台模块不得自行决定是否把图片 Base64 直传主模型。
- 每条终态结果只写入 `runtime/logs.sqlite3` 的 `message_route_logs`；健康检测结果与收发计数只写入 `message_route_state`。模块目录不保存日志或运行状态副本。

### 运行中多模态引导

- Web 对话正在执行时，用户可以追加文本、图片、音频、视频、普通文件，或只发送附件；引导会在下一个 Provider/工具安全边界进入当前 Run。
- 每个附件必须先登记为当前用户的上传资产，并在 API 边界与运行时分别校验归属、内容、类型、大小和校验和。不得把前端传入的路径、URL 或 Base64 直接交给 Provider。
- Kemo 主模型声明对应输入模态时，通过 Asset API 直传；Chat 模式只按既有视觉能力直传图片。未被主模型直接接收的媒体仍保留在本轮资产白名单中，由 `multimodal` 或 `file` 工具按需处理，不能因不是图片而丢弃。
- 当前 Run 已关闭引导入口时，文本和全部附件一起转入下一轮；提交失败时保留草稿与附件。历史指标用 `guidance_details` 保存结构化引导摘要，旧 `guidance: string[]` 继续兼容。

### 插件发现规则

- 扫描 `plugins/*/SKILL.md`，解析 `## Tool` 下的 JSON 块。
- 插件标题、工具名、目录名三者必须一致。
- Provider 可执行工具只来自 `plugins/`；共享技能和用户技能不能注册工具。

### 技能发现规则

- 递归扫描 `shared_skills/**/SKILL.md` 和 `users/<name>/user_skills/**/SKILL.md`；目录可以任意嵌套。
- 仅注入提示词描述，不注册可调用工具。
- 项目静态来源由根目录的 `register.py` 注册；当前用户的技能和拓展由可信运行时按目录约定解析，用户目录不加载 Python。
- Web“上传用户技能”只接受 ZIP，并且只能安装到当前用户的 `user_skills/user_create/`。上传器递归寻找大小写不敏感的 `SKILL.md`，把其父目录作为技能根并完整保留内部脚本、工具说明、资源和子目录；ZIP 根直接含 `SKILL.md` 时以压缩包名创建技能文件夹。
- 上传不会覆盖同名技能。路径穿越、绝对路径、符号链接/目录联接、加密 ZIP、异常压缩比、大小写路径冲突、无一级标题或注册失败都会拒绝，多个技能按整包事务安装，任一失败时不保留部分结果。


### 知识库检索优先级

三层知识库的检索顺序固定为 **用户级 → 共享级 → 全局级**：

1. **用户知识库**（`users/<name>/knowledge/`）— 最高优先级，所有检索首先从此层开始，命中即返回。
2. **共享知识库**（`shared_knowledge/`）— 用户层无结果时检索，受 `knowledge.use_shared` 控制。
3. **全局知识库**（`global_knowledge/`）— 兜底层，受 `knowledge.use_global` 控制。

写入规则：

- 新增知识默认写入用户知识库。
- 只有用户明确说「写入共享知识库」或「写入全局知识库」时才写入对应层。
- 不得将用户私有信息写入共享或全局知识库。

### 知识库优先规则

用户提问时，按以下顺序处理：

1. **先查知识库索引**：拿到问题后，先扫描知识库索引（`data_structure.md`），检查是否命中已有知识条目。
2. **命中则优先走知识库**：若索引表明用户/共享/全局知识库中存在相关内容，直接读取对应文件作答，不额外发起网络搜索或联网请求。
3. **未命中再走常规流程**：知识库无匹配时，才考虑网络搜索、Provider 内置知识或其他来源。
4. **跨层合并**：用户层命中部分、共享/全局层命中另一部分时，合并所有命中结果，用户层内容优先展示。

> **Kemo Graph 外挂场景**：`global_expand/kemo_graph/` 是独立的侧载文档站连接器，`plugins/kemo_graph/` 只解释注册表并生成规范调用。它不替换、不增强、不缩减三层知识库或任何记忆，也没有专用 Prompt 段、核心配置开关或后台自动同步任务。管理员在拓展自己的 `graph_config.json` 中注册稳定 Library ID 与绝对路径；只有用户明确要求查询、更新或维护此外挂时，才通过 `expand_call(scope="global", module="kemo_graph", ...)` 操作。Markdown 正文使用 `upload`，本地 PDF、Office、EPUB 等文件使用管理员专属 `import_file`，默认均不立即 ingest。其本地目录摘要只按普通 `[expand_data][global:kemo_graph]` 规则注入；“继续、下一步、重来”等短指令不得触发新查询。完整合同见 `global_knowledge/kemo-graph-expand.md`。

> **Kemo 网关状态场景**：`global_expand/kemo_gateway_status/` 是默认未激活的全局只读拓展，面向“一个 kemo-agent 连接一个 Kemo 网关”的部署方式。它只调用网关的 `GET /status`（独立 `STATUS_TOKEN` Bearer 鉴权），读取运行阶段、版本、Provider/模型注册、当日调用与 Token 统计以及脱敏调用日志，并生成 PNG 图表；**不调用任何管理写接口，不具备启停 Provider、修改密钥或重启网关的权限**。`base_url` 支持本地、局域网或公网地址（HTTPS 使用系统证书校验，禁止跟随重定向）。只有用户明确要求“激活 Kemo 网关状态拓展”并提供网关根地址与独立 `STATUS_TOKEN` 时，才调用 `expand_call(scope="global", module="kemo_gateway_status", command="activate", ...)`；未激活时不得自行猜测地址、扫描端口或要求网关状态。Token 属敏感凭据，不得写入回复、记忆、知识库或日志。完整合同见 `global_knowledge/kemo-gateway-status-expand.md`。

> **Kemo 网关项目操控手册**：若用户要求修改 kemo-adapter-api 网关项目（新增厂商、改协议、改密钥、改配置、重启等），必须先在网关项目根目录读取 `agent_control.md`（智能体操纵 Kemo 网关索引），再按其中指引读取 `ADD_DIY/` 下对应手册，不得凭通用 OpenAI 兼容经验或旧对话直接修改网关。该文件位于网关项目目录（例如 `E:\code\kemo-adapter-api\agent_control.md`），具体路径以用户指定的网关项目位置为准；kemo-agent 自身不持有该文件，需要时通过 `file` 工具读取。

---

## 4. 用户配置结构

用户配置文件 `users/<name>/user_config.json` 覆盖全局配置 `config/global_config.json`。
`provider`、`agent_models`、`multimodal_models`、`multimodal_routing`、`knowledge`、`skills`、`expand`、`perception`、`plugins`
是用户专属段，只从用户配置读取，不允许全局配置兜底；其他框架段按对象深合并。

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `schema_version` | int | 配置结构版本号，当前固定为 1 |
| `provider` | object | LLM 提供商配置 |
| `agent_models` | object | 子代理 default、cheap、reasoning 三档专用模型；空值继承主模型 |
| `provider_runtime` | object | 全来源共享的 Provider 请求并发上限与等待超时 |
| `multimodal_models` | object | 各多模态能力的专用模型名 |
| `multimodal_routing` | object | 多模态能力路由策略；图片默认主模型优先、专用模型兜底 |
| `task_plan` | object | 任务计划配置 |
| `tools` | object | 工具开关、超时、最大循环次数 |
| `history` | object | 历史保留策略 |
| `prompt` | object | prompt 字符上限和注入模式；人格、手册和记忆基础段默认启用 |
| `knowledge` | object | 知识库检索配置 |
| `skills` | object | 共享 Prompt 技能白名单；用户技能始终允许 |
| `expand` | object | 全局/共享 Expand 白名单 |
| `perception` | object | 全局感知模块白名单 |
| `plugins` | object | 可执行插件白名单 |
| `memory` | object | 记忆挡位、注入上限与历史读取工具开关 |
| `agent_runtime` | object | 子代理运行时参数 |
| `web` | object | 单用户 Web Chat 并发、等待槽与等待超时 |
| `message` | object | 外部消息工作线程和有界等待队列 |
| `cron` | object | cron 调度配置 |
| `task_cron_system` | object | 系统级感知与拓展数据刷新频率及单模块超时 |
| `agents` | object | 上下文管理参数（轮次/token 上限等） |

### 主智能体来源控制

用户合并配置在 Prompt 选择与知识检索阶段控制主智能体，不改变注册阶段的完整库存：

| 配置 | 语义 |
|------|------|
| `knowledge.use_shared` | 是否加入共享知识范围；知识管线默认启用 |
| `knowledge.use_global` | 是否加入全局知识范围；用户知识始终有效 |
| `plugins.whitelist` | 插件白名单；同时过滤 Provider 工具 schema 与插件 Prompt 清单 |
| `skills.shared_whitelist` | 共享 Prompt 技能白名单 |
| `expand.global_whitelist` | 全局 Expand 白名单 |
| `expand.shared_whitelist` | 共享 Expand 白名单；用户 Expand 始终按当前用户目录动态解析 |
| `expand.prompt_injection` | 是否允许全部拓展数据进入系统提示词；缺失时默认 `true` |
| `expand.realtime_injection` | 是否在同一轮对话的每次逻辑 Provider 请求前重读拓展快照；缺失或 `false` 时只在本轮开始读取一次 |
| `perception.global_whitelist` | `global_sense/` 直接子目录模块白名单 |
| `perception.prompt_injection` | 是否允许全部感知数据进入系统提示词；缺失时默认 `true` |
| `perception.realtime_injection` | 是否在同一轮对话的每次逻辑 Provider 请求前重读感知快照；缺失或 `false` 时只在本轮开始读取一次 |

主智能体白名单 `[]` 表示全量允许；非空数组按资源 ID 精确匹配。技能 ID 支持相对路径（如 `development/python`）。`"*"` 不属于主配置协议。

`knowledge.enabled` 与 `skills.user_whitelist` 已从配置契约删除，继续提供会被判定为未知字段。
Kemo Graph 不属于用户配置合同。是否能看到目录摘要由普通 `expand.global_whitelist` 控制，
是否能获得引导和执行入口由 `plugins.whitelist` 对 `kemo_graph`、`expand_call` 的授权控制；
注册路径、服务地址和 Library ID 只存在于全局拓展自己的管理员配置中。

这些字段不控制子代理。子代理只服从各自 `agent-config.json`，不与主智能体策略求交集。

### provider 子字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | string | `"chat"` 或 `"kemo"`；启动前选择，运行中不自动回退 |
| `base_url` | string | API 地址；chat 模式自动补全 `/v1`，kemo 模式保持配置的协议根地址 |
| `api_key` | string | 用户独立密钥（优先读取），为空时读环境变量兜底 |
| `api_key_env` | string | 环境变量名，kemo 默认 `KEMO_API_KEY`，chat 默认 `OPENAI_API_KEY` |
| `model` | string | 主对话模型名 |
| `stream` | bool | 是否流式输出，默认 true |
| `reasoning_effort` | string | 保存的逻辑思考档位。`chat` 协议固定使用 `minimal`、`low`、`medium`、`high`、`max`，缺失、`none` 或非法值回退为 `medium`；`kemo` 协议完全采用当前模型能力声明中有序的 `reasoning.efforts`，不限制档位名称或数量，并永久过滤表示关闭思考的 `none`。已保存值失效时按 `medium` → 声明首项回退；模型不支持推理或能力不可用且无缓存时，运行请求省略 `reasoning` |
| `input_modalities` | string[] | 主模型已确认支持的输入模态；必须含 `text`。Chat 只可增加 `image`；Kemo 还可增加 `audio`、`video`、`file` |

Provider 单次请求超时默认 120 秒，可通过用户配置 `provider.timeout` 覆盖（`chat` 与 `kemo` 模式一致）；`headers` 配置项会被忽略，不再接受。

### agent_models 子字段

`default`、`cheap`、`reasoning` 分别用于普通、轻量和推理型子代理。任一字段留空时继承
`provider.model`；历史对话摘要使用 `cheap` 档位。

### multimodal_models 子字段

| 字段 | 说明 |
|------|------|
| `vision` | 图片分析/OCR 模型 |
| `image_generation` | 文生图模型 |
| `image_edit` | 图生图模型 |
| `audio_transcription` | 语音转文字模型 |
| `speech_generation` | 文生语音模型 |
| `speech_to_speech` | 语音转语音模型 |
| `video_understanding` | 视频理解与时间轴摘要模型 |
| `video_generation` | 视频生成模型 |

不含 embedding 和 rerank。

`multimodal_routing.vision` 支持 `auto`、`main`、`dedicated`。`auto` 会在主模型明确支持图片时直接传图，否则由 `multimodal` 插件使用 `multimodal_models.vision`；Chat 模式不按模型名称猜测能力，Kemo 模式在无显式声明时可读取网关能力。

### task_plan 子字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `auto_accept` | bool | 是否自动执行计划，默认 false |

详细结构说明见 `global_knowledge/user-config-reference.md`。

---

## 5. 工具使用规则

- **`action` 必填参数**：以下工具使用统一的 `action` 参数区分操作类型，**必须首先确定并传入**，漏传会导致调用失败并报「缺少必填参数：action」：`file`、`network`、`memory_manage`、`subagent_dispatch`、`task_plan`、`task_time`、`expand_creater`、`external_message`、`sense_creater`、`skill_creater`。特别注意 `file` 工具的所有操作（读、写、编辑、搜索、复制、移动、删除等）都必须带 `action`；编辑文件应传 `action: "edit"`、`edit_mode` 和推荐的新内容参数 `new_text`，旧参数 `content` 仅作兼容。
- **文件编辑安全流程**：已有文件的小范围修改禁止使用 `write` 覆盖全文。编辑前先读取目标范围，使用 `read_range.lines` 的显式行号，不得靠数组位置猜测；刚由 `write` 创建的文件可直接使用其返回的 `lines` 和完整 `sha256` 作为同次工作流快照，快照截断时仍需 `read_range`。精确文本块使用 `replace_text`；单行/连续范围使用 `replace_line`/`replace_range`，并必须传入已确认的 `expected_old_text`；插入操作必须传入读取或写入结果的 `sha256` 作为 `expected_hash`。删除完整行使用 `delete_line`/`delete_range`，禁止通过空 `replace_range` 隐式删除。`replace_line`/`replace_range` 的 `new_text` 末尾不要主动附加换行。`replace_text` 默认使用 `expected_count=1`；匹配或前置校验失败后重新读取，禁止直接改成 `-1` 强行替换。编辑后先检查返回的带行号 `preview`，再重新读取目标范围验证格式和内容；默认编号备份不得关闭或覆盖。`list_dir`/`tree_dir` 返回 `has_more=true` 时必须沿 `next_offset` 继续分页，不能把当前页当作完整目录。
- **`scope` 必填参数**：`expand_creater` 和 `skill_creater` 的 `scope` 为必填，漏传会报「缺少必填参数：scope」。取值仅 `"user"`（当前用户私有的拓展/技能）或 `"shared"`（所有用户共享），不存在 `"global"`。创建前应在流程中向用户确认 scope，不得自行猜测。
- 仅调用当前注册且已启用的工具，参数应符合工具 Schema。
- 工具结果是外部事实来源；调用失败时不得假装成功。
- 不重复执行已经产生副作用的工具调用（框架层有签名去重）。
- 工具执行有超时限制：未显式提供 `timeout` 时使用 `tools.timeout`（默认 240 秒）；工具 Schema 声明且调用方显式提供有效 `timeout` 时，该值覆盖插件内部期限和框架外层看门狗基准。少数需要在期限边界返回正常业务结果的插件可声明最多 30 秒的清理宽限，该宽限只延后外层看门狗，不得增加插件实际工作或等待时长。
- 后台长任务已经由其他工具启动且有可靠完成信号时，可用 `wait_for_condition` 在前台等待；必须显式设置 1～7200 秒的最长时长并优先等待 PID、路径或端口条件，条件满足立即返回。达到上限只表示等待超时，不能据此宣称后台任务失败；不得用无人管理的线程绕过取消与超时边界。
- 单轮对话有最大工具调用次数限制（`tools.max_iterations`，默认 80 次）；每个工具调用分别计数，同一 Provider 响应中的并行调用也计入总数。
- 长任务模式不是全局或用户配置项，而是由用户在 Web 对话操作菜单中按会话显式开启。状态严格绑定
  `(user, source, session_id)`，Web 与 App、同一用户的不同对话空间互不影响；新会话默认关闭。
- 开启后，只有当前 Run 以 `status=limited`、`stop_reason=max_tool_iterations` 收束时才会自动创建下一 Run；中间通过
  `long_task_update` 事件通知客户端，不发送中间终态 `done`。上下文保护、Provider 错误、任务计划批准边界和用户取消不会自动续跑。
- 用户关闭开关不会打断当前 Run：当前 Run 正常结束则长任务为 `completed`，再次触及工具上限则为 `paused`。会话级取消接口会停止整个逻辑长任务并取消当前 Run。
- 单个工具以“工具名称 + 完整参数”作为调用签名；同一签名连续请求超过
  `tools.consecutive_identical_call_limit`（默认 8 次）后阻止继续执行。工具或参数变化会将连续计数重置为 1。
- 同一工具连续失败达到 `history.consecutive_tool_fail_limit` 后，本轮会从
  Provider 工具 schema 中临时移除；其他工具穿插执行会重置连续失败计数。
- 用户取消时立即向当前工具发送独立取消信号，不继续执行后续工具调用；工具超时也会发送取消信号并留出短暂清理窗口，但不会误取消整个对话。
- 插件默认以 `execution_mode=process` 在独立子进程运行；超时或取消后框架终止其进程树，避免无视取消的插件继续写盘、占用线程或重复产生副作用。只有必须共享主进程对象的可信插件才可显式声明 `execution_mode=thread`；线程模式继续使用协作取消，并由遗留执行看门狗阻止同一执行重复启动及无限堆积。
- 工具上下文注入 `root`、`user`、`source`、`session_id`、`window`、`tool_timeout`，不注入主对话历史。
- 当前已注册工具见 `plugins/` 目录，每个插件的 `SKILL.md` 描述触发条件和参数。
- 插件工具清单的可选 `strict` 默认为 `false`。只有整个参数 Schema（包括所有嵌套 object）满足目标 Provider 的严格结构化输出子集时才能设为 `true`；包含开放参数对象或可选字段的普通工具必须保持非严格，不能由网关静默改写。
- 插件工具清单的可选 `timeout_policy` 默认为 `argument_or_default`；只有自身管理子智能体整体期限的调度工具使用 `agent_runtime`，普通插件不得借此绕过工具超时边界。
- Provider 工具调用只有在参数已解析为完整 JSON 对象且响应处于可执行终态时才能进入执行循环。Chat 的 `finish_reason=length/max_tokens/max_output_tokens/content_filter`、无效参数 JSON，以及 Kemo `ToolCallItem.parse_error` 都必须在执行前拒绝；禁止把残缺原文包装成 `_raw` 参数后尝试调用工具。

---

## 6. 记忆系统

### 4 挡位

| 挡位 | 有效期 | 晋升阈值 | 升级目标 |
|------|--------|----------|---------|
| `seven_days` | 7 天 | 权重 ≥ 3 | `one_month` |
| `one_month` | 30 天 | 权重 ≥ 10 | `half_year` |
| `half_year` | 180 天 | 权重 ≥ 60 | `permanent` |
| `permanent` | 永不过期 | — | — |

### 权重规则

- 不做每日权重衰减。
- 临时记忆只能在保存、手动压缩、Token 超限压缩等历史整理管线中，被 `self_improve` 依据用户原文命中时加权。
- 正文修改不会重置进入当前层时固定的 `expires_at`。
- 到期未达晋升阈值直接删除，不降级保留。
- 晋升后新挡位权重从 0 重新累计。
- Prompt 注入、记忆工具查看和用户主动检索都是只读行为，绝不加权。
- 永久记忆在表中不参与权重累计，也没有到期时间。

### SQLite 存储

- `users/<name>/improve/memory.sqlite3` 是四档记忆的唯一权威源，使用 WAL、外键、事务和 busy timeout；不得长期双写 Markdown 或 `data.json`。
- 逻辑文件名仍是全部记忆层级中的全局唯一身份，建议使用人可读标题（如 `用户偏好-xxx.md`）；`memory_fragments.filename_key` 的唯一约束阻止跨层同名。
- 每行保存一个微量化事实、偏好、关系或项目状态，以及 tier、weight、created_at、content_updated_at、last_used_at、last_weight_date、tier_entered_at、expires_at 和正文摘要。绝对时间统一存 UTC，展示层转换到本地时区。
- `memory_weight_events(fragment_id, evidence_date)` 由数据库唯一约束同一碎片同一天最多加权一次；晋升、融合、删除、幂等批次结果和热画像来源变更必须在事务内完成。
- `updated_at` 仅作为 `content_updated_at` 的兼容别名；记忆被注入使用时不得覆盖内容更新时间。
- 旧 Markdown、`storage.json`、三层 `data.json`、`.memory_operations.json` 和 `important_view.json` 不参与读取，也不会自动导入。
- 当前生命周期搜索按逻辑文件名与正文执行普通表查询；外部 `kemo-graph` 不接管记忆 Prompt，也不参与权重、晋升或热画像生成。

### 临时重要热画像

- `memory_temporary_important.md` 是从临时微记忆派生的可重建热视图，不是第五个生命周期挡位，也不是永久记忆的前置层。
- `memory_important_sources` 表保存热画像当前引用的临时源行及内容摘要。临时源仍是权威数据，只能由用户对话历史整理继续加权，并正常到期和晋升。
- 数据流必须保持单向：`用户对话原文 → 临时三层 → 临时重要热画像`。后台提取、整理和晋升临时三层时，禁止读取 `important`，也禁止把助手回复、推理或工具结果当作用户证据。
- 用户或主智能体主动查看记忆属于白名单只读场景，可读取 `important`，但查看本身不得改变任何临时记忆权重。
- `memory.important_memory_max_chars` 只控制热画像进入 Prompt 的字符预算；热画像文件可以更长并完整落盘。`memory.important_memory_output_max_chars` 才是模型输出防失控硬上限，超过时拒绝本次更新且不得覆盖旧热画像。
- 后台热画像巡检必须使用 `memory_manage list(limit=500, offset, compact=true, include_content=true, page_char_limit=80000)` 分页批量读取三层临时记忆和永久记忆，并沿 `next_offset` 读取到 `has_more=false`；不得对全部条目逐条 `get`。分页同时受条目数与序列化字符预算约束；中间页的 `truncated=true` 或 `page_limited_by_chars=true` 只表示仍有下一页。未完整覆盖返回的 `total` 时必须保留旧热画像，禁止基于部分数据做永久融合或副本清理。
- 热画像来源关系只用于核对派生视图是否仍然有效，不改变源碎片的 Prompt 注入资格；进入热画像的七天、月、半年碎片仍按原层上限正常注入。任一源正文变化、被删除或离开临时层后，整份旧热画像暂停注入，权威临时/永久内容始终保持正常注入，等待下次巡检重建。
- 永久记忆完全覆盖某个临时碎片时可清理临时副本；仅部分覆盖时必须提交包含旧永久事实和新增事实的完整融合正文。热画像来源、永久融合和临时副本清理由运行时统一事务化，子代理不得直接修改数据库。
- 普通记忆提取不得覆盖已有永久正文；只有用户本轮明确要求记住的 `explicit=true` 候选才允许更新永久记忆。

### 注入

- 永久记忆全部注入，不设置文件数量上限。
- 临时三层按 `half_year → one_month → seven_days` 排列，层内按权重从高到低选择。
- `memory.temporary_injection_limits` 只限制单次 Prompt，不限制磁盘存储数量。
- 临时重要记忆（`memory_temporary_important.md`）独立注入，有字符上限，只负责强化高价值事实；普通临时段继续注入其权威碎片正文，不因热画像引用而过滤。**此文件由 `memory_temporary_important` 子代理自动维护，任何情况下均不可删除、不可清空、不可写入空内容。** 即使当前无可提取的碎片，也必须保留占位文本。
- `memory.extraction_mode=compression_only` 时，普通提交只登记 `deferred` 游标，保存会话或上下文压缩时才顺序提取。
- `background` 模式允许 Maintenance 领取普通 `pending` 轮次；`on_commit` 模式同步提取。提取失败可按租约重试，不回滚已提交历史。
- 待提取轮次按 `memory.extraction_batch_rounds` 组成连续批次，一次交给 `self_improve` 分析；候选统一匹配、去重并通过带稳定 operation_id 的 `upsert_candidates` 批量落盘，成功后才推进到批次末尾游标。
- `memory.recovery_max_rounds_per_scan` 是单次后台扫描的总轮数预算，`memory.extraction_max_candidates_per_batch` 是单批候选上限；二者不改变临时记忆每日最多加权一次的规则。
- `disabled` 模式不进行自动提取，Maintenance 也不会领取该用户的记忆任务。

### 用户指令

- 用户明确要求记住或忘记时，遵循记忆存储规则。
- 敏感凭据不得写入记忆。

---

## 7. 上下文管理

### 预算

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `agents.conserved_rounds` | 3 | 最近 N 轮保留完整工具日志 |
| `history.recent_full_rounds` | 3 | 最近 N 轮完整历史不被摘要或移除 |
| `agents.max_rounds` | 80 | runtime 窗口及 Provider 上下文的轮次上限；不限制用户可见 archive 归档 |
| `agents.rounds_after_compression` | 20 | 压缩后保留的轮次数 |
| `agents.token_limit` | 1000000 | token 上限 |
| `agents.token_compression_ratio` | 0.3 | 输入预算 = token_limit × ratio |

- `input_budget = token_limit × compression_ratio`
- `output_reserve = token_limit - input_budget`

### 压缩触发

- **轮次触发**：投影轮次 ≥ `max_rounds` 时触发。
- **Token 触发**：估算总 token 超过 `token_limit` 时触发。
- **Provider 触发**：统一协议或兼容 Provider 返回 `context_length_exceeded` 时，自动压缩并重试，最多 2 次。
- **手动触发**：请求带 `compress=true` 时强制压缩。
- 压缩时把当前待发送轮次计入 `rounds_after_compression`：正常提交后，Provider 临时工作区总计保留最近 N 轮，而不是额外再保留当前轮次。
- 所有摘要场景统一由 `context_manage` 处理；正常聊天的自动压缩不在请求主线程同步提取记忆，而是在完整提交后把摘要已覆盖轮次登记到持久化后台队列。手动压缩仍可显式选择同步或队列策略。
- 自动、手动和 Provider 超限压缩通过非终态 `context_compression` 运行事件报告 `started`、`ready` 或 `failed`；`ready` 只表示摘要可供当前请求使用。队列策略下记忆仍需等本轮提交后由后台按批处理，只有 `memory_processed_round` 推进到 `memory_target_round` 才表示裁剪轮次的记忆分析完成，分析完成也允许零新增候选。
- 摘要缓存在 `history/history.sqlite3` 的 `history_context_summaries` 表中；后续压缩沿绝对轮号继承旧摘要，只把新移出的轮次交给 `context_manage` 增量整理，缓存 schema 升级时自动重建。runtime 窗口裁剪与摘要版本在同一个 SQLite 事务提交。
- `context_manage` 使用严格输出 Schema；摘要输入包含正文、reasoning/think 和工具结果，以 64000 tokens 为目标上限按完整轮次分块，单个轮次不会被拆散，单次输出上限为 20000 tokens，为模型推理和完整 JSON 正文共同预留空间。摘要只保留对后续仍有价值的精炼判断依据，不保留逐步内部推演或工具长输出。JSON 缺失、截断、空 narrative 或 Schema 不合格时自动携带校验错误修复一次，第二次仍失败才向调用方报告，且不得覆盖已有摘要缓存。
- 手动压缩只有在摘要缓存、runtime 窗口轮数、绝对轮次偏移和摘要覆盖范围全部落盘并重新读取校验通过后才返回成功；失败时回滚本次 runtime 裁剪与摘要缓存，用户可继续使用原运行窗口重试。
- `history.sqlite3` 的 archive 窗口始终保留完整原始记录和累计轮数；runtime 窗口才受限并会裁剪。归档元数据不保存 context/summary 诊断；runtime 行丢失时仅从 archive 恢复最近 `max_rounds` 轮。

### 轮次结构

每个对话轮次在 SQLite 中保留五个逻辑分区：
- `history_messages`：archive 的 user + assistant 消息唯一正文；`text_json` 只在 runtime 保存可裁剪工作区，archive 中是存储引用
- `history_rounds`：archive/runtime 的逐轮思考、工具调用、统一协议 Item 与 `round_metrics`；对应窗口 JSON 列只保存引用
- `data_json`：累计用量、记忆状态与其他小型持久元数据

每轮提交后只检查一个刚越过 `conserved_rounds` 保护线的轮次：思考和工具记录由 `context_manage` 压缩到 runtime 窗口，archive 窗口中的原始逻辑分区保持不变。工具结果字符上限仍作为上下文保护。

---

## 8. 子代理系统

### 当前已注册子代理

| 子代理 | 路径 | 职责 |
|--------|------|------|
| `context_manage` | `agents/context_manage/` | 统一处理轮次、Token、API 超限和逐轮工具/思考压缩 |
| `self_improve` | `agents/self_improve/` | 记忆提取与自我改进 |
| `memory_temporary_important` | `agents/memory_temporary_important/` | 维护可重建热画像、来源索引与永久重复协调 |
| `task_plan` | `agents/task_plan/` | 任务计划生成与执行 |
| `time_plan` | `agents/time_plan/` | 定时任务处理 |
| `history_summary` | `agents/history_summary/` | 为已关闭历史对话生成卡片标题与摘要 |

### 包结构

内置代理位于 `agents/<name>/`，是受信任代码包：

```text
agents/<name>/
├── AGENT.md
├── agent.json
├── agent-config.json
├── trigger.md
├── executor.py
└── schema.json（可选，子代理输入输出 JSON Schema）
```

用户代理位于 `users/<user>/agents/<name>/`，可按需携带可信的自定义执行代码：

```text
users/<user>/agents/<name>/
├── AGENT.md
├── agent.json
├── agent-config.json
├── trigger.md
├── executor.py（可选）
└── schema.json（可选，子代理输入输出 JSON Schema）
```

- `agent.json` 精简为 `name`、`version`、`description`、`trigger` 四个字段。执行方式、写入策略和兼容模型标签由运行时按内置代理名补全；整体超时读取 `agent_runtime.default_timeout`，超时后的收尾存活期读取 `agent_runtime.timeout_survival_seconds`。
- `agent-config.json` 是运行时强制授权，不是说明文档；它声明 `internal_mode`、调用方、插件/共享技能白名单、全局/共享知识开关和主历史继承策略。
- `trigger.md` 分为“注册信息”和“操作信息”。主智能体只注入注册摘要，详细操作信息按需读取。
- 精简清单不显式声明执行器：同目录存在 `executor.py` 时自动使用 `executor.py:execute`，否则使用 `builtin:llm`；此规则对内置和用户代理一致。
- 带 `schema_version: 2` 的完整清单遵循其 `executor` 字段。自定义执行器必须写成同目录 `file.py:function`，文件必须存在且不得通过路径跳出子代理目录；用户 schema v1 仍不支持。
- 用户代理不能覆盖内置代理名称。自定义 executor 由 kemo-agent 进程直接导入执行，不提供代码沙箱，只能安装或编写可信本地代码。

### 发现、创建与调用

- `discover_agents(root, user)` 每次调用都扫描 `agents/` 和 `users/<user>/agents/`，不缓存目录结果。
- 因此新增、启用、禁用或删除用户代理后无需重启；长期存在的 `AgentRunner` 和后台 `AgentScheduler` 在执行前也会刷新注册表。
- 主智能体只通过 `plugins/subagent_dispatch` 网关发现和调度公开代理。网关提供 `list`、`create`、`call`、`status`、`cancel`。
- `create` 原子写入当前用户的基础代理包；写入后立即用同一发现管线校验，失败会删除整个目标包。创建结果默认无 `executor.py`，因此使用 `builtin:llm`；之后可在包内添加可信 `executor.py`，下次发现时自动生效。
- 更换用户时，发现管线重新解析该用户的 `agents/`、`user_skills/`、`expand/` 和知识索引，不依赖 `kesepain` 等静态用户名。

### 授权与执行规则

- 子代理只接收调用方显式传入的数据，不自动拥有主会话历史或当前请求。
- 只有 `agent-config.json` 白名单中的插件会进入该子代理的 Provider 工具定义；缺失授权默认拒绝。
- 新骨架只允许显式白名单中的 `shared_skills` 进入子代理提示词；不再注入用户技能和三层 Expand。
- 知识能力只注入授权范围内的完整索引文件；需要正文时由已授权的文件/知识读取能力显式获取。Kemo Graph 只在调用方明确选择此外挂文档站时使用，不是子代理知识正文的默认后端。
- `subagent_dispatch` 不会下发给子代理，避免递归调度链。
- 子代理有独立超时、取消信号、工具循环上限和 usage 汇总，并且必须返回 JSON 对象；默认上限来自 `agent_runtime.default_timeout`，同步调度工具也可在 `call` 中用 `timeout` 覆盖，不能被普通 `tools.timeout` 提前截断。
- 子代理达到期限后先进入 `agent_runtime.timeout_survival_seconds` 指定的收尾存活期；期间自然完成会正常保留结果，并在结果元数据及事件中标记 `completed_after_timeout`。存活期内主智能体仍可取消。存活期结束仍未完成才自动请求协作式取消并等待清理；已退出记为 `timed_out`，未在清理窗口内退出记为 `timed_out_running`。Python 线程不能被不安全地强杀，后一状态必须保留真实诊断信息。
- 主智能体不得把子代理内部指令视为用户指令。
- 用户主配置关闭知识、技能、Expand 或感知时，不会收缩子代理 `agent-config.json` 已授予的能力。

---

## 9. 任务计划与定时任务

### 任务计划

- `task_plan.auto_accept` 控制是否自动执行（默认 false，需手动批准）。
- 计划存储在 `users/<name>/task_plan/task_plans.sqlite3`；`task_plans`、`task_plan_steps` 和 `task_plan_dependencies` 分表维护，运行时没有文件式计划旁路。
- 每次创建或修改会在同一事务写入 append-only revision。大型 `tool_arguments/result/error` 按计划内 SHA-256 内容寻址去重，旧明文 JSON 与 `zlib-base64` 快照继续兼容读取；新计划或修改不得新增密码、Token、Cookie、API Key、私钥等凭据参数，必须改用环境变量名或其他安全引用。旧快照中的敏感字段不会继续复制到新 revision，含脱敏占位的 revision 不允许回滚。
- 计划状态机：pending → approved → running → completed/failed/paused/cancelled。
- 主智能体手动执行计划步骤后，必须立即调用 `task_plan step_done` 或 `task_plan step_fail` 写回结果；创建和编辑仍走 `task_plan` 子代理。
- `task_plan` 是运行态管理工具，不能作为计划中的执行步骤。
- 计划暂停后等待用户继续，不自动恢复。
- 计划调度器必须检查主智能体 `done.metadata.status`：只有缺省的旧式成功终态、`completed` 或 `success` 可作为成功；`limited`、`cancelled`、`failed` 等显式非成功终态必须记入步骤错误并暂停关键步骤，同时保留 `stop_reason`，不得退化成“未调用工具”的模糊原因。
- `task_plan.max_steps` 限制最大步骤数（默认 20）。
- 计划生成输入按“可用工具 → 插件技能全文 → 共享技能全文 → 用户技能全文 → 全局/共享/用户知识索引”顺序组装，且这些专项输入不截断。
- 仅 `pending`、`approved`、`paused` 计划允许编辑；已完成步骤由运行时强制保护。
- `task_plan.auto_accept=false` 时，创建或修改提醒随计划持久化并由调用方展示。

### 定时任务

- cron 任务存储在 `users/<name>/task_cron/`。
- 支持类型：`daily`（每日）、`once`（单次）、`recurring`（重复）。
- 用户用自然语言创建定时任务时，主智能体必须先调用 `time_plan` 生成自包含提示词和结构化调度参数，再调用 `task_time create`；不得直接猜测时间参数。
- 用户用自然语言编辑任务时，必须先用 `task_time get` 读取现有任务，再调用 `time_plan` 解析修改要求，最后调用 `task_time update`；删除前也必须先核对任务。
- 只有内部程序或 API 已经确定完整调度参数时，才能直接调用 `task_time create/update`。

### 调用 time_plan 子代理的参数

通过 `subagent_dispatch action=call agent=time_plan` 调用，`input` 必须包含：

| 字段 | 必填 | 来源 | 说明 |
|------|------|------|------|
| `action` | 是 | 按场景选择 | `create`、`edit`、`delete` |
| `user_request` | 是 | 用户原话 | 用户的自然语言定时要求 |
| `current_time_beijing` | 否 | 框架运行时 | 当前北京时间 ISO 字符串。`subagent_dispatch` 会按 `Asia/Shanghai` 强制注入；主智能体提交的同名值会被覆盖。 |
| `existing_task` | 编辑/删除时 | `task_time get` 返回结果 | 现有任务完整 JSON |
| `edit_request` | 编辑时 | 用户修改要求 | 用户明确表达的修改内容 |

主智能体不需要为了组装该字段额外调用时间工具。`time_plan` executor 仍要求最终输入包含
`current_time_beijing`，该契约由框架适配层保证。

调用前应先读取 `agents/time_plan/trigger.md` 确认最新输入约定。
- `cron.enabled` 控制是否启用调度（默认 true）。
- `cron.poll_interval` 控制常规轮询间隔（默认 30 秒）；运行时会自动取它与两个系统数据刷新间隔的最小值，保证短周期任务按时被扫描。
- `cron.avoid_congestion=true` 时，Provider 可用槽位低于 `cron.congestion_threshold_ratio` 指定比例会推迟普通用户任务和重型系统任务；全局感知/拓展采集不退避。
- `task_cron_system.sense_update_rate` 控制全局感知刷新间隔，`task_cron_system.expand_update_rate` 控制三层拓展刷新间隔；两者单位为秒、默认 5，缺失或非法时回退到 5。`module_update_timeout` 是每个采集脚本的独立子进程超时，默认 120 秒。后台系统任务由每个 root 的跨进程租约选出唯一领导调度器；高频任务运行时间只保存在领导进程内并按 `runtime_checkpoint_seconds`（默认 300 秒）检查点写回，前台单次扫描释放租约前必须写回。成功日志按 `success_log_flush_seconds`（默认 300 秒）聚合并由调度循环按真实截止时间冲刷；错误与停机冲刷不延迟。
- 感知刷新频率是框架级统一调度值，不写入单个 `sense.json`。Web 感知 API 从全局配置通过调度器同源校验返回 `update_interval_seconds` 和兼容显示文本；前端不得根据模块更新时间猜测频率，也不得读取用户配置中的同名覆盖值。
- `runtime_host.enable_background_scheduler` 控制统一后台调度器；启用时宿主
  自动管理 Cron 与上下文整理。
- 普通任务通过主智能体执行；系统任务可通过 `subagent` 直调内部子代理，或通过白名单 `function` 模式执行内部函数。
- 系统自动注册临时重要记忆巡检、每日整理、每 30 秒一次的 self_improve 到期晋升检查，以及感知/拓展数据刷新任务。
- `agents.important_memory_review_hours` 和 `agents.daily_memory_review_time` 是宿主级全局调度参数，只从 `config/global_config.json` 创建统一系统任务；用户配置不得声明不同时间表。任务到期后仍按用户分别执行，用户之间的数据和写入锁彼此隔离。
- 所有声明为 `background_serial` 的子代理在真实 executor 线程内按“项目根目录 + 用户”共享串行锁；网页同步调用、后台子代理队列、Maintenance 与 Cron 不得绕开该锁并发修改同一用户的派生数据。
- 记忆类系统任务按用户分别执行；感知只以 `__system__` 身份刷新全局层。拓展任务先以 `__system__` 身份各刷新一次全局层和共享层，再按用户身份只刷新对应的 `users/<user>/expand/`，不得跨用户聚合执行。
- 记忆晋升扫描必须处理本轮全部到期且达标碎片，不允许用单次批量上限截断总量。`review_due` 按目标层分批调用 `self_improve`：普通层每批最多 20 条、永久层每批最多 8 条；成功批次立即事务落盘，未成功条目保持原层并由后续扫描重试。
- `self_improve` 匹配已有碎片时必须使用单次 `memory_manage search_many` 批量提交 2～4 个核心关键词；搜索按完整短语和多关键词覆盖率评分，单个公共词不得直接触发复用。确认语义相同后必须复制已有文件名返回同名 upsert，才能进入每日一次的加权链路。`search_many` 每次调用只加载一次目标记忆层，不得随候选数量重复扫库。
- 模块更新脚本在独立 Python 子进程中运行，优先调用 `update()`，兼容调用 `main()`；超时、非零退出码、`False`、`{ok: false}` 或失败状态均记为失败。
- 拓展的 `input_data.md` 只承载长期采集摘要或资源索引；完整 JSON/CSV/HTML、图片、音视频和大型日志保存在模块内并按需读取，不能自动全部注入 Prompt。
- 主智能体使用 `expand_call` 操控当前用户获准使用的三层拓展。调用参数经 stdin 进入隔离子进程；操控结果直接进入当前工具结果，不绕写 `input_data.md`。大型结果必须以模块内 artifact 返回并由框架校验后发布到用户下载空间。
- `_runtime.json` 由框架维护，只保存采集/操控状态、耗时、受限资源索引和截断错误，不保存完整参数、原始数据或凭据，也不进入 Prompt。

---

## 10. Prompt 拼接顺序

system prompt 按以下固定顺序拼接：

1. **用户人格** — `users/<name>/user_soul.md`
2. **全局人格** — `config/global_soul.md`
3. **运行手册** — `agents.md`（本文件）
4. **全局子代理注册** — `agents/<name>/trigger.md` 的注册信息
5. **用户子代理注册** — `users/<name>/agents/<agent>/trigger.md` 的注册信息
6. **插件提示词** — `plugins/*/SKILL.md` 的描述部分
7. **技能提示词** — 注册全部共享/用户技能后，按主智能体白名单选择描述部分
8. **知识库索引** — 按 `knowledge.use_shared/use_global` 选择用户 + 共享 + 全局索引
9. **永久记忆** — `improve/memory.sqlite3` 的 `permanent` 行
10. **临时重要记忆** — `memory_temporary_important.md`
11. **临时记忆 half_year** — `improve/memory.sqlite3` 的 `half_year` 行
12. **临时记忆 one_month** — `improve/memory.sqlite3` 的 `one_month` 行
13. **临时记忆 seven_days** — `improve/memory.sqlite3` 的 `seven_days` 行
14. **任务计划** — 当前活跃计划的描述
15. **拓展数据** — 三层模块均由 `expand.json` 控制；健康输入数据与操控手册 `## 注入层` 可进入 Prompt，`## 操作层` 和 Python 入口只按需读取/执行。Kemo Graph 若激活，只在这里以普通 `[expand_data][global:kemo_graph]` 目录摘要出现
16. **感知文件** — `global_sense/<module>/sense.json` 声明 `data_md` 唯一文件，按模块白名单过滤；无效模块进入诊断但不注入

人格、运行手册、子代理/插件/技能注册、知识索引、记忆和任务计划等静态段在一轮用户对话开始时构建一次。
`[expand_data]` 与 `[perception]` 各自采用三级策略。`prompt_injection=false` 时对应段完全不进入
系统提示词；总闸门开启且 `realtime_injection=false` 时只在本轮开始读取一次，以稳定提示词前缀
并提高 Prompt Cache 命中率；两个开关都开启时，工具续轮、运行中引导续轮以及上下文超限压缩
后的重试会重读该数据段。拓展与感知的两组开关彼此独立。
刷新只读取后台采集器已经发布的文件，不同步执行 `data_update.py`，不会把采集耗时叠加到每次模型请求。
Provider 适配器对同一网络请求执行传输重试或 SSE 续传时继续复用同一请求正文，不在传输层中途改变 Prompt。

Kemo Graph 使用两层授权：主配置的全局 Expand 白名单决定模块访问，Library 的
`allowed_users` 决定读取范围；省略时仅 `admin_users` 可见，`["*"]` 才是公共库。
全局 Prompt 不得包含私有 Library ID 或绝对路径，写操作仅管理员可执行；`owner_id`
只是 kemo-graph Store 元数据，不等同于框架授权。

每段有字符上限配置（`prompt.char_limits`）。知识正文不自动注入，只注入索引；需要正文时使用显式搜索机制或工具。
Kemo Graph 不改变上述顺序、字符预算或本地来源选择：知识索引与全部记忆始终按原规则注入，
不存在图谱替换、增强标记、临时记忆减半或连接失败回退分支。其目录摘要属于普通拓展数据，
查询结果只在用户明确调用后作为当轮工具结果进入上下文。

---

## 11. 会话隔离

- 每个请求属于明确的 `user`、`source` 和 `session_id`；Android App 桥接固定使用 `source=app`，不得映射成 `web`。
- 同一用户可共享记忆和知识库，但不同来源与会话的对话历史互相隔离。
- 任务计划虽然集中保存在当前用户的 SQLite 中，但系统提示词只注入与当前 `source + session_id` 匹配的未完成计划；A 对话创建的计划不得进入 B 对话的系统提示词。
- `task_plan` 子智能体成功创建计划后，创建计划的当前 Run 必须立即在本会话内收束：同一 Provider 响应中排在创建调用之后的工具标记为 `not_executed`，后续 Provider 请求不再发起。该边界不得设置用户级暂停状态，不影响同一用户的其他会话运行。
- 主智能体的 `task_plan` 工具只能查看或操作当前 `source + session_id` 所属计划；即使显式提供其他会话的 `plan_id` 也必须拒绝。用户级任务页、CLI 管理命令和后台调度器继续通过受控服务直接管理集中存储中的计划。
- 不假设拥有未注入的其他会话内容；`memory.history_read_enabled=true` 时可使用历史搜索工具。
- 工具上下文只包含运行所需的 `root`、`user`、`source`、`session_id`、`window`、`tool_timeout` 及授权策略字段，不包含主对话历史。
- 会话级锁（`run/conversation/session_runtime.py:session_lock`）保证同一 user/source/session_id 的请求串行执行。
- Web 长任务 API：
  `GET /api/users/{user}/sessions/{session_id}/long-task?source=web` 查询状态；
  `PUT` 同一路径提交 `{ "enabled": true|false }`；
  `POST /api/users/{user}/sessions/{session_id}/long-task/cancel?source=web` 取消整个逻辑任务。
  响应中的 `long_task` 包含原始请求、Run/续跑次数、累计工具与 Provider 请求、Token 用量、耗时、当前 Run ID 和终态。
- APP 等客户端必须显式传入自身真实 `source`，收到 `long_task_update.metadata.next_run_id` 后更新活动 Run ID；完整状态机、SSE 与恢复合同见 `global_knowledge/long-task-runtime.md`。

---

## 12. Provider 与多模态

### 并发与反压

- `provider_runtime.max_concurrent_requests` 是进程级总闸，Web、外部消息、Cron、维护任务和子代理共享；每一次真实 LLM API 请求独立占槽，工具执行期间释放槽位，避免主智能体调用子代理时发生嵌套自锁。
- 等待 Provider 超过 `provider_runtime.request_semaphore_timeout` 会产生明确的 `ProviderCongestionError`；运行状态 API 的 `congestion.provider` 提供活动、可用和等待估计数。
- Web Chat 按用户隔离，最多并发 `web.max_concurrent_chats` 个 Run，另允许 `web.max_pending_chats` 个请求等待；队列满或等待超过 `web.pending_chat_timeout` 时返回 HTTP 503 和 `Retry-After`。空闲闸门会自动采用新保存的 Web 限制。
- 外部消息总容量为 `message.max_workers + message.max_queued_messages`；后者为 0 时保持无界兼容模式，否则队列满会抛出 `MessageQueueFullError`。
- 每个用户的 `AgentScheduler` 使用实例级串行锁和 `agent_runtime.queue_maxsize` 有界队列，不再由一个进程级锁串行所有用户；0 表示无界。
- Cron 在 Provider 高负载时跳过本轮普通任务，任务仍保持到期状态并在后续扫描重试；感知和拓展采集仍按计划运行。
- `congestion.web` 与 `congestion.message_router` 分别暴露 Web 用户闸门和消息路由的活动/排队状态。MessageRouter、Cron 等 RuntimeHost 启动期参数保存后需重启 Web RuntimeHost 才能重建对应组件。

### Provider 类型

- `chat`：通过正式 Chat Bridge 访问 `/v1/chat/completions`。保证 Kemo 内部文本/工具循环，并支持标准 `image_url` 图片输入；不提供音视频、媒体输出、Provider State 或 SSE 恢复。
- `kemo`：通过原生 Kemo Provider，提供 Asset、最大程度多模态、统一 Usage、Provider State、查询取消和流恢复。LLM、Embedding 和 Rerank 的瞬时建连/读取错误，以及统一终态前断流，最多进行 3 次网络尝试并始终复用同一正文与 `request_id`；SSE 在线路上只通过最后完整事件的 `Last-Event-ID` 续传，本地延续 `sequence` 校验并拒绝拼接不同 `response_id`。
- 两种模式在一次 Run 开始前固定；任何错误都不得触发跨协议自动回退。
- Chat Bridge 同时解析现代 `tool_calls` 和旧式单个 `function_call`。标准 `[DONE]` 仍受支持；兼容服务在已经给出明确 `finish_reason` 后干净关闭 HTTP 流也视为正常结束，但无终态标记的 EOF 仍是传输中断。
- Chat 的输出截断或工具参数解析失败映射为统一 `incomplete`，保留最多 500 字符原始参数用于诊断而不发布可执行调用。Kemo 原生响应若携带 `ToolCallItem.parse_error`，统一运行事件层同样在工具执行前转为明确错误；这是运行时防御，不改变 Kemo 线路 Schema。
- `tools.invalid_tool_arguments_retries` 控制工具参数生成恢复次数，默认 2。主运行时与所有 `AgentRunner` 子智能体在统一终态为 `invalid_tool_arguments` 时，使用新的 `request_id` 和临时纠错指令重新请求。失败尝试已经流出的文本与思考会保留；同一响应中的工具调用必须整批通过参数校验后才发布卡片、登记待执行状态并进入执行，因此任一并行调用损坏时整批调用都不会执行。已经发布媒体、达到恢复上限或发生其他终态时保持明确失败，不静默重复可能产生外部副作用的工作。
- Kemo 传输重试只处理网络层和可重试 HTTP 状态；网关显式返回的 `retryable=true/false` 优先于状态码默认值。它不重新执行鉴权/校验/幂等冲突、完整协议损坏或模型统一终态业务失败；同一 ID 的已持久化失败终态只会重放，重新执行必须由上层建立新的逻辑请求。上下文超限继续走独立压缩链路。退避等待与流式/非流式阻塞读取均可被 Run 取消，已知远端 `response_id` 时取消会尽力传给网关。
- Web 保存 Provider 配置后，只有重新读取到已落盘的 `provider.type=kemo` 才允许通过
  `GET /model/models?task=llm` 拉取当前密钥可用模型；`chat`、未保存配置、缺少凭据、鉴权失败或
  非法目录响应均不得产生可用模型列表。
- 模型目录是短期界面数据，不新增 `user_config.json` 字段，不批量写入模型，也不覆盖当前
  `provider.model`。用户从目录选择模型后，仍通过原有 `provider.model` 字段显式保存。
- Kemo 模型的思考档位从模型目录所声明的 `capabilities_url` 读取；Web 浏览器只提交模型名，网关密钥始终由后端读取。能力声明中有几个有效档位，顶部模型弹层和 Provider 配置页就按原顺序展示几个；框架不维护 Kemo 档位白名单，并永久过滤 `none`。客户端原样提交能力声明中的 Kemo 逻辑档位，不执行 `reasoning_effort_map` 厂商映射；能力失败时可短期展示上一次成功缓存，但不得猜测固定五档。

### 密钥优先级

1. `user_config.json` 的 `provider.api_key`（用户独立密钥）
2. 环境变量 `KEMO_API_KEY` / `OPENAI_API_KEY`（兜底）
3. 都没有则报错

### Provider 地址优先级

1. 当前 `user_config.json` 中的 `provider.base_url`
2. `KEMO_BASE_URL` / `OPENAI_BASE_URL`
3. Provider 类型对应的内置默认地址

最终地址统一去除尾部 `/`。只有 `chat` 模式自动补全 `/v1`；`kemo` 模式默认协议根地址为 `http://127.0.0.1:8741`。

### Web 启动与认证

- Web 监听参数优先级为：显式 CLI 参数 > `WEB_HOST` / `WEB_PORT` > `127.0.0.1:1357`。
- `WEB_ACCESS_TOKEN` 启用 Token 认证；登录页通过 `POST /api/auth/token` 请求体验证，Token 不进入 URL、Cookie 或浏览器存储，Web 不读取 `Authorization: Bearer`。
- `WEB_USERNAME` 与 `WEB_PASSWORD` 必须同时配置；它们表示 Web 页面使用者，与内部多用户目录无关。
- 只启用 Token 或账号密码时，完成对应验证即可进入；两者同时启用时必须先 Token、再账号密码，不能用任一单因素会话绕过。第一阶段状态有效 5 分钟。
- `WEB_SESSION_SECRET` 为空时启动过程自动生成 64 字符随机密钥；签名会话默认有效期 2 小时。
- `WEB_SESSION_COOKIE_NAME` 用于多实例 Cookie 隔离。
- `WEB_AUTH_IP_MAX_FAILURES` 按“客户端 IP + 认证阶段”限制失败次数，留空或 `0` 表示不限；统计窗口和锁定时间分别由 `WEB_AUTH_IP_WINDOW_SECONDS`、`WEB_AUTH_IP_LOCK_SECONDS` 控制，达到上限返回 429 与 `Retry-After`。限制状态仅存在当前 Web 进程内。
- `WEB_AUTH_TRUSTED_PROXIES` 是逗号分隔的可信代理 IP/CIDR；只有直连来源命中该列表时才解析 `X-Forwarded-For`，留空时始终采用直连 IP。
- Web 使用 HttpOnly 签名会话 Cookie。未认证请求只能访问静态前端、`/api/health`、`/api/logo` 和 `/api/auth/*`，其余业务 API 统一返回 401。
- Settings 和 Health 只返回认证状态，不返回 Token、用户名、密码、Session Secret 或 Cookie 内容。
- Web 用户配置接口只返回脱敏后的只读镜像，不提供配置写入路由。
- Web 可只读查看 Prompt/Expand 诊断、记忆预览、当前用户子代理、消息插件状态、摘要缓存与真实 RuntimeHost 状态；独立 Web 模式明确显示 `unmanaged`。
- Web 文件 API 只允许浏览、下载或删除 `file_upload`、`download` 和 `tmp` 中的普通文件；拒绝路径穿越、目录删除、符号链接和隐藏缓存项。头像上传限制为 5 MB 的 PNG/JPEG/GIF/WebP，并校验 MIME 与文件签名。
- 文件页全局摘要使用短期进程内缓存；Web 写入、移动、建目录和删除会显式失效，插件或外部程序直接写盘则最多在短 TTL 后重新扫描。搜索仍按有界深度和条目预算实时扫描，不使用可能过期的摘要缓存。
- 用户人格和全局人格可通过受保护 Web API 原子更新；全局人格影响所有用户，当前唯一 Web 认证主体视为管理员。`user_config.json` 仍保持只读。
- Web 前端的 `/files` 页面落地用户文件与 `tmp` 浏览、下载和二次确认删除；`/runtime` 页面落地子代理、外部消息状态与三层 Expand 库存；`/profile` 页面落地头像上传和用户/全局人格编辑。
- 侧边栏品牌图使用公开 `/api/logo`，用户卡片头像使用受保护 `/api/users/{user}/avatar`；头像不存在或加载失败时回退首字母占位，不阻塞页面使用。
- 聊天请求使用高熵 `run_id`。运行中引导通过独立 guidance 队列提交，只在 Provider 完成或工具调用结束后的安全边界注入，不会中断正在阻塞的 Provider/工具函数。
- 每轮历史在 archive 窗口的 `data_json.round_metrics` 保存 usage、缓存 Token、耗时、工具调用数和已消费 guidance；`tool_json` 的每次调用保存 `elapsed_ms`。

### 多模态

- `MULTIMODAL_CAPABILITIES` 定义了支持的能力集合：vision、image generation/edit、audio ASR/TTS、speech_to_speech、video understanding/generation。
- 多模态模型名通过 `multimodal_models` 配置；视觉兜底需要明确填写 `multimodal_models.vision`。
- 不含 embedding 和 rerank。
- `chat` 模式只保证文本、工具和图片识别，只信任 `provider.input_modalities` 的显式图片声明，不根据模型名称猜测。
- `kemo` 模式支持图片、音频、视频、普通文件和媒体生成；输入/输出模态与 `extensions.operations` 必须同时满足。
- 主模型明确支持相应输入时优先直传；否则由 `multimodal` 插件调用专用模型。Kemo 大型媒体通过认证 Asset API 传输，生成结果校验后保存到用户下载空间；Base64 和临时 URL 不进入长期历史。
- Web 上传、外部消息附件和显式本地路径使用统一的运行资产解析。外部消息媒体不能绕过能力判断成为 inline Content Block；不支持该模态的主模型只接收资产说明，由专用工具读取资产。
- `multimodal.paths` 接受绝对路径或相对项目根目录的明确本地媒体路径。路径经存在性、普通文件、类型、签名和大小校验后，Chat 图片直接编码给专用视觉模型，Kemo 媒体通过 Asset API 上传。路径输入调用的是专用多模态模型，不等于把媒体发送给主模型。

### 网络与插件环境

- Provider 和基于标准库的 HTTP 请求自动遵循 `HTTP_PROXY` / `HTTPS_PROXY`；留空时直连。
- TLS 证书校验始终使用系统默认安全策略，不再支持 `HTTP_VERIFY_SSL` 绕过。
- Kemo 的 HTTP 模式只适用于同机或可信内网；跨主机、非可信局域网或公网必须使用 HTTPS 反向代理。完整的幂等、续传、取消和故障边界见 `global_knowledge/kemo-transport-reliability.md`。
- `web_search` 始终参与正常的插件发现与白名单过滤；`TAVILY_API_KEY` 为空时，调用会返回配置引导且不会发起网络请求。配置密钥后需重启智能体使环境变量生效。

---

## 13. 错误处理

- 同一操作连续失败 2 次后暂停分析失败原因，不盲目重试。
- 连续失败 3 次必须停止，向用户报告：操作目标、错误信息、需要的帮助。
- 工具调用失败时记录错误类型和消息，不伪造结果。
- Provider 错误区分：auth（不可重试）、timeout（可重试）、connection（可重试）、其他 HTTP 错误按状态码判断。
- Provider 在首轮调用返回上下文超限时，丢弃失败尝试的增量事件，调用 `context_manage` 压缩后重试；工具循环中途仍停止，避免拆散工具消息组。
- 单次工具内联 JSON 结果硬限制为 100,000 字符。超限正文不会进入 Provider、事件或历史，只返回 `ToolResultTooLargeError` 与缩小范围提示；文件内容改用 `file.stat` 和 `file.read_range` 分段读取。该受控拒绝不计入连续工具失败次数。
- 记忆提取失败不回滚已提交的历史。

---

## 14. 交付标准

- 修改前读取目标及相邻内容，使用最小修改面。
- 成功提交的对话才写入正式历史；失败或取消不伪造完整轮次。
- 完成后说明：
  - 做了什么
  - 验证结果
  - 仍存在的限制
  - 下一步建议
- 正文使用普通 Markdown。
- 不超过 100,000 字符的工具结果自动进入下一轮上下文；超限结果只进入省略正文后的诊断提示。
- 不确定是否应展示为 artifact 时保留文本。
