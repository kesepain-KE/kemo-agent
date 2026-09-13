export type RuntimeLogCategory = 'all' | 'backend' | 'threads' | 'terminal' | 'message'

export interface RuntimeLogEntry {
  id: string
  category: Exclude<RuntimeLogCategory, 'all'>
  title: string
  occurred_at: string
  status: string
  duration_ms: number | null
  exit_code?: number | null
  detail: string
  source: 'sqlite' | 'memory' | 'snapshot'
}

export interface RuntimeLogsResponse {
  user: string
  category: RuntimeLogCategory
  entries: RuntimeLogEntry[]
  counts: Record<RuntimeLogCategory, number>
  generated_at: string
  pagination: { page: number; page_size: number; total_items: number; total_pages: number; has_previous: boolean; has_next: boolean }
  cache: { hit: boolean; ttl_seconds: number }
  source_errors: string[]
}
