export type RunEventType =
  | 'text_delta'
  | 'reasoning_delta'
  | 'tool_call_start'
  | 'tool_call_result'
  | 'usage'
  | 'error'
  | 'done'

export interface RunEvent {
  type: RunEventType
  content?: string
  tool_call_id?: string
  tool_name?: string
  arguments?: Record<string, unknown>
  result?: unknown
  usage?: Record<string, unknown>
  error?: {
    message?: string
    exception_type?: string
    phase?: string
    [key: string]: unknown
  }
  metadata?: Record<string, unknown>
}

export interface UserSummary {
  name: string
}

export interface SessionSummary {
  session_id: string
  window: string
  rounds: number
  updated_at: string
}

export interface HistoryMessage {
  role: 'user' | 'assistant' | 'system' | 'tool' | string
  content: string
}

export interface UsersResponse {
  users: UserSummary[]
}

export interface SessionsResponse {
  user: string
  source: 'web'
  sessions: SessionSummary[]
}

export interface HistoryResponse {
  user: string
  source: 'web'
  session_id: string
  messages: HistoryMessage[]
}

export interface PlanStepSummary {
  step_id: string
  title: string
  description: string
  status: string
  critical: boolean
  tool_name: string
  started_at: string
  finished_at: string
}

export interface PlanSummary {
  plan_id: string
  title: string
  description: string
  status: string
  source: string
  session_id: string
  current_step: string
  revision: number
  created_at: string
  updated_at: string
  progress: { completed: number; total: number; percent: number }
  steps: PlanStepSummary[]
}

export interface CronTaskSummary {
  task_id: string
  title: string
  status: string
  schedule: Record<string, unknown>
  source: string
  session_id: string
  next_run_at: string
  last_run_at: string
  run_count: number
  revision: number
  created_at: string
  updated_at: string
  last_state: 'failed' | 'completed' | 'never' | string
}

export interface TasksResponse {
  user: string
  summary: {
    active_plans: number
    waiting_plans: number
    enabled_crons: number
    completed_plans: number
  }
  plans: PlanSummary[]
  cron_tasks: CronTaskSummary[]
}

export interface KnowledgeDocumentSummary {
  scope: 'user' | 'global' | string
  relative_path: string
  title: string
  size: number
  updated_at: number
}

export interface KnowledgeResponse {
  user: string
  enabled: boolean
  retrieval: {
    max_items: number
    max_chars: number
    minimum_score: number
    mode: string
  }
  summary: { documents: number; user_documents: number; global_documents: number }
  documents: KnowledgeDocumentSummary[]
  extensions: { kemo_graph: string }
}

export interface SkillSummary {
  name: string
  description: string
  version: string
  enabled: boolean
  source: string
  layer: 'user' | 'shared' | 'core' | 'project' | string
  overrides: number
}

export interface SkillsResponse {
  user: string
  summary: { registered: number; enabled: number; user: number; shared: number; core: number }
  tools: SkillSummary[]
}

export interface SenseSourceSummary {
  id: string
  name: string
  description: string
  layer: 'user' | 'shared' | 'project' | string
  enabled: boolean
  status: string
}

export interface SenseResponse {
  user: string
  registry_available: boolean
  injection_enabled: boolean
  core_available: boolean
  core_files: number
  summary: { registered: number; enabled: number; user: number; shared: number; project: number }
  sources: SenseSourceSummary[]
  decisions: Array<Record<string, unknown>>
}

export interface ProviderSummary {
  type: string
  base_url: string
  model: string
  timeout: number
  stream: boolean
  credential_source: 'inline' | 'environment' | 'missing' | string
  configured: boolean
}

export interface SettingsResponse {
  user: string
  schema_version: number
  provider: ProviderSummary
  features: {
    tools: boolean
    knowledge: boolean
    memory_extraction: boolean
    memory_injection: boolean
    task_plan_auto_accept: boolean
    cron: boolean
    cron_auto_start: boolean
  }
  limits: {
    context_tokens: number
    compression_ratio: number
    task_plan_steps: number
    tool_iterations: number
    tool_timeout: number
    knowledge_items: number
    knowledge_chars: number
    memory_items: number
    memory_chars: number
  }
  users: string[]
}

export interface ActivitySummary {
  type: 'session' | 'plan' | 'cron' | string
  title: string
  detail: string
  status: string
  updated_at: string
}

export interface OverviewResponse {
  user: string
  session_id: string
  context: {
    usage: {
      prompt_tokens: number
      completion_tokens: number
      total_tokens: number
      estimated: boolean
    }
    limit: number
    percent: number
  }
  provider: ProviderSummary
  counts: {
    sessions: number
    knowledge_documents: number
    enabled_tools: number
    enabled_agents: number
    active_tasks: number
  }
  active_plan: PlanSummary | null
  activities: ActivitySummary[]
}

export interface ApiErrorPayload {
  error?: {
    code?: string
    message?: string
    status?: number
  }
}

export type ChatItem =
  | { id: string; kind: 'message'; role: 'user' | 'assistant'; content: string; streaming?: boolean }
  | { id: string; kind: 'reasoning'; content: string; streaming?: boolean }
  | {
      id: string
      kind: 'tool'
      callId: string
      name: string
      arguments?: Record<string, unknown>
      result?: unknown
      status: 'running' | 'success' | 'error'
    }
  | { id: string; kind: 'error'; content: string }
