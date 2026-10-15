import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it } from 'vitest'
import { server } from '../test/server'
import { ReferencePage } from './ReferencePage'
import type { ShellOutletContext } from '../components/appShellTypes'

type Category = 'plans' | 'crons' | 'executions'
const labels = { plans: '任务计划', crons: '定时任务', executions: '任务执行记录' }
const tabs = { plans: 'plans', crons: 'cron', executions: 'exec' }
const candidates = Array.from({ length: 8 }, (_, i) => ({
  id: `fixture-${i}`, title: `待清理项目 ${i + 1}`, version: `version-${i}`,
}))

afterEach(() => { document.querySelector('.toast-stack')?.remove() })

function mount(category: Category, options: { empty?: boolean; previewError?: boolean;
  writeError?: boolean; partial?: boolean; populated?: boolean } = {}) {
  let reads = 0
  const posts: any[] = []
  const previews: URL[] = []
  const crons = [...candidates.map(item => ({
    task_id: item.id, title: item.title, status: 'completed', type: 'once',
    user_defined: true, next_run_at: '', latest_run_at: '', created_at: '',
  })), { task_id: 'remaining', title: '保留失败任务', status: 'failed', type: 'once',
    user_defined: true, next_run_at: '', latest_run_at: '', created_at: '' }]
  server.use(
    http.get('/api/users/alice/tasks', () => {
      reads += 1
      return HttpResponse.json({ user: 'alice',
        summary: { active_plans: 0, waiting_plans: 0, enabled_crons: 0, completed_plans: 0 },
        plans: [], cron_tasks: options.populated ? (posts.length ? crons.slice(-1) : crons) : [], executions: [],
      })
    }),
    http.get('/api/users/alice/tasks/crons/:id', ({ params }) => HttpResponse.json({
      user: 'alice', cron_task: { ...crons.find(item => item.task_id === params.id), prompt: 'Fixture only' },
    })),
    http.get('/api/users/alice/tasks/cleanup', ({ request }) => {
      previews.push(new URL(request.url))
      return options.previewError
        ? HttpResponse.json({ error: { message: '预览失败，未作修改' } }, { status: 500 })
        : HttpResponse.json({ user: 'alice', category, count: options.empty ? 0 : 8,
          items: options.empty ? [] : candidates })
    }),
    http.post('/api/users/alice/tasks/cleanup', async ({ request }) => {
      posts.push(await request.json())
      return options.writeError
        ? HttpResponse.json({ error: { message: '清理失败，请重试' } }, { status: 500 })
        : HttpResponse.json({ user: 'alice', category, removed_count: options.partial ? 7 : 8,
          removed: candidates.slice(0, options.partial ? 7 : 8).map(item => item.id),
          skipped: options.partial ? ['fixture-7'] : [], errors: [] })
    }),
  )
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const context = { user: 'alice', sessionId: 's1', chatRunning: false } as ShellOutletContext
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[`/tasks?tab=${tabs[category]}`]}>
    <Routes><Route element={<Outlet context={context} />}><Route path="*" element={<ReferencePage />} /></Route></Routes>
  </MemoryRouter></QueryClientProvider>)
  return { posts, previews, reads: () => reads }
}

describe('任务中枢清理已完成项目', () => {
  it.each(['plans', 'crons', 'executions'] as const)('%s 展示完整清单并仅提交确认快照，成功后刷新', async category => {
    const state = mount(category)
    const button = await screen.findByRole('button', { name: `清理已完成的${labels[category]}` })
    fireEvent.click(button)
    fireEvent.click(button)
    const list = await screen.findByRole('list', { name: '待清理项目' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(8)
    expect(within(list).getByText('待清理项目 8')).toBeInTheDocument()
    expect(state.previews).toHaveLength(1)
    expect(state.previews[0].searchParams.get('category')).toBe(category)
    expect(state.previews[0].searchParams.get('session_id')).toBe('s1')
    expect(state.posts).toHaveLength(0)
    if (category === 'executions') expect(screen.getByText(/原始运行日志及计划步骤结果保留/)).toBeInTheDocument()
    if (category === 'plans') expect(screen.getByText(/所属步骤、修订记录/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '确认清理 8 项' }))
    expect(await screen.findByText('已清理 8 项')).toBeInTheDocument()
    await waitFor(() => expect(state.reads()).toBeGreaterThan(1))
    expect(state.posts).toEqual([{ category, source: 'web', session_id: 's1',
      items: candidates.map(({ id, version }) => ({ id, version })) }])
    expect(screen.queryByRole('list', { name: '待清理项目' })).not.toBeInTheDocument()
  })

  it('取消确认不写入，按钮可再次使用', async () => {
    const state = mount('crons')
    fireEvent.click(await screen.findByRole('button', { name: '清理已完成的定时任务' }))
    await screen.findByRole('list', { name: '待清理项目' })
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    await waitFor(() => expect(screen.getByRole('button', { name: '清理已完成的定时任务' })).toBeEnabled())
    expect(state.posts).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: '清理已完成的定时任务' }))
    await screen.findByRole('list', { name: '待清理项目' })
    expect(state.previews).toHaveLength(2)
  })

  it('没有已完成项目时不弹确认、不写入', async () => {
    const state = mount('plans', { empty: true })
    fireEvent.click(await screen.findByRole('button', { name: '清理已完成的任务计划' }))
    expect(await screen.findByText('没有可清理的已完成项目')).toBeInTheDocument()
    expect(screen.queryByRole('list', { name: '待清理项目' })).not.toBeInTheDocument()
    expect(state.posts).toHaveLength(0)
  })

  it('预览失败不写入，保留原页面并允许重试', async () => {
    const state = mount('crons', { previewError: true })
    fireEvent.click(await screen.findByRole('button', { name: '清理已完成的定时任务' }))
    expect(await screen.findByText('预览失败，未作修改')).toBeInTheDocument()
    expect(state.posts).toHaveLength(0)
    expect(screen.getByRole('button', { name: '清理已完成的定时任务' })).toBeEnabled()
  })

  it('提交失败明确报错且不触发成功刷新', async () => {
    const state = mount('executions', { writeError: true })
    fireEvent.click(await screen.findByRole('button', { name: '清理已完成的任务执行记录' }))
    fireEvent.click(await screen.findByRole('button', { name: '确认清理 8 项' }))
    expect(await screen.findByText('清理失败，请重试')).toBeInTheDocument()
    expect(screen.queryByText('已清理 8 项')).not.toBeInTheDocument()
    expect(state.reads()).toBe(1)
    expect(screen.getByRole('button', { name: '清理已完成的任务执行记录' })).toBeEnabled()
  })

  it('部分项目变化时，刷新后仍展示真实清理数与警告', async () => {
    const state = mount('crons', { partial: true })
    fireEvent.click(await screen.findByRole('button', { name: '清理已完成的定时任务' }))
    fireEvent.click(await screen.findByRole('button', { name: '确认清理 8 项' }))
    await waitFor(() => expect(state.reads()).toBeGreaterThan(1))
    expect(screen.getByText('已清理 7 项；1 项已变化或未清理，请刷新后重试')).toBeInTheDocument()
    expect(screen.queryByText('已处理所选的已完成项目')).not.toBeInTheDocument()
  })

  it('从第二页清理后重置分页与选中项，详情不残留已删除任务', async () => {
    const state = mount('crons', { populated: true })
    const button = await screen.findByRole('button', { name: '清理已完成的定时任务' })
    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    fireEvent.click(screen.getByText('待清理项目 8', { selector: '.entry-title' }))
    await screen.findByText('待清理项目 8', { selector: '.editor-title' })
    fireEvent.click(button.isConnected ? button : screen.getByRole('button', { name: '清理已完成的定时任务' }))
    fireEvent.click(await screen.findByRole('button', { name: '确认清理 8 项' }))
    await waitFor(() => expect(state.reads()).toBeGreaterThan(1))
    expect(await screen.findByText('保留失败任务', { selector: '.editor-title' })).toBeInTheDocument()
    expect(screen.queryByText('待清理项目 8', { selector: '.editor-title' })).not.toBeInTheDocument()
    expect(screen.getByText('共 1 条 · 每页 6 条')).toBeInTheDocument()
    expect(screen.getByText('保留失败任务', { selector: '.entry-title' })).toBeVisible()
  })
})
