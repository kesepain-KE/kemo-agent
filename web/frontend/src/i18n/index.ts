import { createInstance } from 'i18next'
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
import { interfaceMessages } from './interface'
import { pageMessages } from './pages'
import { settingsMessages } from './settings'
import { moduleWorkbenchMessages } from './moduleWorkbench'
import { languages, normalizeLanguage, type Language } from './locales'

export { languages, normalizeLanguage, type Language } from './locales'

export const catalogs = [coreMessages, appearanceMessages, workspaceMessages, backgroundMessages, workbenchMessages, agentDetailMessages, conversationRecordMessages, historyMarkMessages, scratchMessages, agentBudgetMessages, presenceDispatchMessages, interfaceMessages, pageMessages, settingsMessages, moduleWorkbenchMessages]

export const messages = new Map(
  catalogs.flatMap((group) => group.trim().split('\n').map((row) => {
    const [source, ...translations] = row.split('|')
    return [source, translations] as const
  })),
)

const i18n = createInstance()
void i18n.init({
  resources: Object.fromEntries(languages.map(({ code }, index) => [code, {
    translation: Object.fromEntries([...messages].map(([source, translations]) => [source, index === 0 ? source : translations[index - 1]])),
  }])),
  lng: 'zh-CN',
  fallbackLng: 'zh-CN',
  supportedLngs: languages.map(({ code }) => code),
  load: 'currentOnly',
  initAsync: false,
  keySeparator: false,
  nsSeparator: false,
  returnNull: false,
  returnEmptyString: false,
  interpolation: { prefix: '{', suffix: '}', escapeValue: false },
})

/** Explicit UI text lookup, not DOM traversal or user-content machine translation. */
export function translate(source: string, language: Language, values: Record<string, string | number> = {}) {
  return i18n.t(source, { ...values, lng: language, defaultValue: source })
}

export function t(source: string, values?: Record<string, string | number>) {
  return translate(source, useUiStore.getState().language, values)
}

/** Subscribe without remounting the conversation or losing form drafts. */
export function useLanguage() {
  return useUiStore((state) => state.language)
}
