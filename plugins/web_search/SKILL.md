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

## 使用边界

参数、取值和默认值以工具 schema 为准。`urls`、`include_domains`、`exclude_domains`、
`select_paths`、`exclude_paths` 和 `select_domains` 使用逗号分隔字符串；`urls` 在
`extract` 中可包含多个地址，在 `crawl` / `map` 中只使用第一个入口地址。

## 典型示例

### 1. 快速搜索

```text
{
  "action": "search",
  "query": "2026年 AI Agent 框架对比",
  "max_results": 5,
  "search_depth": "basic",
  "answer": "basic"
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

本工具在返回给主智能体前执行本地长度限制：普通正文每条最多 10000 字符，研究报告最多 100000 字符；所有 action 仍受核心统一 100,000 字符 JSON 回传上限约束。

| 字段 | 出现位置 | 说明 |
|---|---|---|
| `content_truncated` | `search.results[]` | `content` 搜索片段是否超过 10000 字符 |
| `raw_content_truncated` | `search.results[]` | `raw_content` 不是 `none` 时，原文是否超过 10000 字符 |
| `content_truncated` | `extract.results[]` / `crawl.results[]` | `raw_content` 是否超过 10000 字符 |
| `truncated` | `research` | 研究报告是否超过 100000 字符；流式和轮询模式均可能出现 |

看到截断标记为 `true` 时必须告诉用户内容不完整。对于搜索片段，可继续用 `extract` 获取页面正文；若提取或爬取结果仍被截断，应缩小查询或爬取范围、拆分页面处理，而不能声称已获得全文。

### 成功、失败与部分失败

- `ok: true` 表示 action 已完成，但 `extract` / `crawl` 仍需检查 `failed_results`，其中可能存在部分 URL 失败。
- `research` 超时、失败或取消会返回 `ok: false`、`status` 和 `error`。
- 参数缺失、API Key 缺失、依赖缺失或客户端异常会由工具运行时报告为调用失败，不保证返回结构化的 `ok: false`。

### research 执行模式

默认 `research` 会轮询任务直到完成、失败、取消或超时。传入 `stream: true` 时消费 Tavily 的流式结果并拼接到 `report`，返回中包含 `stream: true`；这不是后台异步任务，工具调用仍会等待流结束。
