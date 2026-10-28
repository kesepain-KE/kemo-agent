import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import userEvent from '@testing-library/user-event'
import { server } from '../test/server'
import { ReferencePage } from './ReferencePage'
import type { ShellOutletContext } from '../components/appShellTypes'

const tiers = [
  ['seven_days', '七日记忆'], ['one_month', '一月记忆'],
  ['half_year', '半年记忆'], ['permanent', '永久记忆'],
] as const

function renderMemory(path = '/memory') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const context = { user: 'kesepain', sessionId: 's1', chatRunning: false } as ShellOutletContext
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}>
    <Routes><Route element={<Outlet context={context} />}><Route path="*" element={<ReferencePage />} /></Route></Routes>
  </MemoryRouter></QueryClientProvider>)
  return client
}

function mockSearchableMemory() {
  const items = Array.from({ length: 8 }, (_, i) => ({
    tier: 'seven_days', filename: `search-${i}.md`, preview: i === 7 ? 'AbCd 中文搜索目标' : `普通记录 ${i}`,
    weight: 1, created_at: `2026-10-${String(24 - i).padStart(2, '0')}T01:00:00Z`,
    updated_at: `2026-10-${String(24 - i).padStart(2, '0')}T01:00:00Z`,
  }))
  server.use(http.get('/api/users/kesepain/memory/summary', () => HttpResponse.json({
    user: 'kesepain', summary: { total: 8, seven_days: 8, one_month: 0, half_year: 0, permanent: 0 }, items,
  })))
}

describe('生产记忆页面与接口合同', () => {
  it('连续输入不匹配、不重建搜索框，点击右侧放大镜才筛选并回到第一页', async () => {
    mockSearchableMemory()
    const user = userEvent.setup()
    renderMemory()
    await screen.findByText('search-0.md')
    await user.click(screen.getByRole('button', { name: '下一页' }))
    const input = screen.getByRole('searchbox', { name: '搜索记忆内容…' })
    await user.type(input, '  aBcD  ')
    expect(screen.getByRole('searchbox')).toBe(input)
    expect(input).toHaveFocus()
    expect(input).toHaveValue('  aBcD  ')
    expect(document.querySelectorAll('.memory-fragment-list .list-row')).toHaveLength(2)
    expect(screen.getByText('search-6.md')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '搜索' }))
    expect(document.querySelectorAll('.memory-fragment-list .list-row')).toHaveLength(1)
    expect(screen.getByText('search-7.md')).toBeInTheDocument()
    expect(screen.queryByText('search-6.md')).not.toBeInTheDocument()
    expect(screen.getByRole('searchbox')).toHaveValue('  aBcD  ')
    expect(screen.getByRole('searchbox')).toHaveFocus()
    await user.click(screen.getByRole('button', { name: '清除搜索' }))
    expect(screen.getByRole('searchbox')).toHaveValue('')
    expect(screen.getByRole('searchbox')).toHaveFocus()
    expect(document.querySelectorAll('.memory-fragment-list .list-row')).toHaveLength(6)
    expect(screen.getByText('search-0.md')).toBeInTheDocument()
  })

  it('支持回车提交，输入法组合确认不会提前执行搜索', async () => {
    mockSearchableMemory()
    const user = userEvent.setup()
    renderMemory()
    await screen.findByText('search-0.md')
    const input = screen.getByRole('searchbox')
    input.focus()
    fireEvent.compositionStart(input)
    fireEvent.input(input, { target: { value: '中文' } })
    const accepted = fireEvent.keyDown(input, { key: 'Enter', isComposing: true, keyCode: 229 })
    expect(accepted).toBe(false)
    fireEvent.submit(screen.getByRole('search'))
    expect(screen.getByRole('searchbox')).toBe(input)
    expect(input).toHaveValue('中文')
    expect(document.querySelectorAll('.memory-fragment-list .list-row')).toHaveLength(6)
    fireEvent.compositionEnd(input)
    await user.keyboard('{Enter}')
    expect(screen.getByText('search-7.md')).toBeInTheDocument()
    expect(document.querySelectorAll('.memory-fragment-list .list-row')).toHaveLength(1)
    expect(screen.getByRole('searchbox')).toHaveFocus()
  })

  it('排序和重新读取保留输入草稿，但不会把未提交草稿用作筛选条件', async () => {
    mockSearchableMemory()
    const user = userEvent.setup()
    renderMemory()
    await screen.findByText('search-0.md')
    await user.type(screen.getByRole('searchbox'), 'AbCd')
    await user.keyboard('{Enter}')
    await user.type(screen.getByRole('searchbox'), ' 尚未搜索')
    fireEvent.change(screen.getByRole('combobox', { name: '记忆排序' }), { target: { value: 'created_at' } })
    expect(screen.getByRole('searchbox')).toHaveValue('AbCd 尚未搜索')
    expect(screen.getByText('search-7.md')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '重新读取' }))
    await waitFor(() => expect(screen.getByRole('searchbox')).toHaveValue('AbCd 尚未搜索'))
    expect(screen.getByText('search-7.md')).toBeInTheDocument()
    expect(document.querySelectorAll('.memory-fragment-list .list-row')).toHaveLength(1)
  })

  it('临时重要记忆直接呈现单个只读详情，刷新保留分栏', async () => {
    let writes = 0
    server.use(
      http.get('/api/users/kesepain/memory/important', () => HttpResponse.json({
        user: 'kesepain', path: 'users/kesepain/memory_temporary_important.md',
        content: '# 热画像预览\n\n原有内容保留', size: 120,
        updated_at: '2026-10-12T18:14:00+08:00',
        lifecycle: { status: 'invalid', is_current: false, prompt_eligible: false,
          reason_codes: ['source_missing'], reason: '来源记忆已删除或清理。' },
      })),
      http.put('/api/users/kesepain/memory/important', () => { writes++; return HttpResponse.json({}) }),
      http.delete('/api/users/kesepain/memory/important', () => { writes++; return HttpResponse.json({}) }),
    )
    const client = renderMemory()
    await screen.findByRole('heading', { name: '记忆' })
    fireEvent.click(screen.getByText('临时重要记忆', { selector: '.tier-name' }))
    const detail = screen.getByRole('region', { name: '临时重要记忆详情' })
    expect(await within(detail).findByRole('heading', { name: '热画像预览' })).toBeInTheDocument()
    expect(detail).toHaveTextContent('来源记忆已删除或清理。')
    expect(detail).toHaveTextContent('暂不注入')
    expect(detail).toHaveTextContent('2026-10-12 18:14（+08:00）')
    expect(within(detail).queryByRole('button')).not.toBeInTheDocument()
    expect(screen.queryByText('打开编辑器')).not.toBeInTheDocument()
    expect(screen.queryByText('选择一条记忆')).not.toBeInTheDocument()
    expect(document.querySelector('.memory-layout')).toHaveClass('is-important')
    expect(document.querySelector('.memory-layout')?.children[2]).not.toBeVisible()
    await act(() => client.invalidateQueries({ queryKey: ['reference-page'] }))
    expect(screen.getByRole('region', { name: '临时重要记忆详情' })).toBeInTheDocument()
    expect(writes).toBe(0)
  })

  it.each(tiers)('%s 读取、恢复、保存和删除使用正确层级与文件名', async (tier, label) => {
    const filename = `合同 测试-${tier}.md`
    let content = '原始正文'
    let deleted = false
    let readCount = 0
    const writes: unknown[] = []
    const record = () => ({
      user: 'kesepain', filename, memory_ref: `${tier}:${filename}`, tier,
      weight: 2, preview: content, content, timezone: 'UTC', truncated: false,
      created_at: '2026-10-12T01:00:00Z', updated_at: '2026-10-12T01:00:00Z',
      content_updated_at: '2026-10-12T01:00:00Z', last_used_at: null,
      tier_entered_at: '2026-10-12T01:00:00Z',
      expires_at: tier === 'permanent' ? null : '2026-10-19T01:00:00Z',
      weighted_today: true, weight_locked_today: true,
    })
    server.use(
      http.get('/api/users/kesepain/memory/summary', () => HttpResponse.json({
        user: 'kesepain', summary: { total: deleted ? 0 : 1, seven_days: 0, one_month: 0, half_year: 0,
          permanent: 0, [tier]: deleted ? 0 : 1 }, items: deleted ? [] : [record()],
      })),
      http.get('/api/users/kesepain/memory/item', ({ request }) => {
        const query = new URL(request.url).searchParams
        expect(query.get('tier')).toBe(tier)
        expect(query.get('filename')).toBe(filename)
        readCount++
        return HttpResponse.json(record())
      }),
      http.put('/api/users/kesepain/memory/item', async ({ request }) => {
        expect(new URL(request.url).searchParams.get('filename')).toBe(filename)
        const body = await request.json() as { content: string; tier: string }
        expect(body.tier).toBe(tier)
        writes.push(body); content = body.content
        return HttpResponse.json({ ...record(), updated: true })
      }),
      http.delete('/api/users/kesepain/memory/item', ({ request }) => {
        const query = new URL(request.url).searchParams
        expect(query.get('tier')).toBe(tier)
        expect(query.get('filename')).toBe(filename)
        deleted = true
        return HttpResponse.json({ deleted: true, tier, filename })
      }),
    )
    renderMemory(`/memory?tier=${tier}`)
    fireEvent.click(await screen.findByText(filename))
    await waitFor(() => expect(readCount).toBe(1))
    await waitFor(() => expect(document.querySelector('.memory-document-preview')).toHaveTextContent('原始正文'))
    expect(screen.queryByText('权重规则')).not.toBeInTheDocument()
    expect(screen.getAllByText('2026-10-12 01:00（UTC）').length).toBeGreaterThan(0)
    expect(document.querySelector('.memory-layout')).not.toHaveClass('is-important')
    expect(document.querySelector('.section-title')).toHaveTextContent(label)
    const list = document.querySelector('.memory-fragment-list') as HTMLElement
    const sort = screen.getByRole('combobox', { name: '记忆排序' })
    if (tier === 'permanent') {
      expect(list.querySelector('.weight-bars')).not.toBeInTheDocument()
      expect(within(list).queryByText('权重', { exact: true })).not.toBeInTheDocument()
      expect(within(list).queryByText('权重锁')).not.toBeInTheDocument()
      expect(screen.queryByText('当前权重')).not.toBeInTheDocument()
      expect(within(sort).queryByRole('option', { name: '按权重' })).not.toBeInTheDocument()
    } else {
      expect(list.querySelector('.weight-bars')).toBeInTheDocument()
      expect(within(list).getByText('权重', { exact: true })).toBeInTheDocument()
      expect(within(list).getByText('权重锁')).toBeInTheDocument()
      expect(screen.getByText('当前权重')).toBeInTheDocument()
      expect(within(sort).getByRole('option', { name: '按权重' })).toBeInTheDocument()
    }
    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    const editor = document.querySelector<HTMLTextAreaElement>('.memory-document-editor')!
    fireEvent.input(editor, { target: { value: '尚未保存的内容' } })
    fireEvent.click(screen.getByRole('button', { name: '预览' }))
    await waitFor(() => expect(document.querySelector('.memory-document-preview')).toHaveTextContent('尚未保存的内容'))
    expect(screen.getByText('有未保存修改')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '恢复' }))
    await waitFor(() => expect(document.querySelector('.memory-document-preview')).toHaveTextContent('原始正文'))
    expect(readCount).toBe(2)
    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    fireEvent.input(document.querySelector('.memory-document-editor')!, { target: { value: '已更新正文' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(writes).toEqual([{ content: '已更新正文', tier }]))
    await screen.findAllByText('记忆已保存')
    fireEvent.click(await screen.findByText(filename))
    await waitFor(() => expect(document.querySelector('.memory-document-preview')).toHaveTextContent('已更新正文'))
    fireEvent.click(screen.getByRole('button', { name: '删除' }))
    const confirm = await screen.findByRole('dialog', { name: '删除此记忆' })
    expect(deleted).toBe(false)
    fireEvent.click(within(confirm).getByRole('button', { name: '删除记忆' }))
    await waitFor(() => expect(deleted).toBe(true))
    await screen.findAllByText('记忆已删除')
  })

  it('新建长期记忆显式写入永久层', async () => {
    let captured: unknown
    server.use(http.put('/api/users/kesepain/memory/item', async ({ request }) => {
      expect(new URL(request.url).searchParams.get('filename')).toMatch(/^memory_\d+\.md$/)
      captured = await request.json()
      return HttpResponse.json({ updated: true })
    }))
    renderMemory()
    fireEvent.click(await screen.findByRole('button', { name: '新建长期记忆' }))
    const dialog = screen.getByRole('dialog', { name: '新建长期记忆' })
    fireEvent.input(within(dialog).getByRole('textbox'), { target: { value: '明确长期保留的事实' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '创建' }))
    await waitFor(() => expect(captured).toEqual({ content: '明确长期保留的事实', tier: 'permanent' }))
  })

  it('从临时层按权重排序切入永久层时回到更新时间排序，刷新后仍不展示权重', async () => {
    const items = [
      { tier: 'seven_days', filename: '临时低权重.md', preview: '临时低权重', weight: 1, updated_at: '2026-10-22T01:00:00Z' },
      { tier: 'seven_days', filename: '临时高权重.md', preview: '临时高权重', weight: 9, updated_at: '2026-10-21T01:00:00Z' },
      { tier: 'permanent', filename: '永久新记录.md', preview: '永久新记录', weight: 0, updated_at: '2026-10-22T01:00:00Z' },
      { tier: 'permanent', filename: '永久旧记录.md', preview: '永久旧记录', weight: 99, updated_at: '2026-10-21T01:00:00Z', weighted_today: true },
    ]
    server.use(http.get('/api/users/kesepain/memory/summary', () => HttpResponse.json({
      user: 'kesepain', summary: { total: 4, seven_days: 2, one_month: 0, half_year: 0, permanent: 2 }, items,
    })))
    const client = renderMemory()
    await screen.findByText('临时低权重.md')
    fireEvent.change(screen.getByRole('combobox', { name: '记忆排序' }), { target: { value: 'weight' } })
    expect(document.querySelector('.memory-fragment-list .list-row')).toHaveTextContent('临时高权重.md')
    fireEvent.click(screen.getByText('永久记忆', { selector: '.tier-name' }))
    expect(screen.getByRole('combobox', { name: '记忆排序' })).toHaveValue('updated_at')
    expect(document.querySelector('.memory-fragment-list .list-row')).toHaveTextContent('永久新记录.md')
    expect(document.querySelector('.weight-bars')).not.toBeInTheDocument()
    await act(() => client.invalidateQueries({ queryKey: ['reference-page'] }))
    expect(document.querySelector('.section-title')).toHaveTextContent('永久记忆')
    expect(screen.getByRole('combobox', { name: '记忆排序' })).toHaveValue('updated_at')
    expect(document.querySelector('.weight-bars')).not.toBeInTheDocument()
    fireEvent.click(screen.getByText('七日记忆', { selector: '.tier-name' }))
    expect(screen.getByRole('option', { name: '按权重' })).toBeInTheDocument()
    expect(document.querySelectorAll('.weight-bars')).toHaveLength(2)
  })
})
