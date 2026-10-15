import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { UserProfileCard } from './UserProfileCard'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('UserProfileCard', () => {
  it('当前运行禁止切换用户时仍允许新建此用户标签页', () => {
    render(<UserProfileCard username="alice" switchingDisabled users={[{ username: 'bob' }]} newUserTabHref="/chat?user=alice&new_session=1" />)
    fireEvent.click(screen.getByRole('button', { name: '切换当前用户' }))
    expect(screen.getByRole('menuitem', { name: /bob/ })).toBeDisabled()
    const newTab = screen.getByRole('menuitem', { name: '新建此用户标签页' })
    expect(newTab).not.toHaveAttribute('aria-disabled', 'true')
    expect(newTab).toHaveAttribute('href', '/chat?user=alice&new_session=1')
  })
  it('展示用户信息并可打开和关闭菜单', () => {
    render(<UserProfileCard username="kesepain" userPath="users/kesepain" onOpenProfile={() => undefined} onOpenSettings={() => undefined} />)
    const trigger = screen.getByRole('button', { name: '切换当前用户' })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(trigger)
    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    const menu = screen.getByRole('menu', { name: '用户菜单' })
    expect(menu.parentElement).toBe(document.body)
    expect(trigger).toHaveAttribute('aria-controls', menu.id)
    fireEvent.pointerDown(menu)
    expect(screen.getByRole('menu', { name: '用户菜单' })).toBeInTheDocument()
    fireEvent.pointerDown(document.body)
    expect(screen.queryByRole('menu', { name: '用户菜单' })).not.toBeInTheDocument()
  })

  it('执行资料、用户切换、设置和退出动作后关闭菜单', () => {
    const onOpenProfile = vi.fn()
    const onOpenUserSwitch = vi.fn()
    const onOpenSettings = vi.fn()
    const onLogout = vi.fn()
    render(<UserProfileCard onOpenProfile={onOpenProfile} onOpenUserSwitch={onOpenUserSwitch} onOpenSettings={onOpenSettings} onLogout={onLogout} />)
    const trigger = screen.getByRole('button', { name: '切换当前用户' })
    fireEvent.click(trigger)
    fireEvent.click(screen.getByRole('menuitem', { name: '用户资料' }))
    expect(onOpenProfile).toHaveBeenCalledOnce()
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    fireEvent.click(trigger)
    fireEvent.click(screen.getByRole('menuitem', { name: '切换用户' }))
    expect(onOpenUserSwitch).toHaveBeenCalledOnce()
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    fireEvent.click(trigger)
    fireEvent.click(screen.getByRole('menuitem', { name: '用户设置' }))
    expect(onOpenSettings).toHaveBeenCalledOnce()
    fireEvent.click(trigger)
    fireEvent.click(screen.getByRole('menuitem', { name: '退出登录' }))
    expect(onLogout).toHaveBeenCalledOnce()
  })

  it('保留多用户切换能力', () => {
    const onSelectUser = vi.fn()
    render(<UserProfileCard username="alice" userPath="users/alice" users={[{ username: 'alice' }, { username: 'bob' }]} onSelectUser={onSelectUser} />)
    fireEvent.click(screen.getByRole('button', { name: '切换当前用户' }))
    fireEvent.click(screen.getByRole('menuitem', { name: /bob/ }))
    expect(onSelectUser).toHaveBeenCalledWith('bob')
  })

  it('菜单在卡片上方且脱离侧栏裁切，窄屏、短屏和窗口变化保持可用', () => {
    vi.stubGlobal('innerWidth', 1440)
    vi.stubGlobal('innerHeight', 900)
    render(<UserProfileCard />)
    const trigger = screen.getByRole('button', { name: '切换当前用户' })
    vi.spyOn(trigger, 'getBoundingClientRect').mockReturnValue({
      left: 8, right: 260, top: 840, bottom: 892, width: 252, height: 52,
    } as DOMRect)
    fireEvent.click(trigger)
    const menu = screen.getByRole('menu', { name: '用户菜单' })
    expect(menu).toHaveStyle({ left: '12px', bottom: '70px', width: '285px', maxHeight: '818px' })
    vi.stubGlobal('innerWidth', 280)
    vi.stubGlobal('innerHeight', 300)
    vi.mocked(trigger.getBoundingClientRect).mockReturnValue({
      left: 8, right: 260, top: 240, bottom: 292, width: 252, height: 52,
    } as DOMRect)
    fireEvent(window, new Event('resize'))
    expect(menu).toHaveStyle({ left: '12px', bottom: '70px', width: '256px', maxHeight: '218px' })
    fireEvent.keyDown(menu, { key: 'Escape' })
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })

  it('紧凑卡片在有空间时向右弹出，切换用户后不残留旧菜单', () => {
    vi.stubGlobal('innerWidth', 1440)
    vi.stubGlobal('innerHeight', 900)
    const { rerender } = render(<UserProfileCard compact username="alice" />)
    const trigger = screen.getByRole('button', { name: '切换当前用户' })
    vi.spyOn(trigger, 'getBoundingClientRect').mockReturnValue({
      left: 8, right: 50, top: 840, bottom: 892, width: 42, height: 52,
    } as DOMRect)
    fireEvent.click(trigger)
    expect(screen.getByRole('menu')).toHaveStyle({ left: '60px', bottom: '12px', width: '285px' })
    rerender(<UserProfileCard compact username="bob" />)
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })
})
