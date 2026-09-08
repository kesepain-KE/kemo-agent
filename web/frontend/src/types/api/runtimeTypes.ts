import type { CronTaskSummary, TasksResponse } from './taskTypes'

export type RuntimeHealth = 'healthy' | 'warning' | 'error' | 'offline'

export interface RuntimeStatusResponse {
  schema_version: number
  included_sections?: string[]
  generated_at: string
  user: string
  session_id: string
  api: {
    type: string
    base_url: string
    model: string
    thinking_effort: string
    configured: boolean
    credential_source: string
  }
  context: {
    selected: boolean
    available?: boolean
    used_tokens: number
    max_tokens: number
    percent: number
    rounds: number
    round_limit: number
    compression_threshold: number
    source: string
  }
  tokens: {
    date: string
    timezone: string
    sent_tokens: number
    received_tokens: number
    total_tokens: number
    cached_tokens: number
    cache_rate: number
    request_count: number
    estimated: boolean
    trend: number[]
  }
  prompt: {
    content: string
    total_chars: number
    estimated_tokens: number
    components: Array<{
      id: string
      name: string
      state: 'injected' | 'empty' | 'truncated' | 'error' | string
      chars: number
      tokens: number
      source_files: string[]
      injected_items: number
      original_items: number
    }>
  }
  components: {
    sense: Array<{
      id: string
      name: string
      health: RuntimeHealth
      state: string
      description: string
      updated_at: string | number | null
    }>
    expand: Array<{
      id: string
      name: string
      scope: string
      health: RuntimeHealth
      state: string
      description: string
      updated_at: string | number | null
    }>
  }
  memory: {
    updated_today: number
    upgraded_today: number
    upgrade_tracking: 'system_cron_log' | 'not_available' | string
    updates: Array<{
      id: string
      filename: string
      tier: string
      weight: number
      updated_at: string
      upgraded: boolean | null
      from_tier: string
      to_tier: string
    }>
  }
  tasks: {
    summary: TasksResponse['summary']
    items: Array<{
      id: string
      kind: 'plan' | 'cron' | string
      title: string
      status: string
      next_run_at: string
      trigger: string
      updated_at: string
    }>
  }
  system_cron: {
    tracking: 'execution_log' | 'task_state' | string
    tasks: CronTaskSummary[]
    executions: Array<{
      id: string
      task_id: string
      title: string
      executed_at: string
      status: string
      duration_ms: number
      result: Record<string, unknown>
      error: { type?: string; message?: string } | null
      source: 'execution_log' | 'task_state' | string
    }>
  }
  message_routes: {
    summary: {
      total_bindings: number
      total_transports: number
      running_transports: number
      stopped_transports: number
      error_transports: number
      connected_transports: number
      temporary_files: number
      today_logs: number
    }
    routes: Array<{
      id: string
      name: string
      platform: string
      health: RuntimeHealth
      state: string
      latency_ms: number | null
      last_check: string | null
      description: string
    }>
  }
  runtime_host: {
    state: string
    components: Record<string, { name?: string; kind?: string; state?: string; last_error?: unknown }>
  }
  congestion: {
    provider: {
      active_requests: number
      max_requests: number
      available_requests: number
      waiting_estimate: number
    }
    web: {
      active_chats: number
      max_chats: number
      pending_chats: number
      max_pending: number
    }
    message_router: {
      active_workers: number
      max_workers: number
      queued_messages: number
      max_queued: number
    }
  }
}
