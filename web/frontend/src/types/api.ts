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
  event_id?: string
  sequence?: number
  run_sequence?: number
  request_id?: string
  response_id?: string
  item_id?: string
  content_index?: number
  protocol_event_type?: string
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
  depends_on: string[]
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
  auto_accept: boolean
  reminder: string
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
  user_defined: boolean
  status: string
  type: 'daily' | 'once' | 'recurring'
  time?: string
  interval_seconds?: number
  next_run_at: string
  latest_run_at: string
  created_at: string
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
  executions: Array<{
    kind: 'plan_step' | 'cron' | string
    task_id: string
    title: string
    status: string
    updated_at: string
    result?: unknown
    error?: unknown
  }>
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
    mode: 'index_only'
    full_index: boolean
  }
  summary: { documents: number; user_documents: number; shared_documents: number; global_documents: number }
  documents: KnowledgeDocumentSummary[]
  extensions: { kemo_graph: string }
  source_policy: MainAgentSourcePolicySummary
}

export interface KnowledgeDocumentResponse {
  user: string
  scope: 'user' | 'shared' | 'global' | string
  relative_path: string
  content: string
  size: number
  updated_at?: number
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
  display_name: string
  description: string
  layer: 'user' | 'shared' | 'global' | string
  enabled: boolean
  active_for_main_agent: boolean
  status: 'active' | 'filtered' | 'invalid' | string
  data_md: string
  recent_update: string
  health: '正常' | '异常' | string
  valid: boolean
  error: string
  start_update: string
  files: number
  registered_items: number
  injected_items: number
  data_items: string[]
  value_preview: string
  update_interval: string
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
    healthy: number
    unhealthy: number
    invalid: number
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
    invalid: string[]
    unmatched: string[]
    health_status: Record<string, {
      name: string
      explain: string
      valid: boolean
      input_health: string
      open_input: boolean
      open_control: boolean
      input_data: string
      start_update: string
      start_expand: string
      start_control: string
      control_file: string
      error: string
    }>
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

export interface MemoryItemResponse {
  user: string
  filename: string
  content: string
  tier: string
  weight: number
  updated_at: string
  expires_at: string | null
  last_weight_date?: string | null
}

export interface ImportantMemoryResponse {
  user: string
  path: string
  content: string
  size: number
  updated_at?: string
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

export type FileTreeNode =
  | {
      type: 'directory'
      name: string
      relative_path: string
      children: FileTreeNode[]
    }
  | {
      type: 'file'
      name: string
      relative_path: string
      size: number
      updated_at: number
      extension: string
    }

export interface FileTreeSummary {
  total_files: number
  total_dirs: number
  total_size: number
}

export interface UserFilesResponse {
  user: string
  scope: 'file_upload' | 'download'
  root: string
  summary: FileTreeSummary
  tree: FileTreeNode[]
}

export interface TmpFilesResponse {
  root: 'tmp' | string
  summary: FileTreeSummary
  tree: FileTreeNode[]
}

export interface FileDeleteResponse {
  user?: string
  scope?: 'file_upload' | 'download'
  path: string
  deleted: boolean
}

export interface FileMutationResponse {
  user?: string
  scope?: 'file_upload' | 'download'
  root?: string
  path?: string
  new_path?: string
  size?: number
  updated?: boolean
  created?: boolean
  moved?: boolean
}

export interface PreferencesResponse {
  user: string
  appearance: { theme: 'light' | 'dark'; font_size: 'small' | 'medium' | 'large' }
}

export interface AvatarUploadResponse {
  user: string
  avatar_path: string
  size: number
  format: string
}

export interface InventoryFile {
  name: string
  relative_path: string
  size: number
  updated_at: number
}

export interface AgentsResponse {
  user: string
  summary: { total: number; enabled: number; global: number; user: number }
  agents: Array<{
    name: string
    description: string
    enabled: boolean
    source: 'global' | 'user'
    execution: string
    model_profile: string
    exposure: string
    root: string
    files: InventoryFile[]
  }>
}

export interface MessageStatusResponse {
  user: string
  bindings: Array<{
    platform: string
    external_user_id: string
    internal_user: string
    chat_type: string | null
    external_chat_id: string | null
    match_priority: number
  }>
  transports: Array<{
    name: string
    platform: string
    display_name: string
    capabilities: string[]
    state: 'running' | 'stopped' | 'error' | string
    bound_user: string
    allowed_tools: string[] | null
    last_error: unknown
    health: string
    last_check: string | null
    last_message_at: string | null
    latency_ms: number | null
    messages_received_today: number
    messages_sent_today: number
  }>
  summary: {
    total_bindings: number
    total_transports: number
    running_transports: number
    stopped_transports: number
    error_transports: number
  }
  issues: Array<{ name: string; error: string }>
}

export interface SoulResponse {
  user?: string
  path: string
  content: string
  size: number
  updated_at: number
}

export interface ExpandsResponse {
  user: string
  summary: { total: number; global: number; shared: number; user: number }
  expands: Array<{
    scope: 'global' | 'shared' | 'user'
    root: string
    items: Array<{
      name: string
      type: 'directory'
      relative_path: string
      has_register: boolean
      files: InventoryFile[]
    }>
  }>
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
  | { id: string; kind: 'task_plan'; plan: PlanSummary }
  | { id: string; kind: 'guidance'; content: string; status: 'queued' | 'accepted' | 'error' }
  | { id: string; kind: 'error'; content: string }
