import type { QueryClient } from '@tanstack/react-query'
import type { ActiveSessionResponse, SessionsResponse } from './types/api'

/** Only a matching, server-confirmed creation can replace the initial lease POST. */
export function hasCreatedSessionLease(result: ActiveSessionResponse, user: string, clientId: string) {
  return result.created && result.user === user && result.client_id === clientId
    && (result.active_clients ?? 0) > 0
    && result.session.source === 'web' && result.session.state === 'open'
}

export function cacheCreatedSession(client: QueryClient, result: ActiveSessionResponse) {
  client.setQueryData<SessionsResponse>(['sessions', result.user], previous => previous ? {
    ...previous,
    sessions: [result.session, ...previous.sessions.filter(item =>
      item.session_id !== result.session.session_id || (item.source || 'web') !== 'web')],
  } : previous)
}
