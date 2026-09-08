import { z } from 'zod'
import type { ApiErrorPayload, RunEvent } from '../types/api'

export const apiBase = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? ''

export const AUTH_REQUIRED_EVENT = 'kemo-auth-required'
export const AVATAR_UPDATED_EVENT = 'kemo-avatar-updated'

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code = 'request_failed',
  ) {
    super(message)
  }
}

export async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
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

export const runEventSchema = z
  .object({
    type: z.enum([
      'text_delta',
      'reasoning_delta',
      'tool_call_start',
      'tool_call_result',
      'media_output',
      'guidance_applied',
      'context_compression',
      'long_task_update',
      'usage',
      'retrying',
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

export function isProvisionalRunError(event: RunEvent) {
  return event.type === 'error'
    && event.metadata?.retryable === true
    && event.metadata?.committed === false
}
