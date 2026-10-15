import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ProviderSummary } from '../types/api'
import { server } from '../test/server'
import { ComposerModelSelect } from './ComposerModelSelect'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

const provider: ProviderSummary = {
  type: 'chat', model: 'current-model', base_url: 'https://chat.test/v1',
  configured: true, credential_source: 'environment', reasoning_effort: 'medium', timeout: 120, stream: true,
}

function mount(value = provider, disabled = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const view = (p: ProviderSummary, blocked = false) => <QueryClientProvider client={client}>
    <ComposerModelSelect user="alice" provider={p} disabled={blocked} />
  </QueryClientProvider>
  const rendered = render(view(value, disabled))
  return { client, ...rendered, view }
}

describe('输入框模型切换', () => {
  it('Chat 从兼容模型 API 获取并搜索，保存只修改模型且成功后更新共享概览', async () => {
    const patches: unknown[] = []
    let catalogs = 0
    server.use(
      http.get('/api/users/alice/provider/chat-models', () => {
        catalogs += 1
        return HttpResponse.json({ protocol: 'chat', data: [
          { id: 'current-model', owned_by: 'vendor' }, { id: 'new-model', owned_by: 'vendor' },
        ] })
      }),
      http.patch('/api/users/alice/config', async ({ request }) => {
        patches.push(await request.json())
        return HttpResponse.json({ updated: true })
      }),
    )
    const { client } = mount()
    client.setQueryData(['overview', 'alice', 's1'], { provider })
    expect(catalogs).toBe(0)
    const trigger = screen.getByRole('button', { name: '输入框切换模型' })
    fireEvent.click(trigger)
    const popup = screen.getByRole('dialog', { name: '切换对话模型' })
    expect(popup.parentElement).toBe(document.body)
    expect(popup).toHaveTextContent('Chat · 模型 API 列表')
    expect(await screen.findByRole('option', { name: 'current-model vendor' })).toHaveAttribute('aria-selected', 'true')
    fireEvent.change(screen.getByRole('textbox', { name: '搜索可用模型' }), { target: { value: 'new-' } })
    expect(screen.queryByRole('option', { name: 'current-model vendor' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('option', { name: 'new-model vendor' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '切换对话模型' })).not.toBeInTheDocument())
    expect(patches).toEqual([{ changes: { provider: { model: 'new-model' } } }])
    expect(client.getQueryData(['overview', 'alice', 's1'])).toEqual({ provider: { ...provider, model: 'new-model' } })
    expect(trigger).toHaveFocus()
    expect(catalogs).toBe(1)
  })

  it('KEMO 使用内置目录，排除非 LLM、去重且刷新服务端缓存', async () => {
    const requests: string[] = []
    server.use(http.get('/api/users/alice/provider/models', ({ request }) => {
      requests.push(request.url)
      return HttpResponse.json({ protocol: 'kemo', data: [
        { id: 'kemo-llm', task: 'llm', provider_id: 'kemo' },
        { id: 'kemo-llm', task: 'llm', provider_id: 'kemo' },
        { id: 'kemo-embedding', task: 'embedding', provider_id: 'kemo' },
      ] })
    }))
    mount({ ...provider, type: 'kemo', model: 'kemo-llm' })
    fireEvent.click(screen.getByRole('button', { name: '输入框切换模型' }))
    expect(await screen.findByRole('option', { name: 'kemo-llm kemo' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getAllByRole('option')).toHaveLength(1)
    expect(screen.getByRole('dialog', { name: '切换对话模型' })).toHaveTextContent('KEMO · 内置模型目录')
    fireEvent.click(screen.getByRole('button', { name: '刷新模型列表' }))
    await waitFor(() => expect(requests.some((url) => url.endsWith('?refresh=true'))).toBe(true))
  })

  it('获取失败不伪造列表，刷新恢复后保存失败保留原模型并允许重试', async () => {
    let discoveryFails = true
    let saveFails = true
    server.use(
      http.get('/api/users/alice/provider/chat-models', () => discoveryFails
        ? HttpResponse.json({ error: { message: 'fail' } }, { status: 502 })
        : HttpResponse.json({ data: [{ id: 'new-model', owned_by: '' }] })),
      http.patch('/api/users/alice/config', () => saveFails
        ? HttpResponse.json({ error: { message: 'fail' } }, { status: 500 })
        : HttpResponse.json({ updated: true })),
    )
    mount()
    const trigger = screen.getByRole('button', { name: '输入框切换模型' })
    fireEvent.click(trigger)
    expect(await screen.findByRole('alert')).toHaveTextContent('模型列表获取失败')
    expect(screen.queryByRole('option')).not.toBeInTheDocument()
    discoveryFails = false
    fireEvent.click(screen.getByRole('button', { name: '刷新模型列表' }))
    fireEvent.click(await screen.findByRole('option', { name: 'new-model' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('模型切换失败')
    expect(trigger).toHaveTextContent('current-model')
    saveFails = false
    fireEvent.click(screen.getByRole('option', { name: 'new-model' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '切换对话模型' })).not.toBeInTheDocument())
  })

  it.each([
    [404, 'not_found', '接口不存在', '当前后端尚未加载模型列表接口，请重启智能体后重试。'],
    [401, 'provider_access_denied', 'Chat API 密钥无效或没有获取模型列表的权限', 'Chat API 密钥无效或没有获取模型列表的权限'],
    [502, 'provider_discovery_failed', 'Chat 模型列表获取失败，请检查 Provider 的 /models 接口', '请检查 Provider 的 /models 接口'],
  ])('模型目录失败时显示具体原因（HTTP %s）', async (status, code, message, expected) => {
    server.use(http.get('/api/users/alice/provider/chat-models', () =>
      HttpResponse.json({ error: { code, message } }, { status })))
    mount()
    fireEvent.click(screen.getByRole('button', { name: '输入框切换模型' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(expected)
    expect(screen.queryByRole('option')).not.toBeInTheDocument()
  })

  it('同模型、外部点击及 Escape 不保存；运行中禁用，协议变化关闭旧列表', async () => {
    const patch = vi.fn()
    server.use(
      http.get('/api/users/alice/provider/chat-models', () => HttpResponse.json({ data: [{ id: 'current-model' }] })),
      http.patch('/api/users/alice/config', () => { patch(); return HttpResponse.json({ updated: true }) }),
    )
    const { rerender, view } = mount()
    const trigger = screen.getByRole('button', { name: '输入框切换模型' })
    fireEvent.click(trigger)
    fireEvent.click(await screen.findByRole('option', { name: 'current-model' }))
    expect(patch).not.toHaveBeenCalled()
    fireEvent.click(trigger)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(trigger).toHaveFocus()
    fireEvent.click(trigger)
    fireEvent.pointerDown(document.body)
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(trigger)
    rerender(view({ ...provider, type: 'kemo' }))
    expect(screen.queryByRole('dialog', { name: '切换对话模型' })).not.toBeInTheDocument()
    rerender(view(provider, true))
    expect(trigger).toBeDisabled()
    expect(patch).not.toHaveBeenCalled()
  })

  it('气泡限制宽度及高度，可从搜索进入模型选项并使用方向键导航', async () => {
    vi.stubGlobal('innerWidth', 390)
    vi.stubGlobal('innerHeight', 844)
    server.use(http.get('/api/users/alice/provider/chat-models', () => HttpResponse.json({
      data: [{ id: 'current-model' }, { id: 'another-model' }],
    })))
    mount()
    const trigger = screen.getByRole('button', { name: '输入框切换模型' })
    vi.spyOn(trigger, 'getBoundingClientRect').mockReturnValue({
      left: 290, right: 430, width: 140, top: 600, bottom: 625, height: 25,
    } as DOMRect)
    fireEvent.click(trigger)
    const popup = screen.getByRole('dialog', { name: '切换对话模型' })
    expect(popup).toHaveStyle({ left: '58px', width: '320px', bottom: '252px', maxHeight: '420px' })
    await screen.findByRole('option', { name: 'current-model' })
    fireEvent.keyDown(screen.getByRole('textbox', { name: '搜索可用模型' }), { key: 'ArrowDown' })
    expect(within(popup).getByRole('option', { name: 'current-model' })).toHaveFocus()
    fireEvent.keyDown(document.activeElement!, { key: 'ArrowDown' })
    expect(within(popup).getByRole('option', { name: 'another-model' })).toHaveFocus()
  })
})
