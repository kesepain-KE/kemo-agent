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
  stream?: 'stdout' | 'stderr'
}

export interface RuntimeLogsResponse {
  user: string
  category: RuntimeLogCategory
  entries: RuntimeLogEntry[]
  counts: Record<RuntimeLogCategory, number>
  generated_at: string
  pagination: { page: number; page_size: number; total_items: number; total_pages: number; has_previous: boolean; has_next: boolean }
  terminal?: {
    window_size: number
    loaded_items: number
    stdout_items: number
    stderr_items: number
    process_local: boolean
    retention_seconds: number
  } | null
  cache: { hit: boolean; ttl_seconds: number }
  source_errors: string[]
}
