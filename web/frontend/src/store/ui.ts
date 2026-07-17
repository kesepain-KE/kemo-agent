import { create } from 'zustand'

type Theme = 'light' | 'dark'
type FontSize = 'small' | 'medium' | 'large'

interface UiState {
  theme: Theme
  fontSize: FontSize
  sidebarCollapsed: boolean
  drawerOpen: boolean
  setTheme: (theme: Theme) => void
  setFontSize: (size: FontSize) => void
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

export const useUiStore = create<UiState>((set) => ({
  theme: readValue('kemo-theme', ['light', 'dark'], 'light'),
  fontSize: readValue('kemo-font-size', ['small', 'medium', 'large'], 'medium'),
  sidebarCollapsed: readValue('kemo-sidebar', ['expanded', 'collapsed'], 'expanded') === 'collapsed',
  drawerOpen: false,
  setTheme: (theme) => {
    localStorage.setItem('kemo-theme', theme)
    set({ theme })
  },
  setFontSize: (fontSize) => {
    localStorage.setItem('kemo-font-size', fontSize)
    set({ fontSize })
  },
  toggleSidebar: () =>
    set((state) => {
      const sidebarCollapsed = !state.sidebarCollapsed
      localStorage.setItem('kemo-sidebar', sidebarCollapsed ? 'collapsed' : 'expanded')
      return { sidebarCollapsed }
    }),
  setDrawerOpen: (drawerOpen) => set({ drawerOpen }),
}))
