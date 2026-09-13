import { Bot, LoaderCircle } from 'lucide-react'
import type { ChatItem } from '../types/api'
import { ToolCallCard } from './RunEventCards'
import styles from './SubagentProgressBubble.module.css'

function roundLabel(item: Extract<ChatItem, { kind: 'subagent_progress' }>) {
  return `${Math.max(1, item.iteration)}: ${item.toolName}`
}

export function SubagentProgressBubble({ items }: { items: ChatItem[] }) {
  const active = items
    .filter((item): item is Extract<ChatItem, { kind: 'subagent_progress' }> => item.kind === 'subagent_progress' && Boolean(item.toolName))
    .slice()
    .sort((left, right) => left.iteration - right.iteration)
  if (!active.length) return null
  return <section className={styles.stack} aria-label="子代理运行进度" aria-live="polite" aria-atomic="true">
    {active.map((item, index) => {
      const label = roundLabel(item)
      const latest = index === active.length - 1
      return <article key={item.id} className={styles.bubble}>
      <Bot className={styles.icon} aria-hidden="true" />
      <span className={styles.round} title={label}>{label}</span>
      {latest ? <LoaderCircle className={styles.spinner} aria-hidden="true" /> : <span className={styles.spinnerPlaceholder} aria-hidden="true" />}
    </article>
    })}
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
