import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import * as api from '../api/client'
import { useHistoryBrowser } from './useHistoryBrowser'
import type { SessionsResponse } from '../types/api'

const page: SessionsResponse = { user: 'alice', source: 'all', sessions: [], has_more: false, favorites_count: 78 }
afterEach(() => vi.restoreAllMocks())
function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
  client.setQueryData(['sessions', 'alice'], page)
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  return { client, ...renderHook(({ user, open }) => {
    const recent = useQuery({ queryKey: ['sessions', user], queryFn: () => Promise.resolve(page) })
    return useHistoryBrowser(user, open, recent)
  }, { wrapper, initialProps: { user: 'alice', open: true } }) }
}

describe('server-backed history filters', () => {
  it('entering favorites clears stale date; date and submitted search can narrow favorites; close resets filters', async () => {
    const get = vi.spyOn(api, 'getSessions').mockResolvedValue(page)
    const hook = setup()
    act(() => hook.result.current.setArchiveDate('2026-10-20'))
    await waitFor(() => expect(get).toHaveBeenCalledWith('alice', '', 50, '', 'all', '2026-10-20', false))
    act(() => hook.result.current.setFavoritesOnly(true))
    await waitFor(() => expect(get).toHaveBeenCalledWith('alice', '', 50, '', 'all', '', true))
    expect(hook.result.current.archiveDate).toBe('')
    act(() => { hook.result.current.setArchiveDate('2026-10-21'); hook.result.current.setSearch('正文搜索') })
    await waitFor(() => expect(get).toHaveBeenCalledWith('alice', '正文搜索', 50, '', 'all', '2026-10-21', true))
    hook.rerender({ user: 'alice', open: false })
    await waitFor(() => expect(hook.result.current.favoritesOnly).toBe(false))
    expect(hook.result.current.archiveDate).toBe('')
    expect(hook.result.current.search).toBe('')
  })

  it('late pagination cannot overwrite a new filter or a refreshed first page', async () => {
    const first = { ...page, has_more: true, next_cursor: 'old-cursor' }
    let resolvePage!: (value: SessionsResponse) => void
    vi.spyOn(api, 'getSessions').mockImplementation((_u, _q, _l, before) => before
      ? new Promise(resolve => { resolvePage = resolve })
      : Promise.resolve(page))
    const hook = setup()
    act(() => hook.client.setQueryData(['sessions', 'alice'], first))
    await waitFor(() => expect(hook.result.current.query.data?.has_more).toBe(true))
    let pending!: Promise<void>
    act(() => { pending = hook.result.current.loadMore() })
    act(() => hook.result.current.setFavoritesOnly(true))
    await act(async () => { resolvePage({ ...page, sessions: [{ session_id: 'stale', window: '', title: '', rounds: 0, updated_at: '' }] }); await pending })
    await waitFor(() => expect(hook.result.current.query.data?.sessions).toEqual([]))
    expect(hook.client.getQueryData<SessionsResponse>(['sessions', 'alice'])?.next_cursor).toBe('old-cursor')
    act(() => hook.result.current.setFavoritesOnly(false))
    act(() => { pending = hook.result.current.loadMore() })
    act(() => hook.client.setQueryData(['sessions', 'alice'], { ...page, next_cursor: 'refreshed-cursor' }))
    await act(async () => { resolvePage(page); await pending })
    expect(hook.client.getQueryData<SessionsResponse>(['sessions', 'alice'])?.next_cursor).toBe('refreshed-cursor')
  })

  it('leaving and re-entering the same filter invalidates its old page request', async () => {
    let resolvePage!: (value: SessionsResponse) => void
    vi.spyOn(api, 'getSessions').mockImplementation((_u, _q, _l, before) => before
      ? new Promise(resolve => { resolvePage = resolve })
      : Promise.resolve(page))
    const hook = setup()
    act(() => hook.client.setQueryData(['sessions', 'alice'], { ...page, has_more: true, next_cursor: 'cursor' }))
    await waitFor(() => expect(hook.result.current.query.data?.has_more).toBe(true))
    let pending!: Promise<void>
    act(() => { pending = hook.result.current.loadMore() })
    act(() => hook.result.current.setFavoritesOnly(true))
    act(() => hook.result.current.setFavoritesOnly(false))
    await act(async () => {
      resolvePage({ ...page, sessions: [{ session_id: 'stale', window: '', title: '', rounds: 0, updated_at: '' }] })
      await pending
    })
    expect(hook.result.current.query.data?.sessions).toEqual([])
    expect(hook.result.current.query.data?.next_cursor).toBe('cursor')
  })
})
