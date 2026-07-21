import { useEffect, useRef, useState } from 'react'
import ReactMarkdown, { type Components, type UrlTransform } from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'
import rehypeKatex from 'rehype-katex'
import type { Schema } from 'hast-util-sanitize'
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize'
import remarkBreaks from 'remark-breaks'
import remarkEmoji from 'remark-emoji'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import type { PluggableList } from 'unified'
import 'highlight.js/styles/github-dark.min.css'
import 'katex/dist/katex.min.css'
import styles from './MarkdownMessage.module.css'

let mermaidReady = false
let mermaidModule: Promise<typeof import('mermaid')> | null = null

async function getMermaid() {
  mermaidModule ||= import('mermaid')
  const instance = (await mermaidModule).default
  if (!mermaidReady) {
    instance.initialize({
      startOnLoad: false,
      theme: 'dark',
      securityLevel: 'sandbox',
    })
    mermaidReady = true
  }
  return instance
}

const markdownSchema: Schema = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    code: [
      ...(defaultSchema.attributes?.code || []),
      ['className', /^language-./, 'math-inline', 'math-display'],
    ],
    span: [
      ...(defaultSchema.attributes?.span || []),
      ['className', /^hljs-./, 'katex', 'katex-html'],
    ],
    div: [
      ...(defaultSchema.attributes?.div || []),
      ['className', 'mermaid', 'md-table-wrap'],
    ],
  },
}

const EXTERNAL_URL_RE = /^(https?:|mailto:|\/\/)/i

export const safeUrlTransform: UrlTransform = (url) => {
  const value = url.trim()
  if (!value) return ''
  if (EXTERNAL_URL_RE.test(value)) return value
  const hasScheme = /^[a-z][a-z0-9+.-]*:/i.test(value)
  if (!hasScheme) return value
  return ''
}

function MermaidDiagram({ chart }: { chart: string }) {
  const renderId = useRef(`mermaid-${crypto.randomUUID()}`)
  const [svg, setSvg] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    setSvg('')
    setError('')
    void getMermaid()
      .then((instance) => instance.render(renderId.current, chart))
      .then((result) => {
        if (active) setSvg(result.svg)
      })
      .catch((reason: unknown) => {
        if (!active) return
        setError(reason instanceof Error ? reason.message : String(reason))
      })
    return () => {
      active = false
    }
  }, [chart])

  if (error) {
    return <pre className={styles.mermaidError}>Mermaid 渲染失败：{error}</pre>
  }
  if (!svg) {
    return <div className={styles.mermaidLoading}>正在渲染图表…</div>
  }
  return <div className={styles.mermaid} dangerouslySetInnerHTML={{ __html: svg }} />
}

const markdownComponents: Components = {
  a({ node: _node, href, children, ...props }) {
    if (!href) return <span>{children}</span>
    const external = EXTERNAL_URL_RE.test(href)
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
  table({ node: _node, children, ...props }) {
    return (
      <div className={styles.tableWrap}>
        <table {...props}>{children}</table>
      </div>
    )
  },
  pre({ node, children, ...props }) {
    const child = node?.children?.[0]
    if (child?.type === 'element' && child.tagName === 'code') {
      const classes = Array.isArray(child.properties?.className)
        ? child.properties.className.map(String)
        : []
      if (classes.includes('language-mermaid')) {
        const textChild = child.children?.[0]
        const chart = textChild?.type === 'text' ? textChild.value : ''
        return <MermaidDiagram chart={chart} />
      }
    }
    return (
      <div className={styles.codeBlockWrapper}>
        <pre {...props}>{children}</pre>
      </div>
    )
  },
  img({ node: _node, src, alt, ...props }) {
    if (!src) return null
    return <img {...props} src={src} alt={alt || ''} loading="lazy" />
  },
  input({ node: _node, type, checked, ...props }) {
    if (type === 'checkbox') {
      return <input {...props} type="checkbox" checked={Boolean(checked)} disabled readOnly />
    }
    return <input {...props} type={type} />
  },
}

export interface MarkdownMessageProps {
  content: string
  streaming?: boolean
  className?: string
}

export function MarkdownMessage({ content, streaming = false, className = '' }: MarkdownMessageProps) {
  const remarkPlugins = streaming
    ? [remarkGfm, remarkMath]
    : [remarkGfm, remarkMath, remarkBreaks, remarkEmoji]
  const rehypePlugins: PluggableList = streaming
    ? []
    : [[rehypeSanitize, markdownSchema], rehypeKatex, rehypeHighlight]
  const classes = [styles.markdownBody, 'markdown-body', streaming ? styles.streaming : '', className]
    .filter(Boolean)
    .join(' ')

  return (
    <div className={classes}>
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
