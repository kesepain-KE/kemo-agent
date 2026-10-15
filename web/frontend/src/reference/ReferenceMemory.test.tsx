import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
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

describe('生产记忆页面与接口合同', () => {
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
      weighted_today: false, weight_locked_today: true,
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
})
