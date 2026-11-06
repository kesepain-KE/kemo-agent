import { create } from 'zustand'
import { chatRunKey } from '../components/appShellConfig'

export interface CompressionStatus {
  status: 'started' | 'ready' | 'failed'
  trigger: string
  historyTokensBefore: number
  historyTokensAfter: number
  absorbedCards: number
  content: string
}

interface CompressionStatusState {
  entries: Record<string, CompressionStatus>
  setStatus: (user: string, sessionId: string, status: CompressionStatus) => void
  clearStatus: (user: string, sessionId: string) => void
}

const completionTimers = new Map<string, ReturnType<typeof setTimeout>>()
const COMPLETION_VISIBLE_MS = 5000

export const useCompressionStatus = create<CompressionStatusState>((set) => ({
  entries: {},
  setStatus: (user, sessionId, status) => {
    const key = chatRunKey(user, sessionId)
    const existing = completionTimers.get(key)
    if (existing) clearTimeout(existing)
    completionTimers.delete(key)
    set((state) => ({ entries: { ...state.entries, [key]: status } }))
    if (status.status !== 'started') {
      completionTimers.set(key, setTimeout(() => {
        completionTimers.delete(key)
        set((state) => {
          if (state.entries[key] !== status) return state
          const entries = { ...state.entries }
          delete entries[key]
          return { entries }
        })
      }, COMPLETION_VISIBLE_MS))
    }
  },
  clearStatus: (user, sessionId) => {
    const key = chatRunKey(user, sessionId)
    const timer = completionTimers.get(key)
    if (timer) clearTimeout(timer)
    completionTimers.delete(key)
    set((state) => {
      if (!state.entries[key]) return state
      const entries = { ...state.entries }
      delete entries[key]
      return { entries }
    })
  },
}))
