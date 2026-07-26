# history_search

搜索当前用户已提交对话窗口中的用户与助手文本。不会读取思考记录或工具日志。

## 使用原则

1. **与 memory 搜索的区别**：`history_search` 搜索原始对话文本，即用户和助手曾经说过什么；`memory_manage` 的 `search_by_title`、`search_by_content` 搜索提炼后的长期记忆、临时记忆与规则。想找“上次讨论树莓派时说了什么”使用 `history_search`，想找“树莓派的 IP 是多少”优先搜索记忆。
2. **时间过滤优先**：用户提及“昨天”“上周”或具体日期时，应先换算为北京时间的 `YYYY-MM-DD`，再传入 `since`、`until`。工具使用归档 `data.json` 的 `created_at` 判断会话日期，并兼容旧式日期目录；被排除的窗口不会读取正文 JSON。
3. **角色过滤**：用户问“我上次说过什么”时使用 `role=user`；问“你之前怎么回答的”时使用 `role=assistant`。
4. **上下文按需获取**：需要还原对话脉络时传入 `context_messages`，一般取 2–3；只确认是否提到某个词时保持默认值 0。
5. **搜索精度**：搜索 `AI` 等缩写且不希望命中 `main`、`email` 时，使用 `match_mode=word`。只有用户明确需要模式匹配时才启用 `regex=true`。
6. **控制返回规模**：先使用较小的 `limit` 和 `max_snippet`。`truncated=true` 表示还有命中结果，可结合更窄的日期、角色或关键词继续搜索。

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

## 返回字段

| 字段 | 说明 |
|------|------|
| `query` | 原始搜索词 |
| `matches` | 匹配结果数组 |
| `total_matches` | 实际命中总数，包含被 limit 截断的结果 |
| `truncated` | `total_matches > limit` 时为 true |
| `time_range` | 实际使用的 since 与 until |
| `window` | 匹配所在的历史窗口目录名 |
| `source` | 对话来源，如 web、cli、cron |
| `session_id` | 会话 ID |
| `role` | 匹配消息角色 |
| `snippet` | 围绕首次命中位置生成的有界片段 |
| `match_index` | 匹配消息在 `text.json.messages` 中的索引 |
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
      }
    },
    "required": ["query"],
    "additionalProperties": false
  },
  "version": "1.1.1",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
