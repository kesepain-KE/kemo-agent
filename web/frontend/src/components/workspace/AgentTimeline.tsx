import { AlertCircle, CheckCircle2, ChevronDown, CircleHelp, CircleSlash, LoaderCircle, Wrench } from 'lucide-react'
import type { AgentTaskEvent } from '../../api/workspace'
import { t } from '../../i18n'
import { formatDateTime } from '../ModuleUi'
import { toolArgumentSummary } from '../RunEventCards'
import styles from './AgentTaskDetail.module.css'

/** Only pair a verified call identity, never two same-name calls heuristically. */
export function timelineRows(events: AgentTaskEvent[]) {
  const rows: (AgentTaskEvent & { key: string })[] = []
  const tools = new Map<string, number>()
  events.forEach((event, index) => {
    // Presentation only: retain the original event stream and backend records.
    if (event.status === 'model_request') return
    const tool = event.tool_name && ['tool_running', 'tool_finished'].includes(event.status)
    const existing = tool && event.tool_call_id ? tools.get(event.tool_call_id) : undefined
    if (existing !== undefined) {
      const previous = rows[existing]
      rows[existing] = { ...previous, ...event, at: previous.at, key: previous.key }
    } else {
      if (tool && event.tool_call_id) tools.set(event.tool_call_id, rows.length)
      rows.push({ ...event, key: tool && event.tool_call_id
        ? `tool:${event.tool_call_id}` : `${event.at}:${event.status}:${index}` })
    }
  })
  return rows
}

function DetailValue({ value }: { value: unknown }) {
  return <pre className={styles.toolValue}>{typeof value === 'string' ? value : JSON.stringify(value, null, 2)}</pre>
}

function elapsedLabel(ms: number) {
  if (ms >= 60_000) return `${(ms / 60_000).toFixed(1)} min`
  if (ms >= 1_000) return `${(ms / 1_000).toFixed(1)} s`
  return `${ms.toLocaleString()} ms`
}

function ToolState({ event, label }: { event: AgentTaskEvent; label: string }) {
  const state = event.tool_status || ''
  const running = event.status === 'tool_running'
  const success = ['completed', 'success', 'duplicate_reused'].includes(state)
  const failed = ['failed', 'error', 'timed_out', 'timed_out_running', 'tool_limit', 'result_too_large',
    'temporarily_unavailable', 'retry_reuse_blocked', 'recovery_blocked'].includes(state)
  const Icon = running ? LoaderCircle : success ? CheckCircle2 : failed ? AlertCircle
    : ['cancelled', 'interrupted'].includes(state) ? CircleSlash : CircleHelp
  return <span className={styles.actionStatus} data-state={running ? 'running' : success ? 'success' : failed ? 'error' : 'unknown'}
    role="img" aria-label={label} title={label}>
    <Icon size={17} className={running ? 'spin' : undefined} aria-hidden="true" />
  </span>
}

export function AgentTimeline({ events, statusLabel }: {
  events: AgentTaskEvent[]; statusLabel: (status: string) => string
}) {
  return <ol className={styles.timeline}>
    {timelineRows(events).map(event => {
      const label = event.tool_name || statusLabel(event.status)
      const status = statusLabel(event.tool_status || event.status)
      const emptyArguments = event.arguments != null && typeof event.arguments === 'object'
        && !Array.isArray(event.arguments) && Object.keys(event.arguments).length === 0
      const argumentSummary = event.arguments === undefined ? '' : emptyArguments ? t('无参数')
        : toolArgumentSummary(undefined, event.arguments).slice(0, 512)
      return <li className={styles.timelineRow} key={event.key}>
        <time dateTime={event.at}>{formatDateTime(event.at)}</time>
        {event.tool_name ? <details className={styles.actionCard}>
          <summary aria-label={t('查看工具详情 {name}', { name: event.tool_name })}>
            <Wrench size={15} aria-hidden="true" /><strong title={label}>{label}</strong>
            <span className={styles.argumentSummary} title={argumentSummary}>{argumentSummary}</span>
            <span className={styles.actionMeta}>
              {event.elapsed_ms != null && <span className={styles.elapsed}>{elapsedLabel(event.elapsed_ms)}</span>}
              <ChevronDown className={styles.chevron} size={15} aria-hidden="true" />
              <ToolState event={event} label={status} />
            </span>
          </summary>
          <div className={styles.actionBody}>
            <h5>{t('调用参数')}</h5>
            {event.arguments !== undefined ? <DetailValue value={event.arguments} /> : <p>{t('此历史事件未保存调用参数')}</p>}
            <h5>{t('返回结果')}</h5>
            {event.result !== undefined ? <DetailValue value={event.result} /> : <p>{t(event.status === 'tool_running' ? '等待工具返回…' : '此历史事件未保存返回结果')}</p>}
          </div>
        </details> : <div className={styles.phaseCard}>{label}</div>}
      </li>
    })}
  </ol>
}
