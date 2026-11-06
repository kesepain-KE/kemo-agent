import type { ApiErrorPayload, RunEvent } from '../types/api'
import {
  ApiError,
  apiBase,
  isProvisionalRunError,
  isUnknownRunEvent,
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
  const result = await requestJson<{
    ok?: boolean
    run_id: string
    status: 'accepted_current_run' | 'queued_next_turn' | 'not_applied'
    queued: number
    guidance_id?: string
    error?: { code?: string; message?: string }
  }>(`/api/runs/${encodeURIComponent(runId)}/guidance`, {
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
  if (result.ok === false || result.status === 'not_applied') {
    throw new ApiError(result.error?.message || '引导未投递，也未排入下一轮', 409, result.error?.code || 'guidance_closed')
  }
  return { ...result, status: result.status }
}

export async function cancelRun(
  user: string,
  runId: string,
  sessionId: string,
  source: 'web' | 'app' = 'web',
  cancelSubagents = false,
): Promise<{ run_id: string; user: string; session_id: string; status: 'stopping' }> {
  return requestJson(`/api/runs/${encodeURIComponent(runId)}/cancel`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user, source, session_id: sessionId, cancel_subagents: cancelSubagents }),
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
  let exhausted = false
  let unknownEvents = 0
  const dispatch = (frame: SseFrame) => {
    const value: unknown = JSON.parse(frame.data)
    if (isUnknownRunEvent(value)) {
      unknownEvents++
      return
    }
    const event = runEventSchema.parse(value) as RunEvent
    if (frame.event && frame.event !== event.type) {
      throw new ApiError('SSE 事件名称与数据类型不一致', 0, 'invalid_sse')
    }
    options.onEvent(event)
    if ((event.type === 'error' && !isProvisionalRunError(event)) || event.type === 'done') terminal = true
  }
  try {
    while (true) {
      const { done, value } = await reader.read()
      exhausted = done
      buffer += decoder.decode(value, { stream: !done })
      const parsed = parseSseFrames(buffer)
      buffer = parsed.rest
      for (const frame of parsed.frames) dispatch(frame)
      if (done) break
    }
    if (buffer.trim()) {
      for (const frame of parseSseFrames(`${buffer}\n\n`).frames) dispatch(frame)
    }
    if (!terminal) throw new ApiError('聊天流在终态事件前结束', 0, 'missing_terminal')
  } finally {
    try {
      if (!exhausted) await reader.cancel()
    } catch {
      // Preserve the original stream or callback error.
    } finally {
      reader.releaseLock()
      if (unknownEvents) console.warn('[kemo] ignored unknown SSE events', { count: unknownEvents })
    }
  }
}
