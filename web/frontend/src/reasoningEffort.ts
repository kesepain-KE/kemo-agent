export const reasoningEffortOptions = [
  { value: 'minimal', label: '极少思考', settingsLabel: '极少 — 最快，几乎不思考', title: '极少', description: '最快，几乎不思考' },
  { value: 'low', label: '轻度', settingsLabel: '低 — 快速，轻度推理', title: '低', description: '快速，轻度推理' },
  { value: 'medium', label: '中度', settingsLabel: '中 — 均衡（推荐）', title: '中', description: '均衡（推荐）' },
  { value: 'high', label: '深度', settingsLabel: '高 — 深度推理，较慢', title: '高', description: '深度推理，较慢' },
  { value: 'max', label: '最大', settingsLabel: '最大 — 最强推理，最慢', title: '最大', description: '最强推理，最慢' },
] as const

export type ReasoningEffort = typeof reasoningEffortOptions[number]['value']

const reasoningEffortValues = new Set<string>(reasoningEffortOptions.map((item) => item.value))

export function normalizeReasoningEffort(value: unknown): ReasoningEffort {
  return typeof value === 'string' && reasoningEffortValues.has(value)
    ? value as ReasoningEffort
    : 'medium'
}

export function reasoningEffortLabel(value: unknown) {
  const effort = normalizeReasoningEffort(value)
  return reasoningEffortOptions.find((item) => item.value === effort)?.label ?? '中度'
}
