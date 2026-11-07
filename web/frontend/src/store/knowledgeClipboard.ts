import { create } from 'zustand'
import { transferKnowledgeEntry } from '../api/client'

export type KnowledgeScope = 'user' | 'shared' | 'global'
export type ClipboardAction = 'copy' | 'move'
export interface KnowledgeEntryRef {
  scope: KnowledgeScope
  relative_path: string
  name: string
  kind: 'file' | 'directory'
}
export interface KnowledgeClipboard {
  user: string
  action: ClipboardAction
  entries: KnowledgeEntryRef[]
}

interface ClipboardState {
  user: string
  clipboard: KnowledgeClipboard | null
  pending: boolean
  activateUser: (user: string) => void
  setClipboard: (user: string, entries: KnowledgeEntryRef[], action: ClipboardAction) => void
  clear: () => void
  discard: (scope: string, path: string) => void
}

export function isWithin(path: string, parent: string) {
  return path === parent || Boolean(parent && path.startsWith(`${parent}/`))
}

export const useKnowledgeClipboard = create<ClipboardState>((set) => ({
  user: '',
  clipboard: null,
  pending: false,
  activateUser: (user) => set((state) => user === state.user ? state : { user, clipboard: null }),
  setClipboard: (user, entries, action) => {
    const sources = entries.filter((entry) => entry.relative_path && !entries.some((parent) =>
      parent !== entry && parent.scope === entry.scope && parent.kind === 'directory'
      && isWithin(entry.relative_path, parent.relative_path)))
      .map(({ scope, relative_path, name, kind }) => ({ scope, relative_path, name, kind }))
    set({ user, clipboard: sources.length ? { user, action, entries: sources } : null })
  },
  clear: () => set({ clipboard: null }),
  discard: (scope, path) => set((state) => {
    if (!state.clipboard) return state
    const entries = state.clipboard.entries.filter((entry) =>
      entry.scope !== scope || !isWithin(entry.relative_path, path))
    return { clipboard: entries.length ? { ...state.clipboard, entries } : null }
  }),
}))

export function canPasteKnowledge(clipboard: KnowledgeClipboard | null, target: KnowledgeEntryRef) {
  return Boolean(clipboard?.entries.length && target.kind === 'directory'
    && clipboard.entries.every((source) => {
      if (source.scope !== target.scope) return true
      if (source.kind === 'directory' && isWithin(target.relative_path, source.relative_path)) return false
      return clipboard.action !== 'move'
        || [target.relative_path, source.name].filter(Boolean).join('/') !== source.relative_path
    }))
}

export function isCutEntry(clipboard: KnowledgeClipboard | null, entry: KnowledgeEntryRef) {
  return clipboard?.action === 'move' && clipboard.entries.some((source) =>
    source.scope === entry.scope && isWithin(entry.relative_path, source.relative_path))
}

export async function pasteKnowledgeClipboard(user: string, target: KnowledgeEntryRef) {
  const { clipboard, pending } = useKnowledgeClipboard.getState()
  if (pending || clipboard?.user !== user || !canPasteKnowledge(clipboard, target)) return []
  const completed: Array<{ source: KnowledgeEntryRef; scope: string; path: string }> = []
  useKnowledgeClipboard.setState({ pending: true })
  try {
    for (const source of clipboard.entries) {
      const result = await transferKnowledgeEntry(user, source.scope, source.relative_path,
        clipboard.action, target.scope, [target.relative_path, source.name].filter(Boolean).join('/'),
        clipboard.action === 'copy')
      completed.push({ source, scope: result.target_scope, path: result.new_relative_path })
    }
    return completed
  } finally {
    useKnowledgeClipboard.setState((state) => {
      if (clipboard.action !== 'move' || state.clipboard !== clipboard) return { pending: false }
      const entries = clipboard.entries.filter((entry) => !completed.some((item) => item.source === entry))
      return { pending: false, clipboard: entries.length ? { ...clipboard, entries } : null }
    })
  }
}
