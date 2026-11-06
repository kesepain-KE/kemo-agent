import type { HistoryIntegration, HistoryMessage, HistoryResponse, HistoryTool } from '../types/api'

type IntegrationSeed = Partial<HistoryIntegration> & { integration_number?: number }
type TraceSeed = {
  integration_number?: number
  integration_id?: string
  reasoning?: string
  tools?: Array<Partial<HistoryTool> & Pick<HistoryTool, 'call_id' | 'name'>>
}
type HistorySeed = {
  user?: string | readonly string[]
  source?: string
  session_id?: string | readonly string[]
  messages?: Array<HistoryMessage & { id?: string }>
  cards?: HistoryResponse['cards']
  integrations?: IntegrationSeed[]
  integration_traces?: TraceSeed[]
  pagination?: Partial<NonNullable<HistoryResponse['pagination']>>
  engine?: string
}

/** Build explicit RAW cards for UI fixtures; production never converts windows. */
export function cardHistory(seed: HistorySeed): HistoryResponse {
  const cards: HistoryResponse['cards'] = []
  const integrations = new Map<string, HistoryIntegration>()
  let ordinal = Math.max(1, seed.pagination?.first_integration_number || 1)
  let seq = Math.max(0, (seed.pagination?.first_seq || 1) - 1)
  const visited = new Set<string>()
  const append = (iid: string, kind: HistoryResponse['cards'][number]['raw_kind'],
    level: HistoryResponse['cards'][number]['raw_level'], message?: HistoryMessage, tool?: HistoryTool) => {
    seq += 1
    const card_id = `card_${seed.session_id}_${seq}`
    cards.push({ card_id, integration_id: iid, seq, raw_kind: kind, raw_level: level,
      created_at: '2026-11-03T00:00:00Z',
      ...(message ? { message: { ...message, seq, card_id, integration_id: iid, level: level || undefined } } : {}),
      ...(tool ? { tool: { ...tool, seq } } : {}) })
  }
  for (const input of seed.messages || []) {
    if (input.role === 'user' && visited.size) ordinal += 1
    ordinal = input.integration_number || ordinal
    const iid = input.integration_id || `i_${ordinal}`
    const selected = seed.integrations?.find((value) =>
      value.integration_id === iid || value.integration_number === ordinal)
    const metric = integrations.get(iid) || {
      integration_id: iid, integration_number: ordinal, response_closed: Boolean(selected),
      last_seq: 0, acknowledged_seq: 0, usage: {}, elapsed_ms: 0, tool_calls: 0, guidance: [],
      ...selected,
    }
    integrations.set(iid, metric)
    const trace = seed.integration_traces?.find((value) =>
      value.integration_id === iid || value.integration_number === ordinal)
    if (input.role === 'assistant' && !visited.has(iid)) {
      if (trace?.reasoning) append(iid, 'agent_text', 'interim', {
        role: 'assistant', content: '', reasoning: trace.reasoning, integration_number: ordinal,
      })
      for (const value of trace?.tools || []) append(iid, 'tool_result', null, undefined, {
        execution_id: `exec_${value.call_id}`, seq: 0, status: 'success', elapsed_ms: 0,
        arguments_text: '{}', arguments_truncated: false, result_text: '', result_truncated: false,
        ...value,
      })
      for (const text of metric.guidance) append(iid, 'user_text', 'guidance', {
        role: 'user', content: text, integration_number: ordinal,
      })
      visited.add(iid)
    }
    append(iid, input.role === 'user' ? 'user_text' : 'agent_text',
      input.level || (input.role === 'user' ? 'initial' : 'final'),
      { ...input, integration_number: ordinal })
    metric.last_seq = seq
    metric.acknowledged_seq = seq
  }
  const actualCards = seed.cards || cards
  for (const value of seed.integrations || []) {
    const iid = value.integration_id || `i_${value.integration_number || 1}`
    if (!integrations.has(iid)) integrations.set(iid, {
      integration_number: value.integration_number || 1, integration_id: iid,
      response_closed: true, last_seq: 0, acknowledged_seq: 0,
      usage: {}, elapsed_ms: 0, tool_calls: 0, guidance: [], ...value,
    })
  }
  return {
    user: String(seed.user || 'alice'), source: seed.source || 'web', session_id: String(seed.session_id || 's1'), engine: 'cardstream',
    cards: actualCards, messages: actualCards.flatMap((card) => card.message ? [card.message] : []),
    integrations: [...integrations.values()],
    integration_traces: (seed.integration_traces || []).map((value) => ({
      integration_number: value.integration_number || 1,
      integration_id: value.integration_id || `i_${value.integration_number || 1}`,
      reasoning: value.reasoning || '',
      tools: (value.tools || []).map((tool) => ({
        execution_id: `exec_${tool.call_id}`, seq: 0, status: 'success', elapsed_ms: 0,
        arguments_text: '{}', arguments_truncated: false, result_text: '', result_truncated: false,
        ...tool,
      })),
    })),
    ...(seed.pagination ? { pagination: {
      limit: 20, total_cards: seq, first_seq: actualCards[0]?.seq || 0, last_seq: seq,
      integration_count: integrations.size, first_integration_number: integrations.values().next().value?.integration_number || 0,
      last_integration_number: ordinal, has_more_before: false, next_before: null, ...seed.pagination, cursor_unit: 'raw_seq',
    } } : {}),
  }
}
