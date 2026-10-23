import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { describe, expect, it, vi } from 'vitest'
import { server } from '../test/server'
import { ReferencePage, loadReferenceData } from './ReferencePage'
import { FilesPage } from './FilesPage'
import { openCronDialog } from './tasks.js'
import type { ShellOutletContext } from '../components/appShellTypes'

function renderPage(path: string, files = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const context = { user: 'kesepain', sessionId: 's1', chatRunning: false } as ShellOutletContext
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}>
    <Routes><Route element={<Outlet context={context} />}><Route path="*" element={files ? <FilesPage /> : <ReferencePage />} /></Route></Routes>
  </MemoryRouter></QueryClientProvider>)
  return client
}

describe('样例替换后的生产路由', () => {
  it('运行诊断同屏预览提示词与组件状态，小时图显示刻度和极值', async () => {
    renderPage('/status')
    await screen.findByText('系统提示词与拼接状态')
    expect(await screen.findByRole('heading', { name: '用户人格' })).toBeInTheDocument()
    expect(document.querySelectorAll('.runtime-prompt-components tbody tr')).toHaveLength(2)
    expect(screen.queryByRole('button', { name: '拼接组件状态' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '今日 Token' }))
    expect(document.querySelector('.runtime-token-chart')).toHaveTextContent('最高 52 · 最低 8 Tokens')
    expect(document.querySelectorAll('.runtime-token-chart circle')).toHaveLength(10)
    expect(document.querySelector('.runtime-token-chart')).toHaveTextContent('09')
    fireEvent.click(screen.getByRole('button', { name: '外部组件与路由' }))
    expect(document.querySelectorAll('.runtime-component-card')).toHaveLength(4)
    expect(screen.getByText('OneBot 正向 WebSocket')).toBeInTheDocument()
  })

  it('API 预览按真实配置显示流式、超时、模型路由，未知能力不冒充不支持', async () => {
    const data = await loadReferenceData('/status', 'kesepain', 's1')
    Object.assign(data.runtime.api, { stream: false, timeout: 90, context_tokens: 240000,
      input_modalities: ['text', 'image'], agent_models: { default: 'agent-model' },
      multimodal_models: { vision: 'vision-model' }, multimodal_routing: { vision: 'dedicated' } })
    server.use(http.get('/api/users/kesepain/runtime/status', () => HttpResponse.json(data.runtime)))
    renderPage('/status')
    fireEvent.click(await screen.findByRole('button', { name: '用户 API 配置' }))
    expect(screen.getByText('已关闭')).toBeInTheDocument()
    expect(screen.getByText('90 秒')).toBeInTheDocument()
    expect(screen.getByText('240,000 tokens')).toBeInTheDocument()
    expect(screen.getByText('agent-model')).toBeInTheDocument()
    expect(screen.getByText('vision-model')).toBeInTheDocument()
    expect(screen.getByText('dedicated')).toBeInTheDocument()
    expect(screen.getByText(/Chat 协议未提供模型能力声明/)).toBeInTheDocument()
    expect(screen.queryByText('不支持')).not.toBeInTheDocument()
  })

  it('终端显示实际输出，消息日志保留完整输入输出正文', async () => {
    let terminalReads = 0
    const writeText = vi.fn().mockResolvedValue(undefined)
    const clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, 'clipboard')
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    const restoreClipboard = () => {
      if (clipboardDescriptor) Object.defineProperty(navigator, 'clipboard', clipboardDescriptor)
      else Reflect.deleteProperty(navigator, 'clipboard')
    }
    server.use(http.get('/api/users/kesepain/runtime/logs', ({ request }) => {
      const category = new URL(request.url).searchParams.get('category') || 'all'
      if (category === 'terminal') terminalReads += 1
      return HttpResponse.json({
        user: 'kesepain', category,
        entries: category === 'terminal' ? [
          { id: 'console:1', category, title: '实际标准输出', detail: '启动终端 · 标准输出', stream: 'stdout', occurred_at: '', source: 'memory' },
          { id: 'console:2', category, title: '实际错误输出', detail: '启动终端 · 标准错误', stream: 'stderr', occurred_at: '', source: 'memory' },
          ...(terminalReads > 1 ? [{ id: 'console:3', category, title: '新增终端输出', stream: 'stdout', occurred_at: '', source: 'memory' }] : []),
        ] : category === 'message' ? [
          { id: 'message-exchange:1', category, title: '测试消息', detail: '输入 / 输出', direction: 'exchange',
            inputs: [{ id: 'message:1', content: '正文第一行\n正文第二行', kind: 'text', status: 'success' }],
            outputs: [{ id: 'message:2', content: '完整回复正文', kind: 'text', status: 'success' }],
            occurred_at: '', source: 'sqlite' },
        ] : [],
        terminal: { loaded_items: 2, window_size: 300, stdout_items: 1, stderr_items: 1, retention_seconds: 3600 },
        pagination: { page: 1, total_pages: 1 }, source_errors: [],
      })
    }))
    renderPage('/status')
    fireEvent.click(await screen.findByRole('button', { name: '运行日志' }))
    fireEvent.click(screen.getByRole('button', { name: '终端日志' }))
    expect(await screen.findByText('实际标准输出')).toBeInTheDocument()
    expect(screen.getByText('实际错误输出')).toBeInTheDocument()
    expect(screen.queryByText('启动终端 · 标准输出')).not.toBeInTheDocument()
    expect(screen.getByText('OUT')).toBeInTheDocument()
    expect(screen.getByText('ERR')).toBeInTheDocument()
    const terminal = document.querySelector('.runtime-terminal')
    const firstRow = terminal?.firstElementChild
    fireEvent.click(screen.getAllByRole('button', { name: '刷新' }).at(-1)!)
    await screen.findByText('新增终端输出')
    expect(document.querySelector('.runtime-terminal')).toBe(terminal)
    expect(terminal?.firstElementChild).toBe(firstRow)
    fireEvent.click(screen.getByRole('button', { name: '消息日志' }))
    expect(await screen.findByText('完整回复正文')).toBeInTheDocument()
    expect(screen.getByText('正文输入')).toBeInTheDocument()
    expect(screen.getByText('正文输出')).toBeInTheDocument()
    expect(document.querySelector('.runtime-message-body pre')?.textContent).toBe('正文第一行\n正文第二行')
    const cards = document.querySelectorAll('.runtime-log-card')
    expect(cards).toHaveLength(1)
    expect(within(cards[0] as HTMLElement).getByRole('region', { name: '正文输入' })).toHaveTextContent('正文第二行')
    expect(within(cards[0] as HTMLElement).getByRole('region', { name: '正文输出' })).toHaveTextContent('完整回复正文')
    fireEvent.click(within(cards[0] as HTMLElement).getByRole('button', { name: '复制日志' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(
      '测试消息\n输入 / 输出\n正文输入\n正文第一行\n正文第二行\n正文输出\n完整回复正文',
    ))
    restoreClipboard()
  })

  it('同卡显示缺失输入或输出、附件与发送失败，不把单边日志冒充完整回复', async () => {
    server.use(http.get('/api/users/kesepain/runtime/logs', ({ request }) => {
      const category = new URL(request.url).searchParams.get('category') || 'all'
      return HttpResponse.json({
        user: 'kesepain', category, entries: [
          { id: 'message-exchange:input', category: 'message', title: '只有输入', direction: 'exchange', source: 'sqlite',
            inputs: [{ content: '附件名.txt', kind: 'file', status: 'success' }], outputs: [] },
          { id: 'message-exchange:output', category: 'message', title: '只有输出', direction: 'exchange', source: 'sqlite', status: 'failed',
            inputs: [], outputs: [{ content: '发送失败的正文', kind: 'system', status: 'failed' }] },
        ],
        pagination: { page: 1, total_pages: 1 }, source_errors: [],
      })
    }))
    renderPage('/status')
    fireEvent.click(await screen.findByRole('button', { name: '运行日志' }))
    fireEvent.click(screen.getByRole('button', { name: '消息日志' }))
    await screen.findByText('发送失败的正文')
    const cards = document.querySelectorAll('.runtime-log-card')
    expect(cards).toHaveLength(2)
    expect(within(cards[0] as HTMLElement).getByText('附件')).toBeInTheDocument()
    expect(within(cards[0] as HTMLElement).getByText('未记录输出')).toBeInTheDocument()
    expect(within(cards[1] as HTMLElement).getByText('未记录输入')).toBeInTheDocument()
    expect(within(cards[1] as HTMLElement).getByText('失败')).toBeInTheDocument()
  })

  it('外部消息使用子智能体双栏和六条分页，无搜索框，选中模块及日志筛选在刷新后保留', async () => {
    const data = await loadReferenceData('/messages', 'kesepain', 's1')
    const template = data.messages.transports[0]
    data.messages.transports = Array.from({ length: 7 }, (_, index) => ({
      ...template, id: `module-${index}`, name: `module-${index}`, display_name: `外部模块 ${index}`,
      path: `message/out/module-${index}`, description: `**模块说明 ${index}**`,
    }))
    data.messages.transports.push({ ...template, id: 'foreign', bound_user: 'other', display_name: '其他用户模块' })
    server.use(http.get('/api/users/kesepain/message/status', () => HttpResponse.json(data.messages)))
    renderPage('/messages')
    await screen.findByRole('button', { name: '外部模块 0' })
    expect(document.querySelector('.split.messages-split')).not.toBeNull()
    expect(document.querySelectorAll('.message-module-item')).toHaveLength(6)
    expect(document.querySelector('.message-detail-column .doc-content strong')).toHaveTextContent('模块说明 0')
    expect(screen.queryByRole('searchbox')).not.toBeInTheDocument()
    expect(document.querySelector('.messages-split input')).toBeNull()
    expect(screen.queryByText('其他用户模块')).not.toBeInTheDocument()
    fireEvent.click(within(document.querySelector('.message-list-column') as HTMLElement).getByRole('button', { name: '下一页' }))
    expect(document.querySelectorAll('.message-module-item')).toHaveLength(1)
    expect(screen.getByRole('button', { name: '外部模块 6' })).toHaveAttribute('aria-pressed', 'true')
    expect(document.querySelector('.message-detail-column .editor-title')).toHaveTextContent('外部模块 6')
    fireEvent.click(within(document.querySelector('.message-detail-column') as HTMLElement).getByRole('button', { name: '文件' }))
    expect(document.querySelectorAll('.message-detail-column .log-line')).toHaveLength(1)
    expect(document.querySelector('.message-detail-column .log-line')).toHaveTextContent('meeting_notes.docx')
    data.messages.transports[6].description = '**刷新后的模块说明**'
    fireEvent.click(screen.getByRole('button', { name: '刷新' }))
    await screen.findByText('刷新后的模块说明')
    expect(screen.getByRole('button', { name: '外部模块 6' })).toHaveAttribute('aria-pressed', 'true')
    expect(document.querySelectorAll('.message-detail-column .log-line')).toHaveLength(1)
    expect(document.querySelector('.message-detail-column .editor-foot')).toHaveTextContent('检测连接')
  })

  it('外部消息未绑定时保留右侧详情容器，并不展示其他用户的模块', async () => {
    const data = await loadReferenceData('/messages', 'kesepain', 's1')
    data.messages.transports[0].bound_user = 'other'
    server.use(http.get('/api/users/kesepain/message/status', () => HttpResponse.json(data.messages)))
    renderPage('/messages')
    await screen.findByText('当前用户未绑定消息模块')
    expect(document.querySelector('.message-detail-column .editor-panel')).not.toBeNull()
    expect(screen.getByText('暂无可查看的消息模块')).toBeInTheDocument()
    expect(document.querySelectorAll('.message-module-item')).toHaveLength(0)
    expect(screen.queryByRole('button', { name: '删除模块' })).not.toBeInTheDocument()
    expect(screen.queryByText('OneBot 正向 WebSocket')).not.toBeInTheDocument()
  })

  it('文件详情在右侧，移除区域说明；换区域清除预览，下载保持原文件接口', async () => {
    server.use(http.get('/api/users/kesepain/files/file_upload', () => HttpResponse.json({
      user: 'kesepain', scope: 'file_upload', root: 'users/kesepain/file_upload', path: '', search: '',
      summary: { total_files: 1, total_dirs: 0, total_size: 64 },
      pagination: { page: 1, page_size: 6, total_items: 1, total_pages: 1, has_previous: false, has_next: false },
      entries: [{ name: 'sample.png', relative_path: 'sample.png', parent_path: '', extension: '.png',
        type: 'file', size: 64, child_count: 0, updated_at: 1 }],
    })))
    renderPage('/files', true)
    await screen.findByRole('checkbox', { name: '选择 sample.png' })
    expect(document.querySelector('.reference-file-note')).toBeNull()
    expect(document.querySelector('.files-split > .file-list-column')).not.toBeNull()
    expect(document.querySelector('.files-split > .file-detail-column')).not.toBeNull()
    fireEvent.click(document.querySelector('.reference-file-main')!)
    expect(document.querySelector('.file-detail-column img')).toHaveAttribute('src',
      '/api/users/kesepain/files/file_upload/preview?path=sample.png')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: '下载' })).toHaveAttribute('href',
      '/api/users/kesepain/files/file_upload/download?path=sample.png')
    fireEvent.click(screen.getByRole('button', { name: '全局临时区' }))
    expect(screen.queryByRole('button', { name: '放大预览' })).not.toBeInTheDocument()
    expect(screen.getByText('选择一个文件')).toBeInTheDocument()
  })
  it.each([
    ['/tasks', '任务中枢'], ['/knowledge', '知识库'], ['/memory', '记忆'],
    ['/agents', '子智能体'], ['/skills', '工具与技能'], ['/sense', '感知'],
    ['/expand', '拓展'], ['/messages', '外部消息'], ['/status', '运行诊断'], ['/runtime', '运行模块'],
  ])('%s 使用真实响应渲染样例页面', async (path, title) => {
    renderPage(path)
    expect(await screen.findByRole('heading', { name: title })).toBeInTheDocument()
    expect(screen.queryByText(/页面渲染失败/)).not.toBeInTheDocument()
    expect(screen.queryByText(/\[object Object\]/)).not.toBeInTheDocument()
  })

  it('子智能体六条分页，筛选回到首页，详情渲染 Markdown 并移除文件清单', async () => {
    server.use(http.get('/api/users/kesepain/agents', () => HttpResponse.json({
      user: 'kesepain', summary: { total: 8, enabled: 8, global: 7, user: 1 },
      agents: Array.from({ length: 8 }, (_, i) => ({
        name: `agent-${i}`, version: '1.0', enabled: true, source: i === 7 ? 'user' : 'global',
        description: '**格式化定义**', trigger: '## 触发条件\n\n- 明确目标',
        rules: '# 行为规则\n\n1. 保留事实\n2. 验证结果\n\n`安全执行`',
        executor: 'executor.py:execute', execution: 'sync', model_profile: 'cheap', exposure: 'tool',
        root: `agents/agent-${i}`, files: [{ relative_path: 'should-not-display.md', size: 128, updated_at: 1 }],
      })),
    })))
    renderPage('/agents')
    await screen.findByRole('heading', { name: '行为规则' })
    expect(document.querySelectorAll('.agent-item')).toHaveLength(6)
    expect(document.querySelector('.agents-split .doc-content strong')).toHaveTextContent('格式化定义')
    expect(screen.getByRole('heading', { name: '触发条件' })).toBeInTheDocument()
    expect(document.querySelector('.agents-split .doc-content ol')).toHaveTextContent('验证结果')
    expect(screen.queryByText(/文件清单/)).not.toBeInTheDocument()
    expect(screen.queryByText('should-not-display.md')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    expect(document.querySelectorAll('.agent-item')).toHaveLength(2)
    expect(document.querySelector('.editor-title')).toHaveTextContent('agent-6')
    const search = screen.getByRole('searchbox')
    search.focus()
    fireEvent.input(search, { target: { value: 'agent-0' } })
    expect(search).toHaveFocus()
    expect(search).toHaveValue('agent-0')
    expect(document.querySelectorAll('.agent-item')).toHaveLength(1)
    expect(document.querySelector('.editor-title')).toHaveTextContent('agent-0')
    expect(screen.queryByRole('button', { name: '下一页' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '清除搜索' }))
    expect(document.querySelectorAll('.agent-item')).toHaveLength(6)
    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    fireEvent.click(screen.getByRole('button', { name: '全局层 7' }))
    expect(document.querySelectorAll('.agent-item')).toHaveLength(6)
    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '用户层 1' }))
    expect(document.querySelectorAll('.agent-item')).toHaveLength(1)
    expect(document.querySelector('.editor-title')).toHaveTextContent('agent-7')
    fireEvent.input(search, { target: { value: 'missing' } })
    expect(document.querySelectorAll('.agent-item')).toHaveLength(0)
    expect(screen.getByText('暂无可查看的子智能体')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '删除用户包' })).not.toBeInTheDocument()
  })

  it('工具与技能六条分页，右侧详情随页同步，分类和路径搜索重置分页', async () => {
    server.use(http.get('/api/users/kesepain/skills', () => HttpResponse.json({
      user: 'kesepain', summary: { registered: 7, enabled: 7, user: 1, shared: 0, core: 7 },
      catalog_summary: { total: 8, enabled: 8, builtin: 7, shared: 0, agent_generated: 0, user_created: 1 },
      prompt_summary: { registered: 1, active: 1, user: 1, shared: 0 },
      tools: [], prompt_skills: [],
      items: Array.from({ length: 8 }, (_, i) => ({
        id: `skill-${i}`, name: `skill-${i}`, title: `技能-${i}`,
        description: '**插件说明**', category: i === 7 ? 'user_created' : 'builtin',
        version: '1', enabled: true, editable: i === 7, toggleable: false, downloadable: false,
        path: i === 7 ? 'users/kesepain/user_skills/manual' : `plugins/skill-${i}`,
      })),
    })))
    renderPage('/skills')
    await screen.findByRole('heading', { name: '工具与技能' })
    expect(document.querySelector('.skills-split')).toHaveClass('split')
    expect(document.querySelectorAll('.skill-item')).toHaveLength(6)
    expect(document.querySelector('.editor-title')).toHaveTextContent('技能-0')
    expect(await screen.findByRole('heading', { name: 'skill-0' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    expect(document.querySelectorAll('.skill-item')).toHaveLength(2)
    expect(document.querySelector('.editor-title')).toHaveTextContent('技能-6')
    fireEvent.click(screen.getByRole('button', { name: '基础插件 7' }))
    expect(document.querySelectorAll('.skill-item')).toHaveLength(6)
    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    const search = screen.getByRole('searchbox')
    search.focus()
    fireEvent.input(search, { target: { value: 'plugins/skill-0' } })
    expect(search).toHaveFocus()
    expect(search).toHaveValue('plugins/skill-0')
    expect(document.querySelectorAll('.skill-item')).toHaveLength(1)
    expect(document.querySelector('.editor-title')).toHaveTextContent('技能-0')
    expect(screen.queryByRole('button', { name: '下一页' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '清除搜索' }))
    fireEvent.click(screen.getByRole('button', { name: '用户自建 1' }))
    expect(document.querySelectorAll('.skill-item')).toHaveLength(1)
    expect(await screen.findByRole('heading', { name: 'skill-7' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    fireEvent.input(document.querySelector('textarea')!, { target: { value: '# 尚未保存的修改' } })
    fireEvent.click(screen.getByRole('button', { name: '预览' }))
    expect(await screen.findByRole('heading', { name: '尚未保存的修改' })).toBeInTheDocument()
    fireEvent.input(search, { target: { value: 'missing' } })
    expect(document.querySelectorAll('.skill-item')).toHaveLength(0)
    expect(screen.getByText('暂无可查看的技能')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '保存' })).not.toBeInTheDocument()
  })

  it.each(['builtin', 'shared', 'agent_generated', 'user_created'])('%s 预览通过文档接口完整渲染 SKILL.md，而非目录摘要', async category => {
    const name = category === 'agent_generated' ? 'agent_create/full' : category === 'user_created' ? 'user_create/full' : 'full'
    const editable = ['agent_generated', 'user_created'].includes(category)
    let requestedName = ''
    const content = '# 完整技能文档\n\n**正文说明**\n\n'
      + Array.from({ length: 80 }, (_, i) => `- 文档条目 ${i}`).join('\n')
      + '\n\n| 字段 | 值 |\n| --- | --- |\n| 全部内容 | 保留 |\n\n```json\n{"tool": "complete"}\n```\n\n## 文档最后一节\n\n末尾也需要完整显示。'
    server.use(
      http.get('/api/users/kesepain/skills', () => HttpResponse.json({
        user: 'kesepain', summary: { registered: 1, enabled: 1, user: 0, shared: 0, core: 1 },
        catalog_summary: { total: 1, enabled: 1, [category]: 1 },
        prompt_summary: { registered: 1, active: 1, user: 1, shared: 0 },
        tools: [], prompt_skills: [],
        items: [{ id: `${category}:${name}`, category, name, title: '测试技能', path: `skills/${name}`,
          description: '目录只提供摘要', enabled: true, editable, toggleable: false, downloadable: false }],
      })),
      http.get('/api/users/kesepain/skills/:category/document', ({ params, request }) => {
        expect(params.category).toBe(category)
        requestedName = new URL(request.url).searchParams.get('name') || ''
        return HttpResponse.json({ user: 'kesepain', category, name, path: `skills/${name}/SKILL.md`, content, editable })
      }),
    )
    renderPage('/skills')
    await screen.findByRole('heading', { name: '文档最后一节' })
    expect(requestedName).toBe(name)
    const preview = screen.getByRole('region', { name: 'SKILL.md 预览' })
    expect(preview).toHaveClass('doc-content', 'skill-document-preview')
    expect(preview.querySelectorAll('li')).toHaveLength(80)
    expect(preview.querySelector('table')).toHaveTextContent('全部内容')
    expect(preview.querySelector('pre code')).toHaveTextContent('{"tool": "complete"}')
    expect(preview.querySelector('strong')).toHaveTextContent('正文说明')
    expect(preview).toHaveTextContent('末尾也需要完整显示。')
    expect(preview).not.toHaveTextContent('目录只提供摘要')
    expect(screen.queryByText('管理权限')).not.toBeInTheDocument()
    if (editable) expect(screen.getByRole('button', { name: '编辑' })).toBeEnabled()
    else expect(screen.getByRole('button', { name: '编辑' })).toBeDisabled()
  })

  it('技能文档读取失败保留错误和重新读取入口，不能保存空正文', async () => {
    let fail = true
    server.use(http.get('/api/users/kesepain/skills/user_created/document', () => fail
      ? HttpResponse.json({ error: { message: '文档暂时无法读取', code: 'unavailable' } }, { status: 503 })
      : HttpResponse.json({ content: '# 恢复后的完整文档\n\n正文结尾。', editable: true })))
    renderPage('/skills')
    fireEvent.click(await screen.findByText('用户自建技能'))
    expect(await screen.findByRole('alert')).toHaveTextContent('文档暂时无法读取')
    expect(screen.getByRole('button', { name: '编辑' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled()
    fail = false
    fireEvent.click(screen.getByRole('button', { name: '重新读取' }))
    expect(await screen.findByRole('heading', { name: '恢复后的完整文档' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '编辑' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '保存' })).toBeEnabled()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('感知预览移至右侧，完整渲染采集与实际注入，通过小标签切换用户操作', async () => {
    const data = (await loadReferenceData('/sense', 'kesepain', 's1')).sense
    const collected = '# 完整采集信息\n\n| 项目 | 值 |\n| --- | --- |\n| 数据 | 采集值 |\n\n'
      + Array.from({ length: 60 }, (_, i) => `- 采集条目 ${i}`).join('\n') + '\n\n采集末尾。'
    const injected = '# 实际注入信息\n\n**选定片段**\n\n注入末尾。'
    data.sources[0].collected_markdown = collected
    data.sources[0].injected_markdown = injected
    data.injection.content = '# 全局注入正文\n\n' + injected
    data.injection.preview = '这是截断摘要，不能用于正文预览'
    server.use(http.get('/api/users/kesepain/sense', () => HttpResponse.json(data)))
    renderPage('/sense')
    await screen.findByRole('heading', { name: '全局注入正文' })
    expect(document.querySelector('.sense-layout')).toBeInTheDocument()
    expect(screen.queryByText('注册说明')).not.toBeInTheDocument()
    expect(screen.queryByText(/文件热加载：修改后/)).not.toBeInTheDocument()
    const global = screen.getByRole('region', { name: '全局感知注入预览' })
    expect(global).toHaveTextContent('注入末尾。')
    expect(global).not.toHaveTextContent('这是截断摘要')
    fireEvent.click(screen.getByRole('button', { name: '模块详情' }))
    await screen.findByRole('heading', { name: '完整采集信息' })
    const collectedPreview = screen.getByRole('region', { name: '模块采集信息' })
    const injectedPreview = screen.getByRole('region', { name: '系统提示词注入片段' })
    expect(collectedPreview.querySelectorAll('li')).toHaveLength(60)
    expect(collectedPreview.querySelector('table')).toHaveTextContent('采集值')
    expect(collectedPreview).toHaveTextContent('采集末尾。')
    expect(injectedPreview.querySelector('strong')).toHaveTextContent('选定片段')
    expect(injectedPreview).toHaveTextContent('注入末尾。')
    expect(injectedPreview).not.toHaveTextContent('采集末尾。')
    expect(document.querySelector('.sense-document-grid')?.children).toHaveLength(2)
    expect(screen.queryByRole('region', { name: '用户操控组件' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '用户操作' }))
    expect(await screen.findByText('此感知模块无可控组件')).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: '当前用户白名单' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('button', { name: '刷新采集' }).closest('.sense-detail-tabs')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '删除模块' }).closest('.sense-module-actions')).toBeInTheDocument()
    expect(document.querySelectorAll('.sense-module-actions button.btn')).toHaveLength(1)
    expect(screen.queryByRole('region', { name: '模块采集信息' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '注入数据' }))
    expect(await screen.findByRole('heading', { name: '完整采集信息' })).toBeInTheDocument()
    expect(screen.queryByRole('switch')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '刷新采集' }).closest('.sense-detail-tabs')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '全局注入' }))
    expect(await screen.findByRole('heading', { name: '全局注入正文' })).toBeInTheDocument()
  })

  it('感知用户操作保留白名单、刷新及删除确认接口，刷新后保持详情标签', async () => {
    const data = (await loadReferenceData('/sense', 'kesepain', 's1')).sense
    let refreshed = 0
    let enabled: unknown
    let deleted = 0
    server.use(
      http.get('/api/users/kesepain/sense', () => HttpResponse.json({
        ...data, sources: data.sources.map((s: Record<string, unknown>) => ({
          ...s, whitelisted: enabled === undefined ? true : enabled,
          recent_update: `更新时间 ${refreshed}`,
        })),
      })),
      http.patch('/api/users/kesepain/sense/runtime/enabled', async ({ request }) => {
        enabled = (await request.json() as { enabled: boolean }).enabled
        return HttpResponse.json({ updated: true })
      }),
      http.post('/api/users/kesepain/sense/runtime/refresh', () => {
        refreshed += 1
        return HttpResponse.json({ updated: true })
      }),
      http.delete('/api/users/kesepain/sense/runtime', () => {
        deleted += 1
        return HttpResponse.json({ deleted: true })
      }),
    )
    renderPage('/sense')
    fireEvent.click(await screen.findByRole('button', { name: '模块详情' }))
    fireEvent.click(screen.getByRole('button', { name: '用户操作' }))
    fireEvent.click(screen.getByRole('switch', { name: '当前用户白名单' }))
    await waitFor(() => expect(enabled).toBe(false))
    await waitFor(() => expect(document.querySelector('.reference-page-host')).not.toHaveAttribute('aria-busy'))
    await waitFor(() => expect(screen.getByRole('switch', { name: '当前用户白名单' })).toHaveAttribute('aria-checked', 'false'))
    fireEvent.click(screen.getByRole('button', { name: '刷新采集' }))
    await waitFor(() => expect(refreshed).toBe(1))
    await waitFor(() => expect(document.querySelector('.sense-status-grid')).toHaveTextContent('更新时间 1'))
    expect(screen.getByRole('button', { name: '用户操作' })).toHaveClass('is-active')
    fireEvent.click(screen.getByRole('button', { name: '删除模块' }))
    const dialog = screen.getByRole('dialog', { name: '删除全局感知模块' })
    expect(dialog).toHaveTextContent('global_sense/runtime')
    fireEvent.click(within(dialog).getByRole('button', { name: '取消' }))
    expect(deleted).toBe(0)
  })

  it('感知用户操作中的真实配置组件按需读取和保存，并复用采集刷新接口', async () => {
    const panel = {
      schema_version: 1, title: '感知用户设置', containers: [{
        kind: 'config', title: '采集参数', width: 'full', height: 'h1', values: 'module/user_values.json',
        fields: [{ key: 'window', label: '采集窗口', type: 'number', default: 5, min: 1, max: 60 }],
        presets: [], current_values: { window: 5 },
      }],
    }
    let reads = 0
    let refreshed = 0
    let saved: Record<string, unknown> | undefined
    server.use(
      http.get('/api/users/kesepain/sense/runtime/panel', () => {
        reads += 1
        return HttpResponse.json({ user: 'kesepain', module: 'runtime', panel, panel_error: '' })
      }),
      http.put('/api/users/kesepain/sense/runtime/panel', async ({ request }) => {
        saved = await request.json() as Record<string, unknown>
        return HttpResponse.json({ user: 'kesepain', module: 'runtime', panel, panel_error: '', saved: true })
      }),
      http.post('/api/users/kesepain/sense/runtime/refresh', () => {
        refreshed += 1
        return HttpResponse.json({ updated: true })
      }),
    )
    renderPage('/sense')
    fireEvent.click(await screen.findByRole('button', { name: '模块详情' }))
    expect(reads).toBe(0)
    fireEvent.click(screen.getByRole('button', { name: '用户操作' }))
    const field = await screen.findByRole('spinbutton', { name: '采集窗口' })
    fireEvent.change(field, { target: { value: '12' } })
    fireEvent.click(screen.getByRole('button', { name: '保存配置' }))
    await waitFor(() => expect(saved).toEqual({ values: { window: 12 }, clear_secrets: [] }))
    await waitFor(() => expect(refreshed).toBe(1))
    expect(screen.getByRole('button', { name: '用户操作' })).toHaveClass('is-active')
  })

  it('被过滤的感知保留采集预览，实际注入不使用采集正文兜底', async () => {
    const data = (await loadReferenceData('/sense', 'kesepain', 's1')).sense
    Object.assign(data.sources[0], { whitelisted: false, active_for_main_agent: false, injected_markdown: '', injected_tokens: 0 })
    Object.assign(data.injection, { enabled: false, content: '', injected_items: 0 })
    server.use(http.get('/api/users/kesepain/sense', () => HttpResponse.json(data)))
    renderPage('/sense')
    await screen.findByText('（当前没有注入内容）')
    fireEvent.click(screen.getByRole('button', { name: '模块详情' }))
    expect(await screen.findByRole('heading', { name: '运行时采集' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: '系统提示词注入片段' })).toHaveTextContent('（该模块当前没有注入内容）')
    expect(screen.getByRole('region', { name: '系统提示词注入片段' })).not.toHaveTextContent('CPU 使用率')
  })

  it('一次性分钟时刻只补一次秒数', async () => {
    let captured: Record<string, unknown> | undefined
    server.use(http.post('/api/users/kesepain/tasks/crons', async ({ request }) => {
      captured = await request.json() as Record<string, unknown>
      return HttpResponse.json({ created: true })
    }))
    renderPage('/tasks')
    fireEvent.click(await screen.findByRole('button', { name: '创建定时任务' }))
    const dialog = screen.getByRole('dialog', { name: '创建定时任务' })
    fireEvent.input(within(dialog).getByRole('textbox', { name: '任务名称' }), { target: { value: '单次' } })
    fireEvent.input(within(dialog).getByRole('textbox', { name: '执行内容' }), { target: { value: '测试' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '单次' }))
    fireEvent.input(dialog.querySelector('input[type="datetime-local"]')!, { target: { value: '2030-01-01T09:15' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '创建任务' }))
    await waitFor(() => expect(captured?.next_run_at).toBe('2030-01-01T09:15:00+08:00'))
  })

  it('编辑多时刻任务保存实际修改的时刻并保留现有状态', async () => {
    let captured: Record<string, unknown> | undefined
    server.use(http.put('/api/users/kesepain/tasks/crons/cron_edit', async ({ request }) => {
      captured = await request.json() as Record<string, unknown>
      return HttpResponse.json({ updated: true })
    }))
    renderPage('/tasks')
    await screen.findByRole('heading', { name: '任务中枢' })
    openCronDialog({
      task_id: 'cron_edit', title: '多时刻', prompt: '测试',
      type: 'daily', status: 'completed', times: ['09:00', '20:00'],
    }, {})
    const dialog = screen.getByRole('dialog', { name: '编辑定时任务' })
    const clocks = dialog.querySelectorAll('input[type="time"]')
    expect(clocks).toHaveLength(2)
    fireEvent.input(clocks[0], { target: { value: '21:30' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '保存修改' }))
    await waitFor(() => expect(captured?.times).toEqual(['20:00', '21:30']))
    expect(captured).not.toHaveProperty('status')
    expect(captured).not.toHaveProperty('time')
  })

  it('编辑带 UTC 时区的任务按北京时间显示和提交，不拼接重复秒数', async () => {
    let captured: Record<string, unknown> | undefined
    server.use(http.put('/api/users/kesepain/tasks/crons/cron_edit', async ({ request }) => {
      captured = await request.json() as Record<string, unknown>
      return HttpResponse.json({ updated: true })
    }))
    renderPage('/tasks')
    await screen.findByRole('heading', { name: '任务中枢' })
    openCronDialog({
      task_id: 'cron_edit', title: '一次性', prompt: '测试',
      type: 'once', status: 'paused', next_run_at: '2030-01-01T01:15:30Z',
    }, {})
    const dialog = screen.getByRole('dialog', { name: '编辑定时任务' })
    expect(dialog.querySelector<HTMLInputElement>('input[type="datetime-local"]')!.value).toMatch(/^2030-01-01T09:15:30/)
    fireEvent.click(within(dialog).getByRole('button', { name: '保存修改' }))
    await waitFor(() => expect(captured?.next_run_at).toBe('2030-01-01T09:15:30+08:00'))
    expect(captured).not.toHaveProperty('status')
  })

  it('错误库存不伪装为空页面，可重试恢复', async () => {
    let fail = true
    server.use(http.get('/api/users/kesepain/agents', () => fail
      ? HttpResponse.json({ error: { message: '库存读取失败', code: 'unavailable' } }, { status: 503 })
      : HttpResponse.json({ user: 'kesepain', summary: { total: 0, enabled: 0, global: 0, user: 0 }, agents: [] })))
    renderPage('/agents')
    expect(await screen.findByRole('alert')).toHaveTextContent('库存读取失败')
    fail = false
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(await screen.findByRole('heading', { name: '子智能体' })).toBeInTheDocument()
  })

  it('任务读取过滤系统 Cron 记录，并为旧记录补稳定选择键', async () => {
    server.use(http.get('/api/users/kesepain/tasks', () => HttpResponse.json({
      summary: { active_plans: 0, waiting_plans: 0, enabled_crons: 0, completed_plans: 0 },
      plans: [], cron_tasks: [], executions: [
        { kind: 'cron', user_defined: false, task_id: 'maintenance', title: '系统维护', updated_at: '2026-10-10' },
        { kind: 'cron', user_defined: true, task_id: 'daily', title: '用户任务', updated_at: '2026-10-10' },
      ],
    })))
    const data = await loadReferenceData('/tasks', 'kesepain', 's1')
    expect(data.tasks.executions).toHaveLength(1)
    expect(data.tasks.executions[0].record_id).toContain('daily')
  })

  it('任务独立六条分页，选中定时任务后读取实际 prompt', async () => {
    server.use(
      http.get('/api/users/kesepain/tasks', () => HttpResponse.json({
        summary: { active_plans: 0, waiting_plans: 0, enabled_crons: 8, completed_plans: 0 },
        plans: [], executions: [], cron_tasks: Array.from({ length: 8 }, (_, i) => ({
          task_id: `cron-${i}`, title: `调度-${i}`, user_defined: true, type: 'daily', time: '18:00', status: 'enabled',
          next_run_at: `2026-10-${String(11 + i).padStart(2, '0')}T18:00:00+08:00`, latest_run_at: '', created_at: '', last_state: 'never',
        })),
      })),
      http.get('/api/users/kesepain/tasks/crons/:taskId', ({ params }) => HttpResponse.json({
        cron_task: { task_id: params.taskId, title: '实际调度', user_defined: true, type: 'daily', status: 'enabled', prompt: '真实任务正文' },
      })),
    )
    renderPage('/tasks?tab=cron')
    await screen.findByText('共 8 条 · 每页 6 条')
    expect(await screen.findByText('真实任务正文')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /调度-7/ })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    expect(screen.getByRole('button', { name: /调度-7/ })).toBeInTheDocument()
  })

  it('定时创建检查必填字段并提交分钟精度时间', async () => {
    let captured: Record<string, unknown> | undefined
    server.use(http.post('/api/users/kesepain/tasks/crons', async ({ request }) => {
      captured = await request.json() as Record<string, unknown>
      return HttpResponse.json({ created: true })
    }))
    renderPage('/tasks')
    fireEvent.click(await screen.findByRole('button', { name: '创建定时任务' }))
    const dialog = document.querySelector('.modal') as HTMLElement
    expect(within(dialog).getByRole('button', { name: '创建任务' })).toBeDisabled()
    fireEvent.input(dialog.querySelector('input')!, { target: { value: '网页测试调度' } })
    expect(within(dialog).getByRole('button', { name: '创建任务' })).toBeDisabled()
    expect(captured).toBeUndefined()
    fireEvent.input(within(dialog).getByRole('textbox', { name: '执行内容' }), { target: { value: '汇总测试日志' } })
    expect(within(dialog).getByRole('button', { name: '创建任务' })).toBeEnabled()
    fireEvent.click(within(dialog).getByRole('button', { name: '创建任务' }))
    await waitFor(() => expect(captured?.title).toBe('网页测试调度'))
    expect(captured?.type).toBe('daily')
    expect(captured?.time).toBe('03:30')
    expect(captured?.prompt).toBe('汇总测试日志')
    expect(captured?.status).toBe('enabled')
  })

  it.each([
    ['once', '单次'],
    ['weekly', '每周'],
    ['monthly', '每月'],
    ['recurring', '周期'],
  ])('%s 调度类型提交相应字段，切换类型后重新校验', async (type, label) => {
    let captured: Record<string, unknown> | undefined
    server.use(http.post('/api/users/kesepain/tasks/crons', async ({ request }) => {
      captured = await request.json() as Record<string, unknown>
      return HttpResponse.json({ created: true })
    }))
    renderPage('/tasks')
    fireEvent.click(await screen.findByRole('button', { name: '创建定时任务' }))
    const dialog = screen.getByRole('dialog', { name: '创建定时任务' })
    fireEvent.input(within(dialog).getByRole('textbox', { name: '任务名称' }), { target: { value: '调度测试' } })
    fireEvent.input(within(dialog).getByRole('textbox', { name: '执行内容' }), { target: { value: '本地测试' } })
    fireEvent.click(within(dialog).getByRole('button', { name: label }))
    const submit = within(dialog).getByRole('button', { name: '创建任务' })
    if (type === 'once') {
      expect(submit).toBeDisabled()
      fireEvent.input(dialog.querySelector('input[type="datetime-local"]')!, { target: { value: '2030-01-01T09:15:30' } })
    } else if (type === 'weekly') {
      expect(submit).toBeDisabled()
      fireEvent.click(within(dialog).getByRole('button', { name: '周一' }))
    } else if (type === 'monthly') {
      expect(submit).toBeDisabled()
      fireEvent.click(within(dialog).getByRole('button', { name: '15' }))
    }
    expect(submit).toBeEnabled()
    fireEvent.click(submit)
    await waitFor(() => expect(captured?.type).toBe(type))
    if (type === 'once') {
      expect(captured?.next_run_at).toBe('2030-01-01T09:15:30+08:00')
      expect(captured).not.toHaveProperty('time')
      expect(captured).not.toHaveProperty('max_runs')
    } else if (type === 'recurring') {
      expect(captured?.interval_seconds).toBe(3600)
      expect(captured).not.toHaveProperty('time')
    } else {
      expect(captured?.time).toBe('03:30')
      if (type === 'weekly') expect(captured?.weekdays).toEqual([1])
      if (type === 'monthly') expect(captured?.month_days).toEqual([15])
    }
  })

  it('开关写请求失败回滚显示并提供真实错误', async () => {
    server.use(http.patch('/api/users/kesepain/skills/builtin/enabled', () =>
      HttpResponse.json({ error: { message: '不允许禁用', code: 'conflict' } }, { status: 409 })))
    renderPage('/skills')
    const toggle = (await screen.findAllByRole('switch'))[0]
    expect(toggle).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(toggle)
    await screen.findByText('不允许禁用')
    await waitFor(() => expect(toggle).toHaveAttribute('aria-checked', 'true'))
  })

  it('知识正文按需 GET，修改通过 PUT 保存当前用户与路径', async () => {
    let saved: unknown
    server.use(http.put('/api/users/kesepain/knowledge/user/document', async ({ request }) => {
      expect(new URL(request.url).searchParams.get('path')).toBe('notes.md')
      saved = await request.json()
      return HttpResponse.json({ content: '# 已更新', size: 10 })
    }))
    renderPage('/knowledge')
    fireEvent.click(await screen.findByText('个人笔记'))
    await screen.findByText('知识正文')
    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    const editor = document.querySelector<HTMLTextAreaElement>('textarea.editor-md')!
    fireEvent.input(editor, { target: { value: '# 已更新' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(saved).toEqual({ content: '# 已更新' }))
  })

  it('知识库分类下拉支持筛选、新建、保存分类和移出分类', async () => {
    const categoryRequests: Array<{ path: string; category: string }> = []
    const knowledge = {
      user: 'kesepain',
      enabled: true,
      retrieval: { mode: 'index_only', full_index: true },
      summary: { documents: 3, user_documents: 2, shared_documents: 1, global_documents: 0 },
      documents: [
        { scope: 'user', relative_path: 'notes.md', title: '个人笔记', size: 120, updated_at: 1, category: '未分类', active_for_main_agent: true },
        { scope: 'user', relative_path: 'roadmap.md', title: '项目路线图', size: 80, updated_at: 1, category: '项目资料', active_for_main_agent: true },
        { scope: 'shared', relative_path: 'team.md', title: '共享笔记', size: 90, updated_at: 1, category: '团队资料', active_for_main_agent: true },
      ],
      categories: {
        user: [{ name: '未分类', count: 1 }, { name: '项目资料', count: 1 }],
        shared: [{ name: '未分类', count: 0 }, { name: '团队资料', count: 1 }],
        global: [{ name: '未分类', count: 0 }],
        all: [{ name: '未分类', count: 1 }, { name: '项目资料', count: 1 }, { name: '团队资料', count: 1 }],
      },
      classification_file: 'classify.sqlite3',
      classification_errors: [],
      source_policy: {
        knowledge: { effective_scopes: ['user', 'shared'], configured_scopes: ['user', 'shared'], enabled: true },
      },
    }
    server.use(
      http.get('/api/users/kesepain/knowledge', () => HttpResponse.json(knowledge)),
      http.get('/api/users/kesepain/knowledge/:scope/document', ({ params }) => HttpResponse.json({
        user: 'kesepain', scope: params.scope, relative_path: 'notes.md',
        content: '# 知识正文\n\n测试内容', size: 24, updated_at: 1, category: knowledge.documents[0].category,
      })),
      http.patch('/api/users/kesepain/knowledge/:scope/classification', async ({ request }) => {
        const url = new URL(request.url)
        const body = await request.json() as { category?: string }
        categoryRequests.push({ path: url.searchParams.get('path') || '', category: body.category || '' })
        knowledge.documents[0].category = body.category || '未分类'
        for (const scope of ['user', 'shared', 'global', 'all'] as const) {
          const counts = new Map([['未分类', 0]])
          knowledge.documents.filter((document) => scope === 'all' || document.scope === scope).forEach((document) => {
            counts.set(document.category, (counts.get(document.category) || 0) + 1)
          })
          knowledge.categories[scope] = Array.from(counts, ([name, count]) => ({ name, count }))
        }
        return HttpResponse.json({
          user: 'kesepain', scope: 'user', relative_path: 'notes.md',
          category: body.category || '未分类', updated: true, classification_file: 'classify.sqlite3',
        })
      }),
    )

    renderPage('/knowledge')
    const categoryFilter = await screen.findByRole('combobox', { name: '筛选知识分类' })
    expect(screen.getByRole('button', { name: '新建分类名' })).toBeDisabled()
    const toolbar = categoryFilter.closest('.knowledge-toolbar') as HTMLElement
    expect(toolbar).toContainElement(screen.getByRole('searchbox'))
    expect(toolbar).toContainElement(screen.getByRole('textbox', { name: '新的分类名称' }))
    expect(within(toolbar).queryByText('逻辑标签，不改变文件路径')).not.toBeInTheDocument()
    expect(within(toolbar).queryByText('新建分类', { selector: 'strong' })).not.toBeInTheDocument()
    expect(within(categoryFilter).getAllByRole('option')).toHaveLength(6)
    expect(within(categoryFilter).getByRole('option', { name: '共享层 · 团队资料（1）' })).toBeInTheDocument()
    const projectCategory = within(categoryFilter).getByRole('option', { name: '用户层 · 项目资料（1）' })
    fireEvent.change(categoryFilter, { target: { value: projectCategory.getAttribute('value') } })
    expect(await screen.findByText('项目路线图')).toBeInTheDocument()
    expect(screen.queryByText('个人笔记')).not.toBeInTheDocument()

    fireEvent.change(categoryFilter, { target: { value: 'all' } })
    fireEvent.click(await screen.findByText('个人笔记'))
    await screen.findByText('知识正文')
    const categoryInput = await screen.findByRole('textbox', { name: '新的分类名称' })
    fireEvent.input(categoryInput, { target: { value: '学习资料' } })
    const createButton = screen.getByRole('button', { name: '新建分类名' })
    expect(createButton).toBeEnabled()
    createButton.click()
    await waitFor(() => expect(categoryRequests).toEqual([{ path: 'notes.md', category: '学习资料' }]))
    await waitFor(() => expect(screen.getByRole('combobox', { name: '筛选知识分类' })).toContainElement(screen.getByRole('option', { name: '用户层 · 学习资料（1）' })))

    fireEvent.click((await screen.findAllByText('个人笔记'))[0])
    await screen.findByText('知识正文')
    const categoryControls = document.querySelector('.knowledge-category-controls') as HTMLElement
    expect(within(categoryControls).getAllByRole('button')).toHaveLength(2)
    const categorySelect = within(categoryControls).getByRole('combobox', { name: '选择知识分类' })
    expect(within(categoryControls).queryByRole('textbox')).not.toBeInTheDocument()
    fireEvent.change(categorySelect, { target: { value: '项目资料' } })
    fireEvent.click(within(categoryControls).getByRole('button', { name: '保存分类' }))
    await waitFor(() => expect(categoryRequests).toEqual([
      { path: 'notes.md', category: '学习资料' },
      { path: 'notes.md', category: '项目资料' },
    ]))
    await waitFor(() => expect(document.querySelector('[aria-busy="true"]')).not.toBeInTheDocument())
    fireEvent.click((await screen.findAllByText('个人笔记'))[0])
    await screen.findByText('知识正文')
    fireEvent.click(screen.getByRole('button', { name: '移出分类' }))
    await waitFor(() => expect(categoryRequests).toEqual([
      { path: 'notes.md', category: '学习资料' },
      { path: 'notes.md', category: '项目资料' },
      { path: 'notes.md', category: '' },
    ]))
  })

  it('诊断消费真实百分比、数字趋势、组件 state/health 字段', async () => {
    renderPage('/status?tab=tokens')
    expect(await screen.findByText('按小时明细')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /Token 总量趋势/ })).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/NaN|\[object Object\]/)
    fireEvent.click(screen.getByRole('button', { name: /外部组件/ }))
    expect(await screen.findByText('最近更新偏慢')).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('undefined')
  })

  it('刷新保存当前诊断标签，不跳回默认 Prompt', async () => {
    const client = renderPage('/status')
    await screen.findByRole('heading', { name: '运行诊断' })
    fireEvent.click(screen.getByRole('button', { name: /外部组件/ }))
    await screen.findByText('最近更新偏慢')
    await act(() => client.invalidateQueries({ queryKey: ['reference-page'] }))
    expect(screen.getByText('最近更新偏慢')).toBeInTheDocument()
  })

  it('文件使用后端列表，删除必须确认，取消不发 DELETE', async () => {
    let deletes = 0
    server.use(http.delete('/api/users/kesepain/files/:scope', () => {
      deletes++
      return HttpResponse.json({ deleted: [] })
    }))
    renderPage('/files', true)
    fireEvent.click(await screen.findByRole('checkbox', { name: '选择 readme.txt' }))
    fireEvent.click(screen.getByRole('button', { name: /删除选中/ }))
    expect(screen.getByRole('alertdialog')).toBeInTheDocument()
    expect(deletes).toBe(0)
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(deletes).toBe(0)
  })
})
