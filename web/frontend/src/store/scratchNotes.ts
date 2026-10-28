import { create } from 'zustand'
import { chatDraftKey } from './chatDrafts'

interface Note { text: string; revision: number; saved: boolean }
const empty: Note = { text: '', revision: 0, saved: true }
const prefix = 'kemo:scratch:'
export const useScratchNotes = create<{
  notes: Record<string, Note>
  load: (key: string) => void
  write: (key: string, text: string) => void
  clearRevision: (key: string, revision: number) => void
}>((set, get) => ({
  notes: {},
  load: key => {
    if (get().notes[key]) return
    let note = empty
    try {
      const text = localStorage.getItem(prefix + key)
      if (text) note = { ...empty, text }
    } catch { note = { ...empty, saved: false } }
    set(state => ({ notes: { ...state.notes, [key]: note } }))
  },
  write: (key, text) => {
    let saved = true
    try {
      if (text) localStorage.setItem(prefix + key, text)
      else localStorage.removeItem(prefix + key)
    } catch { saved = false }
    set(state => ({ notes: { ...state.notes, [key]: {
      text, saved, revision: (state.notes[key]?.revision ?? 0) + 1,
    } } }))
  },
  clearRevision: (key, revision) => {
    if (get().notes[key]?.revision === revision) get().write(key, '')
  },
}))

export const scratchKey = chatDraftKey
export const EMPTY_SCRATCH = empty
export const useScratchSender = create<{
  handlers: Record<string, (text: string) => Promise<boolean>>
  bind: (key: string, handler: (text: string) => Promise<boolean>) => () => void
}>((set, get) => ({
  handlers: {},
  bind: (key, handler) => {
    set(state => ({ handlers: { ...state.handlers, [key]: handler } }))
    return () => {
      if (get().handlers[key] !== handler) return
      set(state => {
        const handlers = { ...state.handlers }
        delete handlers[key]
        return { handlers }
      })
    }
  },
}))
