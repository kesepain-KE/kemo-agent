# 模块与智能体能力开发说明

本文集中说明拓展、感知、技能、子智能体和外部智能体桥接的创建与运行合同。每个章节保留自己的清单、作用域和安全边界；不属于这些范围的插件、消息路由和内置拓展仍查对应专题文档。

## 拓展模块创建文档

拓展（Expand）用于连接外部设备、API 或服务。它可以同时提供“状态数据进入 Prompt”和“由智能体发起操控”两种能力。纯说明使用技能，纯采集使用感知，独立 LLM 推理使用子智能体。

### 作用域

| 作用域 | 路径 | 创建方式 | 自动刷新 |
|--------|------|----------|----------|
| 全局 | `global_expand/<name>/` | 管理员手动维护 | RuntimeHost 按 `task_cron_system.expand_update_rate` 刷新 |
| 共享 | `shared_expand/<name>/` | `expand_creater` 的 `scope=shared` | 与全局层同周期、每轮只刷新一次 |
| 用户 | `users/<user>/expand/<name>/` | `expand_creater` 的 `scope=user` | 同一系统任务按用户身份隔离刷新 |

`expand_creater` 不创建全局拓展。共享和全局拓展可被用户配置白名单过滤；当前用户拓展按用户目录实时发现。

框架内置的 `global_expand/kemo_gateway_status/` 是默认未激活的只读网关状态拓展。它的激活方式、独立 `STATUS_TOKEN`、脱敏产物和更新保留规则见 `builtin-expansions.md`。

`expand_update` 到期后先以 `__system__` 身份刷新全局层和共享层，再为每个有效用户分别执行其私有目录。全局/共享模块不会因用户数量重复运行，用户模块也不会跨用户拼接结果。每个更新入口在独立 Python 子进程中执行，受 `task_cron_system.module_update_timeout` 限制；热插拔模块不会被导入 Web/Runtime 主进程。

Windows 后台采集和框架发起的 `expand_call` 操作由框架隐藏终端，Linux 使用独立进程组。模块不需要自行设置 `CREATE_NO_WINDOW`、`CREATE_NEW_CONSOLE`、`pythonw` 或终端参数。用户手动运行 `data_update.py`、`start_expand.py` 属于前台调试，是否显示当前终端由用户的启动方式决定。

这里的“隔离子进程”提供崩溃隔离、超时、取消和进程树回收，不是操作系统安全沙箱。拓展代码仍继承 kemo-agent 进程的文件和网络权限，只能安装受信任的模块；需要常驻会话时应连接由系统或用户明确管理的外部服务，当前框架不提供通用 daemon 托管。

模块名必须匹配 `^[A-Za-z][A-Za-z0-9_-]{0,63}$`，并与目录名保持一致。

### 最小框架合同与自由实现

框架把 `<expand-root>/<name>/` 这个直接子目录视为一个完整拓展，只关心 `expand.json` 及其声明的数据、说明、采集和操控入口。创建器附带的几个文件只是可运行的最小合同，不是拓展目录的标准结构或规模上限。

拓展目录内部可以自由包含任意数量和层级的源码、资源、配置、依赖与构建产物，也可以直接容纳一个完整开源项目；框架不会因为文件未出现在模板中就拒绝、删除、注册、注入或自动执行它。极小功能可以只有最小入口，大型功能可以保留原工程结构并使用声明入口作为适配层。不要为了迎合模板把整个工程塞进 `data_update.py` 或 `start_expand.py`，也不要反过来强迫简单拓展采用复杂分层。

清单引用的文件必须位于模块目录内，不得使用绝对路径、`..`、符号链接或目录联接跳出作用域。这是作用域安全边界，不限制模块目录内部采用何种工程结构。

### expand.json

```json
{
  "name": "room_light",
  "explain": "读取并控制房间灯光状态",
  "open_input": true,
  "input_data": "input_data.md",
  "input_health": "正常",
  "start_update": "data_update.py",
  "open_control": true,
  "start_expand": "start_expand.py",
  "start_control": "expand_control.md"
}
```

| 字段 | 类型 | 规则 |
|------|------|------|
| `name` | string | 非空，建议与目录名一致 |
| `explain` | string | 非空的一句话职责说明 |
| `open_input` | bool | 是否允许状态数据进入 Prompt |
| `input_data` | string | 模块内 `.md` 文件名 |
| `input_health` | string | 只能是 `正常` 或 `异常` |
| `start_update` | string | 模块内 `.py` 采集入口 |
| `open_control` | bool | 是否开放操控能力 |
| `start_expand` | string | 模块内 `.py` 操控入口 |
| `start_control` | string | 模块内 `.md` 操作说明 |
| `recent_update` | string，可选 | 若存在必须为 `YYYY-MM-DD HH:MM:SS`；尚未运行时应省略，不能写空字符串 |

清单拒绝未知字段。即使关闭注入或操控，被引用文件仍应存在并保持合法，便于模块恢复启用。

### 数据层与操作层

`input_data.md` 保存适合进入 Prompt 的最近状态摘要或资源索引，不是完整数据库。`start_update` 指向的脚本只是框架入口，可以直接实现采集，也可以调用模块内部任何实现；只需确定性更新 Markdown，且不得把 API Key、Token 或密码写入结果。大型内容保存在模块目录内由拓展自行选择的位置，再由 Markdown 写出按需读取路径，不能把文件正文或 Base64 全部塞入 Prompt。`data/`、`artifacts/` 只是常见名称，不是强制目录。

更新函数可以在成功结果中返回 `resources` 数组，声明模块目录内产生的文件。框架只验证并记录路径、类型、大小和更新时间，不复制资源、不自动读取正文，也不自动注入。新建模块初始为 `input_health=异常` 且没有 `recent_update`；系统刷新器会在首次成功后把健康状态设为 `正常` 并写入真实采集时间，失败时设为 `异常` 并保留上一次成功时间。模板采集器也写回这两个字段，以兼容人工直接运行；框架仍会在隔离执行结束后做最终确认。

调度器优先调用同步、零参数的 `update()`，不存在时回退到同步、零参数的 `main()`。成功可以返回 `None` 或 `{ "ok": true }`；返回 `False`、`{ "ok": false }`、失败状态或抛出异常均视为失败。更新入口必须在超时内自行结束，禁止无限循环、等待交互、常驻线程或自行启动守护进程。

`expand_control.md` 必须包含：

```markdown
## 注入层

说明模块是什么、何时使用、当前可见状态和操控入口。

## 操作层

逐项写清命令名、参数、返回值、失败格式、权限和副作用。
```

注入层可以进入主智能体 Prompt；操作层不会自动全部注入，应由智能体按需读取。Prompt 管线在每次模型请求时读取最新 `input_data.md`，cron 只负责刷新文件，不负责模板变量替换或直接注入。

主智能体应使用 `expand_call`，明确传入 `scope`、`module`、`command` 和 `params`。框架检查用户白名单、`open_control`、路径和符号链接后，在隔离子进程中加载清单声明的操控入口；结构化参数通过 stdin 传输，不能用 Shell 绕过权限检查。入口可以包装模块内的任意程序或完整工程，但必须向框架提供统一函数：

```python
def execute(command: str, params: dict | None = None) -> dict:
    """返回结构化结果；成功与失败都必须有明确状态。"""
```

若用户配置了非空 `plugins.whitelist`，还必须把 `expand_call` 加入插件白名单；空数组仍表示允许全部插件。子代理默认没有此能力，只有自身 `agent-config.json` 明确授权后才能执行具有外部副作用的拓展操作。

结果中的 `data` 可以是任意 JSON 兼容数据。大型 DOM、日志、图片、音视频和二进制内容必须保存为模块内文件，并通过 `artifacts` 返回路径；框架校验后将文件发布到当前用户下载空间。操控结果直接属于当前工具调用，不经过 `input_data.md`。同一模块的自动采集、Web 手动刷新和操控入口由框架串行执行，等待执行锁也计入本次超时，避免同时访问同一设备、浏览器会话或状态文件。不能用占位实现伪造外部操作成功，未接通真实服务时必须返回明确错误。

旧模块的 `execute(command_dict)`、JSON 字符串返回和 argv 人工调用仍兼容，但新模块不得继续使用旧合同。

### 运行诊断

框架在模块目录维护 `_runtime.json`，分别记录 `update` 和 `control` 的最近尝试、最近成功、耗时、错误类型以及受限资源数量。该文件不保存完整参数、stdout、采集正文、Base64 或凭据，也不会进入 Prompt。操控失败不会把 `input_health` 改为异常，因为一次命令参数错误不代表采集数据已经失效。

### 创建流程

1. 判断需求确实需要外部连接或操控，而不是技能、感知或一次性脚本。
2. 确认 `user`/`shared` 作用域、英文名称和职责说明。
3. 确认注入数据、操控命令、参数、返回值及副作用。
4. 先列出现有模块查重，再获得用户最终确认。
5. 使用 `expand_creater action=create` 建立最小框架合同；随后可在新模块目录内自由创建、复制或迁入其他文件及完整工程。全局层由管理员按相同合同手工创建。
6. 完成内部实现后运行 `validate`，再在终端执行一次声明的更新入口，并用 `expand_call` 执行无副作用的健康检查；手动测试是前台执行，RuntimeHost 自动刷新和正式操控仍采用隐藏的隔离子进程。

### 验收清单

- 清单字段完整且无未知字段，所有路径均留在模块目录。
- `input_data.md` 和 `expand_control.md` 为有效 Markdown。
- Python 文件语法通过，不含硬编码凭据。
- 数据采集失败会记录错误，不覆盖成虚假健康状态。
- 更新脚本提供零参数同步 `update()` 或 `main()`，可在配置超时内结束；返回 `False`、`{ "ok": false }` 或失败状态会被调度器判为失败。
- 采集和操控脚本不自行创建终端窗口、守护进程或无限循环；后台隐藏和进程回收由框架负责。
- 所有外部操作返回结构化结果，并清楚标明副作用；大型内容以 artifact 返回，不进入工具 JSON。
- 共享/全局模块已按需加入用户白名单。
- 模板外的内部文件和目录可以保留原工程结构，未被强行合并进入口文件，也不会被校验器仅因“未知”而拒绝。

---

## 感知模块创建文档

感知（Sense）是“外部或系统状态 → Markdown → System Prompt”的单向数据流。它适合 CPU、内存、磁盘、天气、网络或设备在线状态等定期采集，不允许借感知模块操控外部对象。

需要操控时使用拓展；需要独立推理时使用子智能体；只需提供说明时使用技能。

### 位置与发现

感知只有全局层。框架把 `global_sense/<name>/` 这个直接子目录视为一个完整感知模块，只识别 `sense.json` 声明的清单、数据出口和更新入口；这几项是框架合同，不是模块目录的完整结构。

除清单声明的文件外，模块目录内部可以包含任意数量、任意层级和任意用途的文件或目录，也可以容纳一个完整工程。框架不会注册、注入或自动执行这些内部文件。智能体可以按实际需求自由组织实现，也可以把整个功能写成一个极小入口；不得因为模板只附带少量骨架文件，就把所有客户端、解析、缓存或业务逻辑强行堆进 `data_update.py`。

模块必须是 `global_sense/` 的直接子目录。名称匹配 `^[A-Za-z][A-Za-z0-9_-]{0,63}$`。根目录 `global_sense/register.py` 注册整个感知来源，用户配置 `perception.global_whitelist` 决定主智能体可见模块。

RuntimeHost 启用后台调度时，系统任务按 `task_cron_system.sense_update_rate` 扫描所有合法全局感知模块并执行其 `start_update`。每个入口都运行在独立、受超时约束的 Python 子进程中，不会把热插拔代码导入 Web/Runtime 主进程。

这是所有感知模块共享的框架级频率，不是 `sense.json` 的模块级字段。Web `/api/users/{user}/sense` 接口从同一份全局配置为每个来源返回整数 `update_interval_seconds`，并提供兼容显示字段 `update_interval`；前端依据秒数显示“每 N 秒/分钟/小时”。因此不得在模块清单中重复添加 `update_interval` 或 `interval_seconds`，也不得根据 `recent_update` 时间差猜测频率。用户配置中的同名值不会改变这项全局系统任务。

Windows 后台采集由框架使用隐藏窗口模式启动，Linux 使用普通无交互子进程。感知模块不需要也不应该自行设置 `CREATE_NO_WINDOW`、`CREATE_NEW_CONSOLE`、`pythonw` 或终端窗口参数。用户手动执行 `python data_update.py` 属于前台调试，是否显示当前终端由用户的启动方式决定。

### sense.json

清单必须且只能包含以下五个字段：

```json
{
  "name": "system_health",
  "data_md": "sense.md",
  "recent_update": "2026-07-23 13:00:00",
  "health": "正常",
  "start_update": "data_update.py"
}
```

| 字段 | 规则 |
|------|------|
| `name` | 非空模块名，建议与目录名一致 |
| `data_md` | 模块目录内的 `.md` 文件名，文件必须存在 |
| `recent_update` | 必须为 `YYYY-MM-DD HH:MM:SS`，不能留空 |
| `health` | 只能是 `正常` 或 `异常` |
| `start_update` | 模块目录内的 `.py` 文件名 |

路径不得是绝对路径、不得包含 `..`，模块目录和关键文件不得是符号链接或目录联接。

### sense.md

该文件全文可能进入所有启用此模块用户的 System Prompt，因此只保存必要、低敏感、可共享的数据。

框架不规定标题、章节、表格或字段结构；内容只需让模型能够理解当前状态，并保持确定、有界。可以输出几行文本，也可以输出按实际数据组织的 Markdown。不要写入密钥、Cookie、完整环境变量、私人文件正文或无界日志；体积较大的原始数据应留在模块目录中，仅在这里提供必要摘要或资源索引。

### 更新入口合同

`start_update` 指向的是框架调用入口，不是实现必须集中的文件。入口可以直接完成极小采集，也可以只是适配器，导入或调用模块目录内的任意内部实现乃至完整工程。框架只要求最终入口满足以下合同：

1. 读取本机或外部只读数据。
2. 原子或完整地重写 `sense.md`。
3. 更新 `sense.json` 中的 `recent_update` 与 `health`，或由配套运行逻辑维护等价状态。
4. 失败时返回/抛出明确错误，不把旧数据伪装成刚刚更新。
5. 所有认证信息从环境变量读取，绝不硬编码。

模块可以完全重写模板代码；`collect()`、`render_markdown()`、`data/` 等名称和布局都只是骨架示意，不是运行时要求。内部工程采用何种语言、包结构和依赖组织由需求决定，只需由声明的 Python 入口完成协议适配。

系统更新器优先调用同步、零参数的 `update()`，不存在时回退到同步、零参数的 `main()`。成功可以返回 `None` 或 `{ "ok": true }`；返回 `False`、`{ "ok": false }`、`status=error/failed/failure` 或抛出异常均视为失败。不得在模块导入阶段执行采集或副作用。

每次执行受 `task_cron_system.module_update_timeout` 限制。更新函数必须自行结束，禁止无限循环、常驻线程、后台守护进程或等待交互输入。超时、崩溃和异常只会把当前模块标记为失败，不应影响其他模块和主智能体。

### 创建流程

1. 确认需求是只读采集，不含操控。
2. 确认名称、采集指标、数据可见范围，并核对现有全局 `sense_update_rate` 是否满足需要；修改它会同时影响所有感知模块。
3. 设计初始数据出口，明确哪些信息允许进入 Prompt；不要预先规定内部工程结构。
4. 列出现有模块查重并获得最终确认。
5. 使用 `sense_creater action=create` 建立最小框架合同；随后可按需求在新模块目录内自由创建或迁入其他文件和完整工程，再执行 `validate`。
6. 在终端手动运行一次声明的采集入口，确认清单时间格式、Markdown 内容和失败行为；该手动测试是前台执行，不代表 RuntimeHost 后台会弹出窗口。

### 验收清单

- `sense.json` 恰好五字段且值合法。
- 刷新频率只由全局 `task_cron_system.sense_update_rate` 声明，模块清单不重复保存调度频率。
- `sense.md` 与 `data_update.py` 均存在，路径没有越界。
- 采集脚本提供零参数同步 `update()` 或 `main()`，可重复运行、可在超时内结束，导入时无副作用。
- 脚本不自行创建终端窗口、守护进程或无限循环；后台隐藏由框架负责。
- 输出不含凭据、隐私或大段原始日志。
- 用户白名单与期望一致，Prompt 诊断显示模块已选中。
- 额外文件和内部目录不因“模板未列出”而被删除、拒绝或强行合并进入口文件。

---

## 技能创建文档

技能（Skill）是注入智能体 Prompt 的 Markdown 指令，用来提供工作方法、领域规范和可复用流程。技能不会注册 Provider function call；真正可执行的工具只能放在 `plugins/`。

### 作用域

| scope | 路径 | 使用者 |
|-------|------|--------|
| `agent_create` | `users/<user>/user_skills/agent_create/<name>/` | 主智能体与 `self_improve` |
| `user_create` | `users/<user>/user_skills/user_create/<name>/` | 当前用户主智能体 |
| `shared` | `shared_skills/<name>/` | 所有允许该共享技能的用户主智能体 |

共享技能受 `skills.shared_whitelist` 过滤；空数组表示全部允许。用户技能按当前用户目录发现。目录可以嵌套，白名单名称使用相对路径，例如 `development/python`。

### 最小合同与自由工作区

运行时递归寻找技能目录中的 `SKILL.md`；这是唯一必需的框架合同。文件必须包含一级标题，一级标题之后、下一个二级标题或 `---` 之前的内容作为技能描述。除此之外，框架不规定章节数量、名称、写作方式或目录结构。

技能目录可以只含一个短 `SKILL.md`，也可以包含任意文件、任意层级资料、脚本、资源、模板或完整配套工程。目录名如 `references/`、`scripts/`、`assets/` 只是社区常见习惯，不是保留名或必需结构；创建时应按真实内容组织，不能为了匹配样例制造空目录。

额外文件不会自动进入 Prompt，也不会因为位于技能目录就注册成工具或自动执行。`SKILL.md` 应通过相对路径说明何时读取或如何使用它们；需要真实执行时仍必须使用已有获准工具。可以增加 `## Tool` JSON 作为参数文档，但它仅用于说明，不会成为 Provider 可调用工具。

### 选择技能还是其他能力

| 需求 | 应选能力 |
|------|----------|
| 可复用说明、规范、工作流 | 技能 |
| 真实文件、网络或系统操作 | `plugins/` 工具 |
| 外部服务状态与操控 | 拓展 |
| 独立 LLM Prompt、权限和工具循环 | 子智能体 |
| 一次性任务 | 直接执行，不创建长期能力 |

### 创建与更新流程

1. 确认需求具有稳定复用价值。
2. 确认 `agent_create`、`user_create` 或 `shared` 作用域。
3. 确认目录名、标题、描述、适用/不适用场景和正文。
4. 使用 `skill_creater action=list` 查重。
5. 用户确认后执行 `create`；修改已有技能使用 `get` 后再 `update`。
6. 创建器建立 `SKILL.md` 后，可继续用正常文件工具在技能目录内自由添加或迁入配套资源；执行 `validate`，确认主智能体的技能诊断能发现它。

`skill_creater` 支持完整 `content` 写入，或 `title + description + instruction/tool_schema` 结构化写入。`instruction` 与 `tool_schema` 二选一。
`update` 只替换 `SKILL.md`，保留其他内部文件；`delete` 删除整个技能目录，因此删除前必须把配套资源一并纳入影响确认。

### Web 上传用户技能

“工具与技能”页面可通过“上传用户技能”安装当前用户已有的技能包。只接受 ZIP，且只会写入 `users/<user>/user_skills/user_create/`，不会写入共享技能、智能体生成技能或基础插件。

- ZIP 可以带任意层级的外层包装目录；上传器递归寻找大小写不敏感的 `SKILL.md`。
- 每个最外层技能根会成为一个独立的用户技能目录；若 ZIP 根目录直接含 `SKILL.md`，则使用 ZIP 文件名建立外层技能目录。
- 技能根内的脚本、资料、工具说明、资源和子目录会完整保留。嵌套技能仍会被运行时递归发现。
- 一个 ZIP 可以安装多个互不包含的技能；同名目标不会覆盖，整包任一项校验失败都会整体回滚。
- 上传器拒绝路径穿越、绝对路径、符号链接或目录联接、加密 ZIP、异常压缩比、Windows 非法路径、大小写冲突、缺少 `SKILL.md` 或缺少一级标题的技能。

上传成功后无需重启；页面会切换到“用户自建技能”并刷新技能库存。

### 编写规则

- 一个技能只解决一个清晰主题，标题和目录名保持稳定。
- 指令必须可操作，写清触发条件、步骤、失败处理和禁止事项。
- 引用附加资源时使用相对路径；未被显式读取的参考文件不会自动生效。
- 不以模板示例作为固定目录规范，也不因未知内部文件而强制删除或合并。
- 不在技能中存储密钥、Token、密码、Cookie 或个人隐私。
- 不把“文档化 Tool”描述成已经可执行的工具。
- 删除或扩大共享技能影响范围前必须确认。

---

## 子智能体创建文档

子智能体适合需要独立 LLM Prompt、独立工具循环、独立权限或可被主智能体/Cron 重复调用的任务。单次整理、普通文件操作、纯说明或已有工具能完成的需求，不应创建重型子智能体。

### 位置与发现

| 类型 | 路径 | 说明 |
|------|------|------|
| 内置 | `agents/<name>/` | 框架受信任子智能体，可携带执行代码 |
| 用户 | `users/<user>/agents/<name>/` | 当前用户热插拔子智能体，不能覆盖内置名称 |
| 外部绑定 | `global_expand/<name>/agent_bridge.json`、`shared_expand/<name>/agent_bridge.json` 或 `users/<user>/expand/<name>/agent_bridge.json` | 通过已授权拓展隔离进程调用外部智能体；不把远程地址或凭据交给核心 |

发现管线每次执行前重新扫描，因此用户子智能体新增或修改后通常无需重启。自定义 Python 执行器会被主进程直接导入，没有代码沙箱，只能安装可信代码。

名称必须匹配 `^[A-Za-z][A-Za-z0-9_-]{0,63}$` 并与目录名一致。

### 最小包合同与自由实现

框架合同由 `agent.json`、`agent-config.json`、`AGENT.md` 和 `trigger.md` 构成；`executor.py` 与 `schema.json` 均可选。它们只是发现、权限、Prompt、调用和输入输出边界，不是子代理内部工程的完整结构。

子代理目录可以自由包含任意模块、包、配置、资源、测试或完整工程。没有 `executor.py` 时使用内置 LLM 执行器；需要自定义逻辑时，根入口 `executor.py` 可以保持很薄并导入目录内任意层级的内部实现。未被入口导入或未被合同引用的文件不会自动加载、注入 Prompt 或执行，也不会仅因模板未列出而被校验器拒绝。

`subagent_dispatch action=create` 只原子建立安全的数据型最小包。创建成功后可以继续使用正常文件或代码工具完善目录、增加 `schema.json` 或可信执行代码；创建接口的 `definition` 不承担传输完整工程的职责。

#### agent.json

精简清单必须恰好包含四个字段：

```json
{
  "name": "report_reviewer",
  "version": "1.0.0",
  "description": "独立审查报告结构、证据与遗漏项。",
  "trigger": "trigger.md"
}
```

`trigger` 必须是同目录文件名。精简清单存在 `executor.py` 时自动使用 `executor.py:execute`，否则使用内置 LLM 执行器。

#### agent-config.json

```json
{
  "schema_version": 1,
  "internal_mode": false,
  "allowed_callers": ["main_agent"],
  "tools": {
    "plugins": {"allow": ["file"]},
    "shared_skills": {"allow": []},
    "max_iterations": 20
  },
  "global_knowledge": false,
  "shared_knowledge": false,
  "inherit_main_history": false
}
```

| 字段 | 说明 |
|------|------|
| `internal_mode` | `true` 为内部代理，不向主智能体公开；`false` 可作为工具调度 |
| `allowed_callers` | 明确允许的调用方，例如 `main_agent`、`engine` |
| `tools.plugins.allow` | 可调用插件白名单；缺失授权默认拒绝 |
| `tools.shared_skills.allow` | 可注入共享技能白名单 |
| `tools.max_iterations` | 子智能体单次运行允许的工具调用上限，正整数；同时受全局同名配置的硬上限约束 |
| `global_knowledge` | 是否注入全局知识索引 |
| `shared_knowledge` | 是否注入共享知识索引 |
| `inherit_main_history` | 是否继承主会话历史和当前请求；默认关闭 |

主智能体用户配置的白名单不会自动收缩子智能体显式授权。创建时坚持最小权限，不因为“可能有用”扩大工具、知识或历史范围。子智能体不会得到用户技能、三层拓展或 `subagent_dispatch` 递归调度能力。

#### AGENT.md

必须清楚写出：核心职责、输入来源、执行流程、输出对象、禁止事项、失败处理和写入边界。不要把用户输入拼成更高优先级系统规则。

#### trigger.md

必须含准确标题：

```markdown
# 注册信息

- **名称**: report_reviewer
- **触发**: 当用户要求独立审查报告质量时
- **职责**: 检查证据、结构、矛盾和遗漏
- **模型**: reasoning
- **工具**: file

# 操作信息

## 调用方式

说明输入对象、输出对象、错误和注意事项。
```

运行时只把“注册信息”摘要注入主智能体；操作信息按需读取。

#### executor.py 与 schema.json

可选执行器合同：

```python
def execute(context, input_data: dict):
    return context.run_model(input_data)
```

执行器应检查 trigger、取消信号和输出格式。`schema.json` 若存在，必须且只能包含 `input_schema` 与 `output_schema` 两个对象；缺失时使用宽松对象 Schema。

自定义执行器会被主进程直接导入，不具备代码沙箱。内部工程自由不改变这一信任边界：只能放入可信代码，不得在导入阶段启动线程、发起网络请求或产生其他副作用；长期任务仍必须响应 `context.cancel_event` 和整体超时。

### 创建流程

1. 判断是否真的需要独立推理与权限边界。
2. 列出真实可用插件和共享技能，逐项让用户确认授权。
3. 确认是否访问全局/共享知识以及是否继承历史。
4. 确认名称、职责、完整 instruction 和明确触发条件。
5. 使用 `subagent_dispatch action=list` 查重并最终确认。
6. 使用 `action=create` 原子创建最小用户代理包；按需求继续在目录内自由添加内部模块或已有工程，然后重新发现校验。
7. 用最小输入试运行，验证 JSON 输出、超时、取消、工具权限和自定义入口导入行为。

### 调用规则

主智能体通过 `subagent_dispatch` 的 `list/call/status/cancel` 调用公开代理。同步任务使用 `wait=true`；长任务可后台提交并查询状态。调用前读取目标 `trigger.md`，按照约定构造结构化输入。

#### 外部智能体绑定

拓展可以额外放置 `agent_bridge.json`，把外部 kemo-agent、其他 Agent 服务或本地代理程序
包装成可调用的同步子代理。文件只声明公开名称、说明、`command`、输入/输出 JSON Schema
和可选超时，不声明 URL、Token、密码或其他凭据：

```json
{
  "schema_version": 1,
  "agents": [
    {
      "name": "researcher",
      "description": "外部研究智能体",
      "command": "external_agent_call",
      "input_schema": {"type": "object", "additionalProperties": true},
      "output_schema": {"type": "object", "additionalProperties": true},
      "timeout": 600
    }
  ]
}
```

调用句柄为 `external:<scope>:<拓展名>:<代理名>`。核心会在调用前重新检查拓展白名单、
清单、符号链接和 Schema，再通过 `start_expand.py` 传递 `{agent, input, protocol}`；拓展返回
`{"status":"completed","data":{...}}` 后，核心再次校验输出。当前只允许 `wait=true` 同步调用，
因为外部服务尚未共享 kemo-agent 的持久任务状态、取消和恢复合同。远程服务的认证信息必须由
拓展从受信环境变量或私有配置读取，不能写入桥接清单、Prompt、回复或日志。

子智能体整体期限来自 `agent_runtime.default_timeout`，与其内部每次工具调用的 `tools.timeout` 相互独立。同步调用的 `subagent_dispatch` 使用调用方传入的 `timeout` 作为本次等待期限，不会在普通工具默认期限到达时提前截断；期限到达后，如果子代理仍在排队或运行，会先返回 `task_id`，继续保留任务用于状态追踪和取消。收尾存活期内自然完成时记录 `completed_after_timeout`；如果调用方已经收到超时结果、底层线程之后才完成，状态会从 `timed_out_running` 收敛到 `completed`，并在结果元数据中标记 `completed_after_detach`。收尾后执行线程仍未退出时记录 `timed_out_running`，该状态仍允许发起取消请求，不能描述成已经强制终止。

---

## 外部智能体桥接合同

`kemo-agent 1.2.4` 支持把已经授权的拓展模块包装成外部子智能体入口。该能力用于连接另一个
kemo-agent、其他 Agent 服务或本地代理程序，同时保留核心现有的权限、超时、取消和结果大小边界。

### 设计原则

- 核心不接收模型或用户临时提供的任意远程 URL。
- 外部连接由拓展的 `start_expand.py` 执行，拓展继续运行在现有隔离子进程中。
- 远程 URL、Token、密码、Cookie 和私钥只从拓展自己的受信环境变量或私有配置读取，不写入
  `agent_bridge.json`、Prompt、回复、历史或日志。
- 外部代理必须显式放在全局、共享或当前用户的拓展目录中，并通过现有拓展白名单和 `open_control`
  开关授权。
- 当前只支持同步调用。外部服务没有接入 kemo-agent 的持久任务状态、取消和恢复合同前，不允许
  使用 `wait=false` 创建无人管理的后台任务。

### 文件位置

在拓展模块同一目录增加可选文件：

```text
global_expand/<module>/agent_bridge.json
shared_expand/<module>/agent_bridge.json
users/<user>/expand/<module>/agent_bridge.json
```

拓展本身仍须有有效的 `expand.json`、`start_expand.py` 和 `expand_control.md`，并且
`open_control=true`。没有 `agent_bridge.json` 的拓展不作为外部智能体发现。

### `agent_bridge.json`

根节点只允许 `schema_version` 和 `agents`：

```json
{
  "schema_version": 1,
  "agents": [
    {
      "name": "researcher",
      "description": "外部研究智能体",
      "command": "external_agent_call",
      "input_schema": {
        "type": "object",
        "properties": {"request": {"type": "string"}},
        "required": ["request"],
        "additionalProperties": false
      },
      "output_schema": {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": false
      },
      "timeout": 600
    }
  ]
}
```

每个代理项的规则：

| 字段 | 规则 |
|---|---|
| `name` | 以字母开头，只能使用字母、数字、`_`、`-`，最长 64 字符 |
| `description` | 非空，最长 2000 字符；写清外部代理负责什么 |
| `command` | 传给拓展 `start_expand.py` 的合法命令名 |
| `input_schema` | 可选 object JSON Schema，省略时使用宽松对象 Schema |
| `output_schema` | 可选 object JSON Schema，省略时使用宽松对象 Schema |
| `timeout` | 可选正数，最大 3600 秒，默认 600 秒 |

桥接文件最大 256 KiB，最多声明 64 个代理。名称、命令、Schema 和超时在发现与调用时都会重新
校验；解析失败只会隐藏该绑定，不会导致本地内置/用户子代理整体不可用。

### 发现和调用

主智能体使用 `subagent_dispatch action=list` 查看统一列表。内置/用户代理继续使用原名称；外部
代理使用不可冲突的句柄：

```text
external:<scope>:<module>:<name>
```

例如：

```text
external:user:remote_bridge:researcher
```

调用格式：

```json
{
  "action": "call",
  "agent": "external:user:remote_bridge:researcher",
  "wait": true,
  "input": {"request": "请整理这份资料"}
}
```

核心会按当前用户重新确认拓展作用域、白名单、清单、符号链接和输入 Schema，然后通过拓展隔离
进程调用声明的命令。传给 `start_expand.py` 的 `params` 为：

```json
{
  "agent": "researcher",
  "input": {"request": "请整理这份资料"},
  "protocol": "kemo-agent-external-agent-v1"
}
```

拓展应返回：

```json
{
  "status": "completed",
  "data": {"answer": "外部代理结果"},
  "usage": {"total_tokens": 123},
  "model": "remote-model"
}
```

`status` 为 `error`、`failed` 或 `failure`，或 `ok=false` 时，核心按失败处理；成功结果中的
`data` 必须通过声明的输出 Schema。核心只把受限的状态、数据、用量和模型名称返回给主智能体，
不会把桥接文件路径或内部认证信息返回。

### 稳定性与副作用

- 外部调用的等待期限最大 3600 秒；调用方期限到达且任务仍在运行时先返回本地 `task_id`，底层拓展继续保留配置的收尾存活期，最终执行上限还会额外包含该存活期。当前 Run 取消会传给拓展隔离进程。
- 拓展的模块执行锁仍然生效，同一模块的采集和操控不会并行破坏共享状态。
- 外部代理可能产生不可逆副作用时，调用方必须遵循拓展操作手册中的确认规则；核心不会假装远程
  操作具有幂等性，也不会在没有明确合同时自动重复调用。
- 外部代理当前不进入 `AgentRunner` 的本地工具白名单，不继承主会话历史、主智能体工具或用户技能。
  它只接收 `input` 中显式传入的数据。
- 使用 `wait=false` 会被明确拒绝，而不是悄悄放入本地子代理队列。将来若外部服务提供统一的
  持久任务 ID、状态、取消、恢复和幂等合同，可再单独扩展后台模式。

### 验收

至少验证以下路径：

1. `action=list` 能列出合法的外部绑定，且不列出未授权或损坏的拓展。
2. `action=call` 能通过拓展命令获得结构化 `data`，输入和输出 Schema 错误会在调用边界拒绝。
3. global/shared 拓展不在用户白名单时不能调用；user 拓展只能属于当前用户。
4. 符号链接、越界路径、超大桥接文件、非法命令和超时值不会被接受。
5. `wait=false` 明确失败，取消和拓展异常不会留下本地后台任务。
6. 测试不得在输出、日志或快照中写入 URL 中的凭据、Token、密码、Cookie 或私钥。

