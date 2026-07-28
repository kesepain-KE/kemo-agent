export const kemoReasoningEfforts = ['minimal', 'low', 'medium', 'high', 'xhigh', 'max'] as const
export type ReasoningEffort = typeof kemoReasoningEfforts[number]

export interface ReasoningEffortOption {
  value: ReasoningEffort
  label: string
  settingsLabel: string
  title: string
  description: string
}

const optionByEffort: Record<ReasoningEffort, ReasoningEffortOption> = {
  minimal: { value: 'minimal', label: '极少思考', settingsLabel: '极少 — 最快，几乎不思考', title: '极少', description: '最快，几乎不思考' },
  low: { value: 'low', label: '轻度', settingsLabel: '低 — 快速，轻度推理', title: '低', description: '快速，轻度推理' },
  medium: { value: 'medium', label: '中度', settingsLabel: '中 — 均衡（推荐）', title: '中', description: '均衡（推荐）' },
  high: { value: 'high', label: '深度', settingsLabel: '高 — 深度推理，较慢', title: '高', description: '深度推理，较慢' },
  xhigh: { value: 'xhigh', label: '极高', settingsLabel: '极高 — 更强推理，响应更慢', title: '极高', description: '更强推理，响应更慢' },
  max: { value: 'max', label: '最大', settingsLabel: '最大 — 最强推理，最慢', title: '最大', description: '最强推理，最慢' },
}

export const chatReasoningEfforts = ['minimal', 'low', 'medium', 'high', 'max'] as const satisfies readonly ReasoningEffort[]
export const reasoningEffortOptions = reasoningEffortOptionsFor(chatReasoningEfforts)

const reasoningEffortValues = new Set<string>(kemoReasoningEfforts)

export function isReasoningEffort(value: unknown): value is ReasoningEffort {
  return typeof value === 'string' && reasoningEffortValues.has(value)
}

export function reasoningEffortOptionsFor(efforts: readonly ReasoningEffort[]): ReasoningEffortOption[] {
  return efforts.map((effort) => optionByEffort[effort])
}

export function normalizeReasoningEffort(value: unknown): ReasoningEffort {
  return typeof value === 'string' && (chatReasoningEfforts as readonly string[]).includes(value)
    ? value as ReasoningEffort
    : 'medium'
}

export function normalizeKemoReasoningEffort(value: unknown): ReasoningEffort {
  return isReasoningEffort(value) ? value : 'medium'
}

export function selectReasoningEffort(
  value: unknown,
  efforts: readonly ReasoningEffort[],
): ReasoningEffort | undefined {
  if (isReasoningEffort(value) && efforts.includes(value)) return value
  if (efforts.includes('medium')) return 'medium'
  return efforts[0]
}

export function reasoningEffortLabel(value: unknown) {
  return isReasoningEffort(value) ? optionByEffort[value].label : optionByEffort.medium.label
}
