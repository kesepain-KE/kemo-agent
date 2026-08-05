import { create } from 'zustand'

export interface PendingUploadedFile {
  path: string
  name: string
  size: number
  mimeType?: string
  mediaKind?: 'image' | 'audio' | 'video' | 'file'
  checksumSha256?: string
}

export interface UploadFeedback {
  tone: 'pending' | 'success' | 'error'
  text: string
}

export interface ChatDraftSnapshot {
  text: string
  pendingUploads: PendingUploadedFile[]
  uploadFeedback: UploadFeedback | null
  uploading: boolean
}

type ValueUpdater<T> = T | ((current: T) => T)

interface ChatDraftState {
  drafts: Record<string, ChatDraftSnapshot>
  setText: (key: string, value: ValueUpdater<string>) => void
  setPendingUploads: (key: string, value: ValueUpdater<PendingUploadedFile[]>) => void
  setUploadFeedback: (key: string, value: UploadFeedback | null) => void
  setUploading: (key: string, value: boolean) => void
  moveDraft: (fromKey: string, toKey: string) => void
  clearDraft: (key: string) => void
  clearAll: () => void
}

export const EMPTY_CHAT_DRAFT: ChatDraftSnapshot = Object.freeze({
  text: '',
  pendingUploads: Object.freeze([]) as unknown as PendingUploadedFile[],
  uploadFeedback: null,
  uploading: false,
})

export function chatDraftKey(user: string, sessionId: string) {
  return JSON.stringify([user, sessionId || '__new__'])
}

function draftFor(state: ChatDraftState, key: string): ChatDraftSnapshot {
  return state.drafts[key] ?? EMPTY_CHAT_DRAFT
}

function updateValue<T>(current: T, value: ValueUpdater<T>): T {
  return typeof value === 'function' ? (value as (current: T) => T)(current) : value
}

export const useChatDraftStore = create<ChatDraftState>((set) => ({
  drafts: {},
  setText: (key, value) => set((state) => {
    const current = draftFor(state, key)
    const text = updateValue(current.text, value)
    if (text === current.text) return state
    return { drafts: { ...state.drafts, [key]: { ...current, text } } }
  }),
  setPendingUploads: (key, value) => set((state) => {
    const current = draftFor(state, key)
    const pendingUploads = updateValue(current.pendingUploads, value)
    if (pendingUploads === current.pendingUploads) return state
    return { drafts: { ...state.drafts, [key]: { ...current, pendingUploads } } }
  }),
  setUploadFeedback: (key, uploadFeedback) => set((state) => {
    const current = draftFor(state, key)
    if (uploadFeedback === current.uploadFeedback) return state
    return { drafts: { ...state.drafts, [key]: { ...current, uploadFeedback } } }
  }),
  setUploading: (key, uploading) => set((state) => {
    const current = draftFor(state, key)
    if (uploading === current.uploading) return state
    return { drafts: { ...state.drafts, [key]: { ...current, uploading } } }
  }),
  moveDraft: (fromKey, toKey) => set((state) => {
    if (fromKey === toKey || !state.drafts[fromKey]) return state
    const drafts = { ...state.drafts, [toKey]: state.drafts[fromKey] }
    delete drafts[fromKey]
    return { drafts }
  }),
  clearDraft: (key) => set((state) => {
    if (!state.drafts[key]) return state
    const drafts = { ...state.drafts }
    delete drafts[key]
    return { drafts }
  }),
  clearAll: () => set({ drafts: {} }),
}))
