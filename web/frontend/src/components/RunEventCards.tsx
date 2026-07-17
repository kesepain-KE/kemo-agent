import { useMemo, useState } from 'react'
import { AlertCircle, CheckCircle2, ChevronDown, LoaderCircle, Wrench } from 'lucide-react'
import type { ChatItem } from '../types/api'

export function ReasoningTrace({ item }: { item: Extract<ChatItem, { kind: 'reasoning' }> }) {
  const [open, setOpen] = useState(false)
  return (
    <article className={`trace ${open ? 'open' : ''}`}>
      <button className="trace-head" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        <span>{item.streaming ? '正在思考' : '思考过程'}</span><ChevronDown size={15} />
      </button>
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
