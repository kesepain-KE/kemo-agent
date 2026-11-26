# history_search

查询当前用户的历史归档：`dates` 查看归档日期目录，`list` 列出某天或全部归档，`read` 分页读取归档正文或预览锚点上下文，`search` 搜索用户正文、引导正文与智能体正文。搜索、普通分页和默认锚点预览只读正文；只有锚点预览可显式加入工具卡，单独投影工具参数、结果预览和状态。

## 归档目录与正文读取

- **先目录，后正文**：不知道日期时调用 `{"action":"dates"}`；知道日期时调用 `{"action":"list","date":"2026-10-24","limit":20}`；再将返回的 `archive_id` 原样传给 `{"action":"read","archive_id":"...","limit":20}`。list 不要求关键词，不读取正文；read 返回真实已保存文本，不是摘要。
- **从搜索命中直接预览**：拿到 `search` 返回的 `card_id` 后，调用 `{"action":"read","anchor_card_id":"...","before":3,"after":5}`，无需先执行 `list`。两侧独立按正文条数计，各默认 5、范围 0–20，跳过工具卡；`limit` 和 `role` 不改变该窗口。`cursor` 与锚点互斥；如一并传 `archive_id`，它必须与锚点属于同一归档代际。预览先保留锚点正文，预算不够时可截断正文或裁去邻居，不提供无损续读游标；要完整读归档，用 `archive.archive_id` 发起一次普通分页 `read`。
- **按需查看工具卡**：在锚点 `read` 加 `"include_tools":true`。先按正文确定两端 `seq`，再插入区间内的工具卡；首条正文前、末条正文后的工具卡不在窗口内。页预算先给锚点，再给按 `seq` 排列的工具卡，最后给邻居正文。预算不足时会标记 `window_clipped`；可降低 `max_tool_chars` 来容纳更多整条工具卡预览。
- **不截断探头**：`max_tool_chars` 默认 2000，正数范围 200–20000，分别限制每张工具卡的参数或结果预览字符数。设为 `0` 会尝试取完整的预览字段；序列化结果超过十万字符，或完整工具卡预览放不进 `page_char_limit` 时明确报错，不生成 artifact。可缩小 `before/after` 或改用正数 `max_tool_chars`。两个工具参数都不用于普通分页。
- **外部消息**：工具卡预览是否开放由消息模块自身决定，框架不按来源做默认限制。消息模块要关闭时，应在执行前拒绝 `include_tools=true` 或强制设为 `false`，不必禁用正文检索或锚点读取。
- **日期口径**：dates/list 按物理归档的最后更新时间（`updated_at`）换算 `Asia/Shanghai` 自然日，缺失时回退创建时间、旧窗口名日期；返回 `date_basis`、`timezone` 及原始创建/更新时间。无法确定日期的归档在日期目录中为 `date=null`，可在未限定日期的 list 中找到。跨天会话只归属最后更新日，不代表当天逐条发言活动。**旧 search 的 since/until 仍按创建日期，不改变既有语义**。
- **目录分页**：dates/list 都支持 source/session_id 精确过滤、since/until 日期区间，以及 limit/offset/page_char_limit；用 next_offset 续页。目录 total 是筛选后日期组数或物理归档数，不是当前页条数。列表按最后更新时间倒序，日期按天倒序；无日期项在最后。分页期间归档增删可能影响 offset，应重新查询目录。
- **区分物理归档**：分页使用 `list` 或锚点预览返回的 `archive_id`，不能用 `session_id` 代替。ID 只在当前用户数据库内解析，不是文件路径。`summary` 是已保存的会话摘要，可能为空，不触发生成摘要；正文以 `read` 为准。
- **游标无损分页**：`limit` 表示本页消息片段数，超长正文会按 `page_char_limit` 分片。`has_more=true` 时将 `next_cursor` 原样带回相同的 `archive_id` 和 `role`，不能以页面不满 `limit` 推断已结束。相同 `seq` 的 `content` 按 `byte_offset`（UTF-8 字节）顺序拼接，可无损恢复正文。不要把消息行的存储时间冒充发言时间。
- **游标与权限**：游标绑定当前用户、物理归档代际、角色及首读时的 `through_seq`；后续追加不会进入这条续读链。删除后旧 ID 不可读。四个 action 同受插件白名单和 `memory.history_read_enabled` 控制。只读访问不会创建空历史库、修改更新时间、重开会话、触发记忆或摘要。
- **参数分工**：`query`、匹配方式与 `context_messages` 仅用于 `search`；`date` 仅用于 `list`；`archive_id`、`cursor`、`anchor_card_id`、`before`、`after` 仅用于 `read`，`read` 不接受 `since/until/source/session_id/offset`。

## 使用原则

1. **与 memory 搜索的区别**：`history_search` 搜索原始对话文本，即用户和助手曾经说过什么；`memory_manage` 的 `search_by_title`、`search_by_content` 搜索提炼后的长期记忆、临时记忆与规则。想找“上次讨论树莓派时说了什么”使用 `history_search`，想找“树莓派的 IP 是多少”优先搜索记忆。
2. **时间过滤优先**：要看某天有哪些归档时使用 `action=list,date=...`；要找某个时间范围使用 `dates/list` 的 `since/until`（上海自然日、按归档更新时间）。仅当执行旧关键词 `search` 时，`since/until` 继续按创建日期筛选。正文来自结构化消息表，不扫描历史目录。
3. **角色过滤**：用户问“我上次说过什么”时使用 `role=user`，包含初始用户正文和引导正文；问“你之前怎么回答的”时使用 `role=assistant`，包含智能体阶段正文和最终正文。默认 any 也只包含这两类正文，不搜索工具信息。
4. **上下文按需获取**：需要还原对话脉络时传入 `context_messages`，一般取 2–3；前后各 N 条按正文消息计数，跳过中间工具卡片，不因工具调用较多而丢掉相邻发言。只确认是否提到某个词时保持默认值 0。
5. **搜索精度**：搜索 `AI` 等缩写且不希望命中 `main`、`email` 时，使用 `match_mode=word`。只有用户明确需要模式匹配时才启用 `regex=true`。
6. **优先限定会话范围**：已知入口或逻辑会话时，在 search 或 list 中传入 `source`、`session_id` 精确过滤。list 仍会按不同物理归档分别返回，不会把同 session_id 的多个窗口混成一个 archive。
7. **控制返回规模并分页**：search、dates/list 使用 `next_offset`；普通 read 使用 `next_cursor`，以 `has_more=false` 为读完。search 可缩小 `max_snippet` 与 `context_messages`；锚点预览可按需缩小 `before`、`after` 或 `page_char_limit`。

## 中文全文检索

查找相关历史正文时优先显式使用 `match_mode="fulltext"`，例如
`{"query":"历史 压缩","match_mode":"fulltext","role":"user","limit":10}`。
使用 SQLite FTS5 + simple 的 `jieba_query` 分词，查询词之间为 AND，按 BM25 相关性排序，
同分按时间与卡片 ID 倒序。建议输入 2～4 个核心词，不是自然语言语义检索。
本版关闭拼音扩展，避免英文关键词误命中中文；全文检索不区分大小写。
返回 `engine="sqlite-fts5/simple"` 和每条 `rank`（越小越相关）；角色、来源、会话、日期、正文上下文和分页预算继续生效。
全文查询最多 1000 字符，不能包含 NUL；用户文本不会作为 SQL 或原始 FTS 表达式执行。
旧默认仍是 `substring`，`word`、`exact`、`regex=true` 的行为不变；正则优先于 fulltext。
全文索引只保存正文索引，不收录工具、元数据或思考。扩展随安装包离线分发，缺失或不兼容会明确报错，不静默退回扫描。

## 返回字段

| 字段 | 说明 |
|------|------|
| `query` | 原始搜索词 |
| `engine` / `rank` | fulltext 的检索引擎与 BM25 排名；rank 越小越相关，其他匹配模式不返回 |
| `matches` | 匹配结果数组 |
| `total_matches` | 实际命中总数，包含被 limit 截断的结果 |
| `truncated` | 兼容字段，与 `has_more` 同义；受 limit、offset 与字符预算共同影响 |
| `offset` / `next_offset` / `has_more` | 当前页偏移、下一页偏移与是否还有后续命中 |
| `page_char_limit` / `page_limited_by_chars` | 单页字符预算及是否因预算提前结束 |
| `filters` | 实际使用的 source 与 session_id 精确过滤条件 |
| `time_range` | 实际使用的 since 与 until |
| `window` | 匹配所在的历史窗口目录名 |
| `source` | 对话来源，如 web、cli、cron |
| `session_id` | 会话 ID |
| `role` | 匹配消息角色 |
| `snippet` | 围绕首次命中位置生成的有界片段 |
| `match_index` | 匹配消息在归档 text 逻辑分区中的索引 |
| `context` | 命中正文及其前后各 N 条正文，保持原始 seq 顺序；仅在 `context_messages > 0` 时返回，不含工具卡片 |
| `context_index` | 匹配消息在 context 数组中的位置，仅在 `context_messages > 0` 时返回 |
| `dates` | dates 的日期与对应已提交物理归档数；含无法推定日期的 null 组 |
| `archives` | list 的物理归档元数据与 archive_id；含 title/summary/source/session_id/创建更新时间/轮数/date |
| `total` / `offset` / `next_offset` / `has_more` | dates/list 的筛选后总日期/归档数及分页状态 |
| `date_basis` / `timezone` | dates/list 的日期计算口径标识及 `Asia/Shanghai` |
| `archive` | read 当前物理归档的元数据 |
| `messages` | read 的正文片段；只有 `include_tools=true` 的锚点预览会额外插入 `kind=tool_call/tool_result` 的条目 |
| `arguments_preview` / `result_preview` | 工具参数的 JSON 字符串 / 工具结果的文本预览；可能不构成完整 JSON，用对应的 `*_truncated` 判断是否因单卡上限而截断 |
| `ok` / `execution_status` / `execution_live` | 工具结果的原始布尔回执、状态与是否还在运行；`ok` 缺失或非布尔时为 `null`，`execution_live=true` 时不可用 `ok` 判断已完成 |
| `raw_card_bytes` | 整张工具结果 RAW 卡的字节数，不是结果字段或预览的字节数 |
| `byte_offset` / `partial` | 游标分页片段的 UTF-8 字节起点 / 正文是否还有剩余；锚点预览中的 `partial` 表示片段被截短 |
| `next_cursor` / `has_more` | 普通 read 的连续分页标识；同一 archive_id/role 续页直至 has_more=false |
| `mode` / `anchor` | 锚点预览为 `mode=anchor`；`anchor` 含 `card_id`、`seq` 和在 `messages` 中从 0 开始的 `index` |
| `window_clipped` / `page_limited_by_chars` | 锚点预览是否因页预算丢掉窗口内卡片 / 是否因页预算丢掉卡片或截短正文；单卡上限和自然到达会话首尾不算页裁剪 |
