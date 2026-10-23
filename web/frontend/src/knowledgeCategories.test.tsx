import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { ShellOutletContext } from './components/appShellTypes'
import { KnowledgePage } from './pages/KnowledgePage'
import { ReferencePage } from './reference/ReferencePage'
import { server } from './test/server'

describe.each([
  { renderer: 'reference', configured: true },
  { renderer: 'reference', configured: false },
  { renderer: 'react', configured: true },
  { renderer: 'react', configured: false },
])('知识库分类来源隔离：$renderer，分类统计=$configured', ({ renderer, configured }) => {
  it('全部层级分别筛选同名分类和未分类，切换单层后重置筛选', async () => {
    const sources = [
      { scope: 'user', label: '用户层' },
      { scope: 'shared', label: '共享层' },
      { scope: 'global', label: '全局层' },
    ]
    const documents = sources.flatMap(({ scope, label }) => [
      { scope, relative_path: 'notes.md', title: `${label}同名文件`, category: '同名分类', size: 10, updated_at: 1, active_for_main_agent: true },
      { scope, relative_path: 'other.md', title: `${label}未分类文件`, size: 10, updated_at: 1, active_for_main_agent: true },
    ])
    documents.push({ scope: 'global', relative_path: 'all.md', title: '名为all的分类文件', category: 'all', size: 10, updated_at: 1, active_for_main_agent: true })
    const categories: Record<string, { name: string; count: number }[]> = {
      all: [{ name: '未分类', count: 3 }, { name: '同名分类', count: 3 }, { name: 'all', count: 1 }],
    }
    if (configured) {
      for (const { scope } of sources) {
        categories[scope] = [{ name: '未分类', count: 1 }, { name: '同名分类', count: 1 }]
      }
      categories.global.push({ name: 'all', count: 1 })
    }
    server.use(http.get('/api/users/kesepain/knowledge', () => HttpResponse.json({
      user: 'kesepain', enabled: true,
      retrieval: { mode: 'index_only', full_index: true },
      summary: { documents: 7, user_documents: 2, shared_documents: 2, global_documents: 3 },
      documents, categories, classification_file: 'classify.sqlite3', classification_errors: [],
      source_policy: { knowledge: { configured_scopes: ['user', 'shared', 'global'], effective_scopes: ['user', 'shared', 'global'], enabled: true } },
    })))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const context = { user: 'kesepain', sessionId: 's1', chatRunning: false } as ShellOutletContext
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/knowledge']}>
      <Routes><Route element={<Outlet context={context} />}>
        <Route path="knowledge" element={renderer === 'reference' ? <ReferencePage /> : <KnowledgePage />} />
      </Route></Routes>
    </MemoryRouter></QueryClientProvider>)

    let filter = await screen.findByRole('combobox', { name: '筛选知识分类' })
    await within(filter).findByRole('option', { name: '用户层 · 同名分类（1）' })
    const selectCategory = (name: string) => {
      const option = within(filter).getByRole('option', { name })
      fireEvent.change(filter, { target: { value: option.getAttribute('value') } })
    }
    const values = sources.map(({ label }) => (
      within(filter).getByRole('option', { name: `${label} · 同名分类（1）` }).getAttribute('value')
    ))
    expect(new Set(values).size).toBe(3)
    for (const { label } of sources) {
      selectCategory(`${label} · 同名分类（1）`)
      expect(within(screen.getByRole('table')).getAllByRole('row')).toHaveLength(2)
      expect(within(screen.getByRole('table')).getByText(`${label}同名文件`)).toBeInTheDocument()
      selectCategory(`${label} · 未分类（1）`)
      expect(within(screen.getByRole('table')).getAllByRole('row')).toHaveLength(2)
      expect(within(screen.getByRole('table')).getByText(`${label}未分类文件`)).toBeInTheDocument()
    }
    selectCategory('全局层 · all（1）')
    expect(within(screen.getByRole('table')).getAllByRole('row')).toHaveLength(2)
    expect(within(screen.getByRole('table')).getByText('名为all的分类文件')).toBeInTheDocument()

    const tabs = document.querySelector('.tabs, .module-tabs') as HTMLElement
    fireEvent.click(within(tabs).getByRole('button', { name: /^用户层/ }))
    filter = screen.getByRole('combobox', { name: '筛选知识分类' })
    expect(filter).toHaveValue('all')
    expect(within(filter).getByRole('option', { name: '同名分类（1）' })).toBeInTheDocument()
    expect(within(filter).queryByRole('option', { name: /共享层|全局层/ })).not.toBeInTheDocument()
    selectCategory('同名分类（1）')
    expect(within(screen.getByRole('table')).getByText('用户层同名文件')).toBeInTheDocument()
    fireEvent.click(within(tabs).getByRole('button', { name: /^全部/ }))
    filter = screen.getByRole('combobox', { name: '筛选知识分类' })
    expect(filter).toHaveValue('all')
    expect(within(screen.getByRole('table')).getAllByRole('row')).toHaveLength(8)
  })
})
