# kemo-agent Markdown 渲染增强 — 编程方案

## 目标

在 kemo-agent 现有 `react-markdown` + `remark-gfm` 基础上，先对齐 votx-agent 的渲染能力，再超越它（语法高亮 / Mermaid / Emoji）。

---

## 一、依赖变更

### 新增依赖

```bash
npm install remark-math rehype-katex katex remark-breaks rehype-sanitize rehype-highlight highlight.js remark-emoji mermaid
```

| 包 | 用途 |
|---|------|
| `remark-math` | 解析 `$...$` 和 `$$...$$` 数学公式 |
| `rehype-katex` | 将数学公式 AST 渲染为 KaTeX HTML |
| `katex` | 运行时 KaTeX CSS（`rehype-katex` 的 peer dep） |
| `remark-breaks` | 单换行 → `<br>`（非流式时启用） |
| `rehype-sanitize` | 净化 HTML 输出，防 XSS |
| `rehype-highlight` | 代码块语法高亮 |
| `highlight.js` | 高亮语言定义（`rehype-highlight` 的 peer dep） |
| `remark-emoji` | `:rocket:` → 🚀 |
| `mermaid` | Mermaid 图表运行时渲染 |

### 最终 package.json dependencies 区块

```json
{
  "dependencies": {
    "@tanstack/react-query": "^5.83.0",
    "katex": "^0.16.0",
    "highlight.js": "^11.10.0",
    "lucide-react": "^0.468.0",
    "mermaid": "^11.0.0",
    "react": "^19.1.0",
    "react-dom": "^19.1.0",
    "react-markdown": "^10.1.0",
    "react-router-dom": "^7.6.0",
    "rehype-highlight": "^7.0.0",
    "rehype-katex": "^7.0.1",
    "rehype-sanitize": "^6.0.0",
    "remark-breaks": "^4.0.0",
    "remark-emoji": "^5.0.0",
    "remark-gfm": "^4.0.1",
    "remark-math": "^6.0.0",
    "zod": "^3.25.0",
    "zustand": "^5.0.5"
  }
}
```

---

## 二、新增文件

### 2.1 `src/components/Chat/MarkdownMessage.tsx`

**新建这个文件**，从 `ChatPage.tsx` 中抽离 markdown 渲染逻辑。这是本次改造的核心文件。

#### 完整实现

```tsx
import { useRef, useEffect, useState } from 'react'
import ReactMarkdown, { type Components, type UrlTransform } from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'
import rehypeKatex from 'rehype-katex'
import rehypeSanitize, { defaultSchema, type Options as SanitizeOptions } from 'rehype-sanitize'
import remarkBreaks from 'remark-breaks'
import remarkEmoji from 'remark-emoji'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import mermaid from 'mermaid'
import 'katex/dist/katex.min.css'
import 'highlight.js/styles/github-dark.min.css'  // 或其他暗色主题

// ---- Mermaid 初始化 ----
// 在组件外初始化一次即可
let mermaidReady = false
function initMermaid() {
  if (mermaidReady) return
  mermaid.initialize({
    startOnLoad: false,
    theme: 'dark',
    securityLevel: 'sandbox',
  })
  mermaidReady = true
}

// ---- rehype-sanitize schema（允许 math / highlight 的 class） ----
const markdownSchema: SanitizeOptions = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    code: [
      ...(defaultSchema.attributes?.code || []),
      ['className', 'language-math', 'math-inline', 'math-display',
       // highlight.js 会给 code 添加 class
       'hljs', 'hljs-number', 'hljs-string', 'hljs-comment',
       'hljs-keyword', 'hljs-built_in', 'hljs-type', 'hljs-literal',
       'hljs-punctuation', 'hljs-attr', 'hljs-selector-class',
       'hljs-selector-tag', 'hljs-title', 'hljs-params',
       'hljs-meta', 'hljs-section', 'hljs-name', 'hljs-attribute'],
    ],
    span: [
      ...(defaultSchema.attributes?.span || []),
      ['className', 'katex', 'katex-html', 'base', 'strut', 'mord',
       'mclose', 'mopen', 'mord-mathbf', 'mbin', 'mrel', 'msupsub',
       'vlist-t', 'vlist-r', 'vlist', 'accent-body', 'mfrac', 'mtable',
       'col-align-c', 'col-align-l', 'col-align-r', 'hline', 'arraycolsep',
       'sqrt-sign', 'reset-textstyle', 'text', 'fontsize-ensurer',
       'delimsizing', 'delimcenter', 'nulldelimiter', 'rule',
       'hljs-number', 'hljs-string', 'hljs-comment', 'hljs-keyword',
       'hljs-built_in', 'hljs-type', 'hljs-literal', 'hljs-punctuation',
       'hljs-attr', 'hljs-selector-class', 'hljs-selector-tag',
       'hljs-title', 'hljs-params', 'hljs-meta', 'hljs-section',
       'hljs-name', 'hljs-attribute', 'mermaid'],
    ],
    div: [
      ...(defaultSchema.attributes?.div || []),
      ['className', 'mermaid', 'md-table-wrap'],
    ],
    svg: [
      ...(defaultSchema.attributes?.svg || []),
      ['className', 'mermaid-svg'],
    ],
  },
}

// ---- URL 安全过滤（从 votx-agent 适配） ----
const EXTERNAL_URL_RE = /^(https?:|mailto:|\/\/)/i
const DANGEROUS_SCHEME_RE = /^(javascript|data|vbscript|file):/i

const safeUrlTransform: UrlTransform = (url) => {
  const value = url.trim()
  if (!value) return ''
  // 允许外部 http/https/mailto
  if (EXTERNAL_URL_RE.test(value)) return value
  // 允许相对路径和无协议路径
  const hasScheme = /^[a-z][a-z0-9+.-]*:/i.test(value)
  if (!hasScheme) return value
  // 拦截危险协议
  if (DANGEROUS_SCHEME_RE.test(value)) return ''
  return value
}

// ---- Mermaid 图表组件 ----
function MermaidDiagram({ chart }: { chart: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const [svg, setSvg] = useState<string>('')
  const [error, setError] = useState<string>('')

  useEffect(() => {
    initMermaid()
    const id = `mermaid-${Math.random().toString(36).slice(2, 9)}`
    mermaid.render(id, chart)
      .then(({ svg }) => setSvg(svg))
      .catch((err) => setError(err.message))
  }, [chart])

  if (error) {
    return <pre className="mermaid-error">Mermaid 渲染失败: {error}</pre>
  }
  return <div className="mermaid" dangerouslySetInnerHTML={{ __html: svg }} />
}

// ---- Markdown 组件定制 ----
const markdownComponents: Components = {
  // 外部链接新窗口打开
  a({ node: _node, href, children, ...props }) {
    if (!href) return <span>{children}</span>
    const external = /^(https?:|mailto:|\/\/)/i.test(href)
    return (
      <a
        {...props}
        href={href}
        target={external ? '_blank' : undefined}
        rel={external ? 'noopener noreferrer' : undefined}
      >
        {children}
      </a>
    )
  },
  // 表格横向滚动
  table({ node: _node, children, ...props }) {
    return (
      <div className="md-table-wrap">
        <table {...props}>{children}</table>
      </div>
    )
  },
  // 代码块：Mermaid 图表或高亮代码
  pre({ node, children, className, ...props }: any) {
    // 检测是否为 Mermaid 代码块
    const child = node?.children?.[0]
    const codeClassName = child?.properties?.className || []
    const isMermaid = codeClassName.some((c: string) => c === 'language-mermaid')
    
    if (isMermaid) {
      // 提取 mermaid 源码
      const text = child?.children?.[0]?.value || ''
      return <MermaidDiagram chart={text} />
    }

    // 普通代码块（保留 highlight.js 的 class，用原生 pre/code 渲染）
    return (
      <div className="code-block-wrapper">
        <pre className={className} {...props}>{children}</pre>
      </div>
    )
  },
  // 行内代码
  code({ children, className, ...props }: any) {
    const isInline = !className
    if (isInline) {
      return <code {...props}>{children}</code>
    }
    return <code className={className} {...props}>{children}</code>
  },
  // 图片保留（votx-agent 屏蔽了图片，kemo-agent 保留）
  img({ node: _node, src, alt, ...props }) {
    if (!src) return null
    return (
      <img
        {...props}
        src={src}
        alt={alt || ''}
        loading="lazy"
        style={{ maxWidth: '100%', borderRadius: '8px' }}
      />
    )
  },
  // 任务列表渲染为只读 checkbox
  input({ node: _node, type, checked, ...props }) {
    if (type === 'checkbox') {
      return <input {...props} type="checkbox" checked={Boolean(checked)} disabled readOnly />
    }
    return <input {...props} type={type} />
  },
}

// ---- Props ----
interface MarkdownMessageProps {
  content: string
  streaming?: boolean
  className?: string
}

// ---- 导出组件 ----
export function MarkdownMessage({ content, streaming = false, className = '' }: MarkdownMessageProps) {
  // 流式时去掉 remarkBreaks（防断行抖动）和 remarkEmoji（性能）
  const remarkPlugins = streaming
    ? [remarkGfm, remarkMath]
    : [remarkGfm, remarkMath, remarkBreaks, remarkEmoji]

  // KaTeX 和 highlight 只在 static 模式启用（流式时跳过 rehype 插件集）
  const rehypePlugins = streaming
    ? []
    : [[rehypeSanitize, markdownSchema] as any, rehypeKatex, rehypeHighlight]

  return (
    <div className={`markdown-body${streaming ? ' markdown-streaming' : ''}${className ? ` ${className}` : ''}`}>
      <ReactMarkdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={rehypePlugins}
        components={markdownComponents}
        urlTransform={safeUrlTransform}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}
```

### 2.2 `src/components/Chat/MarkdownMessage.module.css`

Mermaid 图表容器和辅助样式：

```css
/* Mermaid 容器 */
.mermaid {
  margin: 0.8em 0;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-control);
  background: var(--surface-2);
  overflow-x: auto;
}
.mermaid svg {
  max-width: 100%;
  height: auto;
  display: block;
}
.mermaid-error {
  margin: 0.8em 0;
  padding: 11px 13px;
  border: 1px solid color-mix(in srgb, var(--red) 35%, var(--line));
  border-radius: var(--radius-control);
  background: var(--red-soft);
  color: var(--red);
  font-size: 0.75rem;
}

/* 表格横向滚动 */
.md-table-wrap {
  margin: 0.7em 0;
  overflow-x: auto;
  border: 1px solid var(--line);
  border-radius: var(--radius-control);
}
.md-table-wrap table {
  margin: 0;
  width: 100%;
  border-collapse: collapse;
}
.md-table-wrap th,
.md-table-wrap td {
  padding: 8px 12px;
  border: 1px solid var(--line);
  text-align: left;
  font-size: 0.8125rem;
}
.md-table-wrap th {
  background: var(--surface-2);
  font-weight: 700;
  color: var(--muted);
  font-size: 0.6875rem;
}

/* 代码块包裹 */
.code-block-wrapper {
  margin: 0.7em 0;
  border: 1px solid var(--line);
  border-radius: var(--radius-control);
  overflow: hidden;
}
.code-block-wrapper pre {
  margin: 0;
  padding: 13px 15px;
  border: 0;
  border-radius: 0;
  background: var(--surface-2);
  overflow-x: auto;
}
.code-block-wrapper code {
  font-family: var(--font-mono);
  font-size: 0.8125rem;
  line-height: 1.6;
}

/* highlight.js 默认主题微调（配合暗色 UI） */
.markdown-body .hljs {
  background: transparent;
}
```

---

## 三、修改现有文件

### 3.1 `src/pages/ChatPage.tsx`

**改动 1**：顶部 import 新增：

```tsx
import { MarkdownMessage } from '../components/Chat/MarkdownMessage'
```

替换原来的 `import ReactMarkdown from 'react-markdown'` 和 `import remarkGfm from 'remark-gfm'`（这两个不再需要，MarkdownMessage 内部已包含）。

**改动 2**：消息气泡渲染。找到：

```tsx
<div className="bubble"><ReactMarkdown remarkPlugins={[remarkGfm]}>...</ReactMarkdown></div>
```

替换为：

```tsx
<div className="bubble">
  <MarkdownMessage
    content={compactPlanAssistantText(item.content || (item.streaming ? '…' : ''), hasPlanBubble)}
    streaming={Boolean(item.streaming)}
  />
</div>
```

用户消息的气泡同理（如果有的话）——用户消息一般不需要 Markdown，但如果当前也用了 ReactMarkdown，同样替换。

---

## 四、CSS 全局引入

### 4.1 `src/main.tsx`（或入口 CSS）

确保 KaTeX CSS 已被引入。由于 `MarkdownMessage.tsx` 中已经 `import 'katex/dist/katex.min.css'`，Vite 会自动处理。

highlight.js 主题也在组件内引入。如果需要全局统一，可改为在 `main.tsx` 或 `src/styles/app.css` 中引入。

---

## 五、能力矩阵（最终状态）

| 能力 | 实现方式 |
|------|---------|
| 基础 Markdown | `react-markdown` |
| GFM（表格/删除线/任务列表） | `remark-gfm` |
| 数学公式 `$...$` `$$...$$` | `remark-math` + `rehype-katex` + `katex` |
| 单换行 → `<br>` | `remark-breaks`（非流式） |
| HTML 净化 | `rehype-sanitize` + 自定义 schema |
| 代码语法高亮 | `rehype-highlight` + `highlight.js` |
| Mermaid 图表 ` ```mermaid ` | `mermaid` + 自定义 `MermaidDiagram` 组件 |
| Emoji 短码 `:rocket:` | `remark-emoji`（非流式） |
| 外部链接 `target="_blank"` | `components.a` |
| 表格横向滚动 | `components.table` + `.md-table-wrap` |
| URL 安全 | `urlTransform` 拦截危险协议 |
| 任务列表只读 | `components.input` |
| 图片保留 | `components.img`（votx-agent 屏蔽，kemo 保留） |
| 流式优化 | 流式时去掉 `remarkBreaks` + `remarkEmoji` + 全部 rehype 插件 |

### 超越 votx-agent 的点

| 能力 | votx-agent | kemo-agent（改造后） |
|------|:---:|:---:|
| 语法高亮 | ❌ | ✅ `rehype-highlight` |
| Mermaid 图表 | ❌ | ✅ `mermaid` |
| Emoji 短码 | ❌ | ✅ `remark-emoji` |
| 图片渲染 | ❌ 屏蔽 | ✅ 保留（`loading="lazy"`） |
| 表格样式 | 仅横向滚动 | 横向滚动 + 表头样式 |

---

## 六、测试要点

1. **基础兼容**：现有消息（纯文本、代码块、列表、链接、表格）渲染无退化
2. **数学公式**：`$E=mc^2$` 和 `$$\int_0^1 x dx$$` 正确渲染 KaTeX
3. **语法高亮**：` ```python `、` ```tsx `、` ```json ` 等有颜色区分
4. **Mermaid**：` ```mermaid ` 代码块渲染为 SVG 图表（flowchart、sequence、class 等）
5. **Emoji**：`:smile:` → 😄，`:warning:` → ⚠️
6. **换行**：Markdown 中单个换行正确显示为 `<br>`（非流式）
7. **安全**：`[click](javascript:alert(1))` 链接被过滤
8. **流式**：流式输出时不抖动，不抛错（KaTeX/Mermaid/highlight 只在 static 渲染）
9. **任务列表**：`- [ ] item` 和 `- [x] item` 显示为只读 checkbox
10. **图片**：`![alt](url)` 正常显示，`loading="lazy"`
11. **表格**：宽表格可横向滚动，不撑破布局
12. **外部链接**：点击在新标签页打开

---

## 七、实施顺序

1. `npm install` 所有新依赖
2. 创建 `src/components/Chat/` 目录（如不存在）
3. 创建 `MarkdownMessage.tsx` + `MarkdownMessage.module.css`
4. 修改 `ChatPage.tsx`，用 `<MarkdownMessage>` 替换 `<ReactMarkdown>`
5. `npm run build` 验证无编译错误
6. 启动 dev server，逐项验证第六条测试要点
