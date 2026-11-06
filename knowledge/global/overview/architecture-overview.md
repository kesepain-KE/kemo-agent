# kemo-agent 架构概览

本文从开发者视角说明 kemo-agent 如何接收一次请求、构建上下文、调用 Provider、执行工具、
提交历史并驱动后台维护。`agents.md` 是智能体自身的操作手册；本文聚焦代码模块、请求生命周期、
并发隔离和数据流。

## 设计定位

kemo-agent 是本地优先、多用户、事件驱动的 Agent Runtime。它把对话、工具、子代理、记忆、
知识、计划、定时任务、感知、拓展和外部消息连接在同一个用户工作空间中。

核心对话运行时位于 `run/`，不依赖 Web 框架。FastAPI、CLI、外部消息和 Cron 都是入口适配层，
最终复用 `run/engine.py` 暴露的稳定公共门面。

事件是入口与运行时之间的主要通信方式。流式文本、思考、工具调用、工具结果、使用量、错误和
最终状态都表示为 `RunEvent`，Web 将其编码为 SSE，CLI 和消息路由则消费同一事件语义。

## 顶层运行组件

`run/` 以领域包作为单入口。外部生产代码只使用 `from run.<领域> import ...`；领域之间也
只通过对方入口协作，不直接依赖私有实现文件。

| 目录/模块 | 主要职责 |
|-----------|----------|
| `run/engine.py` | 对话、CardStream 压缩与上下文状态的稳定总门面 |
| `run/conversation/` | CardStream 整合编排、请求屏障、Provider 事件、引导和终态 |
| `run/cards/` | RAW/VIEW 卡片存储、Token 计量、滚动摘要、请求组装和执行登记 |
| `run/context/` | 输入容量查询及动态上下文状态门面 |
| `run/history/` | 会话索引、生命周期、检索目录与后台历史摘要 |
| `run/memory/` | 记忆生命周期、批量分析、提取管道和 SQLite 存储 |
| `run/tools/` | 插件发现、参数校验、执行、超时、取消和参数恢复 |
| `run/agents/` | 子代理运行、队列、服务与调用合同 |
| `run/tasks/` | 任务计划存储、修订、执行、服务和调度 |
| `run/scheduler/` | Cron、维护、运行状态和 RuntimeHost 生命周期 |
| `run/config/` | 配置合并、用户路径、Prompt 来源/组装和知识范围策略 |
| `run/extensions/` | 感知/拓展子进程、多模态、附件、媒体产物和模型能力 |
| `run/infra/` | 原子 I/O、进程执行、公共错误、日志和 CLI 桥 |
| `provider/` | Kemo 原生协议、Chat 兼容桥和统一内部模型 |
| `web/` | FastAPI API、认证、领域路由/服务和 React 前端 |

`run/__init__.py` 保持懒加载，避免插件或子代理发现时初始化完整对话运行时。`run/` 顶层只保留
该入口和 `run/engine.py` 总门面；旧平铺模块已经删除，生产代码、插件、测试和部署脚本必须统一
使用领域入口，不能再次建立平行兼容层。

### 工具装载原生媒体

用户附件、专用 `multimodal` 与工具主动 `attach_media` 三条路径并存。第三条路径由
`run/extensions/` 的装载意图／授权、Run 队列、原生传输和宿主投影四个职责实现；
`run/conversation/` 与 `run/agents/` 通过公共入口使用宿主，不跨域导入私有实现。
默认隔离进程插件只返回意图，宿主在当前工具批次完整收尾后向下一请求追加原生媒体。
队列属于逻辑 Run 的重试状态，不是全局变量、任务计划或持久跨轮队列。上下文重建只在
尚未获得成功响应的请求中保留媒体消息；成功响应后不再自动重复投影，显式重新装载除外，
终态仅持久安全元数据；
子代理的队列、模型能力及工具授权独立于主会话。
完整合同见 `knowledge/global/modules/plugin-development.md` 的“三种多媒体输入方式与 attach_media”。

## 一次请求的完整生命周期

### 1. 入口建立身份

入口把请求规范化为：

- 项目根目录；
- `user`；
- `source`（Web、Android App、CLI 或消息平台）；
- `session_id`；
- 用户文本、上传文件和可选运行中引导通道；
- 取消事件及任务计划上下文。

不同入口不能绕过用户身份。历史、记忆、知识、文件和配置都按用户目录或明确的全局/共享层级
解析。

### 2. 进入稳定门面

`run/engine.py` 只转发稳定 API：

- `iter_request_events()`；
- `stream_request()`；
- `handle_request()`；
- `compress_context()`；
- `context_status()`。

复杂逻辑留在领域模块，入口不应直接导入 `run/conversation/runtime.py` 的私有函数。

### 3. 获取会话锁并准备 CardStream

`run/conversation/` 使用项目根目录、用户、来源和会话 ID 组成会话身份，并取得会话级锁。
同一会话的整合串行执行，避免并发 Run 交错写入；不同用户和不同会话可并行。
每用户使用 `users/<user>/history/cards.sqlite3`。`context_cards` 同行保存不可变 RAW 与可变 VIEW；
请求快照只选择当前有效 VIEW，并在同一事务中核对会话代际、序号、视图版本和 Token 总量。
压缩不会删除 RAW，也不读取或迁移旧 window 历史。

Web 历史浏览读取当前用户的全部来源；个人置顶按置顶时间优先，普通历史按 `updated_at` 从新到旧展示，
可在搜索框下方展开月历，按 `Asia/Shanghai` 自然日查询指定日期的归档，或用下方“收藏”气泡筛选。
置顶不绕过筛选；筛选与排序在服务端分页前完成。标记独立持久化且不改变会话生命周期，合同见
`knowledge/global/runtime/storage-and-persistence.md`。日期、收藏及搜索查询使用独立前端缓存，不替换当前会话列表，
避免筛选较早日期时影响当前会话的续接、关闭或删除判断。Web 会话可以继续切换和管理；Android App 的
`app`、CLI 与 `message:<platform>` 会话作为只读归档打开，并显示来源、生命周期以及完整记忆处理状态。Web 的 closed 会话是可显式重开的已保存归档：历史卡片切换前先调用 reopen，再恢复当前页面租约；迟到心跳、无参数恢复和旧链接仍不能悄悄复活 closed 会话。deleted 会话只可视为已删除，永久不可恢复。
历史日期缓存中的会话若随后被清理，租约返回 404 时前端会解除 URL 绑定、清除该会话的历史/概览缓存并使全部日期归档查询失效，再回到开始页。开始页没有有效会话身份，因此不得展示用户其他会话或已清理会话遗留的活动计划卡。
App 桥接请求固定使用 `source=app`，不能由设备端改写为其他渠道；其活动会话租约、关闭、
压缩、删除和 CardStream 历史均与 `source=web` 分区隔离。
渠道只表示入口，不建立独立记忆区；同一内部用户的所有入口共享 `memory.sqlite3`。

### 4. 合并配置并建立资源策略

`run/config/` 读取 `config/global_config.yaml` 和用户 `user_config.yaml`。框架级运行参数按对象
深合并，Provider、模型和用户资源选择保持用户所有权。

资源策略决定当前主智能体可见的插件、技能、知识层、拓展和感知。子代理不继承该策略，而是
服从自己的 `agent-config.json`。

### 5. 构建 PromptBundle

`run/config/` 的 Prompt 门面按稳定顺序构建 Prompt：

```text
用户人格
→ 全局人格
→ agents.md
→ 全局/用户子代理注册摘要
→ 插件说明
→ 共享/用户技能
→ 已启用拓展能力说明
→ 永久记忆
→ 临时重要热视图
→ 半年/月/七天临时记忆
```

知识库文件不进入 System Prompt。全局专题入口由 `agents.md` 维护；知识正文通过获准工具
按需读取。Kemo Graph 是手动调用的侧载文档站，不替换本地知识和记忆。CardStream 有效历史
跟在固定 Prompt 之后；任务计划、最新感知和实时拓展数据构成不写入历史卡片的动态尾部。

固定 Prompt 中的人格、手册、能力说明和记忆在整合开始时构建；全局 `memory.prompt_injection=false` 时所有记忆正文不注入，只保留工具检索提示。开启注入时，记忆修订只刷新记忆段；关闭时跳过注入版本轮询，工具和后台维护照常按原规则执行。
每次主 Provider 请求前都会重新读取本会话任务计划及授权的最新感知、拓展快照，并与有效历史一同
冻结为请求快照。`perception.prompt_injection=false` / `expand.prompt_injection=false` 控制各自的动态尾部注入；旧 `realtime_injection`
不再控制刷新频率。请求前刷新不运行采集脚本，采集频率和超时仍由 RuntimeHost/Cron 统一管理。
同一网络请求内部的重试或 SSE 续传复用已经生成的请求正文。

### 6. 估算上下文并按需压缩

CardStream 请求屏障位于 `run/cards/barrier.py`，在每次主 Provider 请求之前冻结一致快照。
它分别估算固定 Prompt、有效历史卡片、动态尾部、虚拟媒体和工具 Schema，并以同一套原生
ToolDefinition 计量和发送。输出预留先受模型声明的输出上限约束，再从配置总容量及共享
context_window 中扣除；独立 max_input_tokens 不重复扣除输出。摘要请求按自己的输出额度
单独计算输入容量，不借用主请求的额度。已知请求 usage 只校准对应快照的估算，不改写卡片内容。
原生媒体的二进制载荷不作为文字 Token 计数，每个媒体块使用本地估算预留；普通 JSON 和工具
结果中的字符串仍按文字计量。本地文字计数统一使用固定版本 DeepSeek tokenizer，其他模型
也使用同一计数器；实报 usage 优先，缺失或标为估算的字段本地补偿。usage 校准按 Provider、
模型、计量版本和原生媒体类型集合隔离，只有实报输入量可参与，
媒体请求的偏差不放大后续纯文字请求；旧口径的 usage 不污染新估算。
只读诊断在有效能力缓存存在时复用相同容量计算，缓存按地址、凭据身份和模型隔离；
不从状态接口发起网络探测，无有效声明时标明能力未知。

自动压缩仅在此屏障触发：总占用超过有效输入上限的 80%、适配后 wire 估算超过该阈值，
或收到显式手动压缩请求。压缩目标为压缩前有效历史 Token 的配置比例（默认 30%），
不把固定 Prompt、动态尾部或工具 Schema 算进比例基数；目标还须受固定内容占用后剩余的
输入容量约束。固定内容单独经过完整请求估算及 usage 校准，已占满容量时直接报错。
若只是固定内容较高触发阈值、历史小到无法容纳最小摘要结构，而原请求仍装得下，则跳过这次
无收益自动压缩；显式手动压缩和实际超容量仍明确失败。

`run/cards/compaction.py` 从最旧的连续有效卡片前缀选批，已有滚动摘要自然位于前缀并继续吸收。
摘要器使用同模型、同 System、同工具 Schema 的只读请求，输出有有效正文的 JSON 检查点；
也接受有章节标题的 Markdown 检查点，不再强制四个固定键或按字符数逼近 token。压缩比例是软目标，
硬约束是实际减少历史且完整请求可容纳。尽量保留最新用户/引导原文，空间不足时才纳入摘要。
整批历史装不下时先按卡片边界分片；单张超大卡片仅将只读摘要输入按容量无损分片，携带原 card_id、
seq、integration_id、kind 及偏移。RAW 和实际工具参数不变，也不执行分片中的工具。
每片吸收前一片临时摘要，所有片段共用绝对 deadline 和额外失败重试额度；只提交最后的完整候选。
已收到响应的 usage 全部计入统计；结构/容量拒绝时换 ID 并追加针对性修正，网络重放保留原 ID。
摘要请求明确遇到上下文超限时缩小输入重新分片，不原样盲重试。

摘要卡片按真实历史消息标签及 JSON 字符串转义计量，选批时以最小摘要投影和保留后缀计算
剩余空间，包含工具调用被吸收后结果变成独立消息所增加的开销。
候选摘要完成结构和 Token 校验后，`CardStore.replace_prefix` 在单一事务中暂写 VIEW、失活被
吸收卡片，并结合未吸收后缀及压缩期间新到卡片再次检查完整请求容量，全部通过才提交。
返回的请求快照在该事务内冻结，动态尾部沿用压缩前的同一份快照。RAW 永不修改；Provider、
验证、容量检查或数据库事务失败时保留原 VIEW，压缩期间独立追加的卡片仍保留。
前台最多保留一个有效滚动摘要。

主 Provider 明确报告上下文超限时，在同一整合内强制压缩、缩小请求再继续，每段连续超限最多恢复 3 次，
必须验证请求实际缩小；不重跑已经执行的工具，不提前消费尚未成功送达的媒体或引导。
401/403、413 和普通参数错误不冒充 token 超限。预防性压缩失败但原请求仍可容纳时保留旧 VIEW 并继续，
本次整合内 60 秒避免重复预防压缩；手动压缩、真实超容量、取消或仍在运行的超时压缩不会被静默忽略。

工具调用、工具结果、用户引导和助手阶段性正文都是同一种可压缩历史卡片；整合/工具执行状态
由独立登记维护，不依赖调用卡片是否仍在前台。动态尾部在请求前读取最新任务计划、感知与拓展
状态，作为虚拟输入排在历史之后，不写入历史卡片表。Provider 适配器只负责协议合法化，
不能把工具结果中的原始调用快照重新解释为可执行调用。

运行时可通过 `request.compress=true` 或稳定 `compress_context()` 门面执行同一请求屏障压缩；
其余普通请求遵循自动阈值。不再有轮数上限、window 裁剪、逐轮摘要或 context_manage 压缩链路。

### 7. 调用 Provider

Provider 内部统一为 Kemo 协议对象：

- `provider/kemo_gateway.py` 访问 Kemo 原生网关；
- `provider/openai_chat.py` 访问 Chat Completions 兼容接口；
- `provider/adapters/compat.py` 在 Chat 与内部 Kemo 结构之间转换。

两条协议都必须在工具执行前确认终态和参数 JSON 完整。Chat 的截断、内容过滤和损坏参数不会
被包装成半个工具调用继续执行。详细规则见 `knowledge/global/runtime/provider-reliability.md`。

### 8. 执行工具循环

Provider 返回可执行工具调用后，运行时：

1. 检查连续相同调用和连续失败；
2. 从 Tool Registry 获取定义；
3. 校验参数；
4. 在独立工具线程执行；
5. 将成功或结构化错误结果回填消息历史；
6. 再次调用 Provider，直到最终回答或明确终态。

主整合默认持续处理工具直到真实终态，不设调用总数限制。工具结果完整写入 RAW，
由 VIEW 压缩及请求容量屏障管理，不静默截断。子代理仍使用独立的 100,000 字符内联结果预算，
超限保存完整产物并返回有界回执。完整机制见 `knowledge/global/modules/plugin-development.md`。

Provider 偶发生成不完整工具 JSON 时，运行时不会进入插件。主智能体和 `AgentRunner` 子智能体
都会按 `tools.invalid_tool_arguments_retries` 使用新请求 ID 请求模型重新生成，默认最多 2 次。
失败尝试的文本与思考会保留，但工具调用卡片和待执行状态会等到同一响应内全部调用通过参数校验
后才发布；任一并行调用损坏时整批调用均不执行。已经发布媒体或达到上限时不做静默重试。

子代理还可以通过拓展目录中的可选 `agent_bridge.json` 绑定外部智能体。绑定只声明公开
Schema 和拓展命令，实际网络连接与凭据由拓展隔离进程负责；核心以
`external:<scope>:<module>:<name>` 句柄统一列出和同步调用，并沿用拓展白名单、超时、取消、
路径及结果大小边界。未建立持久任务状态合同前，外部绑定不支持后台提交。

### 8.1 默认长任务

主智能体在同一 Run 与 CardStream 整合中持续处理工具、引导与必要后台结果，直到最终正文及完成屏障均满足。
Provider 的 completed 是一次模型请求的终态；正常结束整合还需要有效的智能体最终交付。
明确的 commentary、只有思考或空白正文只能作为阶段响应。必要执行仍在运行时保留其阶段事实并等待结果；
没有必要依赖却缺少最终交付时，进入同一次推理的有界恢复，耗尽后记录 missing_final_answer 并失败。
Schema 本身不合法的空输出仍按协议校验失败处理，不能借此扩大重试范围。
最终正文候选在等待必要依赖期间保留；真正因新引导、未处理结果或通知发起下一次模型请求前，
才追加为 interim。网页通知队列中已经被请求快照覆盖的回执不使候选失效。
commentary 与 final_answer 分别追加 RAW；completed 元数据的 text 只返回最终交付，
final_item_ids 指明实际提交的消息，不能拼接此前所有阶段正文。
不设工具调用总数或模型请求轮数上限；网页没有长任务开关，也不按工具次数切换 Run、生成 synthetic 用户消息或转移邮箱。
单工具超时、连续失败、重复副作用防重、Provider 重试、上下文容量与取消仍按各自规则运行。
partial 记录已有正文与未完成项，存活执行继续保护会话；任务计划创建/批准边界仍会正常收束。
具体运行与客户端合同见 `knowledge/global/runtime/long-task-runtime.md`。

### 9. 处理运行中引导

网页运行期间，输入框主按钮为“消息跟进”，Enter 默认把文本和附件加入当前用户/会话的跟进队列，
不立即调用引导 API；当前 Run 可接收引导时，Ctrl+Enter 把当前文本和附件直接提交为本轮引导，
不先停留在跟进列表。Shift+Enter 仍保留换行，停止过渡期间不向旧 Run 投递引导；中文输入法组合
输入时快捷键不发送。输入框还提供“下一轮发送”快捷入口，同样加入该队列。当前 Run 收束后，
队列按展示顺序逐条作为后续用户轮次发送。
空闲时仍使用正常“发送”，不改变首轮交互。

跟进气泡全部显示在输入框上方，可通过拖动或上移/下移排序；文字与附件作为整体移动。只有气泡内
的“本轮引导”按钮会把选中消息提交给当前 Run。提交中和发送中的消息锁定，不允许移动、取消或
重复提交，也不能将其他消息移过它们。引导成功后移出跟进队列，沿既有引导状态显示与归档；
当前引导入口关闭或返回 404 时保留原消息及队列位置，等待下一轮，不重复发送。
失败条目保留内容并提供重试/取消，失败队首不会自动跳过。切换会话时队列及异步结果保持原会话归属。
该队列属于页面宿主的会话内存状态，不承诺浏览器刷新后的持久恢复。

Web 运行中引导进入独立 guidance 邮箱。它不会中断正在阻塞的 Provider 或工具，而是在下一次
Provider/工具安全边界取出。

引导可以包含文本、图片、音频、视频和普通文件。资产重新经过用户目录隔离、签名、大小和模型
能力校验。邮箱接收只表示待投递，不表示模型已收到或已执行；正常安全点注入和原有排序不变，
实际收到 Provider 事件后才记录 `guidance_applied` 和 `guidance_details` 的
`ok=true,status=applied`。Provider 内部暂态重试不提前关闭邮箱，已准备内容沿原重试链路恢复。

真正终止（完成、取消、额度耗尽或最终失败）时原子关闭并排空邮箱；已取出但未确认投递的内容
也必须结算。无法投递的引导写入终态及历史 `guidance_details`，
`ok=false,status=not_applied`，附 `reason` 和明确重发提示；不计入已生效数量，
不声称执行成功或后端已自动排入下一轮。关闭后新提交返回
`ok=false,status=not_applied,error.code=guidance_closed`；前端按失败路径保留原文/附件，
终态按引导 ID 匹配，刷新历史仍显示未生效。历史只保存 UI 安全附件描述符，不保留绝对路径或内联内容。
网页本地跟进队列与后端邮箱不同：既有“下一轮发送”仍由页面队列负责，不承诺后端持久排队。
引导邮箱在整个 Run 内保持同一个实例；正文通过历史卡片投影一次，原生媒体随首次成功响应消费。已结算未投递的内容不会被静默重发。

### Web 暂停与停止状态收口

网页的 `running`、`stopping`、当前 Run ID 和 `AbortController` 必须保存在同一份按
`(user, session_id)` 隔离的运行控制记录中，不能把 `stopping` 作为聊天页局部布尔值。点击停止或
取消时，客户端先以目标 Run ID 标记 `stopping=true`，再发送异步控制请求；取消响应晚于
SSE 终态时不得重新标记已经结束的 Run，也不得影响同一会话后来创建的新 Run。

快照终态、控制器释放、停止状态和运行记录清理都使用目标 Run ID 做 compare-and-set。旧 Run 的
迟到 `done`、请求失败回调或 `finally` 只允许收口自己；若当前记录已切换到新 Run ID，
这些回调必须无操作。终态清理直接删除匹配的运行控制记录，不先把 Run ID 清空再执行无作用域清理。
因此发送框在暂停或停止完成后稳定恢复为“发送”，切换会话时也只显示该会话自己的停止状态。

后端接受取消后，如果取消信号恰好使下一条非终态事件被抑制，或事件源在未给出终态时结束，Web
编排器必须补发 scoped `done`，其中 Run 为 `status=cancelled`、`cancelled=true`、
`committed=false`。前端仍保留断流兜底，但正常取消不应依赖
“缺少终态”错误来恢复按钮。

### 10. 提交终态

成功、限制、取消和失败都会生成明确终态。RAW 用户/助手/工具事件追加到 CardStream，
VIEW 与计量同步维护；整合状态、工具执行状态、Provider 用量和引导结算分别按各自事务提交。

工具产生外部副作用后，即使最终 Provider 失败，运行时也不能假装工具从未执行。

## 记忆与后台维护

记忆提取时机由 `memory.extraction_mode` 控制：

- `compression_only`：会话关闭或 CardStream 压缩成功后登记 RAW seq 范围，由 Maintenance 处理；
- `background`：除已登记范围外，Maintenance 也可领取 pending RAW；
- `on_commit`：兼容配置值，当前同样只开放 pending RAW 给 Maintenance，不在请求线程同步提取；
- `disabled`：关闭自动提取。

历史证据按连续 RAW seq 批次交给 `self_improve`，候选和超限待办全部成功写入
`memory.sqlite3` 后才推进 `memory_processed_seq`。记忆游标独立于 VIEW 压缩覆盖。
临时重要记忆是可重建热视图，不得反向参与三层临时记忆加权。

`self_improve` 使用一次 `memory_manage search_many` 匹配整批候选：完整短语优先，多关键词模糊
匹配必须达到最低命中数量和覆盖率，单个公共词不能触发复用。确认同一事实后复制已有文件名，
由 SQLite 每日加权事件约束执行权重更新；批量搜索每次只加载一次目标层，避免记忆增长后重复扫库。

`RuntimeHost` 管理：

- CronScheduler；
- MaintenanceScheduler；
- HistorySummaryScheduler；
- TaskPlanScheduler；
- 外部消息 MessageRouter；
- 感知和拓展刷新系统任务；
- `session_lifecycle_sweep` 闲置会话兜底任务。

Web 启动入口默认同时启动 RuntimeHost。独立调用核心引擎时，应明确决定是否需要后台宿主。

CLI 拥有独立 `cli:<user>` 续接绑定，进程内交互可接续；`/new` 或 `/use` 切离当前空间时先完成收尾，且 `/use` 不允许复活 closed 历史。进程退出即执行“记忆提取登记 → 逻辑会话关闭”；不再复用 Web 活跃空间。Cron 主智能体每次执行使用独立会话并在入口收束时执行同一流程。Web 页面与 App 设备/Socket 通过 90 秒持久租约表示存活链接，但有数据会话的自动归档阈值读取全局 `cron.session_idle_close_seconds`（默认 90 分钟），空 Web 会话仍按 90 秒宽限删除；Web 生命周期线程每 60 秒同时裁决 Web/App 过期链接并将有数据会话转入记忆队列。RuntimeHost 每小时以
`session_lifecycle_sweep` 扫描超过配置阈值的闲置会话。扫描尊重会话锁、运行态以及 Web/App lease，单轮最多
关闭 50 个且不删除历史；还会补偿“已经关闭但入队失败”的记忆任务。所有兜底动作均为 best-effort，不能覆盖原始请求或 Cron 状态。

## 并发与隔离模型

### Provider 总闸

`provider_runtime.max_concurrent_requests` 默认 10，使用进程级 BoundedSemaphore。每次真实 LLM
请求独立占槽；工具执行期间不占 Provider 槽，避免主智能体调用子代理时发生嵌套自锁。

等待超过 `provider_runtime.request_semaphore_timeout`（默认 300 秒）会产生明确拥塞错误。

### 会话级锁

同一用户、来源和会话 ID 串行提交。它保护历史窗口一致性，不把所有用户全局串行化。

### Web 用户闸门

每个用户默认最多 3 个并发 Chat，另有 5 个有界等待位置；等待超过 30 秒返回 503。等待发生
在线程中，不阻塞 FastAPI 事件循环和其他用户 API。

### 外部消息队列

MessageRouter 默认 8 个工作线程和 20 个排队位置。消息幂等状态存入用户历史数据库，路由健康
状态存入 `runtime/logs.sqlite3`。

### 子代理队列

每用户 `AgentScheduler` 默认队列长度 50。普通任务并行调度，可运行数沿用 Provider 池实际
上限；真实模型请求仍与主模型、其他用户共用同一个进程级 API 池，不另占整段子任务生命周期。
声明 `background_serial` 的维护代理保留用户级串行写入锁，且其排队不阻塞普通任务。
未真实退出的超时/取消执行器继续占调度额度。持久提交序号、列表及同批结果稳定排序、
`wait=false` 并行提交与显式同步合同见 `knowledge/global/runtime/agent-task-coordination.md`。

子代理默认整体超时 600 秒，超时后默认保留 120 秒收尾存活期。可恢复的本地 LLM
业务代理在宽限内尝试一次精简收尾，超时后仍记 `timed_out`，实际未退出为
`timed_out_running`；非恢复执行器保留 `completed_after_timeout` 兼容对账。默认只向主模型交付结论，
私有快照支持显式 resume 同任务并重新计时；不支持任意执行器恢复，详见 `knowledge/global/runtime/agent-task-coordination.md`。

### 模块子进程

感知和拓展等模块使用 `run/extensions/` 的 stdin/stdout JSON 协议在独立 Python 子进程中
执行。运行时限制路径、重定向、输出捕获、超时和取消，并使用模块锁避免同一模块并发写状态。
子进程使用安全基础环境和模块目录自己的 `.env`，不继承框架 Provider/Web 凭据；每次调用
单独构造环境映射，不修改主进程，也不会让并发模块互相覆盖配置。

这不是操作系统级安全沙箱。模块仍必须是可信本地代码。

## 数据存储分工

| 路径 | 权威内容 |
|------|----------|
| `users/<user>/history/cards.sqlite3` | 不可变 RAW、可变 VIEW、有效 Token、整合与工具执行、会话绑定/租约 |
| `users/<user>/history/` | 用户既有文件空间；旧历史不会被新引擎读取、迁移或自动删除 |
| `users/<user>/improve/memory.sqlite3` | 四档记忆、权重证据、幂等操作和热视图来源 |
| `users/<user>/task_plan/task_plans.sqlite3` | 计划、步骤和依赖 |
| `runtime/logs.sqlite3` | Cron 和外部消息路由结构化状态/日志 |

配置、模块清单、附件、Cron 定义和插件持久消息队列仍使用文件，不为了形式统一盲目入表。

深入阅读：

- `knowledge/global/runtime/storage-and-persistence.md`。

## Web 分层

`web/app.py` 创建 FastAPI 应用、安装认证/异常处理、注册领域路由，并保留聊天 SSE 和前端托管
边界。

```text
web/app.py
  → web/routes/       HTTP 路由与请求/响应边界
  → web/service.py    兼容服务门面
  → web/services/     按领域拆分的实现
  → web/schemas.py    API 数据合同
  → web/frontend/     React + Vite 前端
```

路由层不应重新实现历史、记忆或模块业务规则。核心数据仍由 `run/` 和对应 Store 管理。

### Web SSE 保活与断开收尾

`POST /api/chat` 在已经建立的 SSE 流中，业务事件静默达到
`web.sse_heartbeat_seconds`（默认 15 秒，有效范围 1～30 秒）时发送
`: kemo-heartbeat\n\n` 注释帧。正常业务事件到达后重新计算静默间隔；终态之后不再发送心跳、
也不额外拉取下一条事件。响应包含 `Cache-Control: no-cache`、`X-Accel-Buffering: no`、
`X-Kemo-Heartbeat-Seconds`。前端忽略无 `data:` 的注释帧，心跳不进入 RunEvent、序号、
Usage、计费或历史持久化。

Web 异步桥始终只持有一个 `next()` Task，`asyncio.wait` 超时后复用它；不得通过反复
`wait_for(to_thread(next))` 取消等待或创建并发读取。Task 被取消不代表它所使用的线程已经停止。
`web/streaming.py` 管理异步收尾任务，断开、请求取消、响应头或正文发送失败均有明确清理归属；
即使正文迭代尚未启动，也必须关闭已创建的消费者。
内置消费者的关闭使用独立于 `asyncio.to_thread(next)` 的 AnyIO 线程池，避免读取占满默认
执行器后关闭也被排在队尾。只有显式关闭内置消费者后，才有界等待读取包装任务；未能及时完成则
取消其等待/尚未执行的排队读取，不能反过来用取消 Task 代替关闭消费者。普通自定义生成器仍须
等待真实读取返回后再 close。

`web/services/event_stream.py` 的显式队列消费者可从其他线程幂等关闭，关闭时设置消费者
关闭信号和 Run 取消信号、关闭引导邮箱、释放一次 Web 并发槽；队列读取每 0.1 秒检查关闭信号，
不依赖生产者先产出事件或 `_WORKER_DONE`。消费者不 join/跨线程 close 实际 Run 生成器，
实际 Run 的 `next/close` 仍只在其专用工作线程上执行。关闭不等待卡住的 Provider/工具；
ActiveRun 保留至 worker 真正退出，因此仍可按用户、来源、会话查询和取消，不伪造已结束状态。
单纯的用户取消不等于关闭消费者，仍需把正常的取消终态送到客户端。
任务计划使用显式最终化包装器，未调用第一次 `next()` 就关闭也会释放底层消费者并把仍处于
`running` 的计划收口为 `paused`，不覆盖已经完成的计划。

认证守卫使用原生 ASGI 中间件，SessionMiddleware 从外层提供会话；未启用认证时不安装守卫。
不能用 `BaseHTTPMiddleware` 再包装 SSE 的 receive/send 链，否则当前依赖组合下的
`request.is_disconnected()` 可能无法观察真实断开，排队和静默请求也不能及时收尾。

**边界**：获准进入 SSE 之前仍按原有规则返回验证错误或 HTTP 503；排队时探测断开并撤销等待，
但不提前发送 200/心跳。反代的首次响应等待必须大于 `web.pending_chat_timeout` 并留出准备余量；
入流后空闲读取超时必须大于心跳间隔，反代也必须关闭流式缓冲。心跳不能绕过平台的硬性总时长，
不能修复 Provider 本身卡死，也不提供刷新后的重连/事件重放。嵌入式自定义后端若返回普通生成器，
只能在其在途 `next()` 真正返回后安全 close；该后端必须自行响应取消信号，不能保证强制中断
任意第三方阻塞调用。生产内置队列消费者不受这一等待限制。

验证应观测 ASGI send 时序或真实 HTTP 流，而不是仅检查缓冲后的响应正文；覆盖单个在途 next、
终态、完整事件载荷、取消终态、断开/发送失败、重复连接清理、排队撤销、未消费即关闭和前端注释解析。
需要缩短反代超时进行反向验证时使用隔离测试代理，不降低现网超时。

### 子代理执行事件与工作面板

Web/App 前台调用 `subagent_dispatch(action=call)` 时，通过调用级回调实时转发
`subagent_progress` 事件；进度不等待工具返回，也不作为主智能体思考正文或历史消息保存。
事件携带当前工具调用 ID、子代理/任务标识，以及排队、准备、模型请求轮次、工具执行、结果校验、
重试和终态。传输层仅允许阶段、名称和计数字段，禁止透传子代理 Prompt、推理正文、参数、结果或错误正文。
用户、来源、会话和当前 Run 由服务端绑定，上一 Run 的迟到进度不进入新 Run。

旧工具卡旁进度气泡已停止渲染，聊天只保留简洁分派卡与“查看执行”入口。
业务子代理在入队前登记持久父子归属，必要依赖与未处理结果阻止主任务提前完成；
后台结果通知在同一个主执行器内自动续接。右侧工作面板从持久账本恢复总览和详情，
不依赖临时 SSE 气泡。完整完成屏障、取消收尾、跨尝试身份及恢复限制见
`knowledge/global/runtime/agent-task-coordination.md`。
外部桥接和自定义执行器没有细粒度事件时只展示真实的排队/运行状态，不推测百分比或内部步骤。

### 模块注入预览的片段来源

拓展页与感知页展示的「真实注入片段」不由预览侧自行拼装。片段在最终注入文本中的位置由权威侧
`run/config/prompt_sources.py` 计算，随 Selection 以 `InjectedPiece`（标识、起点、终点）返回，
预览侧只按标识取值：`selection.fragment(key)`，拓展用 `scope:module`，感知用模块名。

原因是真实片段格式曾经存在两份实现：权威侧后来增加了框架头部行（数据来源、操作说明），
预览侧没有同步，且预览侧用累加游标在真实文本里切片，导致每块短一截、游标逐块落后，
第二块起渲染出前一块的尾部。片段格式现在只有一份实现，遍历顺序也不再敏感。

未注入、未在白名单或无效的模块自然得到空串；`max_chars` 截断时最后一块的终点会被裁剪到文本长度。
本规则只影响网页预览，真实进入模型的 Prompt 始终由权威侧文本决定。

### Markdown 渲染维护边界

网页中的聊天正文、知识、技能、记忆、智能体规则、感知、拓展和运行状态预览都使用
ReactMarkdown。所有入口必须从 `web/frontend/src/markdownLinks.ts` 导入兼容版 `remarkGfm`；
除该共享模块外，业务组件不能直接导入第三方 `remark-gfm`。共享模块保留 GFM 行为，同时修正
以 `http://`、`https://` 或 `www.` 开头的裸链接紧邻中文标点或界定符时把后续正文吞进 URL 的问题。显式
`[标签](地址)`、`<地址>`、图片、行内代码和代码块不会被改写。

聊天正文的危险协议过滤、外链安全属性、外部图片策略和链接样式继续由
`web/frontend/src/components/Chat/MarkdownMessage.tsx` 及其 CSS 模块负责。长链接必须换行；
鼠标点击可以隐藏浏览器默认黑框，但键盘 `focus-visible` 必须保留清晰的主题焦点。修改共享
Markdown 规则后，至少运行前端定向测试、完整 `npm test` 和 `npm run build`，并验证中文边界、
连续多个裸链接、显式链接、危险协议以及流式/完成态一致性。

Web 智能体正文还支持独立闭合的 `kemo-widget` fenced JSON。它与普通 Markdown 按源顺序分段渲染，
不经过工具调用，也不新增 Provider/SSE 事件；声明作为 assistant 文本进入既有历史。扫描器只识别普通
代码 fence 之外的组件块，未闭合时显示生成占位，失败尝试收口后显示未完成状态。闭合块先经版本化
Zod Schema 校验；内置类型映射为专用 React 组件，未知声明式名称映射为通用数据卡，不再因名称目录
拒绝整块。展示组件只维护当前消息内的本地状态；动作组件经 `MarkdownMessage.onWidgetAction` 限定为
用户点击后的回填输入框、发送纯文本或复制文本，生成中不会并发启动新 Run。声明仍不能携带可执行
HTML、JS、CSS、事件处理器或外部 URL；图片组件只接受站内根相对 URL。复制回复时组件替换为文本
fallback。组件目录、字段上限和历史/重试边界见
`knowledge/global/web/inline-widgets.md`。

## 文件空间排序

文件空间的用户上传、智能体产物和全局临时区域共用后端目录排序。列表 API
`GET /api/users/{user}/files/{scope}` 与 `GET /api/tmp` 接受 `sort_by=name|updated_at|size`
及 `sort_order=asc|desc`，省略时保持名称升序；非法参数由接口与服务层拒绝。
日期指文件系统修改时间，不是创建时间。网页选择日期时默认最新在前，大小默认从大到小，
名称默认升序，并可切换方向；排序条件纳入查询缓存键，切换时回到第一页并清除选中项。

后端先对当前目录候选项或当前区域的递归搜索匹配项排序，再分页（默认每页六项）。
文件夹始终置顶，文件夹与文件分别按所选字段排序；数值相同以名称及相对路径稳定排列。
名称按不区分大小写的文本顺序比较，不做拼音或自然数字排序。

目录普通浏览保持原有展示口径：大小为直接子文件合计，日期为目录本身及直接子项的最新修改时间，
不是全目录递归体积。日期/大小排序在分页前为全部候选目录补齐这些元数据，名称排序仍只补齐当前页目录，
不为排序新增深层递归扫描。递归搜索沿用原有扫描预算和递归汇总口径；命中扫描上限时，排序仅覆盖已扫描的匹配项。
隐藏文件、符号链接、目录联接和用户路径权限的现有过滤不变。排序选择仅保留于当前页面运行状态，不写用户配置。

## 同用户独立浏览器标签页

左下角用户菜单的“新建此用户标签页”使用普通 `target=_blank` 链接，并设置 `noopener noreferrer`。
目标为 `/chat?user=<当前用户>&new_session=1`，不复制当前 session、输入草稿、运行控制器或跟进队列；
当前页不会导航，也不会取消正在运行的任务。即使当前运行禁止切换用户，该入口仍可使用。

只有新页实际加载后才调用现有 `POST /api/users/{user}/sessions` 创建独立会话，以该页面自己的
`client_id` 绑定；不会调用 active-session 恢复用户旧会话。创建期间不展示可发送的聊天编辑器，失败
提供显式重试，不自动退回旧会话。成功后替换 URL，移除 `new_session` 并加入新 session，避免刷新
再次新建。新页仍受现有 Web 登录保护，不通过 URL 传递认证信息，也不绕过每用户运行并发限制。

页面每 15 秒发送会话租约心跳，恢复显示或重新联网时补发，pagehide 与组件卸载时尽力释放页面关联；App active/chat 与会话 WebSocket 使用同一租约语义，Socket 每 30 秒续租并在断开时释放。
空会话离线清理后旧页恢复收到 404，会解除绑定并解释原因，不把迟到心跳当作重建会话请求。
被清理的旧 `session` URL 不会自动创建替代对话；只有用户显式进入 `new_session=1` 链接、执行正常
新建/首次发送操作或调用会话创建 API，才创建新的随机空间。Web 启动 90 秒后执行首轮旧空间巡检，之后每 60 秒继续裁决：离线且有内容的空间关闭活跃绑定
并进入记忆队列，完全空的空间清理；模型执行仍由原维护队列负责。在线租约、启动巡检、刷新宽限、
队列跳过条件、后台清理和数据保护的权威规则见 `knowledge/global/runtime/storage-and-persistence.md` 的
“Web 对话空间启动巡检与空空间生命周期”。

## 更新与重启

根目录 `update.py` 只保留兼容启动，实际更新逻辑位于 `update/` 包，并按 core、agents、plugins、
web 四个板块执行。更新器在写入前取得单实例锁、校验远程清单与克隆源码一致，失败时停止后续
板块并恢复版本管理的源码路径。用户数据、Cron 运行状态、本地配置和模块派生存储使用专门保护
规则。`update.cli` 是唯一编排入口；版本、远程源码、调度、备份、锁、事务、构建和迁移分别由
独立模块负责。全量更新拒绝任一组件降级，所有更新日志和配置差异在显示前脱敏。详细边界见
`knowledge/global/release/version-and-update-modules.md`。

Web 重启由受保护接口启动 `restart.py` 辅助进程：等待旧进程退出后，以兼容启动参数重新拉起
`start_web.py`。`kemo.yaml` 在新进程启动时重新加载。

更新和重启不是同一操作。更新负责同步代码和依赖，重启负责让新进程加载磁盘状态。

## 开发任务导航

| 目标 | 首选文档 |
|------|----------|
| 开发 Provider 工具 | `knowledge/global/modules/plugin-development.md` |
| 创建子代理、技能、感知、拓展或外部代理 | `knowledge/global/modules/module-development.md` |
| 接入外部消息 | `knowledge/global/modules/external-message-route-creation.md` |
| 修改存储、写盘或运行日志 | `knowledge/global/runtime/storage-and-persistence.md` |
| 修改配置 | `knowledge/global/engineering/configuration-reference.md` |
| 修改 Web 页面或 Markdown 渲染 | 本文“Web 分层” |

修改任何核心链路后，应先运行相关定向测试，再运行完整 `python -m pytest -q`。创建或实质修改
子代理、拓展、消息、感知、技能或用户包时，还必须运行 `tests/template_tests/<kind>/` 对应
合同验收。

## 模块用户配置的平行链路

拓展与感知模块可在 `module/panel.json` 声明 Web 用户配置面板。后端只读取声明、状态 JSON 和配置
JSON，前端只渲染受限原语；保存使用原子写，然后由页面复用现有模块刷新接口。该链路与 Prompt
拼接严格平行：面板值、状态和动作结果不会自动注入 `expand_data` / `perception`，只有模块自行更新
既有 `input_data.md` / `sense.md` 后，才可能按原注入规则进入后续对话。

## 上帝模块边界与统一入口

- 生产实现模块以 **800 行**作为架构预警线：同时承担存储、校验、调度、协议、渲染等多个变化原因时，必须按领域职责拆分。
- 拆分后的内部文件按高内聚职责命名；调用方继续从原模块或包入口导入，原入口只负责编排、依赖注入与兼容转发。
- 禁止通过把完整实现整体搬到另一个超长文件来规避限制，也禁止内部模块形成循环依赖。
- 纯类型声明、单一协议校验器、HTTP 客户端资源门面以及已经把渲染/工作流下沉的页面组合根，可经带理由的最小 allowlist 保留；allowlist 由 `tests/core/contracts/test_module_size_boundaries.py` 审计。
- 当前会话、Provider、历史、记忆、任务、Cron、拓展运行时、CLI、文件/Shell 插件与内置拓展均保持原公共入口，内部实现按准备、执行、持久化、错误映射或 UI 状态域拆分。

## 工具回执、身份与清理故障（2026-10-25）

- 显式 `ok=false` 工具结果按失败计数，不写成功复用缓存，也不套一个外层成功状态。
  主整合工具结果完整保存到 CardStream RAW；子代理工具副作用已经发生后，超出 100000 字符内联预算的完整结果保存到用户 artifact，返回带
  execution_completed、路径、字节数与 SHA-256 的有界回执；若 artifact 保存失败，明确先核对结果，
  不能指示重试已经执行的操作。
- 会话锁需要完整 user/source/session_id；stream_request 由专用单线程 executor 驱动
  next/close，保证 RLock 线程归属。取消时先完成同线程生成器清理。
- terminal commit / run registration 清理失败写诊断日志与独立 run_failures 标记，不再 except pass；
  标记不包含原提示词或敏感异常正文。存活查询无权限/缺 psutil/身份不完整是 unknown，不是死亡证明。
- Web 当前是实例管理员认证，不是每个 users/<name> 各有独立登录。WEB_USERNAME 是管理员凭据名，
  不能直接映射目录名或伪造多租户 ACL。非本机监听要求配置认证；直接 ASGI 入口也拒绝
  非本机无认证 API（健康/认证公开入口除外），localhost 无认证体验保留。
