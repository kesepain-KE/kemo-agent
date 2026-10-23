import type { KnowledgeCategorySummary, KnowledgeDocumentSummary } from './types/api'

export type KnowledgeScope = 'user' | 'shared' | 'global'

type CategoryData = {
  documents: Pick<KnowledgeDocumentSummary, 'scope' | 'category'>[]
  categories?: Record<string, KnowledgeCategorySummary[]>
}

const scopeLabels: Record<KnowledgeScope, string> = {
  user: '用户层',
  shared: '共享层',
  global: '全局层',
}
const scopes: KnowledgeScope[] = ['user', 'shared', 'global']

export function getKnowledgeCategories(data: CategoryData | undefined, scope: KnowledgeScope) {
  const configured = data?.categories?.[scope]
  if (configured?.length) return configured
  const counts = new Map<string, number>([['未分类', 0]])
  for (const document of data?.documents ?? []) {
    if (document.scope !== scope) continue
    const category = document.category || '未分类'
    counts.set(category, (counts.get(category) || 0) + 1)
  }
  return Array.from(counts, ([name, count]) => ({ name, count }))
}

export function knowledgeCategoryKey(scope: string, category: string | undefined) {
  return JSON.stringify([scope, category || '未分类'])
}

export function getKnowledgeCategoryFilters(
  data: CategoryData | undefined,
  scope: KnowledgeScope | 'all',
) {
  return (scope === 'all' ? scopes : [scope]).flatMap((source) => (
    getKnowledgeCategories(data, source).map((category) => ({
      ...category,
      value: knowledgeCategoryKey(source, category.name),
      label: scope === 'all' ? `${scopeLabels[source]} · ${category.name}` : category.name,
    }))
  ))
}
