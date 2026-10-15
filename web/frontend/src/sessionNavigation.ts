import { getActiveSession, touchSessionLease } from './api/client'
import { ApiError } from './api/transport'
import type { ActiveSessionResponse } from './types/api'

const sessionKey = (user: string) => `kemo-last-web-session:${encodeURIComponent(user)}`

type StorageName = 'sessionStorage' | 'localStorage'

function read(storage: StorageName, user: string) {
  try { return window[storage].getItem(sessionKey(user)) || '' } catch { return '' }
}

/** 标签页恢复自己的空间；新打开的页面才使用该用户最近访问的空间。 */
export function readRememberedSession(user: string) {
  return read('sessionStorage', user) || read('localStorage', user)
}

export function rememberSession(user: string, sessionId: string, updateLatest = true) {
  if (!user || !sessionId) return
  for (const storage of ['sessionStorage', 'localStorage'] as const) {
    if (storage === 'localStorage' && !updateLatest) continue
    try {
      if (read(storage, user) !== sessionId) window[storage].setItem(sessionKey(user), sessionId)
    } catch { /* URL 仍可续接。 */ }
  }
}

export function forgetSession(user: string, sessionId?: string) {
  for (const storage of ['sessionStorage', 'localStorage'] as const) {
    try {
      if (!sessionId || read(storage, user) === sessionId) window[storage].removeItem(sessionKey(user))
    } catch { /* 浏览器可能禁用存储。 */ }
  }
}

/** 缓存只保存身份；服务端租约验证后才恢复，不隐式重开 closed/deleted 历史。 */
export async function restoreWebSession(user: string, clientId: string): Promise<ActiveSessionResponse> {
  const remembered = readRememberedSession(user)
  if (remembered) {
    try {
      await touchSessionLease(user, remembered, clientId)
      return {
        user, client_id: clientId, active_key: '', created: false,
        session: {
          session_id: remembered, source: 'web', state: 'open',
          window: '', title: '', rounds: 0, updated_at: '',
        },
      }
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 404) throw error
      forgetSession(user, remembered)
    }
  }
  return getActiveSession(user, clientId)
}

export function chatSessionUrl(user: string, sessionId: string) {
  const query = new URLSearchParams()
  if (user) query.set('user', user)
  if (sessionId) query.set('session', sessionId)
  return `/chat${query.size ? `?${query}` : ''}`
}
