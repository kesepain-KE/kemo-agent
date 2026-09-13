import { Bot, LoaderCircle } from 'lucide-react'
import type { ChatItem } from '../types/api'
import { ToolCallCard } from './RunEventCards'
import styles from './SubagentProgressBubble.module.css'

const agentLabels: Record<string, string> = {
  context_manage: '上下文整理', history_summary: '历史摘要', self_improve: '记忆整理',
  memory_temporary_important: '重要记忆整理', task_plan: '任务规划', time_plan: '定时规划',
}

function stage(item: Extract<ChatItem, { kind: 'subagent_progress' }>) {
  switch (item.status) {
    case 'queued': return '正在排队'
    case 'model_request': return `第 ${item.iteration || 1} 轮 · 正在请求模型`
    case 'tool_running': return `第 ${item.iteration || 1} 轮 · 正在调用 ${item.toolName || '工具'}`
    case 'validating': return '正在校验结果'
    case 'retrying': return `正在重试${item.nextAttempt ? ` · 第 ${item.nextAttempt} 次尝试` : ''}`
    case 'timed_out_running': return '等待超时，后台仍在运行'
    case 'running': return '正在执行'
    default: return '正在准备'
  }
}

export function SubagentProgressBubble({ items }: { items: ChatItem[] }) {
  const active = items.filter((item): item is Extract<ChatItem, { kind: 'subagent_progress' }> => item.kind === 'subagent_progress')
  if (!active.length) return null
  return <section className={styles.stack} aria-label="子代理运行进度" aria-live="polite" aria-atomic="true">
    {active.map((item) => <article key={item.id} className={styles.bubble}>
      <Bot className={styles.icon} aria-hidden="true" />
      <div className={styles.copy}>
        <strong>{agentLabels[item.agent] || item.agent}<span>子代理</span></strong>
        <p>{stage(item)}</p>
      </div>
      <LoaderCircle className={styles.spinner} aria-hidden="true" />
    </article>)}
  </section>
}

export function SubagentToolCard({ item, progress, running }: {
  item: Extract<ChatItem, { kind: 'tool' }>
  progress: ChatItem[]
  running: boolean
}) {
  const matching = running ? progress.filter((value) => value.kind === 'subagent_progress' && value.callId === item.callId) : []
  return <div className={styles.anchor} data-subagent-call-id={item.callId}>
    <ToolCallCard item={item} />
    <SubagentProgressBubble items={matching} />
  </div>
}
