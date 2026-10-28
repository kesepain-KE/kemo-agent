import { useEffect, useRef, useState } from 'react'
import { AlertCircle, Check, CheckCircle2, ChevronDown, Copy, LoaderCircle, Wrench } from 'lucide-react'
import type { ChatItem } from '../types/api'
import { copyText } from '../utils/clipboard'
import { ShellJobLink } from './workspace/ShellJobLink'
import { NativeMediaLinks } from './NativeMediaLinks'

const SENSITIVE_ARGUMENT_KEY = /(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|passwd|authorization|cookie|credential|private[_-]?key)/i

function redactToolArgument(value: unknown, key = ''): unknown {
  if (key && SENSITIVE_ARGUMENT_KEY.test(key)) return '••••'
  if (Array.isArray(value)) return value.map((item) => redactToolArgument(item))
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([childKey, childValue]) => [childKey, redactToolArgument(childValue, childKey)]))
  }
  return value
}

function compactArgumentValue(value: unknown) {
  if (typeof value === 'string') return value.replace(/\s+/g, ' ').trim() || '空字符串'
  if (value === null) return 'null'
  if (value === undefined) return '—'
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    return JSON.stringify(value).replace(/\s+/g, ' ')
  } catch {
    return String(value)
  }
}

export function toolArgumentSummary(persistedText: string | undefined, value: unknown) {
  let source = value
  const valueIsEmptyObject = source && typeof source === 'object' && !Array.isArray(source) && Object.keys(source as Record<string, unknown>).length === 0
  if ((source === undefined || valueIsEmptyObject) && persistedText?.trim()) {
    try {
      source = JSON.parse(persistedText)
    } catch {
      const summary = persistedText
        .replace(/\s+/g, ' ')
        .replace(/((?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|passwd|authorization|cookie|credential|private[_-]?key)\s*[:=]\s*)([^\s,;}]+)/gi, '$1••••')
        .trim()
      return summary || '无参数'
    }
  }
  const redacted = redactToolArgument(source ?? {})
  if (redacted && typeof redacted === 'object' && !Array.isArray(redacted)) {
    const entries = Object.entries(redacted as Record<string, unknown>)
    if (!entries.length) return '无参数'
    return entries.map(([key, entry]) => `${key}: ${compactArgumentValue(entry)}`).join(' · ')
  }
  return compactArgumentValue(redacted)
}

function toolPayloadPreview(
  persistedText: string | undefined,
  value: unknown,
  sourceTruncated = false,
) {
  const source = persistedText === undefined ? value : persistedText
  let rendered = formatToolPayload(source)
  rendered ||= ''
  return sourceTruncated
    ? `${rendered}\n\n… 源数据已达到上游限制，以上为当前可用内容`
    : rendered
}

function formatToolPayload(value: unknown): string {
  if (typeof value === 'string') {
    const trimmed = value.trim()
    const parsed = parseNestedToolJson(trimmed)
    if (parsed !== undefined) return formatToolValue(parsed)
    return value
  }
  return formatToolValue(value)
}

function parseNestedToolJson(value: string): unknown | undefined {
  if (!value || !/^[\[{]/.test(value)) return undefined
  try {
    return JSON.parse(value)
  } catch {
    return undefined
  }
}

function formatToolValue(value: unknown, depth = 0): string {
  if (value === null) return 'null'
  if (value === undefined) return 'undefined'
  if (typeof value === 'string') {
    const nested = parseNestedToolJson(value.trim())
    if (nested !== undefined) return formatToolValue(nested, depth)
    if (value.includes('\n')) {
      const continuationIndent = '  '.repeat(depth)
      return `"${escapeToolString(value, true).replace(/\n/g, `\n${continuationIndent}`)}"`
    }
    return JSON.stringify(value)
  }
  if (typeof value !== 'object') return JSON.stringify(value)

  const indent = '  '.repeat(depth)
  const childIndent = '  '.repeat(depth + 1)
  if (Array.isArray(value)) {
    if (!value.length) return '[]'
    return `[\n${value.map((item) => `${childIndent}${formatToolValue(item, depth + 1)}`).join(',\n')}\n${indent}]`
  }

  const entries = Object.entries(value as Record<string, unknown>)
  if (!entries.length) return '{}'
  return `{\n${entries.map(([key, item]) => {
    const formatted = formatToolValue(item, depth + 1)
    return `${childIndent}${JSON.stringify(key)}: ${formatted}`
  }).join(',\n')}\n${indent}}`
}

function escapeToolString(value: string, multiline = false) {
  return value
    .replace(/\\/g, '\\\\')
    .replace(/"/g, '\\"')
    .replace(/\r\n?/g, '\n')
    .replace(/\n/g, multiline ? '\n' : '\\n')
    .replace(/\t/g, multiline ? '\t' : '\\t')
}

export function ReasoningTrace({ item }: { item: Extract<ChatItem, { kind: 'reasoning' }> }) {
  const [open, setOpen] = useState(item.streaming)
  const [copied, setCopied] = useState(false)
  const wasStreaming = useRef(item.streaming)
  const bodyRef = useRef<HTMLDivElement | null>(null)
  const followBodyRef = useRef(true)

  useEffect(() => {
    if (wasStreaming.current && !item.streaming) setOpen(false)
    if (!wasStreaming.current && item.streaming) setOpen(true)
    wasStreaming.current = item.streaming
  }, [item.streaming])

  useEffect(() => {
    const body = bodyRef.current
    if (!body || !open || !item.streaming || !followBodyRef.current) return
    const frame = window.requestAnimationFrame(() => {
      body.scrollTop = body.scrollHeight
    })
    return () => window.cancelAnimationFrame(frame)
  }, [item.content, item.streaming, open])

  const copy = async () => {
    await copyText(item.content)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1200)
  }
  const toggle = () => setOpen((value) => {
    if (!value) followBodyRef.current = true
    return !value
  })
  return (
    <article className={`trace ${item.streaming ? 'streaming' : 'complete'} ${open ? 'open' : ''}`}>
      <button className="trace-head" onClick={toggle} aria-expanded={open}>
        <span>{item.streaming ? '正在思考' : '思考过程'}</span><ChevronDown size={15} />
      </button>
      <div
        className="trace-body"
        ref={bodyRef}
        onScroll={(event) => {
          const element = event.currentTarget
          followBodyRef.current = element.scrollHeight - element.scrollTop - element.clientHeight <= 32
        }}
      >
        <pre>{item.content || '等待内容…'}</pre>
        <div className="trace-body-actions">
          <button className="event-copy-btn" onClick={() => void copy()} disabled={!item.content} aria-label="复制思考过程">{copied ? <Check size={13} /> : <Copy size={13} />}{copied ? '已复制' : '复制'}</button>
        </div>
      </div>
    </article>
  )
}

export function ToolCallCard({ item, user = '', sessionId = '' }: { item: Extract<ChatItem, { kind: 'tool' }>; user?: string; sessionId?: string }) {
  const [open, setOpen] = useState(false)
  const [liveElapsedMs, setLiveElapsedMs] = useState(0)
  const elapsedStartedAt = useRef(Date.now())

  useEffect(() => {
    if (item.status !== 'running') return
    elapsedStartedAt.current = Date.now()
    setLiveElapsedMs(0)
    const timer = window.setInterval(() => {
      setLiveElapsedMs(Date.now() - elapsedStartedAt.current)
    }, 100)
    return () => window.clearInterval(timer)
  }, [item.status])

  const body = open ? {
    arguments: toolPayloadPreview(item.argumentsText, item.arguments ?? {}, item.argumentsTruncated),
    result: item.status === 'running'
      ? null
      : item.result === undefined && item.resultText === undefined
        ? '工具未返回结果'
      : toolPayloadPreview(item.resultText, item.result, item.resultTruncated),
  } : null
  const statusLabel = item.status === 'running' ? '运行中' : item.status === 'success' ? '已完成' : '失败'
  const argumentSummary = toolArgumentSummary(item.argumentsText, item.arguments)
  const elapsed = item.status === 'running'
    ? `${(liveElapsedMs / 1000).toFixed(1)} s`
    : elapsedLabel(item.elapsedMs ?? liveElapsedMs)
  return (
    <article className={`tool-call ${open ? 'open' : ''}`}>
      <button className="tool-call-head" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        <span className="tool-call-icon"><Wrench size={15} /></span>
        <span className="tool-call-copy"><strong>{item.name || '未知工具'}</strong></span>
        <span className="tool-call-summary" title={argumentSummary}>{argumentSummary}</span>
        <span className="tool-call-meta">
          <span className="tool-elapsed">{elapsed}</span>
          <ChevronDown className="tool-call-chevron" size={15} />
          <span className={`tool-call-status ${item.status}`} aria-label={statusLabel} title={statusLabel}>
            {item.status === 'running' && <LoaderCircle className="spin tool-running-spinner" size={16} />}
            {item.status === 'success' && <CheckCircle2 size={17} />}
            {item.status === 'error' && <AlertCircle size={17} />}
          </span>
        </span>
      </button>
      <ShellJobLink user={user} sessionId={sessionId} name={item.name} result={item.result} resultText={item.resultText} />
      <NativeMediaLinks user={user} item={item} />
      {body && <div className="tool-call-body">
        <div className="tool-call-grid">
          <div className="tool-call-panel"><label>输入参数</label><pre>{body.arguments}</pre></div>
          <div className={`tool-call-panel ${body.result === null ? 'waiting' : ''}`}>
            <label>返回结果</label>
            {body.result === null
              ? <div className="tool-result-waiting"><LoaderCircle className="spin tool-running-spinner" size={14} />工具仍在运行，完成后显示结果</div>
              : <pre>{body.result}</pre>}
          </div>
        </div>
      </div>}
    </article>
  )
}

function metric(value: unknown) {
  const number = Number(value)
  return Number.isFinite(number) ? number : 0
}

function elapsedLabel(value: number | undefined) {
  if (value === undefined) return '—'
  if (value >= 60_000) return `${(value / 60_000).toFixed(1)} min`
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)} s`
  return `${value} ms`
}

export function UsageCard({ item }: { item: Extract<ChatItem, { kind: 'usage' }> }) {
  const prompt = metric(item.usage.prompt_tokens)
  const completion = metric(item.usage.completion_tokens)
  const cached = item.usage.cached_prompt_tokens == null ? undefined : metric(item.usage.cached_prompt_tokens)
  const hitRate = item.usage.cache_hit_rate == null ? undefined : metric(item.usage.cache_hit_rate)
  return <article className="usage-card" role="group" aria-label={`${item.round ? `第 ${item.round} 轮` : '本轮'}运行统计`}>
    <div className="usage-card-head">
      <span className="usage-summary-chip"><small>输入</small><strong>{prompt.toLocaleString()}</strong></span>
      <span className="usage-summary-chip"><small>输出</small><strong>{completion.toLocaleString()}</strong></span>
      <span className="usage-summary-chip"><small>缓存</small><strong>{cached === undefined ? '—' : cached.toLocaleString()}</strong></span>
      <span className="usage-summary-chip"><small>命中率</small><strong>{hitRate === undefined ? '—' : `${(hitRate * 100).toFixed(1)}%`}</strong></span>
      {item.providerRequestCount !== undefined ? <span className="usage-summary-chip" title="本轮所有模型请求的累计计量"><small>请求</small><strong>{item.providerRequestCount}</strong></span> : null}
      <span className="usage-summary-chip"><small>耗时</small><strong>{elapsedLabel(item.elapsedMs)}</strong></span>
    </div>
  </article>
}
