export * from './api/taskTypes'
export * from './api/runtimeTypes'
export * from './api/fileTypes'
export * from './api/messageTypes'
export * from './api/expandTypes'
export * from './api/modulePanelTypes'

// Public type-facade index: concrete declarations live in the domain modules.
// interface RuntimeStatusResponse
// interface UsersResponse
// interface SessionsResponse
// interface HistoryResponse
// interface TasksResponse
// interface KnowledgeResponse
// interface SkillsResponse
// interface SenseResponse
// interface SettingsResponse
// interface ConfigFullResponse
// interface MemorySummaryResponse
// interface UserFilesResponse
// interface MessageStatusResponse
// interface ExpandsResponse

import type { PlanSummary } from './api/taskTypes'

import type { ReasoningEffort } from '../reasoningEffort'

export type RunEventType =
  | 'text_delta'
  | 'reasoning_delta'
  | 'tool_call_start'
  | 'tool_call_result'
  | 'media_output'
  | 'media_input_update'
  | 'guidance_applied'
  | 'context_compression'
  | 'context_window'
  | 'subagent_progress'
  | 'background_job_progress'
  | 'usage'
  | 'retrying'
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
  engine?: 'cardstream'
  last_seq?: number
  integration_count?: number
  active_tokens?: number
  pinned?: boolean
  pinned_at?: string
  favorite?: boolean
  source?: string
  bound_platform?: string
  session_id: string
  conversation_id?: string
  window?: string
  title: string
  title_source?: 'manual' | 'summary' | string
  summary?: string
  summary_status?: 'none' | 'queued' | 'processing' | 'retry_wait' | 'completed' | 'failed' | 'exhausted' | string
  summary_target_seq?: number
  summary_completed_seq?: number
  summary_retry_at?: string
  summary_attempt_count?: number
  summary_max_attempts?: number
  summary_last_error?: { message?: string; exception_type?: string; [key: string]: unknown } | null
  state?: 'open' | 'closed' | string
  run_state?: 'idle' | 'running' | 'failed' | string
  chain?: 'interactive' | 'message' | 'background' | string
  memory_status?: 'unknown' | 'disabled' | 'deferred' | 'pending' | 'queued' | 'processing' | 'completed' | 'failed' | string
  memory_processed_seq?: number
  memory_target_seq?: number
  memory_queue_reason?: string
  memory_queued_at?: string
  memory_last_error?: { message?: string; exception_type?: string; [key: string]: unknown } | null
  updated_at: string
}

export interface MediaArtifact {
  asset_id: string
  type: 'image' | 'audio' | 'video' | 'file' | string
  name: string
  scope: 'download'
  path: string
  project_path?: string
  mime_type: string
  size: number
  checksum_sha256: string
  duration_ms?: number
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
  retry_after_seconds?: number
  summary?: {
    status: string
    reason: string
    target_seq: number
  }
  memory: {
    status: 'queued' | 'skipped'
    reason: string
    target_seq: number
    processed_seq: number
    pending_cards?: number
  }
  session: SessionSummary
}

export interface SessionReopenResponse {
  user: string
  source: 'web'
  session_id: string
  client_id?: string
  reopened: boolean
  active_clients?: number
  session: SessionSummary
}

export interface HistoryMessage {
  seq?: number
  card_id?: string
  integration_id?: string
  integration_number?: number
  level?: 'initial' | 'guidance' | 'interim' | 'final'
  guidance_id?: string
  reasoning?: string
  role: 'user' | 'assistant' | 'system' | 'tool' | string
  content: string
  attachments?: InputAttachment[]
  metadata?: Record<string, unknown>
}

export interface InputAttachment {
  asset_id: string
  name: string
  media_kind: 'image' | 'audio' | 'video' | 'file'
  mime_type: string
  size: number
  checksum_sha256: string
  scope: 'file_upload' | 'external'
  relative_path: string
  available: boolean
}

export interface NativeMediaInput extends Omit<InputAttachment, 'scope'> {
  scope: 'file_upload' | 'download' | 'external'
  status: 'queued' | 'prepared' | 'included' | 'failed' | 'not_sent' | 'unconfirmed'
  tool_call_id: string
  attachment_id?: string
  error?: string
}

export interface UsersResponse {
  users: UserSummary[]
}

export interface AuthStatusResponse {
  enabled: boolean
  authenticated: boolean
  stage: 'none' | 'token' | 'password' | 'authenticated'
  requires_both: boolean
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
  ip_rate_limit_enabled: boolean
}

export interface SessionsResponse {
  favorites_only?: boolean
  favorites_count?: number
  user: string
  source: string
  sessions: SessionSummary[]
  query?: string
  date?: string
  has_more?: boolean
  next_cursor?: string
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
  absorbed_cards: number
  summary_exists: boolean
  context: Record<string, unknown>
  memory: SessionMemoryExtractionResponse
}

export interface SessionMemoryExtractionResponse {
  status: 'completed' | 'failed' | 'skipped' | 'queued'
  user: string
  source: 'web'
  session_id: string
  target_seq: number
  candidates: number
  processed_seq?: number
  end_seq?: number
  pending_cards?: number
  reason?: string
  extraction: Record<string, unknown> | null
  extractions?: Array<Record<string, unknown>>
  retry_pending?: boolean
  error?: {
    message: string
    exception_type?: string
  }
}

export interface SessionWithdrawIntegrationResponse {
  user: string
  source: string
  session_id: string
  withdrawn: boolean
  integration_id: string
  integration_count: number
  raw_preserved: true
  prompt: string
  content?: Array<Record<string, unknown>>
}

export interface SessionDeleteAllResponse {
  user: string
  source: 'all'
  deleted: boolean
  deleted_sessions: number
  raw_preserved: boolean
}

export interface HistoryTool {
  call_id: string
  execution_id: string
  seq: number
  name: string
  status: 'running' | 'success' | 'error' | 'timed_out_running' | string
  elapsed_ms: number
  arguments_text: string
  arguments_truncated: boolean
  result_text: string
  result_truncated: boolean
  original_call?: { name: string; arguments: Record<string, unknown> }
  artifacts?: MediaArtifact[]
  media_inputs?: NativeMediaInput[]
}

export interface HistoryIntegration {
  integration_number: number
  integration_id: string
  response_closed: boolean
  last_seq: number
  acknowledged_seq: number
  withdrawn?: boolean
  usage: Record<string, unknown>
  elapsed_ms: number
  tool_calls: number
  guidance: string[]
  guidance_details?: Array<{
    id?: string
    text?: string
    display_text?: string
    uploaded_files?: InputAttachment[]
    ok?: boolean
    status?: 'applied' | 'not_applied'
    reason?: string
    note?: string
  }>
  status?: string
  cancelled?: boolean
  cancel_reason?: string
  artifacts?: MediaArtifact[]
  media_inputs?: NativeMediaInput[]
  initial_card?: HistoryCard
}

export interface HistoryCard {
  card_id: string
  integration_id: string
  seq: number
  raw_kind: 'user_text' | 'agent_text' | 'tool_call' | 'tool_result'
  raw_level?: 'initial' | 'guidance' | 'interim' | 'final' | null
  created_at: string
  message?: HistoryMessage
  tool?: HistoryTool
}

export interface HistoryResponse {
  user: string
  source: string
  session_id: string
  engine: 'cardstream'
  cards: HistoryCard[]
  integrations: HistoryIntegration[]
  integration_traces: Array<{
    integration_number: number
    integration_id: string
    reasoning: string
    tools: HistoryTool[]
  }>
  messages: HistoryMessage[]
  user_messages?: Array<HistoryMessage & { card_id: string; seq: number; integration_number: number }>
  pagination?: {
    limit: number | null
    total_cards: number
    first_seq: number
    last_seq: number
    integration_count: number
    first_integration_number: number
    last_integration_number: number
    has_more_before: boolean
    next_before: number | null
    cursor_unit: 'raw_seq'
  }
}

export interface KnowledgeDocumentSummary {
  scope: 'user' | 'shared' | 'global' | string
  relative_path: string
  title: string
  size: number
  updated_at: number
  active_for_main_agent: boolean
}

export interface KnowledgeRoot {
  scope: string
  relative_path: string
  absolute_path: string
  active_for_main_agent: boolean
}

export interface KnowledgeDirectory {
  scope: string
  relative_path: string
  name: string
  updated_at: number
}

export interface NamePolicySummary {
  mode: 'all' | 'allowlist'
  names: string[]
}

export interface MainAgentSourcePolicySummary {
  knowledge: {
    enabled: boolean
    configured_scopes: string[]
    effective_scopes: string[]
  }
  plugins: NamePolicySummary
  skills: { shared: NamePolicySummary; user: NamePolicySummary }
  expand: { global: NamePolicySummary; shared: NamePolicySummary }
  perception: { global: NamePolicySummary }
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
  roots: KnowledgeRoot[]
  directories: KnowledgeDirectory[]
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
  memory_lifecycle?: {
    status: 'active' | 'stale'
    reason?: string | null
    sources: Array<{ fragment_id: number; filename: string; content_hash: string }>
  } | null
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

export interface SkillUploadResponse {
  user: string
  category: 'user_created'
  installed: Array<{
    name: string
    title: string
    path: string
  }>
  count: number
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
  update_interval_seconds: number
  updated_at: number
  panel?: import('./api/modulePanelTypes').ModulePanelDefinition | null
  panel_error?: string
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

export interface KemoModelCatalogItem {
  id: string
  object: 'kemo.model'
  provider_id: string
  provider_model: string
  task: 'llm' | 'embedding' | 'rerank' | 'unknown'
  capabilities_available: boolean
  capabilities_url: string
}

export interface KemoProviderModelsResponse {
  user: string
  protocol: 'kemo'
  api_valid: true
  count: number
  data: KemoModelCatalogItem[]
}

export interface ChatProviderModelsResponse {
  user: string
  protocol: 'chat'
  api_valid: true
  count: number
  data: Array<{ id: string; object: 'model'; owned_by: string }>
}

export interface KemoReasoningCapabilities {
  supported: boolean
  efforts: ReasoningEffort[]
  summary: boolean
  persisted_state: boolean
}

export interface KemoReasoningPolicy {
  mode?: 'native' | 'mapped' | 'provider_default' | string
  logical_efforts?: ReasoningEffort[]
  upstream_parameter?: string
  collapsed?: boolean
  [key: string]: unknown
}

export interface KemoModelCapabilities {
  model: string
  task: 'llm' | 'embedding' | 'rerank'
  input_modalities: string[]
  output_modalities: string[]
  streaming: boolean
  reasoning: KemoReasoningCapabilities
  tools: {
    function_calling: boolean
    parallel_calls: boolean
    multimodal_results: boolean
  }
  structured_output: boolean
  metadata: Record<string, unknown>
  extensions: {
    reasoning_effort_map?: Partial<Record<ReasoningEffort, string>>
    reasoning_policy?: KemoReasoningPolicy
    [key: string]: unknown
  }
}

export interface KemoModelCapabilitiesResponse {
  user: string
  protocol: 'kemo'
  api_valid: true
  model: string
  stale: boolean
  warning: string
  capabilities: KemoModelCapabilities
}

export interface SettingsResponse {
  user: string
  schema_version: number
  schema_versions: {
    config_schema: number
    card_schema: number
    memory_storage_schema: number
  }
  provider: ProviderSummary
  features: {
    tools: boolean
    knowledge: boolean
    history_read: boolean
    memory_injection: boolean
    task_plan_auto_accept: boolean
    cron: boolean
    background_scheduler: boolean
    perception_prompt_injection?: boolean
    expand_prompt_injection?: boolean
  }
  limits: {
    context_engine?: 'cardstream'
    output_reserve?: number
    context_tokens: number
    compression_ratio: number
    task_plan_steps: number
    tool_timeout: number
    tool_argument_retries: number
    memory_items: number
    memory_chars: number
  }
  users: string[]
  authentication: AuthenticationSummary
  source_policy: MainAgentSourcePolicySummary
  provenance: Record<string, 'user' | 'global' | 'default'>
}

export interface VersionResponse {
  name: string
  version: string
  schema_version: number
  compatibility: Record<string, string>
  components: Array<{
    id: string
    version: string
    description: string
  }>
  read_only: true
}

export type VersionCheckStatus = 'up_to_date' | 'update_available' | 'local_newer' | 'check_failed'

export interface VersionCheckResponse {
  status: VersionCheckStatus
  checked_at: string
  local_version?: string
  remote_version?: string
  components?: Array<{
    id: 'core' | 'agents' | 'plugins' | 'web'
    description: string
    local_version: string
    remote_version: string
    status: 'up_to_date' | 'update_available' | 'local_newer'
  }>
  commands?: {
    check: string
    all: string
    recommended: string
    modules: Record<'core' | 'agents' | 'plugins' | 'web', string>
  }
  source?: string
  error?: {
    code: string
    message: string
  }
  read_only: true
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
  weighted_today?: boolean
  weight_locked_today?: boolean
  weight_day?: string
  weight_timezone?: string
}

export interface ImportantMemoryLifecycle {
  status: 'valid' | 'invalid' | 'untracked' | 'empty'
  is_current: boolean
  reason_codes: string[]
  reason: string
  prompt_eligible: boolean
}

export interface ImportantMemoryResponse {
  lifecycle?: ImportantMemoryLifecycle
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
      cache_hit_rate?: number | null
    }
    limit: number
    percent: number
    engine: 'cardstream'
    cards_active: number
    last_seq: number
    integration_count: number
    active_integrations: number
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
      cards_active: number
      last_seq: number
      active_integrations: number
      total_tool_calls: number
      integration_count: number
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
    }
    messages: { connected: number }
    integrations: {
      expands: number
      senses: number
    }
    injection_policy?: {
      expand: 'dynamic' | 'disabled'
      perception: 'dynamic' | 'disabled'
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
    cards_active: number
    last_seq?: number
  }
  session_context_stats: {
    selected: boolean
    cards_active: number
    last_seq: number
    active_integrations: number
    integration_count: number
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
  summary_cache: { exists: boolean; engine: 'cardstream'; card_id: string | null; host_seq: number | null; compression_stage: number; view_epoch: number }
  runtime_host: {
    state: string
    components: Record<string, { name?: string; kind?: string; state?: string; last_error?: unknown }>
  }
  active_plan: PlanSummary | null
  activities: ActivitySummary[]
}

export type { RuntimeLogCategory, RuntimeLogEntry, RuntimeLogsResponse } from './api/runtimeLogTypes'

export interface ApiErrorPayload {
  error?: {
    code?: string
    message?: string
    status?: number
  }
}

export type ChatItem =
  | { id: string; kind: 'subagent_progress'; callId: string; agent: string; status: string; iteration: number; toolName: string; toolCount: number; nextAttempt: number }
  | { id: string; kind: 'message'; role: 'user' | 'assistant'; content: string; level?: 'initial' | 'guidance' | 'interim' | 'final'; providerItemId?: string; providerResponseId?: string; integrationNumber?: number; attachments?: InputAttachment[]; streaming?: boolean; edited?: boolean; originalContent?: string }
  | { id: string; kind: 'execution_marker'; planId: string }
  | { id: string; kind: 'context_compression'; runId: string; status: 'started' | 'ready' | 'failed'; trigger: string; historyTokensBefore: number; historyTokensAfter: number; absorbedCards: number; content: string }
  | { id: string; kind: 'retry_boundary'; attempt: number; phase: 'snapshot' | 'active' }
  | { id: string; kind: 'media'; artifact: MediaArtifact }
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
      status: 'running' | 'still_running' | 'success' | 'error'
      elapsedMs?: number
      mediaInputs?: NativeMediaInput[]
    }
  | { id: string; kind: 'usage'; usage: Record<string, unknown>; elapsedMs?: number; round?: number; toolCalls?: number; providerRequestCount?: number }
  | { id: string; kind: 'task_plan'; plan: PlanSummary; presentation?: 'record' | 'reference' }
  | { id: string; kind: 'guidance'; content: string; status: 'queued' | 'accepted' | 'completed' | 'not_applied' | 'error'; guidanceId?: string; attachments?: InputAttachment[]; finalized?: boolean }
  | { id: string; kind: 'error'; content: string }
