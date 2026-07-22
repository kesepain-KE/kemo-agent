import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { UserProfileCard } from './UserProfileCard'

describe('UserProfileCard', () => {
  it('展示用户信息并可打开和关闭菜单', () => {
    render(<UserProfileCard username="kesepain" userPath="users/kesepain" onOpenProfile={() => undefined} onOpenSettings={() => undefined} />)
    const trigger = screen.getByRole('button', { name: '切换当前用户' })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(trigger)
    expect(trigger).toHaveAttribute('aria-expanded', 'true')
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
})
