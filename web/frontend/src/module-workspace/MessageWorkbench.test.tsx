import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { HttpResponse, http } from 'msw'
import { createMemoryRouter, Outlet, RouterProvider } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import * as core from '../api/client'
import { server } from '../test/server'
import type { ModulePanelResponse } from '../types/api'
import { MessageWorkbench } from './MessageWorkbench'
import type { WorkspaceItem } from './types'

const clients: Array<{ router: ReturnType<typeof createMemoryRouter>; client: QueryClient }> = []
afterEach(() => {
  cleanup()
  clients.splice(0).forEach(({ router, client }) => { router.dispose(); client.clear() })
  localStorage.clear()
})

function renderWorkbench() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const router = createMemoryRouter([{ path: '/', element: <Outlet context={{ user: 'kesepain' }} />, children: [
    { path: 'messages', element: <MessageWorkbench /> }, { path: 'other', element: <p>other</p> },
  ] }], { initialEntries: ['/messages?user=kesepain&session=s1'] })
  clients.push({ client, router })
  return { ...render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>), router, client }
}

const customItem: WorkspaceItem = {
  key: 'message:onebot', module: 'onebot', kind: 'message', scope: 'user', title: 'OneBot 正向 WebSocket',
  description: '消息模块', health: 'healthy', valid: true, enabled: true, active_for_main_agent: false,
  error: '', ui_error: '', ui_revision: 'ui-1', authorization_revision: 'auth-1', permission_revision: '',
  requested_permissions: ['module.status.read'], granted_permissions: [], default_page: 'overview', actions: [],
  pages: [
    { id: 'overview', type: 'builtin', view: 'overview', label: '总览' },
    { id: 'panel', type: 'panel', label: '通用配置' },
    { id: 'console', type: 'web', label: '专属控制台', entry: 'ui/dist/index.html', permissions: ['module.status.read'] },
  ],
}
function customInventory(item = customItem) {
  server.use(http.get('/api/users/kesepain/module-workspaces/message', () => HttpResponse.json({
    user: 'kesepain', kind: 'message', items: [item], summary: { total: 1, enabled: 1, invalid: 0, injected_tokens: 0 }, registry_revision: 'r1',
  })))
}

describe('消息工作区', () => {
  it('紧凑模块列表保留六条分页与正确计数，移除统计卡和重复说明', async () => {
    const data = await core.getMessageStatus('kesepain')
    const template = data.transports[0]
    data.bindings = []
    data.transports = Array.from({ length: 7 }, (_, index) => ({
      ...template, id: `module-${index}`, name: `machine-${index}`, display_name: `外部模块 ${index}`,
      path: `message/out/module-${index}`, description: `**模块说明 ${index}**`,
    }))
    data.transports.push({ ...template, id: 'foreign', bound_user: 'other', display_name: '其他用户模块' })
    server.use(http.get('/api/users/kesepain/message/status', () => HttpResponse.json(data)))
    renderWorkbench()
    const list = screen.getByRole('complementary', { name: '已绑定消息模块' })
    await within(list).findByRole('button', { name: '外部模块 0' })
    expect(within(list).getAllByRole('button', { name: /外部模块/ })).toHaveLength(6)
    expect(within(list).getByText('已绑定消息模块').parentElement).toHaveTextContent('7')
    expect(screen.queryByText('绑定模块')).toBeNull()
    expect(screen.queryByText('在线通道')).toBeNull()
    expect(screen.queryByText('今日日志')).toBeNull()
    expect(screen.queryByText(/选择左侧模块|仅显示绑定到当前用户|仅查看当前模块/)).toBeNull()
    for (const name of ['刷新', '复制路径', '放大工作区', '检测连接', '删除模块']) {
      expect(screen.getByRole('button', { name }).textContent).toBe('')
      expect(screen.getByRole('button', { name })).toHaveAttribute('title', name)
    }
    expect(screen.getAllByRole('tab').map(tab => tab.textContent)).toEqual(['状态', '文件', '消息', '日志', '诊断'])
    expect(await screen.findByText('模块说明 0')).toBeVisible()
    expect(screen.queryByText('其他用户模块')).toBeNull()
    expect(screen.queryByRole('searchbox')).toBeNull()
    fireEvent.keyDown(screen.getByRole('tab', { name: '状态' }), { key: 'ArrowRight' })
    expect(screen.getByRole('tab', { name: '文件' })).toHaveAttribute('aria-selected', 'true')
    fireEvent.keyDown(screen.getByRole('tab', { name: '文件' }), { key: 'Home' })
    expect(screen.getByRole('tab', { name: '状态' })).toHaveAttribute('aria-selected', 'true')
    fireEvent.click(within(list).getByRole('button', { name: '下一页' }))
    expect(within(list).getAllByRole('button', { name: /外部模块/ })).toHaveLength(1)
    expect(screen.getByRole('button', { name: '外部模块 6' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('heading', { name: '外部模块 6' })).toBeVisible()
  })

  it('刷新保留选中标签、分类和正文容器，消息与日志不混入其他模块', async () => {
    const data = await core.getMessageStatus('kesepain')
    const item = data.transports[0]
    item.logs.push({ ...item.logs[0], id: 'error', kind: 'system', content: '实际系统故障', success: false })
    server.use(http.get('/api/users/kesepain/message/status', () => HttpResponse.json(data)))
    renderWorkbench()
    fireEvent.click(await screen.findByRole('tab', { name: '消息' }))
    expect(screen.queryByText('实际系统故障')).toBeNull()
    const panel = screen.getByRole('tabpanel')
    fireEvent.click(within(panel).getByRole('button', { name: '文件' }))
    expect(within(panel).getByText('meeting_notes.docx')).toBeVisible()
    expect(within(panel).queryByText('请查看今日任务')).toBeNull()
    item.logs[1].content = '刷新后的附件记录'
    fireEvent.click(screen.getByRole('button', { name: '刷新' }))
    expect(await screen.findByText('刷新后的附件记录')).toBeVisible()
    expect(screen.getByRole('tabpanel')).toBe(panel)
    expect(within(panel).getByRole('button', { name: '文件' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('tab', { name: '消息' })).toHaveAttribute('aria-selected', 'true')
    fireEvent.click(screen.getByRole('tab', { name: '日志' }))
    fireEvent.click(screen.getByRole('button', { name: '系统' }))
    expect(screen.getByText('实际系统故障')).toBeVisible()
    expect(screen.getByText('失败')).toBeVisible()
  })

  it('文件标签展示真实临时文件清单并使用绑定模块的下载接口', async () => {
    const data = await core.getMessageStatus('kesepain')
    data.transports[0].temporary_files = [{ name: 'report.txt', relative_path: 'nested/report.txt', size: 42, updated_at: 1 }]
    server.use(http.get('/api/users/kesepain/message/status', () => HttpResponse.json(data)))
    renderWorkbench()
    fireEvent.click(await screen.findByRole('tab', { name: '文件' }))
    expect(screen.getByText('report.txt')).toBeVisible()
    expect(screen.getByRole('link', { name: '下载' })).toHaveAttribute('href',
      '/api/users/kesepain/message/modules/onebot/files/download?path=nested%2Freport.txt')
    expect(screen.queryByText('meeting_notes.docx')).toBeNull()
  })

  it('关联收发默认折叠，刷新保留展开状态，筛选只展示对应方向和类型的记录', async () => {
    const data = await core.getMessageStatus('kesepain')
    const item = data.transports[0]
    const template = { ...item.logs[0], exchange_id: 'exchange-1' }
    item.logs = [
      { ...template, id: 'send-file', direction: 'send', kind: 'file', content: '结果.txt', file_path: 'users/kesepain/download/结果.txt' },
      { ...template, id: 'send-text', direction: 'send', content: '回复正文\n第二行' },
      { ...template, id: 'receive-file', kind: 'file', content: '附件.txt', file_path: 'message/out/onebot/files/附件.txt' },
      { ...template, id: 'receive-text', content: '问题正文\n补充' },
    ]
    server.use(http.get('/api/users/kesepain/message/status', () => HttpResponse.json(data)))
    renderWorkbench()
    fireEvent.click(await screen.findByRole('tab', { name: '消息' }))
    const panel = screen.getByRole('tabpanel')
    const summary = within(panel).getByRole('button', { name: '展开消息' })
    expect(summary).toHaveAttribute('aria-expanded', 'false')
    expect(summary).toHaveTextContent('问题正文 补充 · 附件.txt')
    expect(summary).toHaveTextContent('回复正文 第二行 · 结果.txt')
    expect(panel.querySelector('pre')).toBeNull()
    fireEvent.click(summary)
    const incoming = within(panel).getByRole('region', { name: '接收' })
    const outgoing = within(panel).getByRole('region', { name: '发送' })
    expect(within(incoming).getByText('问题正文 补充')).toBeVisible()
    expect(within(incoming).getByText('附件.txt')).toBeVisible()
    expect(within(outgoing).getByText('回复正文 第二行')).toBeVisible()
    expect(within(outgoing).getByText('结果.txt')).toBeVisible()
    expect(within(incoming).queryByText('结果.txt')).toBeNull()
    item.logs[1].content = '刷新后的完整回复'
    fireEvent.click(screen.getByRole('button', { name: '刷新' }))
    expect(await within(outgoing).findByText('刷新后的完整回复')).toBeVisible()
    expect(within(panel).getByRole('region', { name: '发送' })).toBe(outgoing)
    expect(summary).toHaveAttribute('aria-expanded', 'true')
    fireEvent.click(within(panel).getByRole('button', { name: '接收' }))
    expect(within(summary).getByTitle('接收')).toBeVisible()
    expect(within(summary).queryByTitle('发送')).toBeNull()
    expect(within(panel).getByRole('region', { name: '接收' })).toBe(incoming)
    expect(within(panel).queryByRole('region', { name: '发送' })).toBeNull()
    expect(within(incoming).getByText('问题正文 补充')).toBeVisible()
    expect(within(incoming).getByText('附件.txt')).toBeVisible()
    expect(panel).not.toHaveTextContent('刷新后的完整回复')
    expect(panel).not.toHaveTextContent('结果.txt')
    fireEvent.click(within(panel).getByRole('button', { name: '发送' }))
    expect(within(summary).getByTitle('发送')).toBeVisible()
    expect(within(summary).queryByTitle('接收')).toBeNull()
    expect(within(panel).queryByRole('region', { name: '接收' })).toBeNull()
    const sendOnly = within(panel).getByRole('region', { name: '发送' })
    expect(within(sendOnly).getByText('刷新后的完整回复')).toBeVisible()
    expect(within(sendOnly).getByText('结果.txt')).toBeVisible()
    expect(panel).not.toHaveTextContent('问题正文')
    expect(panel).not.toHaveTextContent('附件.txt')
    fireEvent.click(within(panel).getByRole('button', { name: '文件' }))
    expect(summary).toHaveAttribute('aria-expanded', 'true')
    expect(within(summary).getByTitle('接收')).toBeVisible()
    expect(within(summary).getByTitle('发送')).toBeVisible()
    expect(within(panel).getByRole('region', { name: '发送' })).toBe(sendOnly)
    expect(within(within(panel).getByRole('region', { name: '接收' })).getByText('附件.txt')).toBeVisible()
    expect(within(sendOnly).getByText('结果.txt')).toBeVisible()
    expect(panel).not.toHaveTextContent('问题正文')
    expect(panel).not.toHaveTextContent('刷新后的完整回复')
    expect(Array.from(panel.querySelectorAll('pre'), entry => entry.textContent)).toEqual(['附件.txt', '结果.txt'])
    item.logs = item.logs.filter(entry => entry.id !== 'send-file')
    fireEvent.click(screen.getByRole('button', { name: '刷新' }))
    await waitFor(() => expect(within(sendOnly).queryByRole('article')).toBeNull())
    expect(sendOnly.textContent).toBe('发送')
    expect(panel).not.toHaveTextContent('刷新后的完整回复')
    expect(within(within(panel).getByRole('region', { name: '接收' })).getByText('附件.txt')).toBeVisible()
    fireEvent.click(within(panel).getByRole('button', { name: '全部' }))
    expect(summary).toHaveAttribute('aria-expanded', 'true')
    expect(panel.querySelectorAll('pre')).toHaveLength(3)
    expect(within(sendOnly).getByText('刷新后的完整回复')).toBeVisible()
    fireEvent.click(within(panel).getByRole('button', { name: '收起消息' }))
    expect(within(panel).queryByRole('region', { name: '发送' })).toBeNull()
  })

  it('单向与无关联记录另一侧留空，不跨会话配对，分页不拆散同一收发卡片', async () => {
    const data = await core.getMessageStatus('kesepain')
    const item = data.transports[0]
    const template = item.logs[0]
    item.logs = [
      { ...template, id: 'only-receive', exchange_id: 'same-id', chat_id: 'chat-a', content: '只有接收' },
      { ...template, id: 'only-send', exchange_id: 'same-id', chat_id: 'chat-b', direction: 'send', content: '只有发送' },
      { ...template, id: 'legacy-receive', content: '未关联接收' },
      { ...template, id: 'legacy-send', direction: 'send', content: '未关联发送' },
      ...Array.from({ length: 11 }, (_, index) => [
        { ...template, id: `send-${index}`, exchange_id: `pair-${index}`, direction: 'send' as const, content: `回复${index}` },
        { ...template, id: `receive-${index}`, exchange_id: `pair-${index}`, content: `问题${index}` },
      ]).flat(),
    ]
    server.use(http.get('/api/users/kesepain/message/status', () => HttpResponse.json(data)))
    renderWorkbench()
    fireEvent.click(await screen.findByRole('tab', { name: '消息' }))
    const panel = screen.getByRole('tabpanel')
    expect(within(panel).getAllByRole('button', { name: '展开消息' })).toHaveLength(10)
    for (const [content, blankSide] of [['只有接收', '发送'], ['只有发送', '接收'], ['未关联接收', '发送'], ['未关联发送', '接收']]) {
      const card = within(panel).getByText(content).closest('article')!
      fireEvent.click(within(card).getByRole('button', { name: '展开消息' }))
      const blank = within(card).getByRole('region', { name: blankSide })
      expect(blank.textContent).toBe(blankSide)
      expect(within(blank).queryByRole('article')).toBeNull()
    }
    fireEvent.click(within(panel).getByRole('button', { name: '下一页' }))
    expect(within(panel).getAllByRole('button', { name: '展开消息' })).toHaveLength(5)
    const card = within(panel).getByText('问题6').closest('article')!
    expect(within(card).getByText('回复6')).toBeVisible()
    fireEvent.click(within(card).getByRole('button', { name: '展开消息' }))
    expect(within(within(card).getByRole('region', { name: '接收' })).getByText('问题6')).toBeVisible()
    expect(within(within(card).getByRole('region', { name: '发送' })).getByText('回复6')).toBeVisible()
  })

  it('自定义配置复用面板，刷新不丢草稿，切换功能前确认，保存携带版本', async () => {
    customInventory()
    let savedBody: any
    let value = 'before'
    let revision = 'config-1'
    const panel = (): ModulePanelResponse => ({
      user: 'kesepain', scope: 'user', module: 'onebot', config_revision: revision, panel_error: '',
      panel: { schema_version: 1, title: '消息配置', containers: [{
        kind: 'config', title: '连接配置', width: 'full', height: 'h1', values: 'module/values.json', presets: [],
        current_values: { host: value }, fields: [{ key: 'host', label: '主机', type: 'string' }],
      }] },
    })
    server.use(
      http.get('/api/users/kesepain/module-workspaces/message/user/onebot/panel', () => HttpResponse.json(panel())),
      http.put('/api/users/kesepain/module-workspaces/message/user/onebot/panel', async ({ request }) => {
        savedBody = await request.json()
        value = savedBody.values.host; revision = 'config-2'
        return HttpResponse.json(panel())
      }),
    )
    const view = renderWorkbench()
    fireEvent.click(await screen.findByRole('tab', { name: '通用配置' }))
    const field = await screen.findByRole('textbox', { name: '主机' })
    fireEvent.change(field, { target: { value: 'after' } })
    await act(async () => { await view.client.refetchQueries({ queryKey: ['message-workspace-status'] }) })
    expect(screen.getByRole('textbox', { name: '主机' })).toBe(field)
    expect(field).toHaveValue('after')
    fireEvent.click(screen.getByRole('tab', { name: '通用配置' }))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    fireEvent.click(screen.getByRole('tab', { name: '状态' }))
    await screen.findByRole('alertdialog')
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(screen.getByRole('tab', { name: '通用配置' })).toHaveAttribute('aria-selected', 'true')
    fireEvent.click(screen.getByRole('button', { name: '保存配置' }))
    await waitFor(() => expect(savedBody).toEqual({ values: { host: 'after' }, clear_secrets: [], config_revision: 'config-1' }))
    await waitFor(() => expect(screen.getByRole('button', { name: '保存配置' })).not.toBeDisabled())
    fireEvent.click(screen.getByRole('tab', { name: '状态' }))
    await waitFor(() => expect(screen.getByRole('tab', { name: '状态' })).toHaveAttribute('aria-selected', 'true'))
    expect(screen.queryByRole('alertdialog')).toBeNull()
  })

  it('专属网页先授权，放大与刷新保留同一个沙箱 iframe', async () => {
    const item = structuredClone(customItem)
    customInventory(item)
    let instances = 0
    server.use(
      http.put('/api/users/kesepain/module-workspaces/message/user/onebot/permissions', async ({ request }) => {
        const body = await request.json() as { permissions: string[] }
        item.granted_permissions = body.permissions; item.permission_revision = 'granted'
        return HttpResponse.json({ granted_permissions: body.permissions })
      }),
      http.post('/api/users/kesepain/module-workspaces/message/user/onebot/instances', () => {
        instances++
        return HttpResponse.json({ nonce: 'message-instance', asset_url: '/module-ui/message-instance/index.html', ui_revision: item.ui_revision, permissions: item.granted_permissions })
      }),
      http.delete('/api/module-ui/instances/message-instance', () => HttpResponse.json({ closed: true })),
    )
    const view = renderWorkbench()
    fireEvent.click(await screen.findByRole('tab', { name: '专属控制台' }))
    expect(await screen.findByText('插件页面需要授权')).toBeVisible()
    expect(instances).toBe(0)
    fireEvent.click(screen.getByRole('button', { name: '审查并授权' }))
    fireEvent.click(await screen.findByRole('button', { name: '确认授权' }))
    const iframe = await screen.findByTitle('OneBot 正向 WebSocket · 专属控制台')
    expect(iframe).toHaveAttribute('sandbox', 'allow-scripts')
    fireEvent.click(screen.getByRole('button', { name: '放大工作区' }))
    expect(screen.getByTitle('OneBot 正向 WebSocket · 专属控制台')).toBe(iframe)
    await act(async () => { await view.client.refetchQueries({ queryKey: ['module-workspaces', 'kesepain', 'message'] }) })
    expect(screen.getByTitle('OneBot 正向 WebSocket · 专属控制台')).toBe(iframe)
    expect(instances).toBe(1)
    fireEvent.click(screen.getByRole('button', { name: '恢复布局' }))
    expect(screen.getByTitle('OneBot 正向 WebSocket · 专属控制台')).toBe(iframe)
  })

  it('未绑定时保留详情工作区，不展示其他用户模块或删除按钮', async () => {
    const data = await core.getMessageStatus('kesepain')
    data.transports[0].bound_user = 'other'
    server.use(http.get('/api/users/kesepain/message/status', () => HttpResponse.json(data)))
    renderWorkbench()
    expect(await screen.findByText('当前用户未绑定消息模块')).toBeVisible()
    expect(screen.getByRole('region', { name: '消息模块详情' })).toBeVisible()
    expect(screen.getByText('暂无可查看的消息模块')).toBeVisible()
    expect(screen.queryByRole('button', { name: '删除模块' })).toBeNull()
    expect(screen.queryByText('OneBot 正向 WebSocket')).toBeNull()
  })
})
