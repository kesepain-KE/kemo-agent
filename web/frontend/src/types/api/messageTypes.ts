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
