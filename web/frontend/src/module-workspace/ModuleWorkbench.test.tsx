import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { HttpResponse, http } from 'msw'
import { createMemoryRouter, Outlet, RouterProvider } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ModulePanel } from '../components/ModulePanel'
import type { ModulePanelResponse } from '../types/api'
import { server } from '../test/server'
import { ModuleWorkbench } from './ModuleWorkbench'
import { PluginWebHost } from './PluginWebHost'
import type { WorkspaceItem } from './types'

const item: WorkspaceItem = {
  key: 'global:sample', kind: 'expand', scope: 'global', module: 'sample', title: '真实接口测试模块',
  description: '配置与控制台', health: '正常', valid: true, enabled: true, active_for_main_agent: true,
  error: '', ui_error: '', ui_revision: 'ui-1', authorization_revision: 'auth-1', permission_revision: 'grant-1',
  requested_permissions: ['module.status.read'], granted_permissions: ['module.status.read'],
  default_page: 'overview', actions: [],
  collected_markdown: '原始采集完整正文', injected_markdown: '实际注入片段',
  pages: [{ id: 'overview', label: '总览', type: 'builtin', view: 'overview' }, { id: 'panel', label: '通用配置', type: 'panel' },
    { id: 'console', label: '专属控制台', type: 'web', entry: 'ui/dist/index.html', permissions: ['module.status.read'] },
    { id: 'collection', label: '采集数据', type: 'builtin', view: 'collection' },
    { id: 'injection', label: '注入数据', type: 'builtin', view: 'injection' }],
}
const panel: ModulePanelResponse = {
  user: 'alice', scope: 'global', module: 'sample', panel_error: '', config_revision: 'config-1',
  panel: { schema_version: 1, title: '测试配置', containers: [
    { kind: 'config', title: '连接配置', width: 'full', height: 'h1', values: 'module/values.json', presets: [], current_values: { host: 'before' },
      fields: [{ key: 'host', label: '主机', type: 'string' }] },
  ] },
}
afterEach(() => { vi.restoreAllMocks(); localStorage.clear() })

function handlers(kind: 'expand' | 'sense' = 'expand', overrides: Partial<WorkspaceItem> = {}) {
  server.use(
    http.get(`/api/users/alice/module-workspaces/${kind}`, () => HttpResponse.json({
      user: 'alice', kind, items: [{ ...item, ...overrides, kind, key: kind === 'sense' ? 'sense:sample' : item.key }],
      summary: { total: 1, enabled: 1, invalid: 0, injected_tokens: 0 }, registry_revision: 'registry-1',
    })),
    http.get('/api/users/alice/module-workspaces/expand/global/sample/panel', () => HttpResponse.json(panel)),
  )
}
function renderWorkbench(kind: 'expand' | 'sense' = 'expand') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter([{ path: '/', element: <Outlet context={{ user: 'alice' }} />, children: [
    { path: 'expand', element: <ModuleWorkbench /> }, { path: 'sense', element: <ModuleWorkbench /> }, { path: 'other', element: <p>other</p> },
  ] }], { initialEntries: [`/${kind}?user=alice`] })
  const view = render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>)
  return { ...view, router, client }
}

describe('module workbench', () => {
  it.each(['expand', 'sense'] as const)('separates %s collection and injection tabs and their content', async kind => {
    handlers(kind)
    const view = renderWorkbench(kind)
    try {
      await screen.findByRole('button', { name: /真实接口测试模块/ })
      const features = within(screen.getByRole('navigation', { name: '模块功能' }))
      expect(features.queryByRole('button', { name: '采集与注入' })).toBeNull()
      fireEvent.click(features.getByRole('button', { name: '采集数据' }))
      expect(await screen.findByText(item.collected_markdown!)).toBeVisible()
      expect(screen.queryByText(item.injected_markdown!)).toBeNull()
      expect(new URLSearchParams(view.router.state.location.search).get('page')).toBe('collection')
      fireEvent.click(features.getByRole('button', { name: '注入数据' }))
      expect(await screen.findByText(item.injected_markdown!)).toBeVisible()
      expect(screen.queryByText(item.collected_markdown!)).toBeNull()
      expect(screen.getByText('注入内容遵循当前数据空间的白名单与上下文预算。')).toBeVisible()
      expect(new URLSearchParams(view.router.state.location.search).get('page')).toBe('injection')
      fireEvent.click(features.getByRole('button', { name: '采集数据' }))
      expect(await screen.findByText(item.collected_markdown!)).toBeVisible()
      expect(screen.queryByText(item.injected_markdown!)).toBeNull()
    } finally { view.unmount(); view.router.dispose(); view.client.clear() }
  })
  it.each(['expand', 'sense'] as const)('shows the %s injection empty state without substituting collected content', async kind => {
    handlers(kind, { injected_markdown: '' })
    const view = renderWorkbench(kind)
    try {
      await screen.findByRole('button', { name: /真实接口测试模块/ })
      fireEvent.click(within(screen.getByRole('navigation', { name: '模块功能' })).getByRole('button', { name: '注入数据' }))
      expect(await screen.findByText('当前未注入')).toBeVisible()
      expect(screen.queryByText(item.collected_markdown!)).toBeNull()
    } finally { view.unmount(); view.router.dispose(); view.client.clear() }
  })
  it.each(['expand', 'sense'] as const)('keeps %s rows compact and searches names without matching descriptions', async kind => {
    handlers(kind)
    const view = renderWorkbench(kind)
    try {
      const list = screen.getByRole('complementary', { name: '模块列表' })
      const row = await within(list).findByRole('button', { name: /真实接口测试模块/ })
      expect(row).not.toHaveTextContent(item.description)
      const metadata = within(row).getByText('全局 · sample').parentElement!
      expect(metadata).toHaveTextContent('已启用')
      expect(metadata).toHaveTextContent('0 tok')
      const search = screen.getByRole('textbox', { name: '搜索模块' })
      fireEvent.change(search, { target: { value: item.description } })
      expect(within(list).queryByRole('button')).toBeNull()
      fireEvent.change(search, { target: { value: '  SAMPLE  ' } })
      expect(within(list).getByRole('button', { name: /真实接口测试模块/ })).toBeVisible()
      fireEvent.change(search, { target: { value: '真实接口' } })
      expect(within(list).getByRole('button', { name: /真实接口测试模块/ })).toBeVisible()
    } finally { view.unmount(); view.router.dispose(); view.client.clear() }
  })
  it('separates scopes, modules and detail tabs, and keeps form state when expanded', async () => {
    handlers()
    const view = renderWorkbench()
    try {
      await screen.findByRole('button', { name: /真实接口测试模块/ })
      expect(within(screen.getByRole('complementary', { name: '模块作用域' })).queryByRole('button', { name: /真实接口测试模块/ })).toBeNull()
      expect(within(screen.getByRole('complementary', { name: '模块列表' })).getByRole('button', { name: /真实接口测试模块/ })).toBeVisible()
      expect(within(screen.getByRole('region', { name: '模块详情' })).getByRole('navigation', { name: '模块功能' })).toBeVisible()
      fireEvent.click(screen.getByRole('button', { name: '通用配置' }))
      const input = await screen.findByRole('textbox', { name: '主机' })
      fireEvent.change(input, { target: { value: 'unsaved-host' } })
      fireEvent.click(screen.getByRole('button', { name: '放大工作区' }))
      expect(screen.getByRole('textbox', { name: '主机' })).toBe(input)
      expect(input).toHaveValue('unsaved-host')
      fireEvent.click(screen.getByRole('button', { name: '恢复布局' }))
      expect(screen.getByRole('textbox', { name: '主机' })).toBe(input)
      fireEvent.click(screen.getByRole('button', { name: '总览' }))
      expect(await screen.findByRole('alertdialog')).toHaveTextContent('放弃未保存')
      fireEvent.click(screen.getByRole('button', { name: '取消' }))
      expect(input).toHaveValue('unsaved-host')
      fireEvent.click(screen.getByRole('button', { name: '总览' }))
      fireEvent.click(screen.getByRole('button', { name: '放弃并继续' }))
      await waitFor(() => expect(screen.queryByRole('textbox', { name: '主机' })).toBeNull())
    } finally { view.unmount(); view.router.dispose(); view.client.clear() }
  })
  it('shows an empty detail area for an empty scope instead of selecting another scope', async () => {
    handlers()
    const view = renderWorkbench()
    try {
      await screen.findByRole('button', { name: /真实接口测试模块/ })
      fireEvent.click(within(screen.getByRole('group', { name: '作用域筛选' })).getByRole('button', { name: /共享/ }))
      await screen.findByText('没有匹配的模块')
      expect(screen.getByRole('region', { name: '模块详情' })).toHaveTextContent('选择一个模块开始管理')
      expect(within(screen.getByRole('complementary', { name: '模块列表' })).queryByRole('button')).toBeNull()
      fireEvent.click(within(screen.getByRole('group', { name: '作用域筛选' })).getByRole('button', { name: /全局/ }))
      expect(await screen.findByRole('button', { name: /真实接口测试模块/ })).toHaveAttribute('aria-pressed', 'true')
      await act(async () => { await view.router.navigate('/expand?user=alice&scope=shared&module=&page=') })
      expect(await screen.findByText('没有匹配的模块')).toBeVisible()
      await act(async () => { await view.router.navigate(-1) })
      expect(await screen.findByRole('button', { name: /真实接口测试模块/ })).toHaveAttribute('aria-pressed', 'true')
    } finally { view.unmount(); view.router.dispose(); view.client.clear() }
  })
  it('refreshing panel status preserves drafts and submits their original config revision', async () => {
    const save = vi.fn(async () => undefined)
    const view = render(<ModulePanel kind="expand" data={panel} emptyText="" onSave={save} onAction={async () => undefined} />)
    const input = screen.getByRole('textbox', { name: '主机' })
    fireEvent.change(input, { target: { value: 'draft' } })
    view.rerender(<ModulePanel kind="expand" data={{ ...panel, config_revision: 'config-2' }} emptyText="" onSave={save} onAction={async () => undefined} />)
    expect(input).toHaveValue('draft')
    fireEvent.click(screen.getByRole('button', { name: '保存配置' }))
    await waitFor(() => expect(save).toHaveBeenCalledWith({ host: 'draft' }, [], 'config-1'))
  })
  it('validates iframe source and nonce before forwarding capability requests', async () => {
    const calls: string[] = []
    server.use(
      http.post('/api/users/alice/module-workspaces/expand/global/sample/instances', () => HttpResponse.json({ nonce: 'nonce-1', asset_url: '/module-ui/nonce-1/index.html', ui_revision: 'ui-1', permissions: ['module.status.read'] })),
      http.post('/api/module-ui/instances/nonce-1/rpc', async ({ request }) => { calls.push((await request.json() as { method: string }).method); return HttpResponse.json({ result: { health: 'ready' } }) }),
      http.delete('/api/module-ui/instances/nonce-1', () => HttpResponse.json({ closed: true })),
    )
    const page = item.pages[2]
    const view = render(<PluginWebHost user="alice" item={item} page={page} onDirtyChange={() => undefined} onExpand={() => undefined} />)
    const iframe = await screen.findByTitle(`${item.title} · ${page.label}`) as HTMLIFrameElement
    const request = { protocol: 'kemo.module-ui.v1', kind: 'request', request_id: '1', nonce: 'nonce-1', method: 'module.status.get', params: {} }
    await act(async () => {
      window.dispatchEvent(new MessageEvent('message', { source: window, data: request }))
      window.dispatchEvent(new MessageEvent('message', { source: iframe.contentWindow, data: { ...request, nonce: 'wrong' } }))
    })
    expect(calls).toEqual([])
    await act(async () => { window.dispatchEvent(new MessageEvent('message', { source: iframe.contentWindow, data: request })) })
    await waitFor(() => expect(calls).toEqual(['module.status.get']))
    view.rerender(<PluginWebHost user="alice" item={{ ...item, health: '异常' }} page={page} onDirtyChange={() => undefined} onExpand={() => undefined} />)
    expect(screen.getByTitle(`${item.title} · ${page.label}`)).toBe(iframe)
  })
})
