import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SessionActions } from './SessionActions'
import type { SessionSummary } from '../types/api'
import { useUiStore } from '../store/ui'

const session: SessionSummary = { source: 'web', session_id: 's1', window: 'w', title: '用户标题', rounds: 4, updated_at: '2026-10-20' }
afterEach(() => useUiStore.getState().setLanguage('zh-CN', ''))

function mount(overrides: Partial<SessionSummary> = {}, callbacks = {}) {
  const actions = { onMark: vi.fn().mockResolvedValue(undefined), onRename: vi.fn().mockResolvedValue(undefined),
    onDelete: vi.fn(), onOpen: vi.fn(), ...callbacks }
  render(<SessionActions session={{ ...session, ...overrides }} {...actions}>
    {(trigger, context) => <article data-testid="row" onContextMenu={context}>
      <button onClick={actions.onOpen}>用户标题</button>{trigger}
    </article>}
  </SessionActions>)
  return actions
}

describe('shared history actions', () => {
  it('right click does not open the conversation and pin/favorite send explicit independent values', async () => {
    const actions = mount()
    fireEvent.contextMenu(screen.getByTestId('row'), { clientX: 1000, clientY: 800 })
    expect(actions.onOpen).not.toHaveBeenCalled()
    expect(screen.getByRole('menu')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('menuitem', { name: '置顶' }))
    await waitFor(() => expect(screen.queryByRole('menu')).not.toBeInTheDocument())
    expect(actions.onMark).toHaveBeenCalledWith(session, { pinned: true })
    fireEvent.click(screen.getByRole('button', { name: '对话操作 用户标题' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '收藏' }))
    await waitFor(() => expect(actions.onMark).toHaveBeenLastCalledWith(session, { favorite: true }))
    expect(actions.onDelete).not.toHaveBeenCalled()
  })

  it('keyboard navigation, Escape containment and outside dismissal work', () => {
    mount({ pinned: true, favorite: true })
    const parentEscape = vi.fn()
    document.addEventListener('keydown', parentEscape)
    const trigger = screen.getByRole('button', { name: '对话操作 用户标题' })
    trigger.focus()
    fireEvent.click(trigger)
    expect(screen.getByRole('menuitem', { name: '取消置顶' })).toHaveFocus()
    fireEvent.keyDown(document.activeElement!, { key: 'ArrowDown' })
    expect(screen.getByRole('menuitem', { name: '取消收藏' })).toHaveFocus()
    fireEvent.keyDown(document.activeElement!, { key: 'End' })
    expect(screen.getByRole('menuitem', { name: '删除' })).toHaveFocus()
    parentEscape.mockClear()
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' })
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
    expect(parentEscape).not.toHaveBeenCalled()
    document.removeEventListener('keydown', parentEscape)
    fireEvent.click(trigger)
    fireEvent.pointerDown(document.body)
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })

  it('read-only sources can be marked/opened but never renamed or singly deleted', async () => {
    const actions = mount({ source: 'cli' })
    fireEvent.contextMenu(screen.getByTestId('row'))
    expect(screen.getByRole('menuitem', { name: '重命名' })).toBeDisabled()
    expect(screen.getByRole('menuitem', { name: '删除' })).toBeDisabled()
    expect(screen.getByRole('menuitem', { name: '收藏' })).toBeEnabled()
    fireEvent.click(screen.getByRole('menuitem', { name: '打开' }))
    expect(actions.onOpen).toHaveBeenCalledTimes(1)
  })

  it('opening focus scroll does not instantly dismiss; internal wheel works and external wheel dismisses', () => {
    mount()
    fireEvent.click(screen.getByRole('button', { name: '对话操作 用户标题' }))
    fireEvent.scroll(document.body)
    expect(screen.getByRole('menu')).toBeInTheDocument()
    fireEvent.wheel(screen.getByRole('menu'))
    expect(screen.getByRole('menu')).toBeInTheDocument()
    fireEvent.wheel(document.body)
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })

  it('rename preserves draft on failure; empty name cannot submit; delete requires confirmation', async () => {
    const onRename = vi.fn().mockRejectedValueOnce(new Error('隔离测试失败')).mockResolvedValue(undefined)
    const actions = mount({}, { onRename })
    fireEvent.click(screen.getByRole('button', { name: '对话操作 用户标题' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '重命名' }))
    const input = screen.getByRole('textbox', { name: '对话名称' })
    fireEvent.change(input, { target: { value: ' ' } })
    expect(screen.getByRole('button', { name: '保存名称' })).toBeDisabled()
    fireEvent.change(input, { target: { value: ' 新名称 ' } })
    fireEvent.click(screen.getByRole('button', { name: '保存名称' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('隔离测试失败')
    expect(input).toHaveValue(' 新名称 ')
    fireEvent.click(screen.getByRole('button', { name: '保存名称' }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    expect(onRename).toHaveBeenLastCalledWith('s1', '新名称')
    fireEvent.click(screen.getByRole('button', { name: '对话操作 用户标题' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '删除' }))
    expect(actions.onDelete).not.toHaveBeenCalled()
    fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: '确认删除' }))
    await waitFor(() => expect(actions.onDelete).toHaveBeenCalledWith('s1'))
  })

  it('mark failure stays visible and allows retry without closing the menu', async () => {
    const onMark = vi.fn().mockRejectedValueOnce(new Error('网络暂时不可用')).mockResolvedValue(undefined)
    mount({}, { onMark })
    fireEvent.click(screen.getByRole('button', { name: '对话操作 用户标题' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '收藏' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('网络暂时不可用')
    fireEvent.click(screen.getByRole('menuitem', { name: '收藏' }))
    await waitFor(() => expect(screen.queryByRole('menu')).not.toBeInTheDocument())
    expect(onMark).toHaveBeenCalledTimes(2)
  })

  it('new controls localize without translating the title', () => {
    useUiStore.getState().setLanguage('en', '')
    mount()
    fireEvent.click(screen.getByRole('button', { name: 'Chat actions 用户标题' }))
    expect(screen.getByRole('menuitem', { name: 'Pin' })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: 'Favorites' })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: 'Rename' })).toBeInTheDocument()
  })
})
