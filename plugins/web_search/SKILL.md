# web_search

Tavily 网络搜索。搜索、提取、爬取、网站地图、深度研究。
需要 TAVILY_API_KEY 环境变量 + tavily-python。

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
        "description": "操作: search=网络搜索, extract=正文提取, crawl=深度爬取, map=站点地图, research=深度研究"
      },
      "query": {"type": "string", "description": "搜索关键词(search) 或 聚焦查询(extract)"},
      "input": {"type": "string", "description": "research 的研究课题"},
      "urls": {"type": "string", "description": "extract/crawl/map 的 URL(逗号分隔)"},
      "search_depth": {"type": "string", "enum": ["basic", "advanced", "fast", "ultra-fast"], "description": "搜索深度，默认 basic"},
      "topic": {"type": "string", "enum": ["general", "news", "finance"], "description": "话题分类，默认 general"},
      "time_range": {"type": "string", "enum": ["day", "week", "month", "year"], "description": "时间范围"},
      "start_date": {"type": "string", "description": "search 起始日期 YYYY-MM-DD"},
      "end_date": {"type": "string", "description": "search 结束日期 YYYY-MM-DD"},
      "days": {"type": "integer", "minimum": 0, "description": "search 最近 N 天"},
      "max_results": {"type": "integer", "minimum": 1, "maximum": 20, "description": "search 最多返回条数，默认 5"},
      "include_answer": {"type": "string", "description": "AI 摘要: true/false/basic/advanced"},
      "include_raw_content": {"type": "string", "description": "原文: true/false/markdown/text"},
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
      "model": {"type": "string", "enum": ["mini", "pro", "auto"], "description": "research 研究模型，默认 auto"},
      "citation_format": {"type": "string", "enum": ["numbered", "mla", "apa", "chicago"], "description": "research 引用格式"},
      "output_schema": {"type": "string", "description": "research 结构化输出 JSON Schema 字符串"},
      "stream": {"type": "boolean", "description": "research 是否消费流式结果"}
    },
    "required": ["action"],
    "additionalProperties": false
  },
  "version": "1.0.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
