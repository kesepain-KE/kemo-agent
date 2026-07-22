import { useEffect, useRef, useState } from 'react'
import { AlertCircle, Check, CheckCircle2, ChevronDown, Copy, LoaderCircle, Wrench } from 'lucide-react'
import type { ChatItem } from '../types/api'
import { copyText } from '../utils/clipboard'

const TOOL_TEXT_LIMIT = 5000

function toolPayloadPreview(
  persistedText: string | undefined,
  value: unknown,
  sourceTruncated = false,
) {
  let rendered = persistedText
  if (rendered === undefined) {
    if (typeof value === 'string') rendered = value
    else {
      try {
        rendered = JSON.stringify(value, null, 2)
      } catch {
        rendered = String(value)
      }
    }
  }
  rendered ||= ''
  const truncated = sourceTruncated || rendered.length > TOOL_TEXT_LIMIT
  const preview = rendered.slice(0, TOOL_TEXT_LIMIT)
  return truncated ? `${preview}\n\n… 已截断，仅显示前 5000 个字符` : preview
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

export function ToolCallCard({ item }: { item: Extract<ChatItem, { kind: 'tool' }> }) {
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
  const elapsed = item.status === 'running'
    ? `${(liveElapsedMs / 1000).toFixed(1)} s`
    : elapsedLabel(item.elapsedMs ?? liveElapsedMs)
  return (
    <article className={`tool-call ${open ? 'open' : ''}`}>
      <button className="tool-call-head" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        <span className="tool-call-icon"><Wrench size={15} /></span>
        <span className="tool-call-copy"><strong>{item.name || '未知工具'}</strong><span>{item.callId}</span></span>
        <span className="tool-call-meta">
          <span className="tool-elapsed">{elapsed}</span>
          <ChevronDown className="tool-call-chevron" size={15} />
          <span className={`tool-call-status ${item.status}`} aria-label={statusLabel} title={statusLabel}>
            {item.status === 'running' && <LoaderCircle className="spin" size={16} />}
            {item.status === 'success' && <CheckCircle2 size={17} />}
            {item.status === 'error' && <AlertCircle size={17} />}
          </span>
        </span>
      </button>
      {body && <div className="tool-call-body">
        <div className="tool-call-grid">
          <div className="tool-call-panel"><label>输入参数</label><pre>{body.arguments}</pre></div>
          <div className={`tool-call-panel ${body.result === null ? 'waiting' : ''}`}>
            <label>返回结果</label>
            {body.result === null
              ? <div className="tool-result-waiting"><LoaderCircle className="spin" size={14} />工具仍在运行，完成后显示结果</div>
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
