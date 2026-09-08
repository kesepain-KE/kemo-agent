import { taskPlanFromSummary } from '../components/TaskPlanBubble'
import type { UserMessageMarker } from '../components/UserMessageNavigator'
import { randomUUID } from '../randomId'
import type { ChatItem, HistoryResponse, InputAttachment, MediaArtifact, PlanSummary, RunEvent } from '../types/api'

const HISTORY_PAGE_SIZE = 20
const PLAN_EXECUTION_PROMPT_PREFIX = '【任务计划连续执行】'

function eventId(prefix: string) {
  return `${prefix}_${randomUUID()}`
}

function objectValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

export function extractPlanSummary(value: unknown): PlanSummary | null {
  let payload = objectValue(value)
  const wrapped = objectValue(payload?.result)
  if (payload?.ok === true && wrapped) payload = wrapped
  const raw = objectValue(payload?.plan) || (payload?.plan_id ? payload : null)
  if (!raw || typeof raw.plan_id !== 'string' || typeof raw.title !== 'string' || !Array.isArray(raw.steps)) return null
  const steps = raw.steps.map((value) => objectValue(value)).filter((step): step is Record<string, unknown> => Boolean(step)).map((step) => ({
    step_id: String(step.step_id || ''), title: String(step.title || ''), description: String(step.description || ''), status: String(step.status || 'pending'),
    depends_on: Array.isArray(step.depends_on) ? step.depends_on.map(String) : [], critical: Boolean(step.critical ?? true), tool_name: String(step.tool_name || ''), started_at: String(step.started_at || ''), finished_at: String(step.finished_at || ''),
  }))
  const completed = steps.filter((step) => step.status === 'completed' || step.status === 'skipped').length
  return { plan_id: raw.plan_id, title: raw.title, description: String(raw.description || ''), status: String(raw.status || 'pending'), auto_accept: Boolean(raw.auto_accept), reminder: String(raw.reminder || ''), source: String(raw.source || ''), session_id: String(raw.session_id || ''), current_step: String(raw.current_step || ''), revision: Number(raw.revision || 1), created_at: String(raw.created_at || ''), updated_at: String(raw.updated_at || ''), progress: { completed, total: steps.length, percent: steps.length ? Math.round(completed * 100 / steps.length) : 0 }, steps }
}


export function isNearScrollBottom(
  metrics: Pick<HTMLElement, 'scrollHeight' | 'scrollTop' | 'clientHeight'>,
  threshold = 96,
) {
  return metrics.scrollHeight - metrics.scrollTop - metrics.clientHeight <= threshold
}

export type ConversationBlock =
  | { id: string; kind: 'user'; item: Extract<ChatItem, { kind: 'message' }> }
  | { id: string; kind: 'assistant'; items: ChatItem[] }

export function groupConversationItems(items: ChatItem[]): ConversationBlock[] {
  const blocks: ConversationBlock[] = []
  let activeAssistant: Extract<ConversationBlock, { kind: 'assistant' }> | null = null
  let currentUserId = 'opening'
  let assistantSequence = 0

  const flushAssistant = () => {
    if (!activeAssistant?.items.length) return
    blocks.push(activeAssistant)
    activeAssistant = null
  }

  for (const item of items) {
    if (item.kind === 'retry_boundary') {
      if (!activeAssistant) {
        assistantSequence += 1
        activeAssistant = {
          id: `assistant_turn_${currentUserId}_${assistantSequence}`,
          kind: 'assistant',
          items: [],
        }
      }
      activeAssistant.items.push(item)
      continue
    }
    if (item.kind === 'long_task_boundary') {
      flushAssistant()
      currentUserId = item.id
      assistantSequence += 1
      activeAssistant = {
        id: `assistant_turn_${currentUserId}_${assistantSequence}`,
        kind: 'assistant',
        items: [item],
      }
      continue
    }
    if (item.kind === 'execution_marker') {
      flushAssistant()
      currentUserId = item.id
      continue
    }
    if (item.kind === 'message' && item.role === 'user') {
      flushAssistant()
      currentUserId = item.id
      blocks.push({ id: item.id, kind: 'user', item })
      continue
    }
    if (!activeAssistant) {
      assistantSequence += 1
      activeAssistant = {
        id: `assistant_turn_${currentUserId}_${assistantSequence}`,
        kind: 'assistant',
        items: [],
      }
    }
    activeAssistant.items.push(item)
  }
  flushAssistant()
  return blocks
}

export function buildUserMessageMarkers(items: ChatItem[], firstRound = 1): UserMessageMarker[] {
  const markers: UserMessageMarker[] = []
  let nextRound = Math.max(1, Math.floor(firstRound))
  for (const item of items) {
    if (item.kind === 'execution_marker') {
      const executionRound = /^history_execution_(\d+)$/.exec(item.id)?.[1]
      if (executionRound) nextRound = Math.max(nextRound, Number(executionRound) + 1)
      continue
    }
    if (item.kind === 'long_task_boundary') {
      const continuationRound = /^history_long_task_(\d+)$/.exec(item.id)?.[1]
      if (continuationRound) nextRound = Math.max(nextRound, Number(continuationRound) + 1)
      continue
    }
    if (item.kind !== 'message' || item.role !== 'user') continue
    const historicalRound = /^history_(\d+)_user$/.exec(item.id)?.[1]
    const round = historicalRound ? Number(historicalRound) : nextRound
    markers.push({
      id: item.id,
      content: item.content || item.attachments?.map((attachment) => `[附件] ${attachment.name}`).join('；') || '附件消息',
      round,
    })
    nextRound = Math.max(nextRound, round + 1)
  }
  return markers
}

function currentRoundStartIndex(items: ChatItem[]) {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    if (isConversationBoundary(items[index])) return index + 1
  }
  return 0
}

function snapshotRetryItem(item: ChatItem): ChatItem {
  if (item.kind === 'message' && item.role === 'assistant' && item.streaming) {
    return { ...item, streaming: false }
  }
  if (item.kind === 'reasoning' && item.streaming) {
    return { ...item, streaming: false }
  }
  if (item.kind === 'tool' && item.status === 'running') {
    return {
      ...item,
      status: 'error',
      result: {
        ok: false,
        error: {
          message: '本次尝试未完成，尚未收到工具结果；当前仅保留快照',
          exception_type: 'RetryAttemptBoundary',
        },
      },
    }
  }
  return item
}

export function resetCurrentRoundItemsForRetry(
  items: ChatItem[],
  failedAttempt = 1,
  nextAttempt = failedAttempt + 1,
) {
  const roundStart = currentRoundStartIndex(items)
  const prefix = items.slice(0, roundStart)
  const currentAttempt = items.slice(roundStart)
  if (!currentAttempt.length) return prefix
  const retrySeed = String(prefix.at(-1)?.id || 'opening').replace(/[^A-Za-z0-9_-]/g, '_')
  return [
    ...prefix,
    {
      id: `retry_boundary_${retrySeed}_${failedAttempt}_snapshot`,
      kind: 'retry_boundary' as const,
      attempt: failedAttempt,
      phase: 'snapshot' as const,
    },
    ...currentAttempt.map(snapshotRetryItem),
    {
      id: `retry_boundary_${retrySeed}_${nextAttempt}_active`,
      kind: 'retry_boundary' as const,
      attempt: nextAttempt,
      phase: 'active' as const,
    },
  ]
}

export function isProvisionalRunError(event: RunEvent) {
  return event.type === 'error'
    && event.metadata?.retryable === true
    && event.metadata?.committed === false
}

export function isRetryAttemptProgress(event: RunEvent) {
  return [
    'text_delta',
    'reasoning_delta',
    'tool_call_start',
    'tool_call_result',
    'media_output',
    'guidance_applied',
    'usage',
  ].includes(event.type)
}

function findLastCurrentRoundItemIndex(
  items: ChatItem[],
  predicate: (candidate: ChatItem) => boolean,
) {
  const roundStart = currentRoundStartIndex(items)
  for (let index = items.length - 1; index >= roundStart; index -= 1) {
    if (predicate(items[index])) return index
  }
  return -1
}

export function finalizeCurrentRoundItems(
  items: ChatItem[],
  toolError: Record<string, unknown>,
) {
  const roundStart = currentRoundStartIndex(items)
  return items.map((item, index) => {
    if (index < roundStart) return item
    if (item.kind === 'message' && item.role === 'assistant' && item.streaming) {
      return { ...item, streaming: false }
    }
    if (item.kind === 'reasoning' && item.streaming) {
      return { ...item, streaming: false }
    }
    if (item.kind === 'tool' && item.status === 'running') {
      return { ...item, status: 'error' as const, result: { ok: false, error: toolError } }
    }
    if (item.kind === 'guidance') {
      return {
        ...item,
        status: item.status === 'queued'
          ? 'not_applied' as const
          : item.status === 'accepted'
            ? 'completed' as const
            : item.status,
        finalized: true,
      }
    }
    return item
  })
}

export function prepareRunUserMessage(
  items: ChatItem[],
  userItem: Extract<ChatItem, { kind: 'message' }>,
  replaceExistingRound = false,
) {
  const existingIndex = items.findIndex((item) => item.id === userItem.id)
  if (existingIndex < 0) return [...items, userItem]
  if (!replaceExistingRound) return items
  const hasLaterBoundary = items
    .slice(existingIndex + 1)
    .some(isConversationBoundary)
  return hasLaterBoundary ? items : items.slice(0, existingIndex + 1)
}

function insertCurrentRoundItem(
  items: ChatItem[],
  item: ChatItem,
  insertBefore: (candidate: ChatItem) => boolean,
) {
  const roundStart = currentRoundStartIndex(items)
  const relativeIndex = items.slice(roundStart).findIndex(insertBefore)
  const insertionIndex = relativeIndex < 0 ? items.length : roundStart + relativeIndex
  return [...items.slice(0, insertionIndex), item, ...items.slice(insertionIndex)]
}

export function reduceRunEvent(items: ChatItem[], event: RunEvent): ChatItem[] {
  if (event.type === 'context_compression') {
    const runId = String(event.metadata?.run_id || '')
    const rawStatus = String(event.metadata?.status || 'started')
    const status = rawStatus === 'ready' || rawStatus === 'failed' ? rawStatus : 'started'
    const item: ChatItem = {
      id: `context_compression_${runId || 'active'}`,
      kind: 'context_compression',
      runId,
      status,
      trigger: String(event.metadata?.trigger || ''),
      roundsBefore: Math.max(0, Number(event.metadata?.rounds_before || 0)),
      roundsRemoved: Math.max(0, Number(event.metadata?.rounds_removed || 0)),
      roundsRemaining: Math.max(0, Number(event.metadata?.rounds_remaining || 0)),
      memoryMode: String(event.metadata?.memory_mode || ''),
      memoryStatus: String(event.metadata?.memory_status || ''),
      content: String(event.content || ''),
    }
    const index = items.findIndex((candidate) => candidate.kind === 'context_compression' && candidate.runId === runId)
    return index < 0
      ? [...items, item]
      : items.map((candidate, position) => position === index ? item : candidate)
  }
  if (event.type === 'text_delta') {
    const index = findLastCurrentRoundItemIndex(
      items,
      (item) => item.kind === 'message' && item.role === 'assistant' && Boolean(item.streaming),
    )
    if (index >= 0) {
      return items.map((item, position) => position === index && item.kind === 'message' ? { ...item, content: item.content + (event.content || '') } : item)
    }
    return [...items, { id: eventId('assistant'), kind: 'message', role: 'assistant', content: event.content || '', streaming: true }]
  }
  if (event.type === 'reasoning_delta') {
    const index = findLastCurrentRoundItemIndex(
      items,
      (item) => item.kind === 'reasoning' && Boolean(item.streaming),
    )
    if (index >= 0) {
      return items.map((item, position) => position === index && item.kind === 'reasoning' ? { ...item, content: item.content + (event.content || '') } : item)
    }
    return insertCurrentRoundItem(
      items,
      { id: eventId('reasoning'), kind: 'reasoning', content: event.content || '', streaming: true },
      (candidate) => candidate.kind !== 'reasoning',
    )
  }
  if (event.type === 'tool_call_start') {
    return insertCurrentRoundItem(
      items,
      {
        id: eventId('tool'), kind: 'tool', callId: event.tool_call_id || eventId('call'),
        name: event.tool_name || '未知工具', arguments: event.arguments, status: 'running',
      },
      (candidate) => candidate.kind === 'message' && candidate.role === 'assistant'
        || candidate.kind === 'usage'
        || candidate.kind === 'error',
    )
  }
  if (event.type === 'tool_call_result') {
    const result = event.result && typeof event.result === 'object' ? event.result as Record<string, unknown> : undefined
    const backendStatus = String(event.metadata?.status || '')
    const failed = Boolean(event.error) || backendStatus === 'failed' || result?.ok === false
    const toolStatus: 'error' | 'success' = failed ? 'error' : 'success'
    const elapsedMs = event.metadata?.elapsed_ms === undefined ? undefined : Number(event.metadata.elapsed_ms)
    const toolIndex = findLastCurrentRoundItemIndex(
      items,
      (item) => item.kind === 'tool' && item.callId === event.tool_call_id,
    )
    const withTool = toolIndex >= 0 ? items.map((item, index) => index === toolIndex && item.kind === 'tool'
      ? { ...item, name: event.tool_name || item.name, result: event.result, status: toolStatus, elapsedMs }
      : item) : insertCurrentRoundItem(
        items,
        { id: eventId('tool'), kind: 'tool', callId: event.tool_call_id || eventId('call'), name: event.tool_name || '未知工具', result: event.result, status: toolStatus, elapsedMs },
        (candidate) => candidate.kind === 'message' && candidate.role === 'assistant' || candidate.kind === 'usage' || candidate.kind === 'error',
      )
    const plan = extractPlanSummary(event.result)
    if (!plan) return withTool
    if (withTool.some((item) => item.kind === 'task_plan' && item.plan.plan_id === plan.plan_id)) {
      return withTool.map((item) => item.kind === 'task_plan' && item.plan.plan_id === plan.plan_id ? { ...item, plan } : item)
    }
    return insertCurrentRoundItem(
      withTool,
      { id: `task_plan_${plan.plan_id}`, kind: 'task_plan', plan },
      (candidate) => candidate.kind === 'message' && candidate.role === 'assistant' || candidate.kind === 'usage' || candidate.kind === 'error',
    )
  }
  if (event.type === 'media_output') {
    const value = event.result && typeof event.result === 'object'
      ? event.result as Partial<MediaArtifact>
      : event.metadata?.artifact && typeof event.metadata.artifact === 'object'
        ? event.metadata.artifact as Partial<MediaArtifact>
        : null
    if (!value?.asset_id || !value.path || !value.name || value.scope !== 'download') return items
    const artifact = value as MediaArtifact
    if (items.some((item) => item.kind === 'media' && item.artifact.asset_id === artifact.asset_id && item.artifact.path === artifact.path)) return items
    return insertCurrentRoundItem(
      items,
      { id: eventId('media'), kind: 'media', artifact },
      (candidate) => candidate.kind === 'message' && candidate.role === 'assistant'
        || candidate.kind === 'usage'
        || candidate.kind === 'error',
    )
  }
  if (event.type === 'guidance_applied') {
    const pending = Array.isArray(event.metadata?.guidance)
      ? event.metadata.guidance.map((value) => String(value))
      : []
    const details = Array.isArray(event.metadata?.guidance_details)
      ? event.metadata.guidance_details
        .filter((value): value is Record<string, unknown> => Boolean(value) && typeof value === 'object')
      : []
    return items.map((item) => {
      if (item.kind !== 'guidance' || item.status !== 'queued') return item
      const detail = item.guidanceId
        ? details.find((value) => String(value.id || '') === item.guidanceId)
        : undefined
      if (detail) {
        return {
          ...item,
          status: 'accepted' as const,
          attachments: Array.isArray(detail.uploaded_files)
            ? detail.uploaded_files as InputAttachment[]
            : item.attachments,
        }
      }
      if (item.guidanceId && details.length) return item
      const matched = pending.indexOf(item.content)
      if (matched < 0) return item
      pending.splice(matched, 1)
      return { ...item, status: 'accepted' as const }
    })
  }
  if (event.type === 'long_task_update') {
    const continuation = Math.max(1, Number(event.metadata?.continuation || 0) || Number(event.metadata?.long_task_state && typeof event.metadata.long_task_state === 'object'
      ? (event.metadata.long_task_state as Record<string, unknown>).continuation_count
      : 0))
    const taskId = String(event.metadata?.long_task_state && typeof event.metadata.long_task_state === 'object'
      ? (event.metadata.long_task_state as Record<string, unknown>).task_id || ''
      : event.metadata?.long_task_id || '')
    const completed = items.map((item) => {
      if (item.kind === 'message' || item.kind === 'reasoning') return { ...item, streaming: false }
      if (item.kind === 'tool' && item.status === 'running') {
        return {
          ...item,
          status: 'error' as const,
          result: { ok: false, error: { message: '工具调用因当前 Run 达到工具上限而未执行，将在下一 Run 继续', exception_type: 'LongTaskRunBoundary' } },
        }
      }
      return item
    })
    const id = `long_task_boundary_${taskId || 'active'}_${continuation}`
    if (completed.some((item) => item.id === id)) return completed
    return [...completed, { id, kind: 'long_task_boundary', taskId, continuation }]
  }
  if (event.type === 'error') {
    const message = String(event.error?.message || '聊天执行失败')
    const exceptionType = String(event.error?.exception_type || 'ProviderRunInterrupted')
    return [
      ...finalizeCurrentRoundItems(items, { message, exception_type: exceptionType }),
      { id: eventId('error'), kind: 'error', content: message },
    ]
  }
  if (event.type === 'done') {
    let guidanceRemaining = Number(event.metadata?.guidance_count || 0)
    const terminalStatus = String(event.metadata?.status || '').toLowerCase()
    const cancelled = terminalStatus === 'cancelled' || event.metadata?.cancelled === true
    const limited = terminalStatus === 'limited'
    const controlledStop = cancelled || limited
    const stopReason = String(event.metadata?.stop_reason || '')
    const limitedToolError = stopReason === 'tool_context_limit'
      ? { message: '工具调用因本轮达到上下文保护上限而未执行', exception_type: 'ToolContextLimitExceeded' }
      : stopReason === 'tool_loop_incomplete'
        ? { message: '工具调用因本轮工具循环未能正常收束而未执行', exception_type: 'ToolLoopIncomplete' }
        : { message: '工具调用因本轮工具循环达到最大次数而未执行', exception_type: 'ToolLoopLimitExceeded' }
    const terminalText = String(event.metadata?.text || (cancelled
      ? '[本轮已由用户紧急停止]'
      : '[本轮因运行保护限制而停止]'))
    const completed = items.map((item) => {
      if (item.kind === 'message') {
        return {
          ...item,
          content: controlledStop && item.role === 'assistant' && item.streaming
            ? terminalText
            : item.content,
          streaming: false,
        }
      }
      if (item.kind === 'reasoning') return { ...item, streaming: false }
      if (controlledStop && item.kind === 'tool' && item.status === 'running') {
        return {
          ...item,
          status: 'error' as const,
          result: {
            ok: false,
            error: cancelled
              ? { message: '工具调用因用户紧急停止而取消', cancelled: true }
              : limitedToolError,
          },
        }
      }
      if (item.kind === 'guidance') {
        if (item.status === 'accepted') {
          if (guidanceRemaining > 0) guidanceRemaining -= 1
          return { ...item, status: 'completed' as const, finalized: true }
        }
        if (item.status === 'queued') {
          if (guidanceRemaining > 0) {
            guidanceRemaining -= 1
            return { ...item, status: 'completed' as const, finalized: true }
          }
          return { ...item, status: 'not_applied' as const, finalized: true }
        }
        return { ...item, finalized: true }
      }
      return item
    })
    const withTerminalText = controlledStop && !completed.some((item) => item.kind === 'message' && item.role === 'assistant')
      ? [...completed, { id: eventId('assistant'), kind: 'message' as const, role: 'assistant' as const, content: terminalText }]
      : completed
    return event.usage ? [...withTerminalText, {
      id: eventId('usage'), kind: 'usage', usage: event.usage,
      elapsedMs: event.metadata?.elapsed_ms === undefined ? undefined : Number(event.metadata.elapsed_ms),
      toolCalls: event.metadata?.tool_calls === undefined ? undefined : Number(event.metadata.tool_calls),
      providerRequestCount: event.usage.provider_request_count === undefined ? undefined : Number(event.usage.provider_request_count),
    }] : withTerminalText
  }
  return items
}

function historyToolStatus(status: string): 'running' | 'success' | 'error' {
  if (status === 'running') return 'running'
  if (status === 'completed' || status === 'success' || status === 'duplicate_reused') return 'success'
  return 'error'
}

export function buildHistoryItems(history: HistoryResponse | undefined): ChatItem[] {
  const metrics = new Map((history?.round_metrics || []).map((item) => [item.round, item]))
  const traces = new Map((history?.round_traces || []).map((item) => [item.round, item]))
  const result: ChatItem[] = []
  let round = Math.max(0, Number(history?.pagination?.first_round || 1) - 1)
  let messagePosition = 0
  const renderedArtifacts = new Set<string>()
  const appendArtifacts = (artifacts: MediaArtifact[] | undefined, prefix: string) => {
    for (const artifact of artifacts || []) {
      const key = `${artifact.asset_id}\0${artifact.path}`
      if (renderedArtifacts.has(key)) continue
      renderedArtifacts.add(key)
      result.push({ id: `${prefix}_${renderedArtifacts.size}`, kind: 'media', artifact })
    }
  }

  for (const message of history?.messages ?? []) {
    if (message.role !== 'user' && message.role !== 'assistant') continue
    if (message.role === 'user') {
      round += 1
      messagePosition = 0
      if (message.metadata?.synthetic === true && message.metadata.origin === 'long_task_continuation') {
        result.push({
          id: `history_long_task_${round}`,
          kind: 'long_task_boundary',
          taskId: String(message.metadata.long_task_id || ''),
          continuation: Math.max(1, Number(message.metadata.continuation || 1)),
        })
        continue
      }
      if (message.content.startsWith(PLAN_EXECUTION_PROMPT_PREFIX)) {
        result.push({ id: `history_execution_${round}`, kind: 'execution_marker', planId: message.content.split('\n')[1]?.replace('计划 ID：', '').trim() || '' })
        continue
      }
      result.push({ id: `history_${round}_user`, kind: 'message', role: 'user', content: message.content, attachments: message.attachments })
      continue
    }

    messagePosition += 1
    const trace = traces.get(round)
    if (trace?.reasoning) {
      result.push({
        id: `history_reasoning_${round}`,
        kind: 'reasoning',
        content: trace.reasoning,
        streaming: false,
      })
    }
    trace?.tools.forEach((tool, toolIndex) => {
      result.push({ id: `history_tool_${round}_${toolIndex}`, kind: 'tool', callId: tool.call_id || `history-call-${round}-${toolIndex + 1}`, name: tool.name, status: historyToolStatus(tool.status), elapsedMs: tool.elapsed_ms, argumentsText: tool.arguments_text, argumentsTruncated: tool.arguments_truncated, resultText: tool.result_text, resultTruncated: tool.result_truncated })
      appendArtifacts(tool.artifacts, `history_tool_media_${round}_${toolIndex}`)
      if (!tool.result_truncated) {
        try {
          const plan = extractPlanSummary(JSON.parse(tool.result_text))
          if (plan) result.push({ id: `history_task_plan_${plan.plan_id}_${round}`, kind: 'task_plan', plan })
        } catch { /* historical tool output need not be JSON */ }
      }
    })
    result.push({ id: `history_${round}_assistant_${messagePosition}`, kind: 'message', role: 'assistant', content: message.content })

    const selected = metrics.get(round)
    if (selected) {
      appendArtifacts(selected.artifacts, `history_media_${round}`)
      const guidanceDetails = selected.guidance_details ?? []
      if (guidanceDetails.length) {
        guidanceDetails.forEach((detail, guidanceIndex) => result.push({
          id: `history_guidance_${round}_${guidanceIndex}`,
          kind: 'guidance',
          guidanceId: detail.id,
          content: detail.display_text || detail.text || '附件引导',
          attachments: detail.uploaded_files,
          status: 'completed',
          finalized: true,
        }))
      } else {
        selected.guidance.forEach((content, guidanceIndex) => result.push({ id: `history_guidance_${round}_${guidanceIndex}`, kind: 'guidance', content, status: 'completed', finalized: true }))
      }
      result.push({
        id: `history_usage_${round}`, kind: 'usage', usage: selected.usage,
        elapsedMs: selected.elapsed_ms, toolCalls: selected.tool_calls, round,
        providerRequestCount: selected.usage.provider_request_count === undefined
          ? undefined
          : Number(selected.usage.provider_request_count),
      })
    }
  }
  return result
}

export function mergeHistoryPages(pages: HistoryResponse[] | undefined): HistoryResponse | undefined {
  if (!pages?.length) return undefined
  if (pages.length === 1) return pages[0]
  const ordered = [...pages].reverse()
  const earliest = ordered[0]
  const latest = pages[0]
  return {
    ...latest,
    messages: ordered.flatMap((page) => page.messages),
    round_metrics: ordered.flatMap((page) => page.round_metrics),
    round_traces: ordered.flatMap((page) => page.round_traces),
    pagination: {
      limit: latest.pagination?.limit ?? HISTORY_PAGE_SIZE,
      total_rounds: latest.pagination?.total_rounds
        ?? ordered.reduce((total, page) => total + page.messages.filter((message) => message.role === 'user').length, 0),
      first_round: earliest.pagination?.first_round ?? 1,
      last_round: latest.pagination?.last_round
        ?? latest.pagination?.total_rounds
        ?? ordered.reduce((total, page) => total + page.messages.filter((message) => message.role === 'user').length, 0),
      has_more_before: earliest.pagination?.has_more_before ?? false,
      next_before: earliest.pagination?.next_before ?? null,
    },
  }
}

export function compactPlanAssistantText(content: string, hasPlanBubble: boolean) {
  if (!hasPlanBubble) return content
  const markers = ['以下是计划详情', '以下是计划的详细信息', '新计划已生成', '任务计划已生成', '计划已生成', '计划包含', '计划 ID', '## 任务计划', '📋']
  const cut = markers.map((marker) => content.indexOf(marker)).filter((index) => index >= 0).sort((left, right) => left - right)[0]
  return cut === undefined ? content : '任务计划已创建，请在发送框上方查看并确认。'
}

export function dropLastLiveRound(items: ChatItem[]) {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index]
    if (item.kind === 'message' && item.role === 'user') return items.slice(0, index)
  }
  return items
}

export function createDeltaEventBatcher(
  apply: (events: RunEvent[]) => void,
  delayMs = 80,
) {
  let pending: RunEvent[] = []
  let timer: ReturnType<typeof setTimeout> | null = null
  const flush = () => {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
    if (!pending.length) return
    const events = pending
    pending = []
    apply(events)
  }
  const push = (event: RunEvent) => {
    pending.push(event)
    if (timer === null) timer = setTimeout(flush, Math.max(0, delayMs))
  }
  const dispose = () => {
    flush()
  }
  return { push, flush, dispose }
}

export function resolveHistoryUserMessages(
  activeSession: string,
  currentSession: string,
  persistedUserMessages: number,
  explicitHistoryUserMessages?: number,
  editingSource?: { sessionId: string; remainingRounds: number } | null,
  undoneBaseline?: { sessionId: string; remainingRounds: number } | null,
) {
  if (explicitHistoryUserMessages !== undefined) return explicitHistoryUserMessages
  if (editingSource?.sessionId === activeSession) return editingSource.remainingRounds
  if (undoneBaseline?.sessionId === activeSession) return undoneBaseline.remainingRounds
  return activeSession === currentSession ? persistedUserMessages : 0
}

export async function executeStopRequest(
  request: () => Promise<unknown>,
  onFailure: (error: unknown) => void,
) {
  try {
    await request()
    return true
  } catch (error) {
    onFailure(error)
    return false
  }
}

const terminalPlanStatuses = new Set(['completed', 'failed', 'rejected', 'cancelled'])

export function selectDockedPlan(plans: PlanSummary[]) {
  return [...plans].reverse().find((plan) => !terminalPlanStatuses.has(plan.status))
}

function isConversationBoundary(item: ChatItem) {
  return item.kind === 'execution_marker'
    || item.kind === 'long_task_boundary'
    || item.kind === 'retry_boundary'
    || item.kind === 'message' && item.role === 'user'
}

function containingBoundaryId(items: ChatItem[], itemIndex: number) {
  for (let index = itemIndex; index >= 0; index -= 1) {
    if (isConversationBoundary(items[index])) return items[index].id
  }
  return null
}

function boundaryEndIndex(items: ChatItem[], boundaryId: string | null) {
  const start = boundaryId === null
    ? -1
    : items.findIndex((item) => item.id === boundaryId)
  for (let index = start + 1; index < items.length; index += 1) {
    if (isConversationBoundary(items[index])) return index
  }
  return items.length
}

export function archiveTerminalPlansInConversation(
  items: ChatItem[],
  plans: PlanSummary[],
) {
  let result = [...items]
  const latestPlans = new Map<string, PlanSummary>()
  for (const plan of plans) {
    const current = latestPlans.get(plan.plan_id)
    if (!current || plan.revision >= current.revision) latestPlans.set(plan.plan_id, plan)
  }
  for (const plan of latestPlans.values()) {
    if (!terminalPlanStatuses.has(plan.status)) continue
    const matchingIndexes = result
      .map((item, index) => item.kind === 'task_plan' && item.plan.plan_id === plan.plan_id ? index : -1)
      .filter((index) => index >= 0)
    const executionMarker = [...result]
      .reverse()
      .find((item) => item.kind === 'execution_marker' && item.planId === plan.plan_id)
    const boundaryId = executionMarker?.id
      ?? (matchingIndexes.length ? containingBoundaryId(result, matchingIndexes.at(-1)!) : undefined)
    if (boundaryId === undefined) continue

    result = result.map((item) => item.kind === 'task_plan' && item.plan.plan_id === plan.plan_id
      ? { ...item, plan, presentation: 'reference' as const }
      : item)
    const insertionIndex = boundaryEndIndex(result, boundaryId)
    result.splice(insertionIndex, 0, {
      id: `terminal_task_plan_${plan.plan_id}_${boundaryId ?? 'opening'}`,
      kind: 'task_plan',
      plan,
      presentation: 'record',
    })
  }
  return result
}

type GuidanceItem = Extract<ChatItem, { kind: 'guidance' }>

type AssistantMessageItem = Extract<ChatItem, { kind: 'message' }>
type UsageItem = Extract<ChatItem, { kind: 'usage' }>
type TaskPlanItem = Extract<ChatItem, { kind: 'task_plan' }>

function currentRetryAttemptItems(items: ChatItem[]) {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index]
    if (item.kind === 'retry_boundary' && item.phase === 'active') {
      return items.slice(index + 1)
    }
  }
  return items
}

export function partitionAssistantTurnItems(items: ChatItem[]) {
  const currentAttempt = currentRetryAttemptItems(items)
  return {
    assistantMessages: currentAttempt.filter(
      (item): item is AssistantMessageItem => item.kind === 'message' && item.role === 'assistant',
    ),
    usageItems: currentAttempt.filter((item): item is UsageItem => item.kind === 'usage'),
    planItems: currentAttempt.filter(
      (item): item is TaskPlanItem => item.kind === 'task_plan' && item.presentation !== 'reference',
    ),
    finalizedGuidance: currentAttempt.filter(
      (item): item is GuidanceItem => item.kind === 'guidance' && Boolean(item.finalized),
    ),
  }
}
