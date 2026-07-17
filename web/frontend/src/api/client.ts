import { z } from 'zod'
import type {
  ApiErrorPayload,
  HistoryResponse,
  RunEvent,
  SessionsResponse,
  UsersResponse,
} from '../types/api'

const apiBase = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? ''

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
  const response = await fetch(`${apiBase}${path}`, init)
  if (!response.ok) {
    let payload: ApiErrorPayload | undefined
    try {
      payload = (await response.json()) as ApiErrorPayload
    } catch {
      payload = undefined
    }
    throw new ApiError(
      payload?.error?.message || `请求失败（${response.status}）`,
      response.status,
      payload?.error?.code,
    )
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

export interface StreamChatOptions {
  user: string
  sessionId: string
  prompt: string
  signal?: AbortSignal
  onEvent: (event: RunEvent) => void
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
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({
      user: options.user,
      session_id: options.sessionId,
      prompt: options.prompt,
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
