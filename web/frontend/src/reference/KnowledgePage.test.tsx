import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { ShellOutletContext } from '../components/appShellTypes'
import { ReferencePage } from './ReferencePage'
import { server } from '../test/server'

const roots = ['user', 'shared', 'global'].map(scope => ({ scope,
  relative_path: scope === 'user' ? 'users/kesepain/knowledge' : `knowledge/${scope}`,
  absolute_path: `E:/project/${scope}`, active_for_main_agent: true }))
const directories = ['projects', 'projects/design', 'projects/design/drafts', 'empty'].map(relative_path => ({
  scope: 'user', relative_path, name: relative_path.split('/').at(-1), updated_at: 1,
}))
const documents = [
  { scope: 'user', relative_path: 'projects/design/drafts/notes.md', title: '笔记', size: 20, updated_at: 1, active_for_main_agent: true },
  { scope: 'shared', relative_path: 'notes.md', title: '共享笔记', size: 20, updated_at: 1, active_for_main_agent: true },
]
function renderPage() {
  server.use(http.get('/api/users/kesepain/knowledge', () => HttpResponse.json({ user: 'kesepain', enabled: true,
    retrieval: { mode: 'index_only', full_index: true }, roots, directories, documents,
    summary: { documents: documents.length, user_documents: 1, shared_documents: 1, global_documents: 0 }, source_policy: {},
  })))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const context = { user: 'kesepain', sessionId: 's1', chatRunning: false } as ShellOutletContext
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/knowledge']}>
    <Routes><Route element={<Outlet context={context} />}><Route path="*" element={<ReferencePage />} /></Route></Routes>
  </MemoryRouter></QueryClientProvider>)
}
async function openFolder() {
  fireEvent.click(await screen.findByRole('treeitem', { name: '用户' }))
  for (const name of ['projects', 'design', 'drafts']) fireEvent.click(await screen.findByRole('treeitem', { name }))
  await screen.findByRole('treeitem', { name: 'notes.md' })
}

describe('真实路径知识库', () => {
  it('全部根只有三个入口，展开真实三层路径并保留空目录，不显示分类控件', async () => {
    renderPage()
    const tree = await screen.findByRole('tree', { name: '知识目录' })
    expect(within(tree).getAllByRole('treeitem').map(item => item.getAttribute('aria-label'))).toEqual(['用户', '共享', '全局'])
    const actions = document.querySelector('.knowledge-page .page-head-actions') as HTMLElement
    expect(actions.firstElementChild).toHaveClass('search-box')
    expect(within(actions).getByRole('searchbox')).toBeInTheDocument()
    expect([...actions.querySelectorAll(':scope > button')].map(button => button.textContent?.trim())).toEqual([
      '重新读取', '导入文件', '新建',
    ])
    expect(document.querySelector('.knowledge-toolbar')).toBeNull()
    for (const name of ['全部', '用户', '共享', '全局']) {
      expect([...actions.querySelectorAll('button')].some(button => button.textContent?.trim() === name)).toBe(false)
    }
    expect(within(screen.getByRole('list', { name: '当前目录' })).getAllByRole('listitem')).toHaveLength(3)
    await openFolder()
    expect(within(tree).getByRole('treeitem', { name: 'notes.md' })).toHaveAttribute('aria-level', '5')
    expect(within(tree).getByRole('treeitem', { name: '共享' })).toBeInTheDocument()
    fireEvent.click(within(tree).getByRole('treeitem', { name: 'empty' }))
    expect(await screen.findByText('此文件夹为空')).toBeInTheDocument()
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    expect(document.querySelector('[class*="knowledge-category"]')).toBeNull()
  })

  it('目录与文件右键显示八项操作，根目录禁用破坏自身的操作', async () => {
    renderPage()
    const root = await screen.findByRole('treeitem', { name: '用户' })
    fireEvent.contextMenu(root)
    let menu = screen.getByRole('menu', { name: '知识条目操作' })
    expect(within(menu).getAllByRole('menuitem').map(item => item.textContent)).toEqual([
      '重命名', '删除', '复制', '移动', '复制相对路径', '复制绝对路径', '新建文件', '新建文件夹',
    ])
    for (const name of ['重命名', '删除', '复制', '移动']) expect(within(menu).getByRole('menuitem', { name })).toBeDisabled()
    await openFolder()
    fireEvent.contextMenu(screen.getByRole('treeitem', { name: 'drafts' }))
    menu = screen.getByRole('menu')
    expect(within(menu).getByRole('menuitem', { name: '移动' })).toBeEnabled()
    fireEvent.keyDown(menu, { key: 'Escape' })
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })

  it('复制文件提交当前路径及用户选择的目标层，不再发送分类请求', async () => {
    let body: unknown
    let sourcePath: string | null = null
    server.use(http.patch('/api/users/kesepain/knowledge/user/entry', async ({ request }) => {
      sourcePath = new URL(request.url).searchParams.get('path')
      body = await request.json()
      return HttpResponse.json({ action: 'copy' })
    }))
    renderPage(); await openFolder()
    fireEvent.contextMenu(screen.getByRole('treeitem', { name: 'notes.md' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '复制' }))
    const dialog = screen.getByRole('dialog', { name: '复制' })
    fireEvent.change(within(dialog).getByRole('combobox', { name: '目标知识库' }), { target: { value: 'shared' } })
    fireEvent.input(within(dialog).getByRole('textbox', { name: '相对路径' }), { target: { value: 'design/copy.md' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '确认' }))
    await waitFor(() => expect(body).toEqual({ action: 'copy', target_scope: 'shared', target_path: 'design/copy.md' }))
    expect(sourcePath).toBe('projects/design/drafts/notes.md')
  })

  it('删除文件夹先说明删除范围，取消时不发请求', async () => {
    const remove = vi.fn()
    server.use(http.delete('/api/users/kesepain/knowledge/user/entry', () => { remove(); return HttpResponse.json({ deleted: true }) }))
    renderPage(); await openFolder()
    fireEvent.contextMenu(screen.getByRole('treeitem', { name: 'design' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '删除' }))
    const dialog = await screen.findByRole('dialog', { name: '删除此文件夹？' })
    expect(within(dialog).getByText('将永久删除此文件夹及其全部内容。')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: '取消' }))
    expect(remove).not.toHaveBeenCalled()
  })

  it('搜索展开匹配路径，选中文件按需加载，取消放弃仍保留未保存正文', async () => {
    renderPage()
    fireEvent.input(await screen.findByRole('searchbox'), { target: { value: 'drafts/notes' } })
    fireEvent.click(await screen.findByRole('treeitem', { name: 'notes.md' }))
    await screen.findByText('知识正文')
    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    const editor = screen.getByRole('textbox', { name: '知识文件内容' })
    fireEvent.input(editor, { target: { value: '未保存的正文' } })
    fireEvent.input(screen.getByRole('searchbox'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('treeitem', { name: 'empty' }))
    const dialog = await screen.findByRole('dialog', { name: '放弃未保存的修改？' })
    fireEvent.click(within(dialog).getByRole('button', { name: '取消' }))
    expect(editor).toHaveValue('未保存的正文')
  })
})
