import { Clock3, Square, Workflow } from 'lucide-react'
import type { LongTaskState } from '../types/api'
import styles from './LongTaskBubble.module.css'

function durationLabel(milliseconds: number) {
  const totalSeconds = Math.max(0, Math.floor(Number(milliseconds || 0) / 1000))
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  if (hours) return `${hours} 小时 ${minutes} 分 ${seconds} 秒`
  if (minutes) return `${minutes} 分 ${seconds} 秒`
  return `${seconds} 秒`
}

function numberValue(value: unknown) {
  const number = Number(value)
  return Number.isFinite(number) && number > 0 ? Math.floor(number) : 0
}

function tokenLabel(state: LongTaskState) {
  const total = numberValue(state.usage.total_tokens)
  if (total < 1000) return String(total)
  return `${(total / 1000).toFixed(total >= 100_000 ? 0 : 1)}K`
}

function statusLabel(status: string) {
  if (status === 'running') return '正在跨轮执行'
  if (status === 'pausing') return '本轮结束后暂停'
  if (status === 'paused') return '已暂停'
  if (status === 'cancelling') return '正在停止'
  if (status === 'failed') return '执行失败'
  if (status === 'interrupted') return '执行中断'
  return status
}

export function LongTaskBubble({ state, stopping = false, onCancel }: {
  state: LongTaskState
  stopping?: boolean
  onCancel: () => void
}) {
  return (
    <article className={`${styles.bubble} ${styles[state.status] || ''}`} aria-live="polite">
      <div className={styles.main}>
        <span className={styles.icon}><Workflow aria-hidden="true" /></span>
        <span className={styles.copy}>
          <span className={styles.heading}><strong>长任务</strong><i>{statusLabel(state.status)}</i></span>
          <span className={styles.prompt}>{state.original_prompt || '正在延续当前对话任务'}</span>
        </span>
      </div>
      <div className={styles.stats} aria-label="长任务总统计">
        <span><Clock3 aria-hidden="true" />{durationLabel(state.active_elapsed_ms)}</span>
        <span>{state.run_count} Run</span>
        <span>{state.continuation_count} 次续跑</span>
        <span>{state.total_tool_calls} 工具</span>
        <span>{state.total_provider_requests} 请求</span>
        <span>{tokenLabel(state)} Token</span>
      </div>
      {['running', 'pausing', 'paused', 'cancelling'].includes(state.status) ? (
        <button type="button" className={styles.stop} onClick={onCancel} disabled={stopping || state.status === 'cancelling'}>
          <Square aria-hidden="true" />
          {stopping || state.status === 'cancelling'
            ? '正在停止…'
            : state.status === 'paused'
              ? '结束长任务'
              : '停止长任务'}
        </button>
      ) : null}
    </article>
  )
}
