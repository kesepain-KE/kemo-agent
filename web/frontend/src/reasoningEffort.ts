export type ReasoningEffort = string

export interface ReasoningEffortOption {
  value: ReasoningEffort
  label: string
  settingsLabel: string
  title: string
  description: string
}

const optionByEffort: Record<string, ReasoningEffortOption> = {
  minimal: { value: 'minimal', label: '极少思考', settingsLabel: '极少 — 最快，几乎不思考', title: '极少', description: '最快，几乎不思考' },
  low: { value: 'low', label: '轻度', settingsLabel: '低 — 快速，轻度推理', title: '低', description: '快速，轻度推理' },
  medium: { value: 'medium', label: '中度', settingsLabel: '中 — 均衡（推荐）', title: '中', description: '均衡（推荐）' },
  high: { value: 'high', label: '深度', settingsLabel: '高 — 深度推理，较慢', title: '高', description: '深度推理，较慢' },
  xhigh: { value: 'xhigh', label: '极高', settingsLabel: '极高 — 更强推理，响应更慢', title: '极高', description: '更强推理，响应更慢' },
  max: { value: 'max', label: '最大', settingsLabel: '最大 — 最强推理，最慢', title: '最大', description: '最强推理，最慢' },
}

export const chatReasoningEfforts = ['minimal', 'low', 'medium', 'high', 'max'] as const satisfies readonly ReasoningEffort[]
export const reasoningEffortOptions = reasoningEffortOptionsFor(chatReasoningEfforts)

export function isReasoningEffort(value: unknown): value is ReasoningEffort {
  if (typeof value !== 'string') return false
  const effort = value.trim()
  return effort.length > 0
    && effort.length <= 64
    && effort.toLowerCase() !== 'none'
    && !/[\u0000-\u001f]/.test(effort)
}

export function reasoningEffortOptionsFor(efforts: readonly ReasoningEffort[]): ReasoningEffortOption[] {
  const seen = new Set<string>()
  const options: ReasoningEffortOption[] = []
  for (const rawEffort of efforts) {
    if (!isReasoningEffort(rawEffort)) continue
    const effort = rawEffort.trim().toLowerCase()
    if (seen.has(effort)) continue
    seen.add(effort)
    options.push(optionByEffort[effort] ?? {
      value: effort,
      label: effort,
      settingsLabel: effort,
      title: effort,
      description: 'Kemo 网关声明档位',
    })
  }
  return options
}

export function normalizeReasoningEffort(value: unknown): ReasoningEffort {
  return typeof value === 'string' && (chatReasoningEfforts as readonly string[]).includes(value)
    ? value as ReasoningEffort
    : 'medium'
}

export function normalizeKemoReasoningEffort(value: unknown): ReasoningEffort {
  return isReasoningEffort(value) ? value.trim().toLowerCase() : 'medium'
}

export function selectReasoningEffort(
  value: unknown,
  efforts: readonly ReasoningEffort[],
): ReasoningEffort | undefined {
  const available = reasoningEffortOptionsFor(efforts).map((option) => option.value)
  const selected = isReasoningEffort(value) ? value.trim().toLowerCase() : ''
  if (selected && available.includes(selected)) return selected
  if (available.includes('medium')) return 'medium'
  return available[0]
}

export function reasoningEffortLabel(value: unknown) {
  if (!isReasoningEffort(value)) return optionByEffort.medium.label
  const effort = value.trim().toLowerCase()
  return optionByEffort[effort]?.label ?? effort
}
