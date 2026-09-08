import type { ApiErrorPayload, RunEvent } from '../types/api'
import {
  ApiError,
  apiBase,
  isProvisionalRunError,
  requestJson,
  runEventSchema,
} from './transport'

export interface StreamChatOptions {
  user: string
  sessionId: string
  clientId?: string
  prompt: string
  content?: Array<Record<string, unknown>>
  uploadedFiles?: string[]
  runId: string
  planId?: string
  signal?: AbortSignal
  onEvent: (event: RunEvent) => void
}

export async function submitGuidance(
  user: string,
  runId: string,
  guidance: string,
  options: { sessionId: string; source?: 'web' | 'app'; guidanceId?: string; uploadedFiles?: string[] },
): Promise<{
  run_id: string
  status: 'accepted_current_run' | 'queued_next_turn'
  queued: number
  guidance_id?: string
}> {
  return requestJson(`/api/runs/${encodeURIComponent(runId)}/guidance`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      user,
      source: options.source || 'web',
      session_id: options.sessionId,
      guidance,
      guidance_id: options.guidanceId || '',
      uploaded_files: options.uploadedFiles || [],
    }),
  })
}

export async function cancelRun(
  user: string,
  runId: string,
  sessionId: string,
  source: 'web' | 'app' = 'web',
): Promise<{ run_id: string; user: string; session_id: string; status: 'stopping' }> {
  return requestJson(`/api/runs/${encodeURIComponent(runId)}/cancel`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user, source, session_id: sessionId }),
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
      uploaded_files: options.uploadedFiles ?? [],
      run_id: options.runId,
      plan_id: options.planId ?? '',
      client_id: options.clientId ?? '',
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
      if ((event.type === 'error' && !isProvisionalRunError(event)) || event.type === 'done') terminal = true
    }
    if (done) break
  }
  if (buffer.trim()) {
    const parsed = parseSseFrames(`${buffer}\n\n`)
    for (const frame of parsed.frames) {
      const event = runEventSchema.parse(JSON.parse(frame.data)) as RunEvent
      options.onEvent(event)
      if ((event.type === 'error' && !isProvisionalRunError(event)) || event.type === 'done') terminal = true
    }
  }
  if (!terminal) throw new ApiError('聊天流在终态事件前结束', 0, 'missing_terminal')
}
