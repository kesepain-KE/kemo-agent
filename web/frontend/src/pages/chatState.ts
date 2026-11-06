import { taskPlanFromSummary } from '../components/TaskPlanBubble'
import type { UserMessageMarker } from '../components/UserMessageNavigator'
import { randomUUID } from '../randomId'
import { reduceSubagentProgress } from './subagentProgress'
import { reduceNativeMediaInputs } from '../components/nativeMedia'
import type { ChatItem, HistoryResponse, InputAttachment, MediaArtifact, PlanSummary, RunEvent } from '../types/api'

import { extractPlanSummary } from './planSummary'
export { extractPlanSummary } from './planSummary'
export { buildHistoryItems, mergeHistoryPages } from './chatHistory'

function eventId(prefix: string) {
  return `${prefix}_${randomUUID()}`
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
    if (item.kind === 'execution_marker') {
      flushAssistant()
      currentUserId = item.id
      assistantSequence = 0
      continue
    }
    if (item.kind === 'message' && item.role === 'user') {
      flushAssistant()
      currentUserId = item.id
      assistantSequence = 0
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

export function buildUserMessageMarkers(
  items: ChatItem[],
  firstRound = 1,
  indexedMessages: NonNullable<HistoryResponse['user_messages']> = [],
): UserMessageMarker[] {
  const markers = new Map<number, UserMessageMarker>()
  const indexedItems: ChatItem[] = indexedMessages.map((message) => ({
    id: `history_${message.card_id}_user`, kind: 'message', role: 'user',
    integrationNumber: message.integration_number,
    content: message.content, attachments: message.attachments,
  }))
  let nextRound = Math.max(1, Math.floor(firstRound))
  for (const item of [...indexedItems, ...items]) {
    if (item.kind === 'execution_marker') {
      const executionRound = /^history_execution_(\d+)$/.exec(item.id)?.[1]
      if (executionRound) nextRound = Math.max(nextRound, Number(executionRound) + 1)
      continue
    }
    if (item.kind !== 'message' || item.role !== 'user') continue
    const historicalRound = /^history_(\d+)_user$/.exec(item.id)?.[1]
    const round = item.integrationNumber ?? (historicalRound ? Number(historicalRound) : nextRound)
    markers.set(round, {
      id: item.id,
      content: item.content || item.attachments?.map((attachment) => `[附件] ${attachment.name}`).join('；') || '附件消息',
      integration_number: round,
    })
    nextRound = Math.max(nextRound, round + 1)
  }
  return [...markers.values()]
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
  boundaryKey = nextAttempt,
) {
  items = items.filter((item) => item.kind !== 'subagent_progress')
  const roundStart = currentRoundStartIndex(items)
  const prefix = items.slice(0, roundStart)
  const currentAttempt = items.slice(roundStart)
  if (!currentAttempt.length) return prefix
  const retrySeed = String(prefix.at(-1)?.id || 'opening').replace(/[^A-Za-z0-9_-]/g, '_')
  return [
    ...prefix,
    {
      id: `retry_boundary_${retrySeed}_${boundaryKey}_snapshot`,
      kind: 'retry_boundary' as const,
      attempt: failedAttempt,
      phase: 'snapshot' as const,
    },
    ...currentAttempt.map(snapshotRetryItem),
    {
      id: `retry_boundary_${retrySeed}_${boundaryKey}_active`,
      kind: 'retry_boundary' as const,
      attempt: nextAttempt,
      phase: 'active' as const,
    },
  ]
}

export function resetProviderResponseForRetry(items: ChatItem[], event: RunEvent) {
  if (event.metadata?.partial_response !== true) return items
  const failedAttempt = Math.max(1, Number(event.metadata.failed_attempt || 1))
  const nextAttempt = Math.max(failedAttempt + 1, Number(event.metadata.next_attempt || failedAttempt + 1))
  const key = String(event.metadata.failed_request_id || eventId('provider_retry')).replace(/[^A-Za-z0-9_-]/g, '_')
  const start = currentRoundStartIndex(items)
  const sealed = items.map((item, index) => index >= start
    && (item.kind === 'reasoning' || item.kind === 'message' && item.role === 'assistant')
    && item.streaming ? { ...item, streaming: false } : item)
  return [
    ...sealed,
    { id: `retry_boundary_${key}_snapshot`, kind: 'retry_boundary' as const, attempt: failedAttempt, phase: 'snapshot' as const },
    { id: `retry_boundary_${key}_active`, kind: 'retry_boundary' as const, attempt: nextAttempt, phase: 'active' as const },
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
  guidanceDetails: Record<string, unknown>[] = [],
) {
  items = items.filter((item) => item.kind !== 'subagent_progress')
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
      const detail = item.guidanceId ? guidanceDetails.find(value => String(value.id || '') === item.guidanceId) : undefined
      return {
        ...item,
        status: detail ? detail.ok === false || detail.status === 'not_applied' ? 'not_applied' as const : 'completed' as const
          : item.status === 'queued' ? 'not_applied' as const : item.status === 'accepted' ? 'completed' as const : item.status,
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
  const mediaTerminal = event.type === 'done' || (event.type === 'error' && !isProvisionalRunError(event))
  if (event.type === 'media_input_update' || mediaTerminal) {
    const merged = reduceNativeMediaInputs(items, event, currentRoundStartIndex(items), mediaTerminal)
    if (event.type === 'media_input_update') return merged
    items = merged
  }
  items = reduceSubagentProgress(items, event)
  if (event.type === 'subagent_progress') return items
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
      historyTokensBefore: Math.max(0, Number(event.metadata?.history_tokens_before || 0)),
      historyTokensAfter: Math.max(0, Number(event.metadata?.history_tokens_after || 0)),
      absorbedCards: Math.max(0, Number(event.metadata?.absorbed_cards || 0)),
      content: String(event.content || ''),
    }
    const index = items.findIndex((candidate) => candidate.kind === 'context_compression' && candidate.runId === runId)
    return index < 0
      ? [...items, item]
      : items.map((candidate, position) => position === index ? item : candidate)
  }
  if (event.type === 'text_delta') {
    if (event.content) {
      items = items.map((item, index) => index >= currentRoundStartIndex(items)
        && item.kind === 'reasoning'
        && item.streaming ? { ...item, streaming: false } : item)
    }
    const index = findLastCurrentRoundItemIndex(
      items,
      (item) => item.kind === 'message' && item.role === 'assistant' && Boolean(item.streaming)
        && (!event.response_id || item.providerResponseId === event.response_id)
        && (!event.item_id || item.providerItemId === event.item_id),
    )
    if (index >= 0) {
      return items.map((item, position) => position === index && item.kind === 'message' ? { ...item, content: item.content + (event.content || '') } : item)
    }
    items = items.map((item, position) => position >= currentRoundStartIndex(items)
      && item.kind === 'message' && item.role === 'assistant' && item.streaming
      ? { ...item, streaming: false } : item)
    return [...items, {
      id: eventId('assistant'), kind: 'message', role: 'assistant', content: event.content || '',
      providerItemId: event.item_id || undefined, providerResponseId: event.response_id || undefined,
      streaming: true,
    }]
  }
  if (event.type === 'reasoning_delta') {
    if (event.content) {
      items = items.map((item, index) => index >= currentRoundStartIndex(items)
        && item.kind === 'message'
        && item.role === 'assistant'
        && item.streaming ? { ...item, streaming: false } : item)
    }
    const index = findLastCurrentRoundItemIndex(
      items,
      (item) => item.kind === 'reasoning' && Boolean(item.streaming),
    )
    if (index >= 0) {
      return items.map((item, position) => position === index && item.kind === 'reasoning' ? { ...item, content: item.content + (event.content || '') } : item)
    }
    return [...items, { id: eventId('reasoning'), kind: 'reasoning', content: event.content || '', streaming: true }]
  }
  if (event.type === 'tool_call_start') {
    return [
      ...items.map((item, index) => index >= currentRoundStartIndex(items)
        && item.kind === 'message' && item.role === 'assistant'
        && item.streaming ? { ...item, streaming: false } : item),
      {
        id: eventId('tool'), kind: 'tool', callId: event.tool_call_id || eventId('call'),
        name: event.tool_name || '未知工具', arguments: event.arguments, status: 'running',
      },
    ]
  }
  if (event.type === 'tool_call_result') {
    const result = event.result && typeof event.result === 'object' ? event.result as Record<string, unknown> : undefined
    const backendStatus = String(event.metadata?.status || '')
    const resultError = result?.error && typeof result.error === 'object' ? result.error as Record<string, unknown> : undefined
    const stillRunning = backendStatus === 'timed_out_running' || resultError?.still_running === true || event.error?.still_running === true
    const executionLive = event.metadata?.execution_live === true
    const failed = Boolean(event.error) || backendStatus === 'failed' || result?.ok === false
    const toolStatus = stillRunning ? 'still_running' as const : executionLive ? 'running' as const : failed ? 'error' as const : 'success' as const
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
      : event.metadata?.id ? [event.metadata] : []
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
  if (event.type === 'error') {
    const message = String(event.error?.message || '聊天执行失败')
    const exceptionType = String(event.error?.exception_type || 'ProviderRunInterrupted')
    const details = Array.isArray(event.metadata?.guidance_details) ? event.metadata.guidance_details.filter((value): value is Record<string, unknown> => Boolean(value) && typeof value === 'object') : []
    return [
      ...finalizeCurrentRoundItems(items, { message, exception_type: exceptionType }, details),
      { id: eventId('error'), kind: 'error', content: message },
    ]
  }
  if (event.type === 'done') {
    let guidanceRemaining = Number(event.metadata?.guidance_count || 0)
    const guidanceDetails = Array.isArray(event.metadata?.guidance_details)
      ? event.metadata.guidance_details.filter((value): value is Record<string, unknown> => Boolean(value) && typeof value === 'object')
      : []
    const terminalStatus = String(event.metadata?.status || '').toLowerCase()
    const cancelled = terminalStatus === 'cancelled' || event.metadata?.cancelled === true
    const limited = terminalStatus === 'limited'
    const controlledStop = cancelled || limited
    const unfinished = Array.isArray(event.metadata?.unfinished_tasks)
      ? event.metadata.unfinished_tasks.filter((value): value is Record<string, unknown> => Boolean(value) && typeof value === 'object')
      : []
    const liveCallIds = new Set(unfinished.filter((value) => value.execution_live === true)
      .map((value) => String(value.tool_call_id || '')))
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
      if (item.kind === 'tool' && item.status === 'running' && liveCallIds.has(item.callId)) {
        return { ...item, status: 'still_running' as const }
      }
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
        const detail = item.guidanceId ? guidanceDetails.find(value => String(value.id || '') === item.guidanceId) : undefined
        const applied = detail ? detail.ok !== false && detail.status !== 'not_applied'
          : item.status === 'accepted' || item.status === 'queued' && guidanceRemaining > 0 && (!item.guidanceId || !guidanceDetails.length)
        if (applied && guidanceRemaining > 0) guidanceRemaining -= 1
        if (detail || item.status === 'accepted' || item.status === 'queued') {
          return { ...item, status: applied ? 'completed' as const : 'not_applied' as const, finalized: true }
        }
        return { ...item, finalized: true }
      }
      return item
    })
    let withTerminalText: ChatItem[] = controlledStop && !completed.some((item) => item.kind === 'message' && item.role === 'assistant')
      ? [...completed, { id: eventId('assistant'), kind: 'message' as const, role: 'assistant' as const, content: terminalText }]
      : completed
    const finalItemIds = new Set(Array.isArray(event.metadata?.final_item_ids)
      ? event.metadata.final_item_ids.map(String) : [])
    let lastAssistantIndex = -1
    for (let index = currentRoundStartIndex(withTerminalText); index < withTerminalText.length; index += 1) {
      const item = withTerminalText[index]
      if (terminalStatus === 'completed' && item.kind === 'message' && item.role === 'assistant'
        && item.content.trim() && (!finalItemIds.size || finalItemIds.has(item.providerItemId || ''))) {
        lastAssistantIndex = index
      }
    }
    if (lastAssistantIndex < 0 && terminalStatus === 'completed'
      && typeof event.metadata?.text === 'string' && event.metadata.text.trim()) {
      lastAssistantIndex = withTerminalText.length
      withTerminalText = [...withTerminalText, {
        id: eventId('assistant'), kind: 'message', role: 'assistant', content: event.metadata.text,
        providerItemId: [...finalItemIds].at(-1), streaming: false,
      }]
    }
    const finalized = withTerminalText.map((item, index) => index >= currentRoundStartIndex(withTerminalText)
      && item.kind === 'message' && item.role === 'assistant'
      ? {
          ...item, level: index === lastAssistantIndex ? 'final' as const : 'interim' as const,
          content: index === lastAssistantIndex && typeof event.metadata?.text === 'string'
            ? event.metadata.text : item.content,
        }
      : item)
    return event.usage ? [...finalized, {
      id: eventId('usage'), kind: 'usage', usage: event.usage,
      elapsedMs: event.metadata?.elapsed_ms === undefined ? undefined : Number(event.metadata.elapsed_ms),
      toolCalls: event.metadata?.tool_calls === undefined ? undefined : Number(event.metadata.tool_calls),
      providerRequestCount: event.usage.provider_request_count === undefined ? undefined : Number(event.usage.provider_request_count),
    }] : finalized
  }
  return items
}
export function compactPlanAssistantText(content: string, hasPlanBubble: boolean) {
  if (!hasPlanBubble) return content
  const markers = ['以下是计划详情', '以下是计划的详细信息', '新计划已生成', '任务计划已生成', '计划已生成', '计划包含', '计划 ID', '## 任务计划', '📋']
  const cut = markers.map((marker) => content.indexOf(marker)).filter((index) => index >= 0).sort((left, right) => left - right)[0]
  return cut === undefined ? content : '任务计划已创建，请在左侧计划卡片中查看并确认。'
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
  const assistantMessages = currentAttempt.filter(
    (item): item is AssistantMessageItem => item.kind === 'message' && item.role === 'assistant',
  )
  return {
    assistantMessages,
    finalAssistantMessage: [...assistantMessages].reverse().find((item) => item.level === 'final'),
    usageItems: currentAttempt.filter((item): item is UsageItem => item.kind === 'usage'),
    planItems: currentAttempt.filter(
      (item): item is TaskPlanItem => item.kind === 'task_plan' && item.presentation !== 'reference',
    ),
    finalizedGuidance: currentAttempt.filter(
      (item): item is GuidanceItem => item.kind === 'guidance' && Boolean(item.finalized),
    ),
  }
}
