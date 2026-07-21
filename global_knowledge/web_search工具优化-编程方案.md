# web_search 工具优化 — 编程方案

## 问题

`plugins/web_search` 的 `tool.py` 实现质量很高——5 个 action 完整覆盖 Tavily API、内容截断、流式研究、超时处理均到位。但 SKILL.md 只有 1 行描述 + Tool JSON，缺少：

1. 主智能体面对 5 个 action 不知道怎么选
2. `_boolish` 的字符串值（`basic`/`advanced`/`markdown`/`text`）对主智能体不透明
3. 无使用示例
4. 返回结构中的 `content_truncated` / `truncated` 标记缺少说明

## 能力目标

不改 `tool.py`，只重写 SKILL.md，添加三层引导：

### 第一层：使用决策表

主智能体收到搜索类请求后按此表选择 action：

| 用户意图 | action | 说明 |
|---------|--------|------|
| "搜一下最新的..."、"帮我查..."、"XX 是什么" | `search` | 关键词 → 多源结果 + AI 摘要。最常用，1-2 秒返回 |
| "把这几个链接的内容提取出来" | `extract` | 已知 URL → 正文/原始内容。用于获取 search 返回的链接全文 |
| "把这个网站的所有文档全抓下来" | `crawl` | 一个入口 URL → 沿链接深度爬取全站。适合文档站、知识库 |
| "这个网站有哪些页面" | `map` | 一个入口 URL → 列出所有可访问 URL。先 map 侦察，再 extract/crawl |
| "深度对比分析 A 和 B"、"写一份调研报告" | `research` | 复杂课题 → 多源交叉验证 → 带引用报告。30-180 秒，最强大也最慢 |

### 第二层：参数速查

对于 `_boolish` 参数的可用字符串值，提供速查表：

| 参数 | 可用值 | 效果 |
|------|--------|------|
| `include_answer` | `true` / `false` / `"basic"` / `"advanced"` | AI 摘要：true=开启、false=关闭、basic=简短、advanced=深度 |
| `include_raw_content` | `true` / `false` / `"markdown"` / `"text"` | 原文：true=返回、false=不返回、markdown=保留格式、text=纯文本 |
| `search_depth` | `"basic"` / `"advanced"` / `"fast"` / `"ultra-fast"` | 深度越深越全面但越慢。日常 basic，严格查证 advanced，极速 fast |
| `topic` | `"general"` / `"news"` / `"finance"` | 话题分类，默认 general |
| `format` | `"markdown"` / `"text"` | 输出格式，extract 用，默认 markdown |
| `extract_depth` | `"basic"` / `"advanced"` | 提取深度，extract/crawl 用 |
| `model` | `"mini"` / `"pro"` / `"auto"` | research 模型：mini 快速、pro 深度、auto 自动 |
| `citation_format` | `"numbered"` / `"mla"` / `"apa"` / `"chicago"` | research 引用格式，默认 numbered |
| `time_range` | `"day"` / `"week"` / `"month"` / `"year"` | search 时间范围 |

### 第三层：典型调用示例

每个 action 一个完整示例，含输入参数和返回结果解读。

---

## 详细规划

### 步骤 1：重写 SKILL.md

**改动文件**：`plugins/web_search/SKILL.md`

```markdown
# web_search

Tavily 网络搜索。搜索、提取、爬取、网站地图、深度研究。需要 TAVILY_API_KEY 环境变量 + tavily-python。

## 使用决策

根据用户意图选择 action：

| 用户意图 | action | 说明 |
|---------|--------|------|
| "搜一下 XXX"、"帮我查"、"最新消息" | `search` | 关键词 → 多源结果 + AI 摘要。1-2 秒，最常用 |
| "把这几个链接内容提取出来" | `extract` | 已知 URL → 正文。用于获取 search 返回链接的全文 |
| "把这个网站全抓下来" | `crawl` | 入口 URL → 深度爬取全站。适合文档站、知识库 |
| "这个网站有哪些页面" | `map` | 入口 URL → 列出所有 URL。先侦察，再 extract/crawl |
| "深度对比分析"、"写调研报告" | `research` | 复杂课题 → 多源验证 → 带引用报告。30-180 秒 |

## 参数速查

### 通用参数（多 action 可用）

| 参数 | 可用值 | 说明 |
|------|--------|------|
| `include_answer` | `true` / `false` / `"basic"` / `"advanced"` | AI 摘要开关和深度 |
| `include_raw_content` | `true` / `false` / `"markdown"` / `"text"` | 原文开关和格式 |
| `search_depth` | `"basic"` / `"advanced"` / `"fast"` / `"ultra-fast"` | 搜索深度，越深越全越慢 |
| `topic` | `"general"` / `"news"` / `"finance"` | 话题分类 |
| `extract_depth` | `"basic"` / `"advanced"` | 提取深度 |
| `format` | `"markdown"` / `"text"` | 输出格式 |
| `model` | `"mini"` / `"pro"` / `"auto"` | research 模型 |
| `citation_format` | `"numbered"` / `"mla"` / `"apa"` / `"chicago"` | research 引用格式 |
| `time_range` | `"day"` / `"week"` / `"month"` / `"year"` | search 时间范围 |

### 搜索精度选择

| 场景 | search_depth |
|------|-------------|
| 快速了解、闲聊式查询 | `fast` 或 `ultra-fast` |
| 日常搜索、找资料 | `basic`（默认） |
| 严格查证、需要多源交叉 | `advanced` |

### 答案深度选择

| 场景 | include_answer |
|------|---------------|
| 不需要 AI 回答，只要链接 | `false` |
| 快速摘要 | `"basic"`（默认） |
| 详细分析 | `"advanced"` |

## 典型示例

### 1. 快速搜索

```json
{
  "action": "search",
  "query": "2026年 AI Agent 框架对比",
  "max_results": 5,
  "search_depth": "basic"
}
```

返回：

```json
{
  "ok": true,
  "query": "2026年 AI Agent 框架对比",
  "answer": "2026年主流 AI Agent 框架包括 AutoGPT、CrewAI、LangGraph...",
  "results": [
    {
      "title": "xxx",
      "url": "https://...",
      "content": "正文片段",
      "content_truncated": false,
      "score": 0.95
    }
  ],
  "images": [],
  "response_time": 1.2
}
```

### 2. 提取网页正文

拿到 search 返回的 URL 后，提取全文：

```json
{
  "action": "extract",
  "urls": "https://example.com/article1, https://example.com/article2",
  "format": "markdown"
}
```

返回：

```json
{
  "ok": true,
  "urls": ["https://example.com/article1", "https://example.com/article2"],
  "results": [
    {
      "url": "https://example.com/article1",
      "raw_content": "# 文章标题\n\n正文内容...",
      "content_truncated": false
    }
  ],
  "failed_results": []
}
```

### 3. 深度爬取文档站

```json
{
  "action": "crawl",
  "urls": "https://docs.example.com",
  "max_depth": 3,
  "instructions": "只抓 API 文档页面",
  "format": "markdown"
}
```

返回：

```json
{
  "ok": true,
  "url": "https://docs.example.com",
  "results": [
    {
      "url": "https://docs.example.com/api/overview",
      "raw_content": "# API Overview\n\n...",
      "content_truncated": false
    }
  ],
  "failed_results": []
}
```

### 4. 网站地图侦察

```json
{
  "action": "map",
  "urls": "https://docs.example.com",
  "select_paths": "/api/**,/reference/**"
}
```

返回：

```json
{
  "ok": true,
  "url": "https://docs.example.com",
  "urls": [
    "https://docs.example.com/api/overview",
    "https://docs.example.com/api/auth",
    "https://docs.example.com/reference/index"
  ],
  "total": 42
}
```

拿到 URL 列表后，挑关键页面用 `extract` 提取内容。

### 5. 深度研究

```json
{
  "action": "research",
  "input": "对比 AutoGPT、CrewAI、LangGraph 三个 Agent 框架的架构差异",
  "model": "pro",
  "citation_format": "numbered"
}
```

返回：

```json
{
  "ok": true,
  "topic": "对比 AutoGPT、CrewAI、LangGraph 三个 Agent 框架的架构差异",
  "request_id": "req_abc123",
  "status": "completed",
  "report": "# Agent 框架架构对比\n\n## AutoGPT\n\n... [1][2]\n\n## CrewAI\n\n... [3]\n\n",
  "truncated": false,
  "sources": [
    {"url": "https://...", "title": "..."}
  ]
}
```

## 返回字段解读

### 内容截断标记

`search` 和 `extract` 结果每条最多返回 10000 字符，`research` 报告最多 100000 字符。超出部分被截断：

| 字段 | 出现位置 | 说明 |
|------|---------|------|
| `content_truncated` | search/extract 每条结果 | true 表示该条正文被截断，需用 extract 获取全文 |
| `content_truncated` | extract 每条结果 | true 表示原文超过 10000 字 |
| `truncated` | research 结果 | true 表示报告被截断 |

看到 `truncated: true` 时，告知用户结果不完整，建议缩小范围或用 extract/crawl 获取指定页面的完整内容。

### 成功/失败

所有 action 返回 `ok: true` 表示成功，`ok: false` 表示失败（含 error 字段）。

### research 异步模式

默认 `research` 会等待完成（轮询模式）。传入 `stream: true` 可获取流式结果，此时返回 `stream: true` 且 `report` 字段为流式文本的拼接。

## Tool

```json
{
  "name": "web_search",
  "description": "Tavily 网络搜索 — search/extract/crawl/map/research 五个子操作。当需要搜索最新信息、提取网页正文、爬取网站、发现站点 URL、深度研究时使用。通过 action 参数选择。需要 TAVILY_API_KEY 环境变量。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "enum": ["search", "extract", "crawl", "map", "research"],
        "description": "操作: search=网络搜索(日常查询), extract=正文提取(已知URL), crawl=深度爬取(全站), map=站点地图(URL发现), research=深度研究(复杂课题报告)"
      },
      "query": {"type": "string", "description": "搜索关键词(search) 或 聚焦查询(extract)"},
      "input": {"type": "string", "description": "research 的研究课题"},
      "urls": {"type": "string", "description": "extract/crawl/map 的 URL(逗号分隔)"},
      "search_depth": {"type": "string", "enum": ["basic", "advanced", "fast", "ultra-fast"], "description": "搜索深度，默认 basic。fast=最快, basic=日常, advanced=严格查证"},
      "topic": {"type": "string", "enum": ["general", "news", "finance"], "description": "话题分类，默认 general"},
      "time_range": {"type": "string", "enum": ["day", "week", "month", "year"], "description": "时间范围"},
      "start_date": {"type": "string", "description": "search 起始日期 YYYY-MM-DD"},
      "end_date": {"type": "string", "description": "search 结束日期 YYYY-MM-DD"},
      "days": {"type": "integer", "minimum": 0, "description": "search 最近 N 天"},
      "max_results": {"type": "integer", "minimum": 1, "maximum": 20, "description": "search 最多返回条数，默认 5"},
      "include_answer": {"type": "string", "description": "AI 摘要: true=开启, false=关闭, basic=简短, advanced=深度"},
      "include_raw_content": {"type": "string", "description": "原文: true=返回, false=不返回, markdown=保留格式, text=纯文本"},
      "include_images": {"type": "boolean", "description": "是否返回图片"},
      "include_image_descriptions": {"type": "boolean", "description": "是否返回图片描述"},
      "include_favicon": {"type": "boolean", "description": "是否返回站点图标"},
      "auto_parameters": {"type": "boolean", "description": "是否允许 Tavily 自动调参"},
      "country": {"type": "string", "description": "search 国家/地区代码"},
      "include_domains": {"type": "string", "description": "限定来源域名，逗号分隔"},
      "exclude_domains": {"type": "string", "description": "排除域名，逗号分隔"},
      "extract_depth": {"type": "string", "enum": ["basic", "advanced"], "description": "extract/crawl 提取深度"},
      "format": {"type": "string", "enum": ["markdown", "text"], "description": "extract 输出格式，默认 markdown"},
      "chunks_per_source": {"type": "integer", "minimum": 0, "description": "每个来源的内容块数"},
      "max_depth": {"type": "integer", "minimum": 0, "description": "crawl/map 最大爬取/扫描深度"},
      "max_breadth": {"type": "integer", "minimum": 0, "description": "crawl/map 每层最大广度"},
      "limit": {"type": "integer", "minimum": 0, "description": "crawl/map 最大页面数/URL 数"},
      "instructions": {"type": "string", "description": "crawl/map 自然语言指令"},
      "select_paths": {"type": "string", "description": "crawl/map 路径 glob 白名单"},
      "exclude_paths": {"type": "string", "description": "crawl/map 路径 glob 黑名单"},
      "select_domains": {"type": "string", "description": "crawl/map 域名白名单"},
      "allow_external": {"type": "boolean", "description": "crawl/map 是否允许外部域名"},
      "model": {"type": "string", "enum": ["mini", "pro", "auto"], "description": "research 模型：mini=快速, pro=深度, auto=自动"},
      "citation_format": {"type": "string", "enum": ["numbered", "mla", "apa", "chicago"], "description": "research 引用格式，默认 numbered"},
      "output_schema": {"type": "string", "description": "research 结构化输出 JSON Schema 字符串"},
      "stream": {"type": "boolean", "description": "research 是否流式输出"}
    },
    "required": ["action"],
    "additionalProperties": false
  },
  "version": "1.1.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```

---

### 步骤 2：验证

- 确认 SKILL.md 版本号为 `1.1.0`
- 确认决策表覆盖 5 个 action
- 确认参数速查表覆盖 `_boolish` 的 9 个参数及其可用字符串值
- 确认 5 个示例输入/输出正确（与 tool.py 实际行为一致）
- 确认返回字段解读覆盖 `content_truncated` / `truncated` / `ok`
- 确认 Tool JSON 与 tool.py 实际接受的参数一致
- `tool.py` 未被修改，保持原样

---

## 应达到的效果

1. 主智能体面对 5 个 action 不再迷惑——决策表直接给出选择依据
2. `_boolish` 的字符串值（`"basic"`、`"advanced"`、`"markdown"`、`"text"`）通过参数速查表对主智能体完全透明
3. 5 个典型示例覆盖从简单到复杂的使用场景，主智能体可参照调用
4. `content_truncated: true` / `truncated: true` 的含义和后续操作（用 extract 获取全文、缩小范围）写清楚
5. 只改 SKILL.md，tool.py 保持不动
