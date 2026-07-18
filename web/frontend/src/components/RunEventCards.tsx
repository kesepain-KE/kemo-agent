import { useMemo, useState } from 'react'
import { AlertCircle, Check, CheckCircle2, ChevronDown, Copy, Gauge, LoaderCircle, Wrench } from 'lucide-react'
import type { ChatItem } from '../types/api'
import { copyText } from '../utils/clipboard'

export function ReasoningTrace({ item }: { item: Extract<ChatItem, { kind: 'reasoning' }> }) {
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    await copyText(item.content)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1200)
  }
  return (
    <article className={`trace ${open ? 'open' : ''}`}>
      <button className="trace-head" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        <span>{item.streaming ? '正在思考' : '思考过程'}</span><ChevronDown size={15} />
      </button>
      <button className="event-copy-btn" onClick={() => void copy()} disabled={!item.content} aria-label="复制思考过程">{copied ? <Check size={13} /> : <Copy size={13} />}{copied ? '已复制' : '复制'}</button>
      <div className="trace-body"><pre>{item.content || '等待内容…'}</pre></div>
    </article>
  )
}

export function ToolCallCard({ item }: { item: Extract<ChatItem, { kind: 'tool' }> }) {
  const [open, setOpen] = useState(item.status === 'running')
  const result = useMemo(() => {
    if (item.result === undefined) return '等待工具返回…'
    return typeof item.result === 'string' ? item.result : JSON.stringify(item.result, null, 2)
  }, [item.result])
  return (
    <article className={`tool-call ${open ? 'open' : ''}`}>
      <button className="tool-call-head" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        <span className="tool-call-icon"><Wrench size={15} /></span>
        <span className="tool-call-copy"><strong>{item.name || '未知工具'}</strong><span>{item.callId}</span></span>
        <span className={`tool-call-status ${item.status}`}>
          {item.status === 'running' && <LoaderCircle className="spin" size={14} />}
          {item.status === 'success' && <CheckCircle2 size={14} />}
          {item.status === 'error' && <AlertCircle size={14} />}
          {item.status === 'running' ? '运行中' : item.status === 'success' ? '已完成' : '失败'}
        </span>
        {item.elapsedMs !== undefined ? <span className="tool-elapsed">{item.elapsedMs} ms</span> : null}
        <ChevronDown className="tool-call-chevron" size={15} />
      </button>
      <div className="tool-call-body">
        <div className="tool-call-grid">
          <div className="tool-call-panel"><label>输入参数</label><pre>{JSON.stringify(item.arguments ?? {}, null, 2)}</pre></div>
          <div className="tool-call-panel"><label>返回结果</label><pre>{result}</pre></div>
        </div>
      </div>
    </article>
  )
}

function metric(value: unknown) {
  const number = Number(value)
  return Number.isFinite(number) ? number : 0
}

export function UsageCard({ item }: { item: Extract<ChatItem, { kind: 'usage' }> }) {
  const [open, setOpen] = useState(false)
  const prompt = metric(item.usage.prompt_tokens)
  const completion = metric(item.usage.completion_tokens)
  const total = metric(item.usage.total_tokens)
  const cached = item.usage.cached_prompt_tokens === undefined ? undefined : metric(item.usage.cached_prompt_tokens)
  const hitRate = item.usage.cache_hit_rate === undefined ? undefined : metric(item.usage.cache_hit_rate)
  return <article className={`usage-card ${open ? 'open' : ''}`}>
    <button className="usage-card-head" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
      <span><Gauge size={14} /><strong>{item.round ? `第 ${item.round} 轮` : '本轮'}运行统计</strong></span>
      <span>{total.toLocaleString()} Token · {item.elapsedMs ?? '—'} ms</span><ChevronDown size={14} />
    </button>
    <div className="usage-card-body">
      <span><small>输入</small><strong>{prompt.toLocaleString()}</strong></span>
      <span><small>输出</small><strong>{completion.toLocaleString()}</strong></span>
      <span><small>缓存命中</small><strong>{cached === undefined ? '上游未提供' : cached.toLocaleString()}</strong></span>
      <span><small>命中率</small><strong>{hitRate === undefined ? '上游未提供' : `${(hitRate * 100).toFixed(1)}%`}</strong></span>
      <span><small>工具调用</small><strong>{item.toolCalls ?? '—'}</strong></span>
      <span><small>统计来源</small><strong>{String(item.usage.source || (item.usage.estimated ? '本地估算' : 'Provider'))}</strong></span>
    </div>
  </article>
}
