import { useUiStore } from '../store/ui'
import { coreMessages } from './core'
import { appearanceMessages } from './appearance'
import { workspaceMessages } from './workspace'
import { backgroundMessages } from './background'
import { workbenchMessages } from './workbench'
import { agentDetailMessages } from './agentDetails'
import { conversationRecordMessages } from './conversationRecords'
import { historyMarkMessages } from './historyMarks'
import { scratchMessages } from './scratch'
import { agentBudgetMessages } from './agentBudgets'
import { presenceDispatchMessages } from './presenceDispatch'
import { languages, normalizeLanguage, type Language } from './locales'

export { languages, normalizeLanguage, type Language } from './locales'

export const messages = new Map(
  [coreMessages, appearanceMessages, workspaceMessages, backgroundMessages, workbenchMessages, agentDetailMessages, conversationRecordMessages, historyMarkMessages, scratchMessages, agentBudgetMessages, presenceDispatchMessages].flatMap((group) => group.trim().split('\n').map((row) => {
    const [source, ...translations] = row.split('|')
    return [source, translations] as const
  })),
)

/** Explicit UI text lookup, not DOM traversal or user-content machine translation. */
export function translate(source: string, language: Language, values: Record<string, string | number> = {}) {
  const index = languages.findIndex(({ code }) => code === language) - 1
  const translated = index < 0 ? source : messages.get(source)?.[index] || source
  return translated.replace(/\{(\w+)\}/g, (placeholder, key: string) => Object.hasOwn(values, key) ? String(values[key]) : placeholder)
}

export function t(source: string, values?: Record<string, string | number>) {
  return translate(source, useUiStore.getState().language, values)
}

/** Subscribe without remounting the conversation or losing form drafts. */
export function useLanguage() {
  return useUiStore((state) => state.language)
}
