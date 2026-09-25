# history_search

搜索当前用户已提交对话窗口中的用户与助手文本。不会读取思考记录或工具日志。

## 使用原则

1. **与 memory 搜索的区别**：`history_search` 搜索原始对话文本，即用户和助手曾经说过什么；`memory_manage` 的 `search_by_title`、`search_by_content` 搜索提炼后的长期记忆、临时记忆与规则。想找“上次讨论树莓派时说了什么”使用 `history_search`，想找“树莓派的 IP 是多少”优先搜索记忆。
2. **时间过滤优先**：用户提及“昨天”“上周”或具体日期时，应先换算为北京时间的 `YYYY-MM-DD`，再传入 `since`、`until`。工具使用 SQLite 归档元数据的 `created_at` 判断会话日期；正文来自结构化消息表，不扫描历史目录。
3. **角色过滤**：用户问“我上次说过什么”时使用 `role=user`；问“你之前怎么回答的”时使用 `role=assistant`。
4. **上下文按需获取**：需要还原对话脉络时传入 `context_messages`，一般取 2–3；只确认是否提到某个词时保持默认值 0。
5. **搜索精度**：搜索 `AI` 等缩写且不希望命中 `main`、`email` 时，使用 `match_mode=word`。只有用户明确需要模式匹配时才启用 `regex=true`。
6. **优先限定会话范围**：已知入口或会话时传入 `source`、`session_id`，避免扫描无关归档。该过滤只匹配已提交的 archive 消息，不会读取 runtime 缓存、未完成 Run、思考记录或工具日志。
7. **控制返回规模并分页**：先使用较小的 `limit`、`max_snippet` 与 `context_messages`。结果同时受 `page_char_limit` 限制；`has_more=true` 时把 `next_offset` 传给下一次调用。`page_limited_by_chars=true` 表示本页因字符预算提前结束，但仍可正常续页。

## 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `query` | string | — | 搜索关键词（必填）；`regex=true` 时为正则表达式 |
| `limit` | integer | 10 | 最多返回的匹配数（1–100） |
| `since` | string | 无 | 起始日期 `YYYY-MM-DD`，包含当天 |
| `until` | string | 无 | 结束日期 `YYYY-MM-DD`，包含当天 |
| `role` | string | any | 角色过滤：`any`、`user`、`assistant` |
| `match_mode` | string | substring | `substring`=子串、`word`=整词边界、`exact`=去除首尾空白后完全相等 |
| `regex` | boolean | false | 将 query 作为正则表达式处理，并忽略 `match_mode` |
| `max_snippet` | integer | 500 | 每条匹配片段的字符上限（1–5000，包含省略号） |
| `context_messages` | integer | 0 | 匹配消息前后各取 N 条上下文（0–20） |
| `max_context_chars` | integer | 1000 | 每条上下文消息的文本上限（50–5000），超出时追加省略号 |
| `offset` | integer | 0 | 搜索结果分页偏移；下一页使用返回的 `next_offset` |
| `page_char_limit` | integer | 80000 | 单页匹配项的序列化字符预算（1000–90000），与 limit 共同限制返回规模 |
| `source` | string | 无 | 精确过滤对话来源，如 `web`、`app`、`cli`、`cron` |
| `session_id` | string | 无 | 精确过滤逻辑会话 ID；可与 source 组合使用 |

## 返回字段

| 字段 | 说明 |
|------|------|
| `query` | 原始搜索词 |
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
| `context` | 上下文消息数组，仅在 `context_messages > 0` 时返回 |
| `context_index` | 匹配消息在 context 数组中的位置，仅在 `context_messages > 0` 时返回 |

## Tool

```json
{
  "name": "history_search",
  "description": "搜索当前用户已提交对话窗口中的用户与助手文本。支持时间、角色、整词、精确、正则、上下文窗口和有界片段；不读取思考记录或工具日志。",
  "input_schema": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "搜索关键词；regex=true 时为正则表达式"
      },
      "limit": {
        "type": "integer",
        "description": "最多返回的匹配数",
        "minimum": 1,
        "maximum": 100,
        "default": 10
      },
      "since": {
        "type": "string",
        "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
        "description": "起始日期 YYYY-MM-DD，包含当天"
      },
      "until": {
        "type": "string",
        "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
        "description": "结束日期 YYYY-MM-DD，包含当天"
      },
      "role": {
        "type": "string",
        "enum": ["any", "user", "assistant"],
        "default": "any",
        "description": "角色过滤"
      },
      "match_mode": {
        "type": "string",
        "enum": ["substring", "word", "exact"],
        "default": "substring",
        "description": "匹配方式；regex=true 时忽略"
      },
      "regex": {
        "type": "boolean",
        "default": false,
        "description": "是否将 query 作为正则表达式"
      },
      "max_snippet": {
        "type": "integer",
        "minimum": 1,
        "maximum": 5000,
        "default": 500,
        "description": "每条匹配片段的最大字符数，包含省略号"
      },
      "context_messages": {
        "type": "integer",
        "minimum": 0,
        "maximum": 20,
        "default": 0,
        "description": "匹配消息前后各取 N 条上下文"
      },
      "max_context_chars": {
        "type": "integer",
        "minimum": 50,
        "maximum": 5000,
        "default": 1000,
        "description": "每条上下文消息的最大字符数，超出时追加省略号"
      },
      "offset": {
        "type": "integer",
        "minimum": 0,
        "maximum": 1000000,
        "default": 0,
        "description": "匹配结果分页偏移；下一页使用 next_offset"
      },
      "page_char_limit": {
        "type": "integer",
        "minimum": 1000,
        "maximum": 90000,
        "default": 80000,
        "description": "单页匹配项的序列化字符预算"
      },
      "source": {
        "type": "string",
        "maxLength": 200,
        "description": "精确过滤对话来源"
      },
      "session_id": {
        "type": "string",
        "maxLength": 200,
        "description": "精确过滤逻辑会话 ID"
      }
    },
    "required": ["query"],
    "additionalProperties": false
  },
  "version": "1.2.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
