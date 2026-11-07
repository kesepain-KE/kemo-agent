import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { ComponentProps } from 'react'
import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { KnowledgeDirectory, KnowledgeDocumentSummary, KnowledgeRoot } from '../types/api'
import { server } from '../test/server'
import { KnowledgeReferenceDrawer } from './KnowledgeReferenceDrawer'
import { useKnowledgeClipboard } from '../store/knowledgeClipboard'

const roots: KnowledgeRoot[] = ['user', 'shared', 'global'].map((scope) => ({
  scope,
  relative_path: `knowledge/${scope}`,
  absolute_path: `/project/knowledge/${scope}`,
  active_for_main_agent: true,
}))
const directories: KnowledgeDirectory[] = [
  { scope: 'user', relative_path: 'projects', name: 'projects', updated_at: 1 },
  { scope: 'user', relative_path: 'projects/design', name: 'design', updated_at: 1 },
  { scope: 'shared', relative_path: 'archive', name: 'archive', updated_at: 1 },
]
const documents: KnowledgeDocumentSummary[] = [
  { scope: 'user', relative_path: 'projects/design/notes.md', title: '个人笔记', size: 120, updated_at: 1, active_for_main_agent: true },
  { scope: 'shared', relative_path: 'team.md', title: '共享规范', size: 90, updated_at: 1, active_for_main_agent: true },
  { scope: 'global', relative_path: 'guide.md', title: '全局指南', size: 160, updated_at: 1, active_for_main_agent: true },
]

function renderDrawer(overrides: Partial<ComponentProps<typeof KnowledgeReferenceDrawer>> = {}) {
  const onReference = vi.fn()
  const onReferenceDirectory = vi.fn()
  const onClose = vi.fn()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const props = { open: true, user: 'kesepain', roots, directories, documents, onClose, onReference, onReferenceDirectory, ...overrides }
  const result = render(<QueryClientProvider client={client}><KnowledgeReferenceDrawer {...props} /></QueryClientProvider>)
  return { ...result, props, onReference, onReferenceDirectory, onClose }
}

describe('KnowledgeReferenceDrawer', () => {
  beforeEach(() => useKnowledgeClipboard.setState({ user: '', clipboard: null, pending: false }))
  it('保留关闭过渡并将目录树作为唯一层级导航', () => {
    const props = { user: 'kesepain', roots, directories, documents, onClose: vi.fn(), onReference: vi.fn(), onReferenceDirectory: vi.fn() }
    const client = new QueryClient()
    const { rerender } = render(<QueryClientProvider client={client}><KnowledgeReferenceDrawer open={false} {...props} /></QueryClientProvider>)
    const drawer = screen.getByRole('dialog', { hidden: true })
    expect(drawer).toHaveAttribute('aria-label', '知识库引用')
    expect(drawer).toHaveAttribute('aria-hidden', 'true')
    expect(drawer).toHaveAttribute('inert')

    rerender(<QueryClientProvider client={client}><KnowledgeReferenceDrawer open {...props} /></QueryClientProvider>)
    expect(screen.getByRole('tree', { name: '知识目录' })).toBeInTheDocument()
    expect(within(screen.getByRole('tree')).getAllByRole('treeitem').map((item) => item.getAttribute('aria-label'))).toEqual(['用户', '共享', '全局'])
    expect(screen.queryByRole('button', { name: '全部' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '用户' })).not.toBeInTheDocument()
    expect(drawer).not.toHaveAttribute('inert')
  })

  it('按真实路径展开目录，搜索匹配文件并保留原有文件引用标记', () => {
    const { onReference } = renderDrawer()
    const tree = screen.getByRole('tree', { name: '知识目录' })
    fireEvent.click(within(tree).getByText('用户'))
    expect(within(tree).getByRole('treeitem', { name: '用户' })).toHaveAttribute('aria-expanded', 'true')
    fireEvent.click(within(tree).getByText('用户'))
    expect(within(tree).getByRole('treeitem', { name: '用户' })).toHaveAttribute('aria-expanded', 'false')
    expect(within(tree).queryByRole('treeitem', { name: 'projects' })).not.toBeInTheDocument()
    fireEvent.click(within(tree).getByText('用户'))
    fireEvent.click(within(tree).getByRole('treeitem', { name: 'projects' }))
    fireEvent.click(within(tree).getByRole('treeitem', { name: 'design' }))
    expect(within(tree).getByRole('treeitem', { name: 'notes.md' })).toHaveAttribute('aria-level', '4')

    fireEvent.contextMenu(within(tree).getByRole('treeitem', { name: 'notes.md' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '引用此文件' }))
    expect(onReference).toHaveBeenCalledWith(documents[0])

    fireEvent.change(screen.getByRole('textbox', { name: '搜索知识库' }), { target: { value: 'team.md' } })
    expect(within(tree).getByRole('treeitem', { name: '共享' })).toBeInTheDocument()
    expect(within(tree).getByRole('treeitem', { name: '用户' })).toBeInTheDocument()
    expect(within(tree).queryByRole('treeitem', { name: 'notes.md' })).not.toBeInTheDocument()
    fireEvent.click(within(tree).getByRole('treeitem', { name: '共享' }))
    expect(within(tree).getByRole('treeitem', { name: 'team.md' })).toBeInTheDocument()
  })

  it('将文件夹引用写入单独的相对路径标记', () => {
    const { onReferenceDirectory } = renderDrawer()
    const tree = screen.getByRole('tree', { name: '知识目录' })
    fireEvent.click(within(tree).getByRole('treeitem', { name: '用户' }))
    fireEvent.contextMenu(within(tree).getByRole('treeitem', { name: 'projects' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '引用文件夹相对路径' }))
    expect(onReferenceDirectory).toHaveBeenCalledWith('user', 'projects', 'projects')
  })

  it('在 Agent 工作台缓冲区内复制并粘贴到另一知识目录', async () => {
    let requestPath = ''
    let requestBody: unknown
    server.use(http.patch('/api/users/kesepain/knowledge/:scope/entry', async ({ params, request }) => {
      requestPath = `${params.scope}:${new URL(request.url).searchParams.get('path')}`
      requestBody = await request.json()
      expect(request.headers.get('content-type')).toBe('application/json')
      return HttpResponse.json({ action: 'copy', target_scope: 'shared', new_relative_path: 'archive/notes.md' })
    }))
    renderDrawer()
    const tree = screen.getByRole('tree', { name: '知识目录' })
    fireEvent.click(within(tree).getByRole('treeitem', { name: '用户' }))
    fireEvent.click(within(tree).getByRole('treeitem', { name: 'projects' }))
    fireEvent.click(within(tree).getByRole('treeitem', { name: 'design' }))
    fireEvent.contextMenu(within(tree).getByRole('treeitem', { name: 'notes.md' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '复制' }))
    expect(screen.getByText('待粘贴副本：notes.md')).toBeInTheDocument()

    fireEvent.click(within(tree).getByRole('treeitem', { name: '共享' }))
    fireEvent.contextMenu(within(tree).getByRole('treeitem', { name: 'archive' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '粘贴' }))
    await waitFor(() => expect(requestBody).toEqual({ action: 'copy', target_scope: 'shared', target_path: 'archive/notes.md', auto_rename: true }))
    expect(requestPath).toBe('user:projects/design/notes.md')
    expect(await screen.findByText('知识条目已复制')).toBeInTheDocument()
  })

  it('剪切后移动到目标目录，成功后清空待移动状态', async () => {
    let requestBody: unknown
    server.use(http.patch('/api/users/kesepain/knowledge/:scope/entry', async ({ request }) => {
      requestBody = await request.json()
      return HttpResponse.json({ action: 'move', target_scope: 'shared', new_relative_path: 'archive/notes.md' })
    }))
    renderDrawer()
    const tree = screen.getByRole('tree', { name: '知识目录' })
    fireEvent.click(within(tree).getByRole('treeitem', { name: '用户' }))
    fireEvent.click(within(tree).getByRole('treeitem', { name: 'projects' }))
    fireEvent.click(within(tree).getByRole('treeitem', { name: 'design' }))
    fireEvent.contextMenu(within(tree).getByRole('treeitem', { name: 'notes.md' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '剪切' }))
    expect(screen.getByText('待移动：notes.md')).toBeInTheDocument()

    fireEvent.click(within(tree).getByRole('treeitem', { name: '共享' }))
    fireEvent.contextMenu(within(tree).getByRole('treeitem', { name: 'archive' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '粘贴' }))
    await waitFor(() => expect(requestBody).toEqual({ action: 'move', target_scope: 'shared', target_path: 'archive/notes.md' }))
    expect(await screen.findByText('知识条目已移动')).toBeInTheDocument()
    expect(screen.queryByText('待移动：notes.md')).not.toBeInTheDocument()
  })

  it('快捷键使用当前聚焦的条目，搜索输入保留原生复制粘贴', async () => {
    let requestBody: unknown
    server.use(http.patch('/api/users/kesepain/knowledge/shared/entry', async ({ request }) => {
      expect(new URL(request.url).searchParams.get('path')).toBe('team.md')
      requestBody = await request.json()
      return HttpResponse.json({ target_scope: 'user', new_relative_path: 'team.md' })
    }))
    renderDrawer()
    const tree = screen.getByRole('tree')
    fireEvent.click(within(tree).getByRole('treeitem', { name: '共享' }))
    fireEvent.keyDown(within(tree).getByRole('treeitem', { name: 'team.md' }), { key: 'c', ctrlKey: true })
    expect(screen.getByText('待粘贴副本：team.md')).toBeInTheDocument()
    const search = screen.getByRole('textbox', { name: '搜索知识库' })
    expect(fireEvent.keyDown(search, { key: 'x', ctrlKey: true })).toBe(true)
    expect(fireEvent.keyDown(search, { key: 'v', ctrlKey: true })).toBe(true)
    fireEvent.keyDown(within(tree).getByRole('treeitem', { name: '用户' }), { key: 'v', ctrlKey: true })
    await waitFor(() => expect(requestBody).toEqual({
      action: 'copy', target_scope: 'user', target_path: 'team.md', auto_rename: true,
    }))
    await screen.findByText('知识条目已复制')
  })

  it('粘贴失败保留剪切来源，切换用户后清空应用剪贴板', async () => {
    server.use(http.patch('/api/users/kesepain/knowledge/:scope/entry', () =>
      HttpResponse.json({ error: { message: '目标已存在', code: 'conflict' } }, { status: 409 })))
    const view = renderDrawer()
    const tree = screen.getByRole('tree')
    fireEvent.click(within(tree).getByRole('treeitem', { name: '用户' }))
    fireEvent.click(within(tree).getByRole('treeitem', { name: 'projects' }))
    fireEvent.contextMenu(within(tree).getByRole('treeitem', { name: 'design' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '剪切' }))
    fireEvent.contextMenu(within(tree).getByRole('treeitem', { name: '共享' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '粘贴' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('目标已存在')
    expect(screen.getByText('待移动：design')).toBeInTheDocument()
    view.unmount()
    renderDrawer({ user: 'other-user' })
    expect(screen.queryByText('待移动：design')).not.toBeInTheDocument()
  })

  it('重命名复用知识库移动接口，删除则调用知识库条目接口', async () => {
    const mutations: Array<{ scope: string; path: string | null; body?: unknown; method: string }> = []
    server.use(
      http.patch('/api/users/kesepain/knowledge/:scope/entry', async ({ params, request }) => {
        mutations.push({ scope: String(params.scope), path: new URL(request.url).searchParams.get('path'), body: await request.json(), method: 'PATCH' })
        return HttpResponse.json({ action: 'move' })
      }),
      http.delete('/api/users/kesepain/knowledge/:scope/entry', ({ params, request }) => {
        mutations.push({ scope: String(params.scope), path: new URL(request.url).searchParams.get('path'), method: 'DELETE' })
        return HttpResponse.json({ deleted: true })
      }),
    )
    renderDrawer()
    const tree = screen.getByRole('tree', { name: '知识目录' })
    fireEvent.click(within(tree).getByRole('treeitem', { name: '用户' }))
    fireEvent.click(within(tree).getByRole('treeitem', { name: 'projects' }))
    fireEvent.click(within(tree).getByRole('treeitem', { name: 'design' }))
    fireEvent.contextMenu(within(tree).getByRole('treeitem', { name: 'notes.md' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '重命名' }))
    const renameDialog = screen.getByRole('alertdialog', { name: '重命名' })
    fireEvent.change(within(renameDialog).getByRole('textbox', { name: '名称' }), { target: { value: 'renamed.md' } })
    fireEvent.click(within(renameDialog).getByRole('button', { name: '保存名称' }))
    await waitFor(() => expect(mutations[0]).toEqual({
      scope: 'user', path: 'projects/design/notes.md',
      body: { action: 'move', target_scope: 'user', target_path: 'projects/design/renamed.md' }, method: 'PATCH',
    }))

    fireEvent.contextMenu(within(tree).getByRole('treeitem', { name: 'design' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '删除' }))
    const deleteDialog = await screen.findByRole('alertdialog', { name: '删除此文件夹？' })
    fireEvent.click(within(deleteDialog).getByRole('button', { name: '删除' }))
    await waitFor(() => expect(mutations[1]).toEqual({ scope: 'user', path: 'projects/design', method: 'DELETE' }))
  })

  it('右键打开文件预览，并在删除前要求确认', async () => {
    renderDrawer()
    const tree = screen.getByRole('tree', { name: '知识目录' })
    fireEvent.click(within(tree).getByRole('treeitem', { name: '用户' }))
    fireEvent.click(within(tree).getByRole('treeitem', { name: 'projects' }))
    fireEvent.click(within(tree).getByRole('treeitem', { name: 'design' }))
    fireEvent.contextMenu(within(tree).getByRole('treeitem', { name: 'notes.md' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '打开' }))
    expect(await screen.findByRole('dialog', { name: 'notes.md' })).toBeInTheDocument()

    fireEvent.keyDown(document, { key: 'Escape' })
    fireEvent.contextMenu(within(tree).getByRole('treeitem', { name: 'design' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '删除' }))
    const confirmation = await screen.findByRole('alertdialog', { name: '删除此文件夹？' })
    expect(within(confirmation).getByText('将永久删除此文件夹及其全部内容。')).toBeInTheDocument()
    fireEvent.click(within(confirmation).getByRole('button', { name: '取消' }))
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  })
})
