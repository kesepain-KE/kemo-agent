# memory_manage

记忆管理插件。按当前用户隔离，支持列出、获取、单项/批量搜索、删除、编辑和新增记忆。记忆先按用户能理解的主题组织，逐步积累上下文；不要把每句话拆成一个文件。支持三层临时记忆、永久记忆与临时重要记忆热画像。

## 使用原则

1. **层级选择**：
   - `seven_days`：7 天主题记忆，有权重和到期时间。
   - `one_month`：30 天主题记忆，有权重和到期时间。
   - `half_year`：180 天主题记忆，有权重和到期时间。
   - `permanent`：永久主题记忆，不会过期，权重为 null。
   - `all`：`list` 和三个搜索 action 统一读取上述四个碎片层，顺序为七天、月、半年、永久；不包含热画像 `important`。
   - `important`：单文件临时重要记忆热画像，由 `memory_temporary_important` 子代理自动维护，权重和到期时间为 null。**此文件不可删除，不可写入空内容。** 主智能体只允许 `get` 和 `search_by_title`/`search_by_content` 读取此层级，不得使用 `add`、`edit` 或 `delete` 操作。
2. **权限范围**：主智能体可以使用全部 action，但 `important` 层级只允许读取（`get`、`search_by_title`、`search_by_content`、`search_many`），禁止写入或删除；`self_improve` 子代理只能使用三个搜索 action，且后台 `context_compression` / `memory_promotion` 模式禁止读取 `important`，只有用户主动的 `manual_review` 保留只读白名单；`memory_temporary_important` 只能使用 `list/get`。候选、热视图、遗忘、永久协调和晋升均由运行时原子持久化。所有查看和搜索都不改变权重。
3. **搜索、列出与获取**：列出整层摘要使用 `list`，`tier="all"` 直接合并四个碎片层；跨层列表每项携带实际 `tier` 和 `memory_ref`，`total` 为四层总数，分页游标作用于合并后的列表。大层级使用 `offset`、`next_offset`、`has_more` 逐页读取。后台热画像全量巡检应使用 `limit=500, compact=true, include_content=true, page_char_limit=80000`，在分页结果中直接取得正文；工具会按“最多 500 条且序列化条目不超过 80,000 字符”双重边界提前截页，并返回准确的 `next_offset`，禁止再对全部条目逐条 `get`。按文件名搜索使用 `search_by_title`；按正文搜索使用 `search_by_content`，只返回 snippet；三个搜索 action 均可传 `tier: "all"` 跨 `seven_days`、`one_month`、`half_year`、`permanent` 四个碎片层查询。默认 `match_mode="keywords"` 先判断规范化完整短语，再按英文词与中文关键词/bigram 的覆盖率评分；多词模糊查询至少需要两个有效片段且覆盖率不低于 50%，避免单个公共词造成误命中。结果使用 `match_score`、`matched_terms`、`matched_by`、`tier` 与 `memory_ref` 解释和定位。多个候选应优先使用一次 `search_many`；它会对四层记忆只加载一次并在内存中匹配整批查询，适合数百至数千碎片。查询建议提供 2～4 个空格分隔的核心词，需要对命中项做完整融合时可传 `include_content=true`；`self_improve` 调用 `search_many` 时运行时强制开启完整正文（即使参数省略或为 false），用于引用与正文快照校验。也可选择与 `history_search` 共用的 `substring`（子串）、`word`（整词）、`exact`（去除正文首尾空白后整段匹配）模式；`regex=true` 使用同一正则机制并优先于匹配模式，`case_sensitive` 控制大小写。默认返回有界摘要，完整正文仍须显式获取。单项搜索仍可用于发现，但修改旧碎片前须批量取得完整回执；该子代理仍无 `get` 或写入权限。其他调用方获取单条完整正文使用 `get`。
4. **禁止空搜索**：两个搜索 action 的 query 都必须是非空字符串。列出全部记忆不能再依赖空 query，应使用 `list`；少量精确读取可用 `get`，后台全量读取必须使用分页 `list(include_content=true)`。
5. **敏感凭据检测**：`add` 与 `edit` 会拒绝包含疑似密码、API Key、Token、Cookie 或私钥的内容。
6. **控制结果规模**：`list` 与搜索默认最多返回 50 条。`list.has_more=true` 时使用返回的 `next_offset` 读取下一页；`truncated` 为兼容字段，与 `has_more` 同义。`compact=true` 时列表省略时间元数据；`include_content=true` 时额外附带完整正文。`page_char_limit` 会在条目数量上限之前按序列化字符预算截页，避免触发框架 100,000 字符硬限制；`page_limited_by_chars=true` 表示本页由字符预算截断，但仍应正常沿 `next_offset` 继续。
7. **精确寻址**：`get`、`edit`、`delete` 使用 `tier + filename` 寻址 SQLite 表行；逻辑文件名在全部层级全局唯一，跨层同名会由数据库拒绝。删除成功返回 `row_removed=true`，不再存在文件孤儿修复语义。
8. **稳定引用**：`list`、搜索及所有单条 CRUD 结果均返回 `memory_ref`，格式为 `tier:filename`；展示标题仍使用 `filename`，程序传递目标时优先保留 `memory_ref`。
9. **按主题组织**：`add` / `edit` 前先检索同主题。A 类可承载连贯画像或持续主题，硬上限 1000 字；B 类用于确需单独取用的约定、参数或事件，硬上限 150 字。超过 150 字需显式传 `memory_type: "A"`；这只是存储上限，不是拆碎画像的理由。`important` 是运行时维护的只读热画像，主智能体不能通过本工具直接新增、编辑或删除。

全局 `memory.prompt_injection=false` 只关闭自动记忆注入，本工具仍按原授权开放；需要背景时先检索相关记忆，再按需获取正文。

## 中文全文检索

查找相关记忆时可优先显式使用 `match_mode="fulltext"`，三个搜索 action 均支持，例如
`{"action":"search_by_content","tier":"all","query":"树莓派 检索","match_mode":"fulltext"}`。
四个碎片层共用每用户独立的 SQLite FTS5 + simple 索引，使用 `jieba_query` 分词、BM25 排序；
同一字段的查询词为 AND，批量查询的 title 与 content 两个字段为 OR。标题权重 1.15，正文权重 1，
同分优先原有记忆权重较高者，再按文件名排序。search_many 使用一个数据库读取快照，不全量载入四层正文。
全文模式不区分大小写且关闭拼音扩展，不接受 `case_sensitive=true`；每个字段最多 1000 字符，不含 NUL。
`tier=important` 是独立热画像文件，不属于全文索引，使用原有 keywords/文本模式查询。
返回 `engine="sqlite-fts5/simple"`、`rank`（越小越相关）、`match_score=-rank`、实际命中词与字段；
全文分数不能与 keywords 覆盖率阈值混用，因此不返回 `match_coverage` 或 `exact_match`。
后台整理需要覆盖率与同一事实判定时继续使用默认 keywords，不以全文排名代替记忆融合证据。
旧默认 keywords、substring/word/exact 与正则模式保持不变；`regex=true` 优先。
索引随新增、改名、正文编辑和删除在同一事务更新，检索不加权、不改变记忆注入开关。

## 返回字段

| 字段 | 适用 action | 说明 |
|------|-------------|------|
| `entries` | list | 记忆条目数组；默认不含正文，`include_content=true` 时每项附带 `content` |
| `memory_ref` | list / get / search_* / add / edit / delete | 稳定组合引用，格式为 `tier:filename` |
| `total` | list | 当前层级总条目数；tier=all 时为四个碎片层合计 |
| `tier` | list(all) / search_*(all) 的条目 | 条目实际所属层级，与 memory_ref 的层级一致 |
| `offset` / `next_offset` / `has_more` | list | 当前页起点、下一页起点和是否仍有后续页面 |
| `page_char_limit` / `page_limited_by_chars` | list | 本页字符预算，以及本页是否因该预算提前截断 |
| `content` | get / list(include_content) / search_many(include_content) | 单条或批量返回的完整记忆正文 |
| `featured_sources` | get important | 当前有效热画像来源的 `{tier, filename}` 数组 |
| `filename` | get / add / edit / delete | 记忆文件名 |
| `snippet` | search_by_content | 首次命中位置附近的有界片段 |
| `matches` | search_* | 匹配结果数组；跨层搜索时每项额外包含实际 `tier` |
| `engine` / `rank` | fulltext | SQLite FTS5/simple 与 BM25 排名；match_score 为 -rank，不代表关键词覆盖率 |
| `match_score` / `match_coverage` | search_* | 排序分数与查询关键词覆盖率；分数越高越优先，模糊命中仍需结合正文核对 |
| `matched_by` / `matched_terms` | search_* | 命中的字段（title/content）与实际命中的关键词或中文 bigram |
| `exact_match` / `field_scores` | search_* | 是否按所选模式完整命中（keywords 为完整短语）；批量搜索同时返回各字段独立分数 |
| `weight` | list / get / search_* | 临时层权重；permanent 与 important 为 null |
| `expires_at` | list / get / search_* | 临时层到期时间；permanent 与 important 为 null |
| `total_matches` | search_* | 实际命中总数，包含被 limit 截断的结果 |
| `truncated` | list / search_* | 返回结果是否被 limit 截断 |

## Tool

```json
{
  "name": "memory_manage",
  "description": "按当前用户和层级列出、获取、搜索、增删改记忆。相关记忆可显式选 match_mode=fulltext，使用 simple 中文分词和 BM25；默认 keywords 保留覆盖率评分，也支持子串、整词、精确和正则。search_many 批量检索四层，关闭自动注入后仍可按需使用。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "enum": ["list", "get", "search_by_title", "search_by_content", "search_many", "add", "edit", "delete"],
        "description": "记忆操作"
      },
      "tier": {
        "type": "string",
        "enum": ["seven_days", "one_month", "half_year", "permanent", "important", "all"],
        "description": "记忆层级；all 用于 list 和搜索，统一读取 seven_days、one_month、half_year、permanent 四层，不含 important"
      },
      "query": {"type": "string", "minLength": 1, "description": "search_* 使用的非空关键词；多词查询建议传 2～4 个空格分隔的核心词，列出全部请使用 list"},
      "queries": {
        "type": "array",
        "minItems": 1,
        "maxItems": 20,
        "items": {
          "type": "object",
          "properties": {
            "title": {"type": "string"},
            "content": {"type": "string"}
          },
          "additionalProperties": false
        },
        "description": "search_many 的批量标题和正文查询；keywords 模式建议传核心词，其他模式按完整查询匹配"
      },
      "filename": {"type": "string", "description": "get/add/edit/delete 使用的记忆文件名"},
      "content": {"type": "string", "description": "add/edit 使用的 Markdown 正文"},
      "new_filename": {"type": "string", "description": "edit 使用的重命名目标"},
      "memory_type": {
        "type": "string",
        "enum": ["A", "B"],
        "description": "add/edit 的主题记忆类型；A 为连贯画像或主题，超过 150 字需显式声明 A，硬上限 1000 字"
      },
      "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50, "description": "list/search 最大返回条数"},
      "offset": {
        "type": "integer",
        "minimum": 0,
        "maximum": 1000000,
        "default": 0,
        "description": "list 分页起点；使用上一页 next_offset 继续"
      },
      "compact": {"type": "boolean", "default": false, "description": "list 是否省略时间与到期元数据"},
      "include_content": {"type": "boolean", "default": false, "description": "list/search_many 是否为返回条目附带完整正文"},
      "page_char_limit": {
        "type": "integer",
        "minimum": 1000,
        "maximum": 90000,
        "default": 80000,
        "description": "list(include_content=true) 的单页序列化条目字符预算"
      },
      "context_chars": {
        "type": "integer",
        "minimum": 60,
        "maximum": 2000,
        "default": 240,
        "description": "search_by_content 的 snippet 最大字符数，包含省略号"
      },
      "match_mode": {
        "type": "string",
        "enum": ["keywords", "substring", "word", "exact", "fulltext"],
        "default": "keywords",
        "description": "相关记忆可显式选 fulltext（中文分词+BM25，仅四个碎片层）；keywords 保留覆盖率评分，其他模式与 history_search 共用文本匹配"
      },
      "regex": {"type": "boolean", "default": false, "description": "search_* 是否使用正则；为 true 时优先于 match_mode"},
      "case_sensitive": {"type": "boolean", "default": false, "description": "search_* 是否区分大小写"}
    },
    "required": ["action", "tier"],
    "additionalProperties": false
  },
  "version": "1.9.0",
  "enabled": true,
  "entrypoint": "tool.py:run",
  "permission": "delete"
}
```
