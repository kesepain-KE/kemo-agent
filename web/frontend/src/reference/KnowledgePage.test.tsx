import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ShellOutletContext } from '../components/appShellTypes'
import { ReferencePage } from './ReferencePage'
import { server } from '../test/server'
import { useKnowledgeClipboard } from '../store/knowledgeClipboard'
import { KnowledgeReferenceDrawer } from '../components/KnowledgeReferenceDrawer'

const roots = ['user', 'shared', 'global'].map(scope => ({ scope,
  relative_path: scope === 'user' ? 'users/kesepain/knowledge' : `knowledge/${scope}`,
  absolute_path: `E:/project/${scope}`, active_for_main_agent: true }))
const directories = ['projects', 'projects/design', 'projects/design/drafts', 'empty'].map(relative_path => ({
  scope: 'user', relative_path, name: relative_path.split('/').at(-1)!, updated_at: 1,
})).concat([{ scope: 'shared', relative_path: 'archive', name: 'archive', updated_at: 1 }])
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
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/knowledge']}>
    <Routes><Route element={<Outlet context={context} />}><Route path="*" element={<ReferencePage />} /></Route></Routes>
  </MemoryRouter></QueryClientProvider>)
}
async function openFolder() {
  fireEvent.click(await screen.findByRole('treeitem', { name: '用户' }))
  for (const name of ['projects', 'design', 'drafts']) fireEvent.click(await screen.findByRole('treeitem', { name }))
  await screen.findByRole('treeitem', { name: 'notes.md' })
}

describe('真实路径知识库', () => {
  beforeEach(() => useKnowledgeClipboard.setState({ user: '', clipboard: null, pending: false }))
  afterEach(() => document.querySelector('.toast-stack')?.remove())
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
    expect(within(screen.getByRole('listbox', { name: '当前目录' })).getAllByRole('option')).toHaveLength(3)
    fireEvent.click(within(tree).getByText('用户'))
    await waitFor(() => expect(within(tree).getByRole('treeitem', { name: '用户' })).toHaveAttribute('aria-expanded', 'true'))
    fireEvent.click(within(tree).getByText('用户'))
    await waitFor(() => expect(within(tree).getByRole('treeitem', { name: '用户' })).toHaveAttribute('aria-expanded', 'false'))
    expect(within(tree).queryByRole('treeitem', { name: 'projects' })).not.toBeInTheDocument()
    await openFolder()
    expect(within(tree).getByRole('treeitem', { name: 'notes.md' })).toHaveAttribute('aria-level', '5')
    fireEvent.click(within(tree).getByText('design'))
    await waitFor(() => expect(within(tree).getByRole('treeitem', { name: 'design' })).toHaveAttribute('aria-expanded', 'false'))
    expect(within(tree).queryByRole('treeitem', { name: 'notes.md' })).not.toBeInTheDocument()
    fireEvent.keyDown(within(tree).getByRole('treeitem', { name: 'design' }), { key: 'Enter' })
    await waitFor(() => expect(within(tree).getByRole('treeitem', { name: 'design' })).toHaveAttribute('aria-expanded', 'true'))
    expect(within(tree).getByRole('treeitem', { name: 'notes.md' })).toBeInTheDocument()
    expect(within(tree).getByRole('treeitem', { name: '共享' })).toBeInTheDocument()
    fireEvent.click(within(tree).getByRole('treeitem', { name: 'empty' }))
    expect(await screen.findByText('此文件夹为空')).toBeInTheDocument()
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    expect(document.querySelector('[class*="knowledge-category"]')).toBeNull()
  })

  it('目录与文件右键包含复制、剪切、粘贴，根目录禁用破坏自身的操作', async () => {
    renderPage()
    const root = await screen.findByRole('treeitem', { name: '用户' })
    fireEvent.contextMenu(root)
    let menu = screen.getByRole('menu', { name: '知识条目操作' })
    expect(within(menu).getAllByRole('menuitem').map(item => item.textContent)).toEqual([
      '重命名', '删除', '复制', '剪切', '粘贴', '复制相对路径', '复制绝对路径', '新建文件', '新建文件夹',
    ])
    for (const name of ['重命名', '删除', '复制', '剪切', '粘贴']) expect(within(menu).getByRole('menuitem', { name })).toBeDisabled()
    await openFolder()
    fireEvent.contextMenu(screen.getByRole('treeitem', { name: 'drafts' }))
    menu = screen.getByRole('menu')
    expect(within(menu).getByRole('menuitem', { name: '剪切' })).toBeEnabled()
    fireEvent.keyDown(menu, { key: 'Escape' })
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })

  it('复制后不发请求，在进入目标目录粘贴时提交 JSON 文件操作', async () => {
    let body: unknown
    let sourcePath: string | null = null
    server.use(http.patch('/api/users/kesepain/knowledge/user/entry', async ({ request }) => {
      sourcePath = new URL(request.url).searchParams.get('path')
      expect(request.headers.get('content-type')).toBe('application/json')
      body = await request.json()
      return HttpResponse.json({ action: 'copy', target_scope: 'shared', new_relative_path: 'archive/notes.md' })
    }))
    renderPage(); await openFolder()
    fireEvent.contextMenu(screen.getByRole('treeitem', { name: 'notes.md' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '复制' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(body).toBeUndefined()
    expect(screen.getByText('待粘贴副本：notes.md')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('treeitem', { name: '共享' }))
    fireEvent.click(await screen.findByRole('treeitem', { name: 'archive' }))
    await screen.findByText('此文件夹为空')
    fireEvent.contextMenu(screen.getByRole('listbox', { name: '当前目录' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '粘贴' }))
    await waitFor(() => expect(body).toEqual({ action: 'copy', target_scope: 'shared', target_path: 'archive/notes.md', auto_rename: true }))
    expect(sourcePath).toBe('projects/design/drafts/notes.md')
    await screen.findByText('知识条目已复制')
  })

  it('文件列表全选可批量复制，搜索框和正文编辑器保留原生快捷键', async () => {
    const bodies: Array<{ path: string; body: unknown }> = []
    server.use(http.patch('/api/users/kesepain/knowledge/user/entry', async ({ request }) => {
      bodies.push({ path: new URL(request.url).searchParams.get('path')!, body: await request.json() })
      return HttpResponse.json({ target_scope: 'shared', new_relative_path: `archive/${bodies.length}.md` })
    }))
    renderPage()
    fireEvent.click(await screen.findByRole('treeitem', { name: '用户' }))
    await screen.findByRole('option', { name: 'projects' })
    const list = screen.getByRole('listbox', { name: '当前目录' })
    fireEvent.keyDown(list, { key: 'a', ctrlKey: true })
    expect(within(screen.getByRole('listbox')).getAllByRole('option', { selected: true })).toHaveLength(2)
    fireEvent.keyDown(screen.getByRole('listbox'), { key: 'c', ctrlKey: true })
    expect(screen.getByText('待粘贴副本：2 个条目')).toBeInTheDocument()
    const search = screen.getByRole('searchbox')
    expect(fireEvent.keyDown(search, { key: 'x', ctrlKey: true })).toBe(true)
    expect(fireEvent.keyDown(search, { key: 'v', ctrlKey: true })).toBe(true)
    fireEvent.click(screen.getByRole('treeitem', { name: '共享' }))
    fireEvent.click(await screen.findByRole('treeitem', { name: 'archive' }))
    await screen.findByText('此文件夹为空')
    fireEvent.keyDown(screen.getByRole('listbox'), { key: 'v', ctrlKey: true })
    await waitFor(() => expect(bodies).toHaveLength(2))
    expect(bodies.map(item => item.path).sort()).toEqual(['empty', 'projects'])
    expect(bodies.every(item => (item.body as { target_scope: string }).target_scope === 'shared')).toBe(true)
    await screen.findByText('知识条目已复制')
  })

  it('从知识库页面复制后，侧边栏可以继续粘贴', async () => {
    let body: unknown
    server.use(http.patch('/api/users/kesepain/knowledge/user/entry', async ({ request }) => {
      body = await request.json()
      return HttpResponse.json({ target_scope: 'shared', new_relative_path: 'archive/notes.md' })
    }))
    const page = renderPage()
    await openFolder()
    const source = screen.getByRole('treeitem', { name: 'notes.md' })
    fireEvent.keyDown(source, { key: 'c', ctrlKey: true })
    page.unmount()
    const client = new QueryClient()
    render(<QueryClientProvider client={client}><KnowledgeReferenceDrawer open user="kesepain"
      roots={roots} directories={directories} documents={documents}
      onClose={vi.fn()} onReference={vi.fn()} onReferenceDirectory={vi.fn()} /></QueryClientProvider>)
    expect(screen.getByText('待粘贴副本：notes.md')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('treeitem', { name: '共享' }))
    const target = screen.getByRole('treeitem', { name: 'archive' })
    fireEvent.contextMenu(target)
    fireEvent.click(screen.getByRole('menuitem', { name: '粘贴' }))
    await waitFor(() => expect(body).toEqual({ action: 'copy', target_scope: 'shared', target_path: 'archive/notes.md', auto_rename: true }))
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
