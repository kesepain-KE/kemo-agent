import { getHistory, releaseSessionLease, touchSessionLease } from './api/client'
import { ApiError } from './api/transport'
import { HISTORY_PAGE_SIZE } from './pages/chatRunSupport'
import type { HistoryResponse } from './types/api'

const HEARTBEAT_MS = 15_000
const EVENT_BURST_MS = 250

export type SessionAccess =
  | { kind: 'open' | 'checking' | 'unverified' | 'missing' }
  | { kind: 'closed' | 'readable'; history: HistoryResponse }

export function watchWebSession({
  user, sessionId, clientId, leaseProven, isCurrent, onAccess, onResume,
}: {
  user: string
  sessionId: string
  clientId: string
  leaseProven: boolean
  isCurrent: () => boolean
  onAccess: (state: SessionAccess, updateLatest: boolean) => void
  onResume: () => void
}) {
  let phase: 'running' | 'frozen' | 'left' = 'running'
  let disposed = false
  let pending: { startedAt: number; controller?: AbortController } | undefined
  let request = 0
  let access: SessionAccess['kind'] = leaseProven ? 'open' : 'unverified'
  let heartbeat: number
  let checkpointAt = -Infinity
  let resumedAt = performance.now()

  const report = (state: SessionAccess, updateLatest = false) => {
    access = state.kind
    onAccess(state, updateLatest && document.visibilityState !== 'hidden')
  }
  const current = () => !disposed && isCurrent()
  const invalidate = () => {
    request += 1
    pending?.controller?.abort()
    pending = undefined
  }
  const touch = async (mode: 'normal' | 'checkpoint' | 'restore') => {
    if (!current() || phase !== 'running' || access === 'missing') return
    const now = performance.now()
    if (mode === 'normal' && (access === 'closed' || (pending && now - pending.startedAt < 2 * HEARTBEAT_MS))) return
    invalidate()
    const id = request
    const ownsRequest = () => current() && phase === 'running' && id === request
    // Keepalive must survive a freeze. Other probes can be superseded by a new heartbeat.
    const controller = mode === 'checkpoint' ? undefined : new AbortController()
    pending = { startedAt: now, controller }
    try {
      const leased = await touchSessionLease(user, sessionId, clientId, mode === 'checkpoint', controller?.signal)
      if (!ownsRequest()) return
      if (leased) report({ kind: 'open' }, mode === 'restore')
      else if (access !== 'closed' && access !== 'readable') report({ kind: 'unverified' })
    } catch (error) {
      if (!ownsRequest()) return
      if (!(error instanceof ApiError) || error.status !== 404) {
        if (access !== 'open' && access !== 'closed' && access !== 'readable') report({ kind: 'unverified' })
        return
      }
      if (access !== 'closed' && access !== 'readable') report({ kind: 'checking' })
      try {
        const history = await getHistory(user, sessionId, { source: 'web', limit: HISTORY_PAGE_SIZE }, controller?.signal)
        if (!ownsRequest()) return
        if (history.user !== user || history.source !== 'web' || history.session_id !== sessionId) {
          report({ kind: 'unverified' })
          return
        }
        // History is newer than the failed lease; another tab may have reopened.
        const closed = history.state === 'closed' || (history.state === undefined && error.code === 'session_closed')
        report({ kind: closed ? 'closed' : 'readable', history }, mode === 'restore')
      } catch (historyError) {
        if (!ownsRequest()) return
        if (historyError instanceof ApiError && historyError.status === 404 && historyError.code === 'not_found') {
          report({ kind: 'missing' })
        } else if (access !== 'closed' && access !== 'readable') {
          report({ kind: 'unverified' })
        }
      }
    } finally {
      if (ownsRequest()) pending = undefined
    }
  }
  const startHeartbeat = () => {
    window.clearInterval(heartbeat)
    heartbeat = window.setInterval(() => { void touch('normal') }, HEARTBEAT_MS)
  }
  const checkpoint = () => {
    const now = performance.now()
    if (phase !== 'running' || now - checkpointAt < EVENT_BURST_MS) return
    checkpointAt = now
    void touch('checkpoint')
  }
  const freeze = () => {
    if (phase !== 'running') return
    checkpoint()
    phase = 'frozen'
    invalidate()
    window.clearInterval(heartbeat)
  }
  const release = () => {
    if (phase === 'left') return
    phase = 'left'
    invalidate()
    window.clearInterval(heartbeat)
    if (access !== 'missing') void releaseSessionLease(user, sessionId, clientId, true).catch(() => undefined)
  }
  const resume = async (force = false, pageShown = false) => {
    if (!current() || (phase === 'left' && !pageShown)) return
    const now = performance.now()
    if (phase === 'running' && !force && now - resumedAt < EVENT_BURST_MS) return
    phase = 'running'
    resumedAt = now
    checkpointAt = -Infinity
    startHeartbeat()
    onResume()
    await touch('restore')
    if (current()) return access
  }
  const pagehide = (event: PageTransitionEvent) => {
    if (event.persisted) freeze()
    else release()
  }
  const thaw = () => { void resume() }
  const pageshow = () => { void resume(false, true) }
  const online = () => { if (phase === 'running') void resume() }
  const visibility = () => {
    if (phase === 'left') return
    if (document.visibilityState === 'hidden') {
      resumedAt = -Infinity
      checkpoint()
    } else {
      void resume()
    }
  }

  window.addEventListener('pagehide', pagehide)
  window.addEventListener('pageshow', pageshow)
  window.addEventListener('online', online)
  document.addEventListener('freeze', freeze)
  document.addEventListener('resume', thaw)
  document.addEventListener('visibilitychange', visibility)
  startHeartbeat()
  if (leaseProven) report({ kind: 'open' }, true)
  else {
    onResume()
    void touch('restore')
  }

  return {
    recheck: () => phase === 'running' ? resume(true) : undefined,
    dispose: () => {
      disposed = true
      window.removeEventListener('pagehide', pagehide)
      window.removeEventListener('pageshow', pageshow)
      window.removeEventListener('online', online)
      document.removeEventListener('freeze', freeze)
      document.removeEventListener('resume', thaw)
      document.removeEventListener('visibilitychange', visibility)
      window.clearInterval(heartbeat)
      // A same-document cleanup during suspension must not undo BFCache retention.
      if (phase !== 'frozen' || !isCurrent()) release()
    },
  }
}
