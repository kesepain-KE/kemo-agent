import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it } from 'vitest'
import { cacheCreatedSession, hasCreatedSessionLease } from './sessionCreation'
import type { ActiveSessionResponse, SessionsResponse } from './types/api'

const result: ActiveSessionResponse = {
  user: 'alice', active_key: 'interactive:alice:web_fixture', created: true,
  client_id: 'web_fixture', active_clients: 1,
  session: { session_id: 'new', source: 'web', state: 'open', window: '',
    title: '', rounds: 0, updated_at: '2026-10-15T10:00:00Z' },
}

describe('新会话确认结果复用', () => {
  it('仅匹配用户、标签页及真实租约时跳过初始 lease 请求', () => {
    expect(hasCreatedSessionLease(result, 'alice', 'web_fixture')).toBe(true)
    expect(hasCreatedSessionLease(result, 'bob', 'web_fixture')).toBe(false)
    expect(hasCreatedSessionLease(result, 'alice', 'different')).toBe(false)
    expect(hasCreatedSessionLease({ ...result, active_clients: 0 }, 'alice', 'web_fixture')).toBe(false)
    expect(hasCreatedSessionLease({ ...result, client_id: undefined }, 'alice', 'web_fixture')).toBe(false)
    expect(hasCreatedSessionLease({ ...result, created: false }, 'alice', 'web_fixture')).toBe(false)
    expect(hasCreatedSessionLease({ ...result, session: { ...result.session, state: 'closed' } },
      'alice', 'web_fixture')).toBe(false)
  })

  it('立即补入当前用户列表且去重，不触碰其他用户及其他来源', () => {
    const client = new QueryClient()
    const initial = { user: 'alice', source: 'all', sessions: [
      result.session, { ...result.session, source: 'app', title: 'App same id' },
      { ...result.session, session_id: 'old', title: 'Old conversation' },
    ] } as SessionsResponse
    const other = { user: 'bob', source: 'all', sessions: [] } as SessionsResponse
    client.setQueryData(['sessions', 'alice'], initial)
    client.setQueryData(['sessions', 'bob'], other)
    cacheCreatedSession(client, result)
    const cached = client.getQueryData<SessionsResponse>(['sessions', 'alice'])!
    expect(cached.sessions).toHaveLength(3)
    expect(cached.sessions[0]).toEqual(result.session)
    expect(cached.sessions[1].source).toBe('app')
    expect(client.getQueryData(['sessions', 'bob'])).toEqual(other)
  })

  it('列表尚未载入时不伪造全量库存', () => {
    const client = new QueryClient()
    cacheCreatedSession(client, result)
    expect(client.getQueryData(['sessions', 'alice'])).toBeUndefined()
  })
})
