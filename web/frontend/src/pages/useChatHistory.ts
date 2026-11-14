import { useEffect } from 'react'
import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query'
import { getHistory } from '../api/client'
import { HISTORY_PAGE_SIZE } from './chatRunSupport'

export function useChatHistory(user: string, sessionId: string, enabled: boolean) {
  const client = useQueryClient()
  const pageOptions = (before: number) => ({
    queryKey: ['history', user, sessionId, 'page', before],
    queryFn: ({ signal }: { signal: AbortSignal }) => getHistory(
      user, sessionId, { limit: 100, before }, signal,
    ),
    staleTime: Infinity,
    gcTime: Infinity,
    retry: false,
  })
  const history = useInfiniteQuery({
    queryKey: ['history', user, sessionId],
    queryFn: ({ pageParam, signal }) => pageParam === undefined
      ? getHistory(user, sessionId, { limit: HISTORY_PAGE_SIZE }, signal)
      : client.fetchQuery(pageOptions(pageParam)),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (lastPage) => lastPage.pagination?.has_more_before
      ? lastPage.pagination.next_before ?? undefined
      : undefined,
    enabled,
    retry: false,
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
  }, [client, user, sessionId, enabled, before, history.isFetching, history.dataUpdatedAt])

  useEffect(() => () => {
    const queryKey = ['history', user, sessionId, 'page']
    void client.cancelQueries({ queryKey })
    client.removeQueries({ queryKey })
  }, [client, user, sessionId])

  return history
}
