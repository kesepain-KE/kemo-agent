import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Outlet, Route, Routes, useLocation } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../test/server'
import { ReferencePage, loadReferenceData } from './ReferencePage'
import type { ShellOutletContext } from '../components/appShellTypes'

function renderPage(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const context = { user: 'kesepain', sessionId: 's1', chatRunning: false } as ShellOutletContext
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}>
    <Routes><Route element={<Outlet context={context} />}><Route path="/chat" element={<ChatDestination />} /><Route path="*" element={<ReferencePage />} /></Route></Routes>
  </MemoryRouter></QueryClientProvider>)
}

function ChatDestination() {
  const params = new URLSearchParams(useLocation().search)
  return <output data-testid="chat-draft" data-user={params.get('user')} data-session={params.get('session')}>{params.get('prompt')}</output>
}

describe('感知与拓展模块详情', () => {
  it('增加到用户层跳转到同一用户会话的聊天页，仅预置指定提示词', async () => {
    renderPage('/expand')
    fireEvent.click(await screen.findByRole('button', { name: '增加到用户层' }))
    const draft = await screen.findByTestId('chat-draft')
    expect(draft).toHaveTextContent('创建一个新的用户拓展，扩展要求为：')
    expect(draft).toHaveAttribute('data-user', 'kesepain')
    expect(draft).toHaveAttribute('data-session', 's1')
  })

  it('总览只读，数据中的填入或发送动作不能跳转聊天或执行', async () => {
    const data = (await loadReferenceData('/expand', 'kesepain', 's1')).expand
    data.injection.data_content = '# 数据总览\n\n```kemo-widget\n'
      + JSON.stringify({ schema_version: 1, id: 'readonly-preview', component: 'follow-up', props: { prompts: [{ label: '执行数据中的提示词', text: '不应被执行', send: true }] } })
      + '\n```'
    server.use(http.get('/api/users/kesepain/expand', () => HttpResponse.json(data)))
    renderPage('/expand')
    const action = await screen.findByRole('button', { name: /执行数据中的提示词/ })
    expect(action).toBeDisabled()
    await userEvent.click(action)
    expect(screen.queryByTestId('chat-draft')).not.toBeInTheDocument()
    expect(screen.getByRole('region', { name: '拓展注入预览' })).toBeInTheDocument()
  })

  it('感知每页六个，翻页同步详情，重新读取后保留页面并处理库存减少', async () => {
    const data = (await loadReferenceData('/sense', 'kesepain', 's1')).sense
    data.sources = Array.from({ length: 14 }, (_, i) => ({
      ...data.sources[0], id: `sense-${i}`, name: `sense-${i}`, display_name: `感知模块 ${i}`,
      collected_markdown: `# 采集内容 ${i}`, injected_markdown: `# 注入内容 ${i}`,
    }))
    server.use(http.get('/api/users/kesepain/sense', () => HttpResponse.json(data)))
    renderPage('/sense')
    fireEvent.click(await screen.findByRole('button', { name: '模块详情' }))
    expect(await screen.findByRole('heading', { name: '采集内容 0' })).toBeInTheDocument()
    expect(document.querySelectorAll('.module-card')).toHaveLength(6)
    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    expect(await screen.findByRole('heading', { name: '采集内容 6' })).toBeInTheDocument()
    expect(document.querySelectorAll('.module-card')).toHaveLength(6)
    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    expect(await screen.findByRole('heading', { name: '采集内容 12' })).toBeInTheDocument()
    expect(document.querySelectorAll('.module-card')).toHaveLength(2)
    expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '重新读取' }))
    await waitFor(() => expect(document.querySelector('.reference-page-host')).not.toHaveAttribute('aria-busy'))
    expect(document.querySelector('.pager .is-active')).toHaveTextContent('3')
    data.sources = data.sources.slice(0, 9)
    fireEvent.click(screen.getByRole('button', { name: '重新读取' }))
    await waitFor(() => expect(document.querySelectorAll('.module-card')).toHaveLength(3))
    expect(await screen.findByRole('heading', { name: '采集内容 6' })).toBeInTheDocument()
    expect(document.querySelector('.pager .is-active')).toHaveTextContent('2')
  })

  it('拓展使用左右布局，完整渲染总览、采集、真实注入和操作文档', async () => {
    const data = (await loadReferenceData('/expand', 'kesepain', 's1')).expand
    const module = data.expands[0].items[0]
    data.injection.data_content = '# 三层注入总览\n\n**完整总览末尾**'
    data.injection.content = data.injection.data_content + '\n\n## 操控能力\n不应出现在数据预览中的操作说明'
    module.collected_markdown = '# 完整采集\n\n| 项目 | 值 |\n| --- | --- |\n| 灯光 | 开启 |\n\n'
      + Array.from({ length: 70 }, (_, i) => `- 采集项 ${i}`).join('\n') + '\n\n采集末尾。'
    module.data_injected_markdown = '# 注入内容\n\n**实际注入末尾**'
    module.injected_markdown = module.data_injected_markdown + '\n\n## 操控能力\n不应出现在数据预览中的操作说明'
    module.control_document = '# 模块使用指南\n\n1. 选择房间\n2. 设置灯光\n\n完整操作末尾。'
    module.control_injection_markdown = '## 注入层声明\n\n`light_control`'
    module.control_operation_markdown = '## 操作层说明\n\n- 调用白名单动作\n\n操作层末尾。'
    server.use(http.get('/api/users/kesepain/expand', () => HttpResponse.json(data)))
    renderPage('/expand')
    expect(await screen.findByRole('heading', { name: '三层注入总览' })).toBeInTheDocument()
    expect(document.querySelector('.expand-layout')).toHaveClass('sense-layout')
    expect(screen.getByRole('region', { name: '拓展注入预览' }).querySelector('strong')).toHaveTextContent('完整总览末尾')
    expect(screen.queryByText('不应出现在数据预览中的操作说明')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '模块详情' }))
    expect(await screen.findByRole('heading', { name: '完整采集' })).toBeInTheDocument()
    const collected = screen.getByRole('region', { name: '模块采集信息' })
    expect(collected.querySelector('table')).toHaveTextContent('开启')
    expect(collected.querySelectorAll('li')).toHaveLength(70)
    expect(collected).toHaveTextContent('采集末尾。')
    const injected = screen.getByRole('region', { name: '系统提示词注入片段' })
    await waitFor(() => expect(injected.querySelector('strong')).toHaveTextContent('实际注入末尾'))
    expect(injected).not.toHaveTextContent('采集末尾。')
    expect(injected).not.toHaveTextContent('不应出现在数据预览中的操作说明')
    expect(screen.getByRole('button', { name: '刷新采集' }).closest('.sense-detail-tabs')).toBeInTheDocument()
    expect(screen.queryByRole('switch')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '操作文档' }))
    expect(await screen.findByRole('heading', { name: '模块使用指南' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: '完整操作文档' }).querySelectorAll('ol li')).toHaveLength(2)
    await waitFor(() => expect(screen.getByRole('region', { name: '注入层文档' }).querySelector('code')).toHaveTextContent('light_control'))
    await waitFor(() => expect(screen.getByRole('region', { name: '操作层文档' })).toHaveTextContent('操作层末尾。'))
    fireEvent.click(screen.getByRole('button', { name: '用户配置' }))
    expect(await screen.findByText('此模块没有声明配置面板')).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: '当前用户准入' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: '删除模块' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '注入总览' }))
    expect(await screen.findByRole('heading', { name: '三层注入总览' })).toBeInTheDocument()
  })

  it('拓展分层每页六个，翻页同步选中模块，切换层级回首页并保持操作文档标签', async () => {
    const data = (await loadReferenceData('/expand', 'kesepain', 's1')).expand
    const base = data.expands[0].items[0]
    data.expands.forEach((group: any) => {
      group.items = Array.from({ length: group.scope === 'global' ? 8 : group.scope === 'user' ? 7 : 0 }, (_, i) => ({
        ...base, id: `${group.scope}:module-${i}`, name: `module-${i}`, scope: group.scope,
        display_name: `${group.scope} 模块 ${i}`, path: `${group.root}/module-${i}`,
        collected_markdown: `# ${group.scope} 采集 ${i}`,
      }))
    })
    data.summary = { total: 15, global: 8, shared: 0, user: 7 }
    server.use(http.get('/api/users/kesepain/expand', () => HttpResponse.json(data)))
    renderPage('/expand')
    fireEvent.click(await screen.findByRole('button', { name: '模块详情' }))
    expect(await screen.findByRole('heading', { name: 'global 采集 0' })).toBeInTheDocument()
    expect(document.querySelectorAll('.module-card')).toHaveLength(6)
    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    expect(await screen.findByRole('heading', { name: 'global 采集 6' })).toBeInTheDocument()
    expect(document.querySelectorAll('.module-card')).toHaveLength(2)
    fireEvent.click(document.querySelectorAll('.module-card')[1])
    expect(await screen.findByRole('heading', { name: 'global 采集 7' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '操作文档' }))
    fireEvent.click(screen.getByRole('button', { name: '刷新' }))
    await waitFor(() => expect(screen.getByRole('button', { name: '操作文档' })).toHaveClass('is-active'))
    expect(document.querySelector('.editor-title')).toHaveTextContent('global 模块 7')
    expect(document.querySelector('.pager .is-active')).toHaveTextContent('2')
    fireEvent.click(screen.getByRole('button', { name: '用户拓展 7' }))
    expect(await screen.findByRole('heading', { name: 'user 采集 0' })).toBeInTheDocument()
    expect(document.querySelectorAll('.module-card')).toHaveLength(6)
    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '共享拓展 0' }))
    expect(await screen.findByText('尚无共享拓展')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '模块详情' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: '下一页' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '删除模块' })).not.toBeInTheDocument()
  })

  it.each(['global', 'shared'])('拓展 %s 白名单和刷新使用对应接口，保存后保留用户操作', async (scope) => {
    const data = (await loadReferenceData('/expand', 'kesepain', 's1')).expand
    const module = { ...data.expands[0].items[0], scope, id: `${scope}:example` }
    data.expands.forEach((group: any) => { group.items = group.scope === scope ? [module] : [] })
    data.summary = { total: 1, global: scope === 'global' ? 1 : 0, shared: scope === 'shared' ? 1 : 0, user: 0 }
    let enabled: unknown
    let refreshed = 0
    server.use(
      http.get('/api/users/kesepain/expand', () => HttpResponse.json(data)),
      http.patch(`/api/users/kesepain/expand/${scope}/example/enabled`, async ({ request }) => {
        enabled = (await request.json() as { enabled: boolean }).enabled
        module.whitelisted = enabled as boolean
        return HttpResponse.json({ updated: true })
      }),
      http.post(`/api/users/kesepain/expand/${scope}/example/refresh`, () => {
        refreshed += 1
        return HttpResponse.json({ updated: true })
      }),
    )
    renderPage('/expand')
    await screen.findByRole('heading', { name: '拓展' })
    if (scope === 'shared') fireEvent.click(screen.getByRole('button', { name: '共享拓展 1' }))
    fireEvent.click(screen.getByRole('button', { name: '模块详情' }))
    fireEvent.click(screen.getByRole('button', { name: '用户配置' }))
    fireEvent.click(screen.getByRole('switch', { name: '当前用户准入' }))
    await waitFor(() => expect(enabled).toBe(false))
    await waitFor(() => expect(screen.getByRole('switch', { name: '当前用户准入' })).toHaveAttribute('aria-checked', 'false'))
    fireEvent.click(screen.getByRole('button', { name: '刷新采集' }))
    await waitFor(() => expect(refreshed).toBe(1))
    expect(screen.getByRole('button', { name: '用户配置' })).toHaveClass('is-active')
    expect(document.querySelector('.tab.is-active')).toHaveTextContent(scope === 'shared' ? '共享拓展' : '全局拓展')
    expect(screen.queryByRole('button', { name: '删除模块' })).not.toBeInTheDocument()
  })

  it('用户拓展始终可用，只能通过带确认的用户层删除接口删除', async () => {
    const data = (await loadReferenceData('/expand', 'kesepain', 's1')).expand
    const module = { ...data.expands[0].items[0], scope: 'user', id: 'user:example', path: 'users/kesepain/expand/example' }
    data.expands.forEach((group: any) => { group.items = group.scope === 'user' ? [module] : [] })
    data.summary = { total: 1, global: 0, shared: 0, user: 1 }
    let deleted = 0
    let whitelistChanges = 0
    server.use(
      http.get('/api/users/kesepain/expand', () => HttpResponse.json(data)),
      http.patch('/api/users/kesepain/expand/user/example/enabled', () => {
        whitelistChanges += 1
        return HttpResponse.json({ enabled: false })
      }),
      http.delete('/api/users/kesepain/expand/user/example', () => {
        deleted += 1
        data.expands[2].items = []
        return HttpResponse.json({ deleted: true })
      }),
    )
    renderPage('/expand')
    fireEvent.click(await screen.findByRole('button', { name: '用户拓展 1' }))
    fireEvent.click(screen.getByRole('button', { name: '模块详情' }))
    fireEvent.click(screen.getByRole('button', { name: '用户配置' }))
    const whitelist = screen.getByRole('switch', { name: '当前用户准入' })
    expect(whitelist).toBeDisabled()
    await userEvent.click(whitelist)
    expect(whitelistChanges).toBe(0)
    fireEvent.click(screen.getByRole('button', { name: '删除模块' }))
    let dialog = screen.getByRole('dialog', { name: '删除用户拓展' })
    expect(dialog).toHaveTextContent('users/kesepain/expand/example')
    expect(deleted).toBe(0)
    fireEvent.click(within(dialog).getByRole('button', { name: '取消' }))
    expect(deleted).toBe(0)
    fireEvent.click(screen.getByRole('button', { name: '删除模块' }))
    dialog = screen.getByRole('dialog', { name: '删除用户拓展' })
    fireEvent.click(within(dialog).getByRole('button', { name: '删除' }))
    await waitFor(() => expect(deleted).toBe(1))
    expect(await screen.findByText('尚无用户拓展')).toBeInTheDocument()
    expect(screen.queryByRole('switch')).not.toBeInTheDocument()
  })

  it('拓展配置和动作使用原 panel 接口，动作后保持选中模块和用户操作标签', async () => {
    const panel = {
      schema_version: 1, title: '拓展用户设置', containers: [
        { kind: 'config', title: '灯光配置', width: 'half', height: 'h1', values: 'module/values.json',
          fields: [{ key: 'brightness', label: '亮度', type: 'number', default: 10, min: 0, max: 100 }],
          presets: [], current_values: { brightness: 10 } },
        { kind: 'action', title: '灯光操作', width: 'full', height: 'h1', controls: [
          { type: 'button', label: '应用灯光', command: 'apply', inputs: [
            { key: 'room', label: '房间', type: 'string', default: '客厅' },
          ] },
        ] },
      ],
    }
    let saved: unknown
    let action: unknown
    let refreshed = 0
    server.use(
      http.get('/api/users/kesepain/expand/global/example/panel', () => HttpResponse.json({
        user: 'kesepain', scope: 'global', module: 'example', panel, panel_error: '',
      })),
      http.put('/api/users/kesepain/expand/global/example/panel', async ({ request }) => {
        saved = await request.json()
        return HttpResponse.json({ panel, panel_error: '', saved: true })
      }),
      http.post('/api/users/kesepain/expand/global/example/panel/action', async ({ request }) => {
        action = await request.json()
        return HttpResponse.json({ panel, panel_error: '' })
      }),
      http.post('/api/users/kesepain/expand/global/example/refresh', () => {
        refreshed += 1
        return HttpResponse.json({ updated: true })
      }),
    )
    renderPage('/expand')
    fireEvent.click(await screen.findByRole('button', { name: '模块详情' }))
    fireEvent.click(screen.getByRole('button', { name: '用户配置' }))
    fireEvent.change(await screen.findByRole('spinbutton', { name: '亮度' }), { target: { value: '55' } })
    fireEvent.click(screen.getByRole('button', { name: '保存配置' }))
    await waitFor(() => expect(saved).toEqual({ values: { brightness: 55 }, clear_secrets: [] }))
    await waitFor(() => expect(refreshed).toBe(1))
    await waitFor(() => expect(screen.getByRole('button', { name: '应用灯光' })).toBeEnabled())
    fireEvent.change(screen.getByRole('textbox', { name: '房间' }), { target: { value: '卧室' } })
    fireEvent.click(screen.getByRole('button', { name: '应用灯光' }))
    await waitFor(() => expect(action).toEqual({ command: 'apply', params: { room: '卧室' } }))
    await waitFor(() => expect(refreshed).toBe(2))
    expect(screen.getByRole('button', { name: '用户配置' })).toHaveClass('is-active')
    expect(document.querySelector('.editor-title')).toHaveTextContent('智能灯光控制')
  })
})
