import type { ReasoningEffort } from '../reasoningEffort'

export type RunEventType =
  | 'text_delta'
  | 'reasoning_delta'
  | 'tool_call_start'
  | 'tool_call_result'
  | 'guidance_applied'
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
  conversation_id?: string
  window: string
  title: string
  summary?: string
  summary_status?: 'none' | 'queued' | 'processing' | 'completed' | 'failed' | string
  summary_target_round?: number
  summary_completed_round?: number
  summary_retry_at?: string
  summary_retry_count?: number
  state?: 'open' | 'closed' | string
  run_state?: 'idle' | 'running' | 'failed' | string
  chain?: 'interactive' | 'message' | 'background' | string
  rounds: number
  updated_at: string
}

export interface ActiveSessionResponse {
  user: string
  active_key: string
  created: boolean
  client_id?: string
  active_clients?: number
  session: SessionSummary
}

export interface SessionCloseResponse {
  user: string
  source: 'web'
  session_id: string
  closed: boolean
  deferred?: boolean
  active_clients?: number
  summary?: {
    status: string
    reason: string
    rounds: number
  }
  memory: {
    status: 'queued' | 'skipped'
    reason: string
    rounds: number
    processed_round: number
    pending_rounds?: number
  }
  session: SessionSummary
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

export interface SessionCompressResponse {
  user: string
  source: 'web'
  session_id: string
  requested: boolean
  compressed: boolean
  rounds_removed: number
  summary_cache_exists: boolean
  context: Record<string, unknown>
  memory: SessionMemoryExtractionResponse
}

export interface SessionMemoryExtractionResponse {
  status: 'completed' | 'failed' | 'skipped' | 'queued'
  user: string
  source: 'web'
  session_id: string
  round: number
  candidates: number
  processed_round?: number
  target_round?: number
  pending_rounds?: number
  reason?: string
  extraction: Record<string, unknown> | null
  extractions?: Array<Record<string, unknown>>
  retry_pending?: boolean
  error?: {
    message: string
    exception_type?: string
  }
}

export interface SessionUndoLastRoundResponse {
  user: string
  source: 'web'
  session_id: string
  found: boolean
  rolled_back: boolean
  round: number
  remaining_rounds: number
  prompt: string
  content: Array<Record<string, unknown>>
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
    status?: 'completed' | 'cancelled' | string
    cancelled?: boolean
    cancel_reason?: string
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
  pagination?: {
    limit: number | null
    total_rounds: number
    first_round: number
    last_round: number
    has_more_before: boolean
    next_before: number | null
  }
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

export type SkillCategory = 'builtin' | 'shared' | 'agent_generated' | 'user_created'

export interface SkillCatalogItem {
  id: string
  name: string
  title: string
  description: string
  category: SkillCategory
  version: string
  enabled: boolean
  editable: boolean
  toggleable: boolean
  downloadable: boolean
  path: string
}

export interface SkillDocumentResponse {
  user: string
  category: SkillCategory
  name: string
  path: string
  content: string
  size: number
  updated_at: number
  editable: boolean
}

export interface SkillsResponse {
  user: string
  summary: { registered: number; enabled: number; user: number; shared: number; core: number }
  tools: SkillSummary[]
  catalog_summary: {
    total: number
    enabled: number
    builtin: number
    shared: number
    agent_generated: number
    user_created: number
  }
  items: SkillCatalogItem[]
  prompt_summary: { registered: number; active: number; user: number; shared: number }
  prompt_skills: Array<{
    name: string
    title: string
    description: string
    scope: 'shared' | 'user'
    category: 'shared' | 'agent_generated' | 'user_created'
    path: string
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
  whitelisted: boolean
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
  collected_markdown: string
  injected_markdown: string
  injected_tokens: number
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
    content: string
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
  reasoning_effort: ReasoningEffort
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
    memory_ref: string
    filename: string
    tier: string
    weight: number
    created_at: string
    content_updated_at: string
    updated_at: string
    last_used_at: string | null
    tier_entered_at: string | null
    expires_at: string | null
    timezone: 'UTC'
    preview: string
    truncated: boolean
  }>
}

export interface MemoryItemResponse {
  user: string
  memory_ref: string
  filename: string
  content: string
  tier: string
  weight: number
  created_at: string
  content_updated_at: string
  updated_at: string
  last_used_at: string | null
  tier_entered_at: string | null
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
  context_window: {
    tokens: {
      system_prompt_tokens: number
      tool_schema_tokens: number
      conversation_tokens: number
      summary_tokens: number
      other_tokens: number
      context_tokens: number
      total_tokens: number
      capacity_tokens: number
      percent: number
      source: string
      measurement: string
      captured_at: string
    }
    conversation: {
      foreground_rounds: number
      archived_rounds: number
      total_tool_calls: number
      session_total_rounds: number
      session_tool_calls: number
    }
    tasks: {
      active_plans: number
      waiting_crons: number
    }
    capabilities: {
      tools_enabled: number
      tools_disabled: number
      agents_enabled: number
    }
    knowledge: {
      enabled: number
      disabled: number
      graph_enabled: boolean
    }
    messages: { connected: number }
    integrations: {
      expands: number
      senses: number
    }
  }
  context_snapshot: {
    available: boolean
    source: string
    measurement: string
    captured_at: string
    system_prompt_tokens: number
    tool_schema_tokens: number
    conversation_tokens: number
    summary_tokens: number
    other_tokens: number
    total_tokens: number
    capacity_tokens: number
    percent: number
    foreground_rounds: number
  }
  session_context_stats: {
    selected: boolean
    foreground_rounds: number
    background_archived_rounds: number
    session_total_rounds: number
    session_tool_calls: number
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

export type RuntimeHealth = 'healthy' | 'warning' | 'error' | 'offline'

export interface RuntimeStatusResponse {
  schema_version: number
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

export interface FilesDeleteResponse {
  user?: string
  scope?: 'file_upload' | 'download'
  deleted_paths: string[]
  deleted_count: number
}

export type TmpFilesDeleteResponse = FilesDeleteResponse

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
  renamed?: boolean
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
    version: string
    description: string
    enabled: boolean
    source: 'global' | 'user'
    trigger: string
    rules: string
    executor: string
    execution: string
    model_profile: string
    exposure: string
    root: string
    files: InventoryFile[]
  }>
}

export interface AgentDeleteResponse {
  user: string
  name: string
  path: string
  deleted: boolean
}

export interface MessageLogEntry {
  id: string
  direction: 'send' | 'receive'
  kind: 'text' | 'file' | 'system'
  timestamp: string
  content: string
  file_path: string | null
  success: boolean
  chat_type: string
  chat_id: string
  source: string
  mime?: string
  size?: number
}

export interface MessageTransportSummary {
  id: string
  name: string
  platform: string
  display_name: string
  description: string
  capabilities: string[]
  state: 'running' | 'stopped' | 'error' | string
  connection_status: 'connected' | 'disconnected' | 'error'
  bound_user: string
  allowed_tools: string[] | null
  last_error: unknown
  health: string
  last_check: string | null
  last_message_at: string | null
  latency_ms: number | null
  messages_received_today: number
  messages_sent_today: number
  path: string
  files_path: string
  log_path: string
  message_buffer: string
  modules: Record<string, string>
  api_imported: boolean
  polling_interval: string
  health_interval: string
  file_relay_enabled: boolean
  log_rotation: string
  temporary_file_count: number
  temporary_file_bytes: number
  today_log_count: number
  logs: MessageLogEntry[]
  logs_truncated: boolean
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
  transports: MessageTransportSummary[]
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
  issues: Array<{ name: string; error: string }>
}

export interface MessageCheckResponse {
  user: string
  module: string
  checked: boolean
  state: Record<string, unknown>
  transport: MessageTransportSummary | null
}

export interface MessageDeleteResponse {
  user: string
  module: string
  platform: string
  path: string
  deleted: boolean
}

export interface SoulResponse {
  user?: string
  path: string
  content: string
  size: number
  updated_at: number
}

export type ExpandScope = 'global' | 'shared' | 'user'

export interface ExpandModuleSummary {
  id: string
  scope: ExpandScope
  name: string
  display_name: string
  description: string
  type: 'directory'
  root: string
  path: string
  relative_path: string
  has_register: boolean
  valid: boolean
  error: string
  whitelisted: boolean
  active_for_main_agent: boolean
  input_health: string
  open_input: boolean
  open_control: boolean
  input_data: string
  start_update: string
  start_expand: string
  start_control: string
  control_document: string
  control_injection_markdown: string
  control_operation_markdown: string
  collected_markdown: string
  injected_markdown: string
  injected_tokens: number
  files: InventoryFile[]
  updated_at: number
}

export interface ExpandsResponse {
  user: string
  summary: { total: number; global: number; shared: number; user: number }
  status_summary: {
    enabled: number
    healthy: number
    invalid: number
  }
  expands: Array<{
    scope: ExpandScope
    root: string
    items: ExpandModuleSummary[]
  }>
  injection: {
    content: string
    source_files: string[]
    original_chars: number
    injected_chars: number
    original_items: number
    injected_items: number
    estimated_tokens: number
    truncated: boolean
    prompt_section: string
    prompt_position: string
  }
  source_policy: MainAgentSourcePolicySummary
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
  | { id: string; kind: 'execution_marker'; planId: string }
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
  | { id: string; kind: 'usage'; usage: Record<string, unknown>; elapsedMs?: number; round?: number; toolCalls?: number; providerRequestCount?: number }
  | { id: string; kind: 'task_plan'; plan: PlanSummary }
  | { id: string; kind: 'guidance'; content: string; status: 'queued' | 'accepted' | 'completed' | 'not_applied' | 'error'; finalized?: boolean }
  | { id: string; kind: 'error'; content: string }
