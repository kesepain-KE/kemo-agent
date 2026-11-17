import type { ApiErrorPayload, RunEvent } from '../types/api'
import { z } from 'zod'
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
  onReset?: (snapshot: RunSnapshot) => Promise<void>
  onConnectionChange?: (connected: boolean) => void
}

const runSnapshotSchema = z.object({
  run_id: z.string(), user: z.string(), source: z.string(), session_id: z.string(),
  cursor: z.number().int().nonnegative(), history_seq: z.number().int().nonnegative(),
  events: z.array(runEventSchema), finished: z.boolean(), stopping: z.boolean(),
  snapshot_incomplete: z.boolean(), terminal: runEventSchema.nullable(),
})
export type RunSnapshot = z.infer<typeof runSnapshotSchema>

export async function getCurrentRun(user: string, sessionId: string, signal?: AbortSignal): Promise<RunSnapshot | null> {
  const value = await requestJson<{ run: unknown }>(
    `/api/users/${encodeURIComponent(user)}/sessions/${encodeURIComponent(sessionId)}/run`, { signal },
  )
  return value.run ? runSnapshotSchema.parse(value.run) : null
}

function runQuery(options: Pick<StreamChatOptions, 'user' | 'sessionId'>) {
  return new URLSearchParams({ user: options.user, session_id: options.sessionId, source: 'web' })
}

function transientConnectionError(error: unknown) {
  return error instanceof TypeError
    || error instanceof ApiError && (
      error.status === 0 && !['invalid_sse', 'resume_required'].includes(error.code)
      || [502, 503, 504].includes(error.status)
    )
}

function reconnectDelay(ms: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer)
      reject(new DOMException('连接已取消', 'AbortError'))
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', abort)
      resolve()
    }, ms)
    if (signal?.aborted) abort()
    else signal?.addEventListener('abort', abort, { once: true })
  })
}

export async function followRun(
  options: Pick<StreamChatOptions, 'user' | 'sessionId' | 'runId' | 'signal' | 'onEvent' | 'onReset' | 'onConnectionChange'>,
  initial?: RunSnapshot,
): Promise<void> {
  let snapshot = initial
  let initialized = Boolean(initial)
  let cursor = 0
  let historySeq = 0
  let delay = 500
  let eventCallbackFailed = false
  const dispatch = (event: RunEvent) => {
    try { options.onEvent(event) }
    catch (error) { eventCallbackFailed = true; throw error }
  }
  const reset = async (value: RunSnapshot) => {
    if (!options.onReset) throw new ApiError('运行仍在后台，请刷新页面恢复进度', 0, 'resume_required')
    await options.onReset(value)
    cursor = value.cursor
    historySeq = value.history_seq
    if (value.finished && value.terminal) dispatch(value.terminal as RunEvent)
    return value.finished
  }
  while (!options.signal?.aborted) {
    try {
      if (!initialized) {
        snapshot = runSnapshotSchema.parse(await requestJson(
          `/api/runs/${encodeURIComponent(options.runId)}?${runQuery(options)}`, { signal: options.signal },
        ))
        initialized = true
      }
      if (snapshot) {
        if (await reset(snapshot)) return
        snapshot = undefined
      }
      const query = runQuery(options)
      query.set('after', String(cursor))
      query.set('history_seq', String(historySeq))
      const batch = await requestJson<{
        reset: boolean; snapshot?: unknown; events?: unknown[]; cursor?: number; finished?: boolean
      }>(`/api/runs/${encodeURIComponent(options.runId)}/events?${query}`, { signal: options.signal })
      options.onConnectionChange?.(true)
      delay = 500
      if (batch.reset) {
        snapshot = runSnapshotSchema.parse(batch.snapshot)
        continue
      }
      for (const value of batch.events || []) {
        const event = runEventSchema.parse(value) as RunEvent
        const sequence = Number(event.metadata?.web_event_seq || 0)
        if (sequence <= cursor) continue
        dispatch(event)
        cursor = sequence
        if (event.type === 'done' || event.type === 'error' && !isProvisionalRunError(event)) return
      }
      if (batch.finished) throw new ApiError('运行已结束，请核对已保存的记录', 409, 'missing_terminal')
    } catch (error) {
      if (eventCallbackFailed || options.signal?.aborted || !transientConnectionError(error)) throw error
      options.onConnectionChange?.(false)
      await reconnectDelay(delay, options.signal)
      delay = Math.min(5000, delay * 2)
    }
  }
  throw new DOMException('连接已取消', 'AbortError')
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

async function openChatStream(options: StreamChatOptions): Promise<void> {
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

export async function streamChat(options: StreamChatOptions): Promise<void> {
  let eventCallbackFailed = false
  try {
    await openChatStream({ ...options, onEvent: event => {
      try { options.onEvent(event) }
      catch (error) { eventCallbackFailed = true; throw error }
    } })
  } catch (error) {
    if (eventCallbackFailed || options.signal?.aborted || !options.onReset || !transientConnectionError(error)) throw error
    options.onConnectionChange?.(false)
    // Read-only recovery uses the existing run_id. Never repeat POST /api/chat.
    try { await followRun(options) }
    catch (recoveryError) {
      if (recoveryError instanceof ApiError && recoveryError.status === 404
        && error instanceof ApiError && error.code === 'missing_terminal') throw error
      throw recoveryError
    }
  }
}
