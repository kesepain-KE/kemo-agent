import { create } from 'zustand'
import { applyDocumentLanguage, defaultLanguage, normalizeLanguage, type Language } from '../i18n/locales'

type Theme = 'light' | 'dark'
type FontSize = 'small' | 'medium' | 'large'

interface UiState {
  theme: Theme
  fontSize: FontSize
  language: Language
  languageUser: string
  sidebarCollapsed: boolean
  drawerOpen: boolean
  setTheme: (theme: Theme) => void
  setFontSize: (size: FontSize) => void
  setLanguage: (language: Language, user: string) => void
  activateLanguage: (user: string) => void
  toggleSidebar: () => void
  setDrawerOpen: (open: boolean) => void
}

function readValue<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const value = localStorage.getItem(key) as T | null
    return value && allowed.includes(value) ? value : fallback
  } catch {
    return fallback
  }
}

export function cacheLanguage(language: Language, user: string) {
  try { localStorage.setItem(`kemo-language:${user}`, language) } catch { /* server remains authoritative */ }
}

export const useUiStore = create<UiState>((set) => ({
  theme: readValue('kemo-theme', ['light', 'dark'], 'light'),
  fontSize: readValue('kemo-font-size', ['small', 'medium', 'large'], 'medium'),
  language: defaultLanguage,
  languageUser: '',
  sidebarCollapsed: readValue('kemo-sidebar', ['expanded', 'collapsed'], 'expanded') === 'collapsed',
  drawerOpen: false,
  setTheme: (theme) => {
    try { localStorage.setItem('kemo-theme', theme) } catch { /* preference API remains available */ }
    set({ theme })
  },
  setFontSize: (fontSize) => {
    try { localStorage.setItem('kemo-font-size', fontSize) } catch { /* preference API remains available */ }
    set({ fontSize })
  },
  setLanguage: (value, user) => {
    const language = normalizeLanguage(value)
    cacheLanguage(language, user)
    applyDocumentLanguage(language)
    set({ language, languageUser: user })
  },
  activateLanguage: (user) => {
    let language: Language = defaultLanguage
    try { language = normalizeLanguage(localStorage.getItem(`kemo-language:${user}`)) } catch { /* private browsing */ }
    applyDocumentLanguage(language)
    set({ language, languageUser: user })
  },
  toggleSidebar: () =>
    set((state) => {
      const sidebarCollapsed = !state.sidebarCollapsed
      localStorage.setItem('kemo-sidebar', sidebarCollapsed ? 'collapsed' : 'expanded')
      return { sidebarCollapsed }
    }),
  setDrawerOpen: (drawerOpen) => set({ drawerOpen }),
}))
