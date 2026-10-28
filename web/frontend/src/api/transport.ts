import { z } from 'zod'
import { validateApiResponse } from './contracts'
import type { RunEvent } from '../types/api'

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

const apiErrorSchema = z.object({
  error: z.object({
    message: z.string().refine(value => value.trim().length > 0).optional().catch(undefined),
    code: z.string().refine(value => value.trim().length > 0).optional().catch(undefined),
  }),
})

export async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    credentials: 'same-origin',
    ...init,
  })
  if (!response.ok) {
    let payload: z.infer<typeof apiErrorSchema> | undefined
    try {
      const parsed = apiErrorSchema.safeParse(await response.json())
      payload = parsed.success ? parsed.data : undefined
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
  return validateApiResponse(path, await response.json()) as T
}

export const runEventSchema = z
  .object({
    type: z.enum([
      'text_delta',
      'reasoning_delta',
      'tool_call_start',
      'tool_call_result',
      'media_output',
      'media_input_update',
      'guidance_applied',
      'context_compression',
      'long_task_update',
      'subagent_progress',
      'background_job_progress',
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
