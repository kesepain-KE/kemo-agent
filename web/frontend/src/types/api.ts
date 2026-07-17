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
