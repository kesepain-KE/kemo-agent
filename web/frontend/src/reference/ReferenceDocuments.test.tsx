import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../test/server'
import { ReferencePage, loadReferenceData } from './ReferencePage'
import { ModulePanel } from '../components/ModulePanel'
import type { ShellOutletContext } from '../components/appShellTypes'
import type { ModulePanelResponse } from '../types/api'

const document = '# 统一文档\n\n> 引用说明\n\n1. 第一步\n2. 第二步\n\n'
  + '| 字段 | 内容 |\n| --- | --- |\n| 状态 | **完整** |\n\n```python\nprint("保留换行")\nprint("末尾")\n```\n\n文档末尾。'

function renderPage(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const context = { user: 'kesepain', sessionId: 's1', chatRunning: false } as ShellOutletContext
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}>
    <Routes><Route element={<Outlet context={context} />}><Route path="*" element={<ReferencePage />} /></Route></Routes>
  </MemoryRouter></QueryClientProvider>)
}

describe('业务文档复用知识库放大预览', () => {
  it('知识预览与放大预览渲染同一完整文档，编辑仍显示原始 Markdown', async () => {
    server.use(http.get('/api/users/kesepain/knowledge/:scope/document', () => HttpResponse.json({
      user: 'kesepain', scope: 'user', relative_path: 'notes.md', content: document, size: document.length, updated_at: 1,
    })))
    renderPage('/knowledge')
    fireEvent.click(await screen.findByText('个人笔记'))
    await screen.findByRole('heading', { name: '统一文档' })
    const preview = window.document.querySelector('.knowledge-document-preview .document-markdown')!
    const html = preview.innerHTML
    expect(preview.querySelector('blockquote')).toHaveTextContent('引用说明')
    expect(preview.querySelector('ol')).toHaveTextContent('第二步')
    expect(preview.querySelector('table')).toHaveTextContent('完整')
    expect(within(preview as HTMLElement).getByRole('button', { name: '复制' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: '放大预览' }))
    const dialog = await screen.findByRole('dialog')
    await within(dialog).findByRole('heading', { name: '统一文档' })
    expect(dialog.querySelector('.document-markdown')?.innerHTML).toBe(html)
    fireEvent.click(within(dialog).getAllByRole('button', { name: '关闭' })[0])
    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    expect(screen.getByRole('textbox')).toHaveValue(document)
  })

  it('定时任务执行内容渲染 Markdown，编辑保留完整原文', async () => {
    server.use(http.get('/api/users/kesepain/tasks/crons/:taskId', () => HttpResponse.json({
      user: 'kesepain', cron_task: {
        task_id: 'daily-check', title: '每日检查', user_defined: true, status: 'enabled', type: 'daily',
        time: '18:00', exec_mode: 'agent', prompt: document,
      },
    })))
    renderPage('/tasks?tab=cron')
    await screen.findByRole('heading', { name: '统一文档' })
    expect(window.document.querySelector('.task-cron-preview table')).toHaveTextContent('完整')
    expect(window.document.querySelector('.task-cron-preview pre')).toHaveTextContent('print("末尾")')
    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    expect(await screen.findByRole('textbox', { name: '执行内容' })).toHaveValue(document)
  })

  it.each([document, { value: '原始 JSON 结果', items: ['完整', '末尾'] }])('执行结果按正文或 JSON 语义完整渲染', async result => {
    const data = (await loadReferenceData('/tasks', 'kesepain', 's1')).tasks
    data.executions = [{
      record_id: 'record-1', kind: 'cron', task_id: 'daily-check', title: '执行记录', status: 'completed',
      user_defined: true, updated_at: '2026-10-12T10:00:00+08:00', duration_ms: 100, result,
    }] as typeof data.executions
    server.use(http.get('/api/users/kesepain/tasks', () => HttpResponse.json(data)))
    renderPage('/tasks?tab=exec')
    await screen.findByText('结果摘要')
    const preview = window.document.querySelector('.doc-content') as HTMLElement
    if (typeof result === 'string') {
      expect(await within(preview).findByRole('heading', { name: '统一文档' })).toBeInTheDocument()
      expect(preview).toHaveTextContent('文档末尾。')
    } else {
      expect(await within(preview).findByRole('button', { name: '复制' })).toBeEnabled()
      expect(preview.querySelector('code')?.textContent?.trimEnd()).toBe(JSON.stringify(result, null, 2))
    }
  })

  it('感知和拓展状态 Markdown 字段使用完整渲染并阻止外部图片与不安全链接', async () => {
    const data: ModulePanelResponse = { user: 'kesepain', module: 'example', panel_error: '',
      panel: { schema_version: 1, title: '面板', containers: [{
      kind: 'status', title: '状态文档', width: 'full', height: 'h2', source: 'status.json',
      fields: [{ key: 'body', type: 'markdown', label: '说明' }],
      data: { body: document + '\n\n[危险](javascript:alert(1))\n\n![外部图片](https://example.com/track.png)' },
    }] } }
    render(<ModulePanel kind="sense" data={data} emptyText="无配置"
      onSave={async () => {}} onAction={async () => {}} />)
    expect(await screen.findByRole('heading', { name: '统一文档' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '复制' })).toBeEnabled()
    expect(window.document.querySelector('.document-markdown table')).toHaveTextContent('完整')
    expect(window.document.querySelector('a[href^="javascript:"]')).toBeNull()
    expect(window.document.querySelector('img[src^="https:"]')).toBeNull()
  })
})
