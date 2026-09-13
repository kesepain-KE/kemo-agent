import type { ChatItem, RunEvent } from '../types/api'

const terminalStatuses = new Set(['completed', 'completed_after_timeout', 'completed_after_detach', 'failed', 'cancelled', 'timed_out'])
const liveStatuses = new Set(['queued', 'started', 'running', 'model_request', 'tool_running', 'validating', 'retrying', 'timed_out_running'])

export function reduceSubagentProgress(items: ChatItem[], event: RunEvent): ChatItem[] {
  if (['done', 'error', 'long_task_update', 'retrying'].includes(event.type)) {
    if (event.type === 'error' && event.metadata?.retryable === true && event.metadata?.committed === false) return items
    return items.filter((item) => item.kind !== 'subagent_progress')
  }
  const callId = event.tool_call_id || String(event.metadata?.task_id || '')
  if (!callId) return items
  const remove = () => items.filter((item) => item.kind !== 'subagent_progress' || item.callId !== callId)
  if (event.type === 'tool_call_result') {
    const envelope = event.result as { result?: { status?: string }; status?: string } | undefined
    const status = envelope?.result?.status || envelope?.status || ''
    return liveStatuses.has(status) ? items : remove()
  }
  const start = event.type === 'tool_call_start' && event.tool_name === 'subagent_dispatch' && event.arguments?.action === 'call'
  if (!start && event.type !== 'subagent_progress') return items
  const status = start ? 'started' : String(event.metadata?.status || '')
  if (terminalStatuses.has(status)) return remove()
  if (!liveStatuses.has(status)) return items
  const progress = items.filter((item): item is Extract<ChatItem, { kind: 'subagent_progress' }> => item.kind === 'subagent_progress' && item.callId === callId)
  const previous = progress.at(-1)
  const count = (key: string, fallback = 0) => {
    const value = event.metadata?.[key]
    return typeof value === 'number' && Number.isFinite(value) ? Math.max(0, Math.floor(value)) : fallback
  }

  const record = (id: string, iteration: number, toolName: string): ChatItem => ({
    id, kind: 'subagent_progress', callId,
    agent: String(start ? event.arguments?.agent || '子代理' : event.metadata?.agent || previous?.agent || '子代理'),
    status, iteration, toolName,
    toolCount: count('tool_count', previous?.toolCount), nextAttempt: count('next_attempt'),
  })

  // Keep an invisible pending record until the subagent reports an actual tool.
  // The UI deliberately renders only tool-name rows, so it never exposes dispatch
  // arguments or intermediary status text before a numbered tool row.
  if (start) {
    return progress.length ? items : [...items, record(`subagent_progress_${callId}`, 0, '')]
  }

  if (status !== 'tool_running') {
    return progress.length ? items : [...items, record(`subagent_progress_${callId}`, 0, '')]
  }

  const iteration = Math.max(1, count('iteration', previous?.iteration || 1))
  const toolName = String(event.metadata?.tool_name || '工具')
  const existingRound = progress.find((item) => item.iteration === iteration && item.toolName)
  const pending = progress.find((item) => !item.toolName)
  const next = record(existingRound?.id || pending?.id || `subagent_progress_${callId}_${iteration}`, iteration, toolName)

  if (existingRound || pending) {
    const replaceId = existingRound?.id || pending!.id
    return items.map((item) => item.id === replaceId ? next : item)
  }
  return [...items, next]
}
