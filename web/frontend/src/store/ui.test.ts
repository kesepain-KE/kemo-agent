import { beforeEach, describe, expect, it } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useUiStore } from './ui'

describe('useUiStore', () => {
  beforeEach(() => {
    localStorage.clear()
    useUiStore.setState({ theme: 'light', fontSize: 'medium', sidebarCollapsed: false, drawerOpen: false })
  })

  it('持久化主题、字号与侧栏偏好', () => {
    const { result } = renderHook(() => useUiStore())
    act(() => {
      result.current.setTheme('dark')
      result.current.setFontSize('large')
      result.current.toggleSidebar()
    })
    expect(localStorage.getItem('kemo-theme')).toBe('dark')
    expect(localStorage.getItem('kemo-font-size')).toBe('large')
    expect(localStorage.getItem('kemo-sidebar')).toBe('collapsed')
  })
})
