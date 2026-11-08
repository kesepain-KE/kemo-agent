import type { ChatItem, HistoryResponse, MediaArtifact } from '../types/api'
import { extractPlanSummary } from './planSummary'

const HISTORY_PAGE_SIZE = 20
const PLAN_EXECUTION_PROMPT_PREFIX = '【任务计划连续执行】'

export function shouldPrefetchEarlierHistory(
  activeRound: number,
  firstLoadedRound: number,
  hasEarlierHistory: boolean,
  leadRounds = 2,
) {
  return hasEarlierHistory
    && activeRound > 0
    && firstLoadedRound > 0
    && activeRound <= firstLoadedRound + Math.max(0, leadRounds)
}

function historyToolStatus(status: string): Extract<ChatItem, { kind: 'tool' }>['status'] {
  if (status === 'timed_out_running') return 'still_running'
  if (status === 'running') return 'running'
  if (status === 'completed' || status === 'success' || status === 'duplicate_reused') return 'success'
  return 'error'
}
export function buildHistoryItems(
  history: HistoryResponse | undefined,
  liveIntegrationNumbers?: ReadonlySet<number>,
): ChatItem[] {
  const integrations = history?.integrations || []
  const metrics = new Map(integrations.map((item) => [item.integration_id, item]))
  const withdrawn = new Set(integrations.filter((item) => item.withdrawn).map((item) => item.integration_id))
  const result: ChatItem[] = []
  const byCard = new Map(integrations
    .flatMap((integration) => !integration.withdrawn && integration.initial_card ? [integration.initial_card] : [])
    .map((card) => [card.card_id, card]))
  for (const card of history?.cards || []) {
    if (!withdrawn.has(card.integration_id)) byCard.set(card.card_id, card)
  }
  // Group late receipts with their original reply, preserving order within it.
  const groupedCards = new Map<string, HistoryResponse['cards']>()
  for (const card of [...byCard.values()].sort((left, right) => left.seq - right.seq)) {
    const group = groupedCards.get(card.integration_id)
    if (group) group.push(card)
    else groupedCards.set(card.integration_id, [card])
  }
  const cards = [...groupedCards.values()].flat()
  const lastInPage = new Map(cards.map((card) => [card.integration_id, card.seq]))
  const toolPositions = new Map<string, number>()
  const renderedArtifacts = new Set<string>()
  let activeReasoningPosition = -1
  const appendArtifacts = (artifacts: MediaArtifact[] | undefined, prefix: string) => {
    for (const artifact of artifacts || []) {
      const key = `${artifact.asset_id}\0${artifact.path}`
      if (renderedArtifacts.has(key)) continue
      renderedArtifacts.add(key)
      result.push({ id: `${prefix}_${renderedArtifacts.size}`, kind: 'media', artifact })
    }
  }

  for (const card of cards) {
    const message = card.message
    const selected = metrics.get(card.integration_id)
    const round = message?.integration_number ?? selected?.integration_number ?? 0
    if (liveIntegrationNumbers?.has(round)) continue
    if (message?.role === 'user' && card.raw_level !== 'guidance') {
      activeReasoningPosition = -1
    }
    if (message?.role === 'user' && card.raw_level === 'guidance') {
      const detail = selected?.guidance_details?.find((item) => item.id === message.guidance_id)
      result.push({
        id: `history_guidance_${card.card_id}`, kind: 'guidance',
        guidanceId: message.guidance_id, content: message.content, attachments: message.attachments,
        status: detail?.ok === false || detail?.status === 'not_applied'
          ? 'not_applied' : card.seq <= (selected?.acknowledged_seq || 0)
            ? selected?.response_closed ? 'completed' : 'accepted'
            : selected?.response_closed ? 'not_applied' : 'queued',
        finalized: Boolean(selected?.response_closed),
      })
    } else if (message?.role === 'user') {
      if (message.content.startsWith(PLAN_EXECUTION_PROMPT_PREFIX)) {
        result.push({ id: `history_execution_${round}`, kind: 'execution_marker', planId: message.content.split('\n')[1]?.replace('计划 ID：', '').trim() || '' })
      } else {
        result.push({ id: `history_${card.card_id}_user`, kind: 'message', role: 'user',
          integrationNumber: round, content: message.content, attachments: message.attachments })
      }
    } else if (message?.role === 'assistant') {
      if (message.reasoning) {
        const activeReasoning = result[activeReasoningPosition]
        if (activeReasoning?.kind === 'reasoning') {
          result[activeReasoningPosition] = {
            ...activeReasoning,
            content: activeReasoning.content + message.reasoning,
          }
        } else {
          activeReasoningPosition = result.length
          result.push({
            id: `history_reasoning_${card.card_id}`, kind: 'reasoning', content: message.reasoning, streaming: false,
          })
        }
      }
      if (message.content) {
        result.push({
          id: `history_${card.card_id}_assistant`, kind: 'message', role: 'assistant',
          content: message.content, level: card.raw_level || message.level,
        })
        activeReasoningPosition = -1
      }
    }
    const tool = card.tool
    if (tool) {
      const id = `history_tool_${tool.execution_id}`
      const item: ChatItem = { id, kind: 'tool', callId: tool.call_id, name: tool.name,
        status: historyToolStatus(tool.status), elapsedMs: tool.elapsed_ms,
        argumentsText: tool.arguments_text, argumentsTruncated: tool.arguments_truncated,
        resultText: tool.result_text, resultTruncated: tool.result_truncated, mediaInputs: tool.media_inputs }
      const position = toolPositions.get(id)
      if (position === undefined) {
        toolPositions.set(id, result.length)
        result.push(item)
      } else result[position] = item
      appendArtifacts(tool.artifacts, `history_tool_media_${tool.execution_id}`)
      if (!tool.result_truncated) {
        try {
          const plan = extractPlanSummary(JSON.parse(tool.result_text))
          if (plan) {
            const existing = result.findIndex((item) => item.kind === 'task_plan' && item.plan.plan_id === plan.plan_id)
            const planned: ChatItem = { id: `history_task_plan_${plan.plan_id}`, kind: 'task_plan', plan }
            if (existing < 0) result.push(planned)
            else result[existing] = planned
          }
        } catch { /* historical tool output need not be JSON */ }
      }
    }
    if (selected?.response_closed && selected.last_seq === card.seq && lastInPage.get(card.integration_id) === card.seq) {
      appendArtifacts(selected.artifacts, `history_media_${card.integration_id}`)
      result.push({
        id: `history_usage_${card.integration_id}`, kind: 'usage', usage: selected.usage,
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
  const byCard = new Map(ordered.flatMap((page) => page.cards || []).map((card) => [card.card_id, card]))
  const byIntegration = new Map(ordered.flatMap((page) => page.integrations || [])
    .map((integration) => [integration.integration_id, integration]))
  return {
    ...latest,
    messages: [...byCard.values()].sort((left, right) => left.seq - right.seq)
      .flatMap((card) => card.message ? [card.message] : []),
    cards: [...byCard.values()].sort((left, right) => left.seq - right.seq),
    integrations: [...byIntegration.values()],
    integration_traces: [...new Map(ordered.flatMap((page) => page.integration_traces)
      .map((trace) => [trace.integration_id, trace])).values()],
    pagination: {
      limit: latest.pagination?.limit ?? HISTORY_PAGE_SIZE,
      integration_count: latest.pagination?.integration_count ?? byIntegration.size,
      first_integration_number: earliest.pagination?.first_integration_number ?? 0,
      last_integration_number: latest.pagination?.last_integration_number ?? 0,
      has_more_before: earliest.pagination?.has_more_before ?? false,
      next_before: earliest.pagination?.next_before ?? null,
      cursor_unit: 'raw_seq',
      total_cards: latest.pagination?.total_cards ?? byCard.size,
      first_seq: earliest.pagination?.first_seq ?? 0,
      last_seq: latest.pagination?.last_seq ?? 0,
    },
  }
}
