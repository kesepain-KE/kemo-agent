import { t as tr, useLanguage } from '../../i18n'
import { type HTMLAttributes, type ReactNode, memo, useEffect, useMemo, useRef, useState } from 'react'
import { Check, Copy, TriangleAlert } from 'lucide-react'
import ReactMarkdown, { type Components, type UrlTransform } from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'
import rehypeKatex from 'rehype-katex'
import type { Schema } from 'hast-util-sanitize'
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize'
import remarkBreaks from 'remark-breaks'
import remarkEmoji from 'remark-emoji'
import remarkGfm from '../../markdownLinks'
import remarkMath from 'remark-math'
import type { PluggableList } from 'unified'
import 'highlight.js/styles/github-dark.min.css'
import 'katex/dist/katex.min.css'
import { randomUUID } from '../../randomId'
import { InlineWidgetBlock, InlineWidgetPending } from './InlineWidget'
import { splitInlineWidgets, type InlineWidgetAction } from './inlineWidgetProtocol'
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

function sameOriginImageUrl(src: string): boolean {
  if (typeof window === 'undefined') return !EXTERNAL_URL_RE.test(src)
  try {
    const resolved = new URL(src, window.location.href)
    return ['http:', 'https:'].includes(resolved.protocol) && resolved.origin === window.location.origin
  } catch {
    return false
  }
}

export const safeUrlTransform: UrlTransform = (url) => {
  const value = url.trim()
  if (!value) return ''
  if (EXTERNAL_URL_RE.test(value)) return value
  const hasScheme = /^[a-z][a-z0-9+.-]*:/i.test(value)
  if (!hasScheme) return value
  return ''
}

function MermaidDiagram({ chart }: { chart: string }) {
  const renderId = useRef(`mermaid-${randomUUID()}`)
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

type MarkdownTreeNode = {
  type?: string
  value?: unknown
  children?: MarkdownTreeNode[]
}

function codeNodeText(node: unknown): string {
  if (!node || typeof node !== 'object') return ''
  const current = node as MarkdownTreeNode
  if (current.type === 'text') return typeof current.value === 'string' ? current.value : ''
  return Array.isArray(current.children) ? current.children.map(codeNodeText).join('') : ''
}

function legacyCopyText(text: string): boolean {
  if (typeof document.execCommand !== 'function') return false
  const textarea = document.createElement('textarea')
  const activeElement = document.activeElement instanceof HTMLElement ? document.activeElement : null
  textarea.value = text
  textarea.readOnly = true
  textarea.setAttribute('aria-hidden', 'true')
  textarea.style.position = 'fixed'
  textarea.style.inset = '0 auto auto 0'
  textarea.style.width = '1px'
  textarea.style.height = '1px'
  textarea.style.padding = '0'
  textarea.style.border = '0'
  textarea.style.opacity = '0'
  textarea.style.pointerEvents = 'none'
  document.body.appendChild(textarea)
  try {
    textarea.focus({ preventScroll: true })
    textarea.select()
    textarea.setSelectionRange(0, textarea.value.length)
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    textarea.remove()
    activeElement?.focus({ preventScroll: true })
  }
}

async function copyCodeText(text: string): Promise<void> {
  if (window.isSecureContext && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return
    } catch {
      // A secure-context clipboard call may still be denied by browser policy.
      // Fall through to the user-gesture-compatible legacy path.
    }
  }
  if (!legacyCopyText(text)) throw new Error('clipboard_unavailable')
}

type CopyState = 'idle' | 'copied' | 'error'

function CodeBlock({ code, children, preProps }: {
  code: string
  children: ReactNode
  preProps: HTMLAttributes<HTMLPreElement>
}) {
  useLanguage()
  const [copyState, setCopyState] = useState<CopyState>('idle')
  const resetTimer = useRef<number | null>(null)

  useEffect(() => {
    setCopyState('idle')
    return () => {
      if (resetTimer.current !== null) window.clearTimeout(resetTimer.current)
    }
  }, [code])

  const copy = () => {
    if (!code) return
    if (resetTimer.current !== null) window.clearTimeout(resetTimer.current)
    void copyCodeText(code)
      .then(() => setCopyState('copied'))
      .catch(() => setCopyState('error'))
      .finally(() => {
        resetTimer.current = window.setTimeout(() => setCopyState('idle'), 1600)
      })
  }

  const label = copyState === 'copied' ? tr('已复制') : copyState === 'error' ? tr('复制失败') : tr('复制')
  const Icon = copyState === 'copied' ? Check : copyState === 'error' ? TriangleAlert : Copy

  return (
    <div className={styles.codeBlockWrapper}>
      <button
        type="button"
        className={`${styles.copyButton} ${copyState !== 'idle' ? styles[copyState] : ''}`}
        onClick={copy}
        disabled={!code}
        aria-label={label}
        title={label}
      >
        <Icon size={14} aria-hidden="true" />
        <span aria-live="polite">{label}</span>
      </button>
      <pre {...preProps}>{children}</pre>
    </div>
  )
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
    const code = child?.type === 'element' ? codeNodeText(child).replace(/\n$/, '') : ''
    return <CodeBlock code={code} preProps={props}>{children}</CodeBlock>
  },
  img({ node: _node, src, alt, ...props }) {
    if (!src) return null
    if (!sameOriginImageUrl(src)) {
      return (
        <a href={src} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">
          {alt ? `外部图片：${alt}` : '查看外部图片'}
        </a>
      )
    }
    return <img {...props} src={src} alt={alt || ''} loading="lazy" referrerPolicy="no-referrer" />
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
  onWidgetAction?: (action: InlineWidgetAction) => void
}

const streamingRemarkPlugins: PluggableList = [[remarkGfm, { singleTilde: false }], remarkMath]
const staticRemarkPlugins: PluggableList = [...streamingRemarkPlugins, remarkBreaks, remarkEmoji]
const streamingRehypePlugins: PluggableList = []
const staticRehypePlugins: PluggableList = [[rehypeSanitize, markdownSchema], rehypeKatex, rehypeHighlight]

const MarkdownText = memo(function MarkdownText({ source, streaming }: { source: string; streaming: boolean }) {
  return <ReactMarkdown
    remarkPlugins={streaming ? streamingRemarkPlugins : staticRemarkPlugins}
    rehypePlugins={streaming ? streamingRehypePlugins : staticRehypePlugins}
    components={markdownComponents}
    urlTransform={safeUrlTransform}
  >{source}</ReactMarkdown>
})

// The parent keys this mounted memo boundary by message id. Its parsed tree and
// local widget state survive deltas appended to other messages in the same run.
export const MarkdownMessage = memo(function MarkdownMessage({ content, streaming = false, className = '', onWidgetAction }: MarkdownMessageProps) {
  const classes = [styles.markdownBody, 'markdown-body', streaming ? styles.streaming : '', className]
    .filter(Boolean)
    .join(' ')
  const segments = useMemo(() => splitInlineWidgets(content), [content])

  return (
    <div className={classes}>
      {segments.map((segment) => segment.type === 'widget'
        ? segment.complete
          ? <InlineWidgetBlock key={segment.key} source={segment.source} blocked={segment.blocked} onAction={onWidgetAction} />
          : <InlineWidgetPending key={segment.key} interrupted={!streaming} />
        : segment.source
          ? (
            <MarkdownText
              key={segment.key}
              source={segment.source}
              streaming={streaming}
            />
          )
          : null)}
    </div>
  )
})
