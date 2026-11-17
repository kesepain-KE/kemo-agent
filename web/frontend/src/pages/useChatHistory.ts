import { useEffect } from 'react'
import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query'
import { getHistory } from '../api/client'
import { HISTORY_PAGE_SIZE } from './chatRunSupport'
import { chatHistoryKey } from './chatHistory'

export function useChatHistory(user: string, sessionId: string, enabled: boolean, frozenBefore?: number) {
  const client = useQueryClient()
  const pageOptions = (before: number) => ({
    queryKey: [...chatHistoryKey(user, sessionId, frozenBefore), 'page', before],
    queryFn: ({ signal }: { signal: AbortSignal }) => getHistory(
      user, sessionId, { limit: 100, before }, signal,
    ),
    staleTime: Infinity,
    gcTime: 120_000,
    retry: false,
  })
  const history = useInfiniteQuery({
    queryKey: chatHistoryKey(user, sessionId, frozenBefore),
    queryFn: ({ pageParam, signal }) => pageParam === undefined
      ? getHistory(user, sessionId, { limit: HISTORY_PAGE_SIZE, before: frozenBefore }, signal)
      : client.fetchQuery(pageOptions(pageParam)),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (lastPage) => lastPage.pagination?.has_more_before
      ? lastPage.pagination.next_before ?? undefined
      : undefined,
    enabled,
    retry: false,
    staleTime: 60_000,
    gcTime: 120_000,
  })
  const before = history.hasNextPage
    ? history.data?.pages.at(-1)?.pagination?.next_before
    : undefined

  useEffect(() => {
    if (!enabled || !before || history.isFetching) return
    // Warm one page without rendering it or moving the reader's scroll position.
    const timer = window.setTimeout(() => {
      void client.prefetchQuery(pageOptions(before))
    }, 150)
    return () => window.clearTimeout(timer)
  }, [client, user, sessionId, frozenBefore, enabled, before, history.isFetching, history.dataUpdatedAt])

  useEffect(() => {
    // Retain recent pages across switches, bounded by both bytes and sessions.
    const cached = client.getQueryCache().findAll({ queryKey: ['history'] })
      .filter(query => !query.isActive())
      .sort((a, b) => b.state.dataUpdatedAt - a.state.dataUpdatedAt)
    let bytes = 0
    const sessions = new Set<string>()
    for (const query of cached) {
      const scope = JSON.stringify(query.queryKey.slice(0, 3))
      sessions.add(scope)
      bytes += JSON.stringify(query.state.data ?? '').length * 2
      if (sessions.size > 6 || bytes > 32 * 1024 * 1024) {
        client.removeQueries({ queryKey: query.queryKey, exact: true })
      }
    }
  }, [client, user, sessionId, history.dataUpdatedAt])

  return history
}
