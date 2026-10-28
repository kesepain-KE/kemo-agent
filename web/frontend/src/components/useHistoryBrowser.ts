import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query'
import { getSessions } from '../api/client'
import type { SessionsResponse } from '../types/api'

/** Filters belong to server pagination; drafts remain inside the search input. */
export function useHistoryBrowser(user: string, open: boolean, recent: UseQueryResult<SessionsResponse, Error>) {
  const client = useQueryClient()
  const [archiveDate, setArchiveDate] = useState('')
  const [favoritesOnly, setFavoritesOnly] = useState(false)
  const [search, setSearch] = useState('')
  const [loadingMore, setLoadingMore] = useState(false)
  const filtered = Boolean(archiveDate || favoritesOnly || search)
  const queryKey = filtered ? ['history-sessions', user, archiveDate, favoritesOnly, search] : ['sessions', user]
  const identity = JSON.stringify([user, open, ...queryKey])
  const currentIdentity = useRef(identity)
  const generation = useRef(0)
  if (currentIdentity.current !== identity) {
    currentIdentity.current = identity
    generation.current += 1
  }
  const pendingPage = useRef<number | null>(null)
  const filteredQuery = useQuery({
    queryKey: ['history-sessions', user, archiveDate, favoritesOnly, search],
    queryFn: () => getSessions(user, search, 50, '', 'all', archiveDate, favoritesOnly),
    enabled: Boolean(user && open && filtered),
  })
  const query = filtered ? filteredQuery : recent
  useEffect(() => {
    setLoadingMore(false)
  }, [identity])
  useEffect(() => {
    if (!open) { setArchiveDate(''); setFavoritesOnly(false); setSearch('') }
  }, [open])

  const loadMore = async () => {
    const data = query.data
    const requestedGeneration = generation.current
    if (!user || !data?.has_more || !data.next_cursor || pendingPage.current === requestedGeneration) return
    pendingPage.current = requestedGeneration
    setLoadingMore(true)
    try {
      const page = await getSessions(user, search, 50, data.next_cursor, 'all', archiveDate, favoritesOnly)
      // A late page must never repopulate a different user/filter, or undo a refetch.
      if (generation.current !== requestedGeneration) return
      client.setQueryData<SessionsResponse>(queryKey, current => {
        if (!current || current !== data) return current
        const keys = new Set(current.sessions.map(item => `${item.source || 'web'}:${item.session_id}`))
        return { ...page, sessions: [...current.sessions, ...page.sessions.filter(item => !keys.has(`${item.source || 'web'}:${item.session_id}`))] }
      })
    } finally {
      if (pendingPage.current === requestedGeneration) pendingPage.current = null
      if (generation.current === requestedGeneration) setLoadingMore(false)
    }
  }
  return {
    archiveDate, setArchiveDate, favoritesOnly,
    setFavoritesOnly: (value: boolean) => { if (value) setArchiveDate(''); setFavoritesOnly(value) },
    search, setSearch, query, loadingMore, loadMore,
  }
}
