import { z } from 'zod'
import type {
  ApiErrorPayload,
  AuthStatusResponse,
  ConfigFullResponse,
  HistoryResponse,
  KnowledgeResponse,
  MemorySummaryResponse,
  OverviewResponse,
  PromptDiagnosticsResponse,
  RunEvent,
  SenseResponse,
  SessionDeleteAllResponse,
  SessionDeleteResponse,
  SessionRenameResponse,
  SessionsResponse,
  SettingsResponse,
  SkillsResponse,
  TasksResponse,
  UsersResponse,
} from '../types/api'

const apiBase = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? ''

export const AUTH_REQUIRED_EVENT = 'kemo-auth-required'

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code = 'request_failed',
  ) {
    super(message)
  }
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    credentials: 'same-origin',
    ...init,
  })
  if (!response.ok) {
    let payload: ApiErrorPayload | undefined
    try {
      payload = (await response.json()) as ApiErrorPayload
    } catch {
      payload = undefined
    }
    const error = new ApiError(
      payload?.error?.message || `请求失败（${response.status}）`,
      response.status,
      payload?.error?.code,
    )
    if (
      response.status === 401
      && error.code === 'authentication_required'
      && typeof window !== 'undefined'
    ) {
      window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT))
    }
    throw error
  }
  return (await response.json()) as T
}

const runEventSchema = z
  .object({
    type: z.enum([
      'text_delta',
      'reasoning_delta',
      'tool_call_start',
      'tool_call_result',
      'usage',
      'error',
      'done',
    ]),
    content: z.string().optional(),
    tool_call_id: z.string().optional(),
    tool_name: z.string().optional(),
    arguments: z.record(z.string(), z.unknown()).optional(),
    result: z.unknown().optional(),
    usage: z.record(z.string(), z.unknown()).optional(),
    error: z.record(z.string(), z.unknown()).optional(),
    metadata: z.record(z.string(), z.unknown()).optional(),
  })
  .passthrough()

export async function getHealth(): Promise<{ status: string; service: string; version: number }> {
  return requestJson('/api/health')
}

export async function getUsers(): Promise<UsersResponse> {
  return requestJson('/api/users')
}

export async function getSessions(user: string): Promise<SessionsResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/sessions`)
}

export async function getHistory(user: string, sessionId: string): Promise<HistoryResponse> {
  return requestJson(
    `/api/users/${encodeURIComponent(user)}/sessions/${encodeURIComponent(sessionId)}/history`,
  )
}

export async function renameSession(
  user: string,
  sessionId: string,
  title: string,
): Promise<SessionRenameResponse> {
  return requestJson(
    `/api/users/${encodeURIComponent(user)}/sessions/${encodeURIComponent(sessionId)}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    },
  )
}

export async function deleteSession(
  user: string,
  sessionId: string,
): Promise<SessionDeleteResponse> {
  return requestJson(
    `/api/users/${encodeURIComponent(user)}/sessions/${encodeURIComponent(sessionId)}`,
    { method: 'DELETE' },
  )
}

export async function deleteAllSessions(user: string): Promise<SessionDeleteAllResponse> {
  return requestJson(
    `/api/users/${encodeURIComponent(user)}/sessions`,
    { method: 'DELETE' },
  )
}

export async function getAuthStatus(): Promise<AuthStatusResponse> {
  return requestJson('/api/auth/status')
}

export async function bootstrapAuth(token: string): Promise<AuthStatusResponse> {
  return requestJson(`/api/auth/status?token=${encodeURIComponent(token)}`)
}

export async function loginAuth(
  username: string,
  password: string,
): Promise<AuthStatusResponse> {
  return requestJson('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
}

export async function logoutAuth(): Promise<{ authenticated: boolean }> {
  return requestJson('/api/auth/logout', { method: 'POST' })
}

export async function getOverview(user: string, sessionId = ''): Promise<OverviewResponse> {
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return requestJson(`/api/users/${encodeURIComponent(user)}/overview${query}`)
}

export async function getTasks(user: string): Promise<TasksResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/tasks`)
}

export async function getKnowledge(user: string): Promise<KnowledgeResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/knowledge`)
}

export async function getSkills(user: string): Promise<SkillsResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/skills`)
}

export async function getSense(user: string): Promise<SenseResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/sense`)
}

export async function getSettings(user: string): Promise<SettingsResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/settings`)
}

export async function getUserConfig(user: string): Promise<ConfigFullResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/config/full`)
}

export async function getPromptDiagnostics(user: string): Promise<PromptDiagnosticsResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/prompt/sections`)
}

export async function getMemorySummary(user: string): Promise<MemorySummaryResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/memory/summary`)
}

export interface StreamChatOptions {
  user: string
  sessionId: string
  prompt: string
  content?: Array<Record<string, unknown>>
  runId: string
  signal?: AbortSignal
  onEvent: (event: RunEvent) => void
}

export async function submitGuidance(
  user: string,
  runId: string,
  guidance: string,
): Promise<{ run_id: string; status: string; queued: number }> {
  return requestJson(`/api/runs/${encodeURIComponent(runId)}/guidance`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user, guidance }),
  })
}

export interface SseFrame {
  event?: string
  data: string
}

export function parseSseFrames(buffer: string): { frames: SseFrame[]; rest: string } {
  const normalized = buffer.replace(/\r\n/g, '\n')
  const chunks = normalized.split('\n\n')
  const rest = chunks.pop() ?? ''
  const frames = chunks.flatMap((chunk) => {
    let event: string | undefined
    const data: string[] = []
    for (const line of chunk.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
    }
    return data.length ? [{ event, data: data.join('\n') }] : []
  })
  return { frames, rest }
}

export async function streamChat(options: StreamChatOptions): Promise<void> {
  const response = await fetch(`${apiBase}/api/chat`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({
      user: options.user,
      session_id: options.sessionId,
      prompt: options.prompt,
      content: options.content ?? [],
      run_id: options.runId,
    }),
    signal: options.signal,
  })
  if (!response.ok) {
    const payload = (await response.json().catch(() => undefined)) as ApiErrorPayload | undefined
    throw new ApiError(
      payload?.error?.message || `聊天请求失败（${response.status}）`,
      response.status,
      payload?.error?.code,
    )
  }
  if (!response.body) throw new ApiError('浏览器没有提供流式响应正文', 0)

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let terminal = false
  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value, { stream: !done })
    const parsed = parseSseFrames(buffer)
    buffer = parsed.rest
    for (const frame of parsed.frames) {
      const event = runEventSchema.parse(JSON.parse(frame.data)) as RunEvent
      if (frame.event && frame.event !== event.type) {
        throw new ApiError('SSE 事件名称与数据类型不一致', 0, 'invalid_sse')
      }
      options.onEvent(event)
      if (event.type === 'error' || event.type === 'done') terminal = true
    }
    if (done) break
  }
  if (buffer.trim()) {
    const parsed = parseSseFrames(`${buffer}\n\n`)
    for (const frame of parsed.frames) {
      const event = runEventSchema.parse(JSON.parse(frame.data)) as RunEvent
      options.onEvent(event)
      if (event.type === 'error' || event.type === 'done') terminal = true
    }
  }
  if (!terminal) throw new ApiError('聊天流在终态事件前结束', 0, 'missing_terminal')
}
