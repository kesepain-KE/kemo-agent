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
  title: string
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

export interface AuthStatusResponse {
  enabled: boolean
  authenticated: boolean
  methods: {
    token: boolean
    password: boolean
  }
  session_cookie_configured: boolean
}

export interface AuthenticationSummary {
  enabled: boolean
  token_enabled: boolean
  password_enabled: boolean
  session_cookie_configured: boolean
}

export interface SessionsResponse {
  user: string
  source: 'web'
  sessions: SessionSummary[]
}

export interface SessionRenameResponse {
  user: string
  source: 'web'
  session: SessionSummary
}

export interface SessionDeleteResponse {
  user: string
  source: 'web'
  session_id: string
  deleted: boolean
}

export interface SessionDeleteAllResponse {
  user: string
  source: 'web'
  deleted: boolean
  deleted_sessions: number
  deleted_windows: number
}

export interface HistoryResponse {
  user: string
  source: 'web'
  session_id: string
  messages: HistoryMessage[]
  round_metrics: Array<{
    round: number
    usage: Record<string, unknown>
    elapsed_ms: number
    tool_calls: number
    guidance: string[]
    tool_pause?: {
      reason?: string
      limit?: number
      executed?: number
    } | null
  }>
  round_traces: Array<{
    round: number
    reasoning: string
    tools: Array<{
      call_id: string
      name: string
      status: 'running' | 'success' | 'error' | string
      elapsed_ms: number
      arguments_text: string
      arguments_truncated: boolean
      result_text: string
      result_truncated: boolean
    }>
  }>
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
  scope: 'user' | 'shared' | 'global' | string
  relative_path: string
  title: string
  size: number
  updated_at: number
  active_for_main_agent: boolean
}

export interface NamePolicySummary {
  mode: 'all' | 'allowlist'
  names: string[]
}

export interface MainAgentSourcePolicySummary {
    knowledge: { enabled: boolean; effective_scopes: string[] }
    plugins: NamePolicySummary
    skills: { shared: NamePolicySummary; user: NamePolicySummary }
  expand: { global: NamePolicySummary; shared: NamePolicySummary }
  perception: { global: NamePolicySummary }
  kemo_graph: {
    requested: boolean
    connected: false
    effective: false
    status: 'disabled' | 'not_connected'
    replacement_active: boolean
    replaces_knowledge: boolean
    replaces_memory: boolean
  }
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
  summary: { documents: number; user_documents: number; shared_documents: number; global_documents: number }
  documents: KnowledgeDocumentSummary[]
  extensions: { kemo_graph: string }
  source_policy: MainAgentSourcePolicySummary
}

export interface SkillSummary {
  name: string
  description: string
  version: string
  enabled: boolean
  source: string
  layer: 'user' | 'shared' | 'core' | string
  overrides: number
}

export interface SkillsResponse {
  user: string
  summary: { registered: number; enabled: number; user: number; shared: number; core: number }
  tools: SkillSummary[]
  prompt_summary: { registered: number; active: number; user: number; shared: number }
  prompt_skills: Array<{
    name: string
    title: string
    description: string
    scope: 'shared' | 'user'
    active_for_main_agent: boolean
  }>
  source_policy: MainAgentSourcePolicySummary
}

export interface SenseSourceSummary {
  id: string
  name: string
  description: string
  layer: 'user' | 'shared' | 'global' | string
  enabled: boolean
  active_for_main_agent: boolean
  status: string
  files: number
  registered_items: number
  injected_items: number
  data_items: string[]
  updated_at: number
}

export interface SenseResponse {
  user: string
  registry_available: boolean
  injection_enabled: boolean
  core_available: boolean
  core_files: number
  summary: {
    registered: number
    enabled: number
    user: number
    shared: number
    global: number
    registered_data: number
    injected_data: number
  }
  sources: SenseSourceSummary[]
  injection: {
    enabled: boolean
    registered_items: number
    injected_items: number
    original_chars: number
    injected_chars: number
    estimated_tokens: number
    truncated: boolean
    preview: string
    preview_truncated: boolean
    source_files: string[]
    prompt_section: string
    prompt_position: string
  }
  decisions: Array<Record<string, unknown>>
  source_policy: MainAgentSourcePolicySummary
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
    history_read: boolean
    memory_injection: boolean
    task_plan_auto_accept: boolean
    cron: boolean
    background_scheduler: boolean
  }
  limits: {
    context_rounds: number
    context_tokens: number
    compression_ratio: number
    task_plan_steps: number
    tool_iterations: number
    tool_timeout: number
    tool_max_per_round: number | null
    knowledge_items: number
    knowledge_chars: number
    memory_items: number
    memory_chars: number
  }
  users: string[]
  authentication: AuthenticationSummary
  source_policy: MainAgentSourcePolicySummary
  provenance: Record<string, 'user' | 'global' | 'default'>
}

export interface ConfigFullResponse {
  user: string
  config: Record<string, unknown>
  redacted_paths: string[]
}

export interface PromptDiagnosticsResponse {
  user: string
  total_chars: number
  sections: Array<{
    name: string
    status: 'injected' | 'omitted'
    original_items: number
    injected_items: number
    original_chars: number
    injected_chars: number
    truncated: boolean
    source_files: string[]
  }>
  source_policy: MainAgentSourcePolicySummary
  source_selection: Record<string, unknown>
  expand: Record<string, {
    mode: string
    discovered: string[]
    selected: string[]
    filtered: string[]
    unmatched: string[]
  }>
}

export interface MemorySummaryResponse {
  user: string
  summary: { total: number; seven_days: number; one_month: number; half_year: number; permanent: number }
  items: Array<{
    filename: string
    tier: string
    weight: number
    updated_at: string
    expires_at: string | null
    preview: string
    truncated: boolean
  }>
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
    rounds: number
    round_limit: number
  }
  provider: ProviderSummary
  counts: {
    sessions: number
    knowledge_documents: number
    enabled_tools: number
    enabled_agents: number
    active_tasks: number
  }
  agents: Array<{
    name: string
    description: string
    enabled: boolean
    source: 'builtin' | 'user'
    execution: string
    model_profile: string
    exposure: string
  }>
  summary_cache: { exists: boolean; covered_rounds: number[]; created_at: string; window: string; invalid?: boolean }
  runtime_host: {
    state: string
    components: Record<string, { name?: string; kind?: string; state?: string; last_error?: unknown }>
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
  | { id: string; kind: 'message'; role: 'user' | 'assistant'; content: string; streaming?: boolean; edited?: boolean; originalContent?: string }
  | { id: string; kind: 'reasoning'; content: string; streaming?: boolean }
  | {
      id: string
      kind: 'tool'
      callId: string
      name: string
      arguments?: Record<string, unknown>
      argumentsText?: string
      argumentsTruncated?: boolean
      result?: unknown
      resultText?: string
      resultTruncated?: boolean
      status: 'running' | 'success' | 'error'
      elapsedMs?: number
    }
  | { id: string; kind: 'usage'; usage: Record<string, unknown>; elapsedMs?: number; round?: number; toolCalls?: number }
  | { id: string; kind: 'guidance'; content: string; status: 'queued' | 'accepted' | 'error' }
  | { id: string; kind: 'error'; content: string }
