import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { server } from '../test/server'
import { ApiError, AUTH_REQUIRED_EVENT, requestJson } from './transport'

afterEach(() => vi.restoreAllMocks())

describe('requestJson boundaries', () => {
  it('preserves request options and sends same-origin credentials', async () => {
    let request: Request | undefined
    server.use(http.post('/transport-test', ({ request: incoming }) => {
      request = incoming
      return HttpResponse.json({ value: 'ok' })
    }))
    expect(await requestJson('/transport-test', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Test': 'sent' },
      body: JSON.stringify({ prompt: 'offline' }),
    })).toEqual({ value: 'ok' })
    expect(request?.credentials).toBe('same-origin')
    expect(request?.headers.get('x-test')).toBe('sent')
    expect(await request?.json()).toEqual({ prompt: 'offline' })
  })

  it('validates registered endpoint contracts without echoing private values', async () => {
    server.use(http.get('/api/users/test/tasks', () =>
      HttpResponse.json({ user: 'PRIVATE-CONTENT' })))
    await expect(requestJson('/api/users/test/tasks?view=all')).rejects.toThrow(
      'API response contract mismatch (/api/users/test/tasks)',
    )
    await expect(requestJson('/api/users/test/tasks')).rejects.not.toThrow('PRIVATE-CONTENT')
  })

  it('rejects invalid success JSON rather than pretending success', async () => {
    server.use(http.get('/transport-test', () =>
      new HttpResponse('not-json', { headers: { 'Content-Type': 'application/json' } })))
    await expect(requestJson('/transport-test')).rejects.toBeInstanceOf(SyntaxError)
  })

  it.each([401, 403, 500])('keeps structured error status and code (%s)', async (status) => {
    const auth = vi.fn()
    window.addEventListener(AUTH_REQUIRED_EVENT, auth)
    try {
      server.use(http.get('/transport-test', () => HttpResponse.json({
        error: { message: 'access denied', code: 'authentication_required' },
      }, { status })))
      const error = await requestJson('/transport-test').catch((value: unknown) => value)
      expect(error).toBeInstanceOf(ApiError)
      expect(error).toMatchObject({ message: 'access denied', status, code: 'authentication_required' })
      expect(auth).toHaveBeenCalledTimes(status === 401 ? 1 : 0)
    } finally {
      window.removeEventListener(AUTH_REQUIRED_EVENT, auth)
    }
  })

  it.each([
    '<html>upstream unavailable</html>', null, [], { error: null },
    { error: { message: { secret: 'private' }, code: 12 } },
    { error: { message: '', code: '' } },
  ])('uses safe error fallback for malformed payload %j', async (body) => {
    const auth = vi.fn()
    window.addEventListener(AUTH_REQUIRED_EVENT, auth)
    try {
      server.use(http.get('/transport-test', () => typeof body === 'string'
        ? new HttpResponse(body, { status: 401 })
        : HttpResponse.json(body, { status: 401 })))
      const error = await requestJson('/transport-test').catch((value: unknown) => value)
      expect(error).toBeInstanceOf(ApiError)
      expect(error).toMatchObject({ message: '请求失败（401）', status: 401, code: 'request_failed' })
      expect(auth).not.toHaveBeenCalled()
    } finally {
      window.removeEventListener(AUTH_REQUIRED_EVENT, auth)
    }
  })

  it('preserves network and caller cancellation errors unchanged', async () => {
    const error = new DOMException('caller aborted', 'AbortError')
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(error)
    await expect(requestJson('/transport-test')).rejects.toBe(error)
  })
})
