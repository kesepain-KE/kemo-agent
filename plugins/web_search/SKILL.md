# web_search

Tavily 网络搜索工具，支持搜索、正文提取、整站爬取、站点地图和深度研究。
运行前必须安装 `tavily-python`，并设置 `TAVILY_API_KEY` 环境变量。工具在未配置时仍可被发现，调用会返回明确的配置提示且不会发起网络请求。

## 使用决策

根据用户意图选择 action：

| 用户意图 | action | 说明 |
|---|---|---|
| “搜一下 XXX”“帮我查”“最新消息”“XX 是什么” | `search` | 关键词 → 多源结果和 AI 摘要。通常 1～2 秒返回，是最常用的入口 |
| “把这几个链接的内容提取出来” | `extract` | 已知 URL → 网页正文。适合继续读取 `search` 找到的关键链接 |
| “把这个网站的文档全抓下来” | `crawl` | 一个入口 URL → 沿站内链接爬取内容。适合文档站和知识库 |
| “这个网站有哪些页面” | `map` | 一个入口 URL → 可访问 URL 列表。适合先侦察，再选择 `extract` 或 `crawl` |
| “深度对比分析 A 和 B”“写一份调研报告” | `research` | 复杂课题 → 多源交叉验证和带引用报告。通常需要 30～180 秒 |

优先使用满足需求的最轻 action。已有明确 URL 时不要重新 `search`；只需站点目录时不要直接 `crawl`；只有复杂、多来源、需要成文报告的任务才使用 `research`。

## 参数速查

### 关键参数

| 参数 | 可用值 | 效果 |
|---|---|---|
| `include_answer` | `"true"` / `"false"` / `"basic"` / `"advanced"` | `search` 的 AI 摘要：开启、关闭、简短或深度；默认 `"basic"` |
| `include_raw_content` | `"true"` / `"false"` / `"markdown"` / `"text"` | `search` 是否附带原文，以及原文格式；默认 `"false"` |
| `search_depth` | `"basic"` / `"advanced"` / `"fast"` / `"ultra-fast"` | `search` 的速度和深度；日常用 `basic`，严格查证用 `advanced` |
| `topic` | `"general"` / `"news"` / `"finance"` | `search` 的话题分类；默认 `general` |
| `time_range` | `"day"` / `"week"` / `"month"` / `"year"` | `search` 的相对时间范围 |
| `extract_depth` | `"basic"` / `"advanced"` | `extract` / `crawl` 的正文提取深度；默认 `basic` |
| `format` | `"markdown"` / `"text"` | `extract` / `crawl` 的正文格式；默认 `markdown` |
| `model` | `"mini"` / `"pro"` / `"auto"` | `research` 模型：快速、深度或自动选择；默认 `auto` |
| `citation_format` | `"numbered"` / `"mla"` / `"apa"` / `"chicago"` | `research` 引用格式；默认 `numbered` |

`include_answer` 和 `include_raw_content` 在工具 schema 中是字符串参数，因此调用时应使用上表中的字符串值，而不是 JSON 布尔值。

### 搜索精度选择

| 场景 | `search_depth` |
|---|---|
| 极低延迟、只需快速线索 | `ultra-fast` 或 `fast` |
| 日常搜索、寻找资料 | `basic`（默认） |
| 严格查证、多源交叉验证 | `advanced` |

### AI 摘要选择

| 场景 | `include_answer` |
|---|---|
| 只需要链接，不需要 Tavily 生成答案 | `"false"` |
| 快速摘要 | `"basic"`（默认） |
| 详细综合分析 | `"advanced"` |

### URL 与列表参数

`urls`、`include_domains`、`exclude_domains`、`select_paths`、`exclude_paths` 和 `select_domains` 使用逗号分隔字符串。`urls` 在 `extract` 中可包含多个地址，在 `crawl` / `map` 中只使用第一个入口地址。

## 典型示例

### 1. 快速搜索

```text
{
  "action": "search",
  "query": "2026年 AI Agent 框架对比",
  "max_results": 5,
  "search_depth": "basic",
  "include_answer": "basic"
}
```

典型返回：

```text
{
  "ok": true,
  "query": "2026年 AI Agent 框架对比",
  "answer": "主流 AI Agent 框架包括……",
  "results": [
    {
      "title": "示例结果",
      "url": "https://example.com/article",
      "content": "正文片段……",
      "content_truncated": false,
      "score": 0.95
    }
  ],
  "images": [],
  "response_time": 1.2
}
```

先依据 `answer` 和结果片段回答；若需要核对完整上下文，再对关键 `url` 使用 `extract`。

### 2. 提取网页正文

```text
{
  "action": "extract",
  "urls": "https://example.com/article1,https://example.com/article2",
  "extract_depth": "basic",
  "format": "markdown"
}
```

典型返回：

```text
{
  "ok": true,
  "urls": [
    "https://example.com/article1",
    "https://example.com/article2"
  ],
  "results": [
    {
      "url": "https://example.com/article1",
      "raw_content": "# 文章标题\n\n正文内容……",
      "content_truncated": false
    }
  ],
  "failed_results": []
}
```

逐项检查 `failed_results` 和 `content_truncated`，不要把部分提取成功误报为全部成功。

### 3. 深度爬取文档站

```text
{
  "action": "crawl",
  "urls": "https://docs.example.com",
  "max_depth": 3,
  "limit": 50,
  "instructions": "只抓 API 文档页面",
  "extract_depth": "advanced",
  "format": "markdown"
}
```

典型返回：

```text
{
  "ok": true,
  "url": "https://docs.example.com",
  "results": [
    {
      "url": "https://docs.example.com/api/overview",
      "raw_content": "# API Overview\n\n……",
      "content_truncated": false
    }
  ],
  "failed_results": []
}
```

整站规模不明时先用 `map`。使用 `limit`、`max_depth`、路径筛选或 `instructions` 控制范围，避免无边界爬取。

### 4. 网站地图侦察

```text
{
  "action": "map",
  "urls": "https://docs.example.com",
  "select_paths": "/api/**,/reference/**",
  "limit": 100
}
```

典型返回：

```text
{
  "ok": true,
  "url": "https://docs.example.com",
  "urls": [
    "https://docs.example.com/api/overview",
    "https://docs.example.com/api/auth",
    "https://docs.example.com/reference/index"
  ],
  "total": 3
}
```

从 URL 列表中选择与任务有关的页面，再用 `extract` 精确读取；需要批量抓取时再用 `crawl`。

### 5. 深度研究

```text
{
  "action": "research",
  "input": "对比 AutoGPT、CrewAI、LangGraph 三个 Agent 框架的架构差异",
  "model": "pro",
  "citation_format": "numbered"
}
```

典型返回：

```text
{
  "ok": true,
  "topic": "对比 AutoGPT、CrewAI、LangGraph 三个 Agent 框架的架构差异",
  "request_id": "req_abc123",
  "status": "completed",
  "report": "# Agent 框架架构对比\n\n…… [1][2]",
  "truncated": false,
  "sources": [
    {
      "url": "https://example.com/source",
      "title": "示例来源"
    }
  ]
}
```

报告应连同 `sources` 一起解读。`model: "pro"` 适合重要报告；时间敏感或初步研究可使用 `mini` 或 `auto`。

## 返回字段解读

### 内容截断标记

本工具在返回给主智能体前执行本地长度限制：普通正文每条最多 10000 字符，研究报告最多 100000 字符。

| 字段 | 出现位置 | 说明 |
|---|---|---|
| `content_truncated` | `search.results[]` | `content` 搜索片段是否超过 10000 字符 |
| `raw_content_truncated` | `search.results[]` | 启用 `include_raw_content` 后，`raw_content` 是否超过 10000 字符 |
| `content_truncated` | `extract.results[]` / `crawl.results[]` | `raw_content` 是否超过 10000 字符 |
| `truncated` | `research` | 研究报告是否超过 100000 字符；流式和轮询模式均可能出现 |

看到截断标记为 `true` 时必须告诉用户内容不完整。对于搜索片段，可继续用 `extract` 获取页面正文；若提取或爬取结果仍被截断，应缩小查询或爬取范围、拆分页面处理，而不能声称已获得全文。

### 成功、失败与部分失败

- `ok: true` 表示 action 已完成，但 `extract` / `crawl` 仍需检查 `failed_results`，其中可能存在部分 URL 失败。
- `research` 超时、失败或取消会返回 `ok: false`、`status` 和 `error`。
- 参数缺失、API Key 缺失、依赖缺失或客户端异常会由工具运行时报告为调用失败，不保证返回结构化的 `ok: false`。

### research 执行模式

默认 `research` 会轮询任务直到完成、失败、取消或超时。传入 `stream: true` 时消费 Tavily 的流式结果并拼接到 `report`，返回中包含 `stream: true`；这不是后台异步任务，工具调用仍会等待流结束。

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
      "query": {"type": "string", "description": "搜索关键词(search) 或聚焦查询(extract)"},
      "input": {"type": "string", "description": "research 的研究课题"},
      "urls": {"type": "string", "description": "extract/crawl/map 的 URL，多个地址用逗号分隔"},
      "search_depth": {"type": "string", "enum": ["basic", "advanced", "fast", "ultra-fast"], "description": "搜索深度，默认 basic。fast=快速，basic=日常，advanced=严格查证"},
      "topic": {"type": "string", "enum": ["general", "news", "finance"], "description": "话题分类，默认 general"},
      "time_range": {"type": "string", "enum": ["day", "week", "month", "year"], "description": "search 相对时间范围"},
      "start_date": {"type": "string", "description": "search 起始日期 YYYY-MM-DD"},
      "end_date": {"type": "string", "description": "search 结束日期 YYYY-MM-DD"},
      "days": {"type": "integer", "minimum": 0, "description": "search 最近 N 天"},
      "max_results": {"type": "integer", "minimum": 1, "maximum": 20, "description": "search 最多返回条数，默认 5"},
      "include_answer": {"type": "string", "description": "search AI 摘要: true=开启, false=关闭, basic=简短, advanced=深度"},
      "include_raw_content": {"type": "string", "description": "search 原文: true=返回, false=不返回, markdown=保留格式, text=纯文本"},
      "include_images": {"type": "boolean", "description": "search/extract/crawl 是否返回图片"},
      "include_image_descriptions": {"type": "boolean", "description": "search 是否返回图片描述"},
      "include_favicon": {"type": "boolean", "description": "是否返回站点图标"},
      "auto_parameters": {"type": "boolean", "description": "search 是否允许 Tavily 自动调参"},
      "country": {"type": "string", "description": "search 国家/地区代码"},
      "include_domains": {"type": "string", "description": "search 限定来源域名，逗号分隔"},
      "exclude_domains": {"type": "string", "description": "search/crawl/map 排除域名，逗号分隔"},
      "extract_depth": {"type": "string", "enum": ["basic", "advanced"], "description": "extract/crawl 提取深度"},
      "format": {"type": "string", "enum": ["markdown", "text"], "description": "extract/crawl 输出格式，默认 markdown"},
      "chunks_per_source": {"type": "integer", "minimum": 0, "description": "search/extract/crawl 每个来源的内容块数"},
      "max_depth": {"type": "integer", "minimum": 0, "description": "crawl/map 最大爬取或扫描深度"},
      "max_breadth": {"type": "integer", "minimum": 0, "description": "crawl/map 每层最大广度"},
      "limit": {"type": "integer", "minimum": 0, "description": "crawl/map 最大页面数或 URL 数"},
      "instructions": {"type": "string", "description": "crawl/map 自然语言筛选指令"},
      "select_paths": {"type": "string", "description": "crawl/map 路径 glob 白名单，逗号分隔"},
      "exclude_paths": {"type": "string", "description": "crawl/map 路径 glob 黑名单，逗号分隔"},
      "select_domains": {"type": "string", "description": "crawl/map 域名白名单，逗号分隔"},
      "allow_external": {"type": "boolean", "description": "crawl/map 是否允许外部域名"},
      "model": {"type": "string", "enum": ["mini", "pro", "auto"], "description": "research 模型：mini=快速, pro=深度, auto=自动"},
      "citation_format": {"type": "string", "enum": ["numbered", "mla", "apa", "chicago"], "description": "research 引用格式，默认 numbered"},
      "output_schema": {"type": "string", "description": "research 结构化输出 JSON Schema 字符串"},
      "stream": {"type": "boolean", "description": "research 是否消费流式结果"}
    },
    "required": ["action"],
    "additionalProperties": false
  },
  "version": "1.1.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
