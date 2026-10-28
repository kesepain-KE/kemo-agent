import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { AppShell } from '../components/AppShell'
import { ProfilePage } from './ProfilePage'
import { SettingsPage } from './SettingsPage'
import { server } from '../test/server'

const pages = [
  { path: 'profile', element: <ProfilePage /> },
  { path: 'settings', element: <SettingsPage /> },
]

function renderPage(path: string) {
  const router = createMemoryRouter([{ path: '/', element: <AppShell />, children: pages }], { initialEntries: [`/${path}?user=kesepain&session=s1`] })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>)
}

describe('current settings and profile pages', () => {
  it('主题页只提供高级白/高级黑，切换立即生效并保存兼容的 light/dark 值', async () => {
    const saves: unknown[] = []
    server.use(http.patch('/api/users/kesepain/preferences', async ({ request }) => {
      const appearance = await request.json()
      saves.push(appearance)
      return HttpResponse.json({ user: 'kesepain', appearance })
    }))
    renderPage('settings')
    const group = await screen.findByRole('radiogroup', { name: '界面主题' })
    expect(within(group).getAllByRole('radio')).toHaveLength(2)
    const white = within(group).getByRole('radio', { name: '高级白' })
    const black = within(group).getByRole('radio', { name: '高级黑' })
    await waitFor(() => expect(white).toHaveAttribute('aria-checked', 'true'))
    fireEvent.click(black)
    await waitFor(() => expect(saves).toEqual([{ theme: 'dark' }]))
    expect(black).toHaveAttribute('aria-checked', 'true')
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(localStorage.getItem('kemo-theme')).toBe('dark')
    fireEvent.click(white)
    await waitFor(() => expect(saves).toEqual([{ theme: 'dark' }, { theme: 'light' }]))
    expect(white).toHaveAttribute('aria-checked', 'true')
    expect(document.documentElement.dataset.theme).toBe('light')
  })

  it('记忆与上下文页读取并保存中途上下文压缩开关', async () => {
    let enabled = true
    let savedChanges: Record<string, unknown> | undefined
    server.use(
      http.get('/api/global-config', () => HttpResponse.json({
        scope: 'global',
        config: {
          agents: {
            enable_mid_context_compression: enabled,
            token_limit: 1000000,
            token_compression_ratio: 0.3,
            max_rounds: 80,
            rounds_after_compression: 20,
          },
          memory: { temporary_injection_limits: { seven_days: 100, one_month: 200, half_year: 300 } },
        },
        redacted_paths: [],
      })),
      http.patch('/api/global-config', async ({ request }) => {
        const body = await request.json() as { changes: Record<string, unknown> }
        savedChanges = body.changes
        const agents = body.changes.agents as { enable_mid_context_compression?: boolean }
        enabled = agents.enable_mid_context_compression ?? enabled
        return HttpResponse.json({
          scope: 'global',
          config: {
            agents: {
              enable_mid_context_compression: enabled,
              token_limit: 1000000,
              token_compression_ratio: 0.3,
              max_rounds: 80,
              rounds_after_compression: 20,
            },
            memory: { temporary_injection_limits: { seven_days: 100, one_month: 200, half_year: 300 } },
          },
          redacted_paths: [],
          updated: true,
        })
      }),
    )
    renderPage('settings')
    fireEvent.click(await screen.findByRole('button', { name: '记忆与上下文' }))
    const toggle = await screen.findByRole('switch', { name: '中途上下文压缩' })
    expect(toggle).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-checked', 'false')
    fireEvent.click(screen.getByRole('button', { name: '保存记忆与上下文' }))
    await waitFor(() => expect(savedChanges).toMatchObject({
      agents: expect.objectContaining({ enable_mid_context_compression: false }),
    }))
    expect(await screen.findByText('记忆与上下文 已保存')).toBeInTheDocument()
    await waitFor(() => expect(toggle).toHaveAttribute('aria-checked', 'false'))
  })

  it('结束音效设置只在 Windows 桌面端显示且支持上传和清除', async () => {
    const userAgent = vi.spyOn(window.navigator, 'userAgent', 'get').mockReturnValue('Mozilla/5.0 (Windows NT 10.0; Win64; x64)')
    let uploaded = false
    let deleted = false
    let uploadRequests = 0
    server.use(
      http.get('/api/users/kesepain/completion-sound/status', () => HttpResponse.json({
        user: 'kesepain', enabled: uploaded && !deleted, available: uploaded && !deleted,
        filename: uploaded && !deleted ? 'completion_sound.mp3' : '', mime_type: uploaded && !deleted ? 'audio/mpeg' : '',
        size: uploaded && !deleted ? 16 : 0, updated_at: uploaded && !deleted ? '2026-08-22T08:00:00Z' : '',
      })),
      http.get('/api/users/kesepain/failure-sound/status', () => HttpResponse.json({
        user: 'kesepain', enabled: false, available: false, filename: '', mime_type: '', size: 0, updated_at: '',
      })),
      http.post('/api/users/kesepain/completion-sound', () => {
        uploadRequests += 1
        uploaded = true
        deleted = false
        return HttpResponse.json({
          ok: true,
          status: { user: 'kesepain', enabled: true, available: true, filename: 'completion_sound.mp3', mime_type: 'audio/mpeg', size: 16, updated_at: '2026-08-22T08:00:00Z' },
        })
      }),
      http.delete('/api/users/kesepain/completion-sound', () => {
        deleted = true
        return HttpResponse.json({ ok: true, deleted: true })
      }),
    )

    renderPage('settings')
    expect(await screen.findByText('运行结束音效')).toBeInTheDocument()
    expect(screen.getByText('运行失败音效')).toBeInTheDocument()
    expect(screen.getByText('支持 Windows 桌面网页端；格式：MP3、WAV、Ogg、WebM；智能体成功完成运行后自动播放。')).toBeInTheDocument()
    expect(screen.queryByText('播放规则')).not.toBeInTheDocument()
    expect(screen.getAllByText('未设置')).toHaveLength(2)
    fireEvent.change(screen.getByLabelText('选择结束音效文件'), {
      target: { files: [new File(['ID3audio'], 'done.mp3', { type: 'audio/mpeg' })] },
    })
    expect(await screen.findByText('结束音效已保存。')).toBeInTheDocument()
    expect(uploadRequests).toBe(1)
    expect(screen.getByText(/completion_sound\.mp3/)).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: '试听' })[0]).toBeEnabled()
    fireEvent.click(screen.getAllByRole('button', { name: '清除' })[0])
    expect(await screen.findByText('结束音效已清除。')).toBeInTheDocument()
    userAgent.mockRestore()
  })

  it('手机端隐藏结束音效入口且不请求状态 API', async () => {
    const userAgent = vi.spyOn(window.navigator, 'userAgent', 'get').mockReturnValue('Mozilla/5.0 (Linux; Android 15; Mobile)')
    let statusRequests = 0
    server.use(http.get('/api/users/kesepain/completion-sound/status', () => {
      statusRequests += 1
      return HttpResponse.json({ user: 'kesepain', enabled: false, available: false, filename: '', mime_type: '', size: 0, updated_at: '' })
    }))
    renderPage('settings')
    expect(await screen.findByText('界面主题')).toBeInTheDocument()
    expect(screen.queryByText('运行结束音效')).not.toBeInTheDocument()
    expect(statusRequests).toBe(0)
    userAgent.mockRestore()
  })

  it('配置页提供七个纵向栏目、结构化字段编辑和只读版本查看', async () => {
    let savedChanges: Record<string, unknown> | undefined
    let modelDiscoveryCalls = 0
    server.use(http.patch('/api/users/kesepain/config', async ({ request }) => {
      const body = await request.json() as { changes: Record<string, unknown> }
      savedChanges = body.changes
      return HttpResponse.json({ user: 'kesepain', config: body.changes, redacted_paths: ['provider.api_key'], updated: true })
    }), http.get('/api/users/kesepain/provider/models', () => {
      modelDiscoveryCalls += 1
      return HttpResponse.json({
        user: 'kesepain', protocol: 'kemo', api_valid: true, count: 1,
        data: [{ id: 'deepseek-deepseek-v4-flash', object: 'kemo.model', provider_id: 'deepseek', provider_model: 'deepseek-v4-flash', task: 'llm', capabilities_available: true, capabilities_url: '/model/models/deepseek-deepseek-v4-flash/capabilities' }],
      })
    }))
    renderPage('settings')
    expect(await screen.findByRole('heading', { name: '配置' })).toBeInTheDocument()
    expect(screen.queryByText('结构化配置与敏感字段保护')).not.toBeInTheDocument()
    expect(screen.getByText('界面主题')).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: '小' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: '中' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: '大' })).toBeInTheDocument()
    expect(screen.queryByText('Prompt 与 Expand')).not.toBeInTheDocument()
    expect(screen.queryByText('配置文件 JSON')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '模型与 Provider' }))
    expect(await screen.findByText('Kemo API 已验证，已获取 1 个可用模型')).toBeInTheDocument()
    expect(modelDiscoveryCalls).toBe(1)
    expect(screen.getByRole('button', { name: '主模型选择模型' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '外观与主题' }))
    expect(screen.queryByText('Kemo API 已验证，已获取 1 个可用模型')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '模型与 Provider' }))
    expect(screen.getByText('Kemo API 已验证，已获取 1 个可用模型')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '主模型选择模型' })).toBeInTheDocument()
    expect(modelDiscoveryCalls).toBe(1)
    const providerSelect = await screen.findByRole('combobox', { name: 'Provider 类型' })
    expect(providerSelect).toHaveTextContent('kemo')
    expect(providerSelect).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(providerSelect)
    expect(screen.getByRole('listbox', { name: 'Provider 类型选项' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /chat/ })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('option', { name: /kemo/ }))
    expect(screen.getByLabelText('模型')).toHaveValue('test-model')
    expect(screen.getByLabelText('Base URL')).toHaveValue('http://127.0.0.1:8741')
    expect(screen.getByLabelText('API Key')).toHaveAttribute('type', 'password')
    expect(screen.getByRole('switch', { name: '流式输出' })).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByRole('switch', { name: '流式输出' })).toHaveTextContent('已关闭')
    expect(screen.getByRole('combobox', { name: '思考强度' })).toHaveTextContent('中 — 均衡（推荐）')
    expect(screen.getByRole('switch', { name: '主模型支持图片输入' })).toHaveAttribute('aria-checked', 'false')
    const visionRoutingSelect = screen.getByRole('combobox', { name: '图片路由' })
    expect(visionRoutingSelect).toHaveTextContent('自动 — 主模型优先')
    expect(visionRoutingSelect).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(visionRoutingSelect)
    expect(screen.getByRole('listbox', { name: '图片路由选项' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /仅专用视觉模型/ })).toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('listbox', { name: '图片路由选项' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('默认子智能体模型')).toHaveValue('agent-default')
    expect(screen.getByLabelText('轻量子智能体模型')).toHaveValue('summary-model')
    expect(screen.getByLabelText('推理子智能体模型')).toHaveValue('agent-reasoning')
    expect(screen.getByText('普通用户子智能体和未指定档位的后台代理；留空时继承主对话模型')).toBeInTheDocument()
    expect(screen.getByText('历史摘要、上下文压缩和临时记忆整理等高频轻量任务；留空时继承主对话模型')).toBeInTheDocument()
    expect(screen.getByText('任务计划、自我改进和需要较深分析的后台任务；留空时继承主对话模型')).toBeInTheDocument()
    expect(screen.getByLabelText('图片识别')).toHaveValue('vision-model')
    fireEvent.click(screen.getByRole('combobox', { name: '思考强度' }))
    fireEvent.click(screen.getByRole('option', { name: /高.*深度推理/ }))
    fireEvent.change(screen.getByLabelText('模型'), { target: { value: 'next-model' } })
    fireEvent.change(screen.getByLabelText('轻量子智能体模型'), { target: { value: 'summary-model-next' } })
    fireEvent.click(screen.getByRole('switch', { name: '主模型支持图片输入' }))
    fireEvent.keyDown(visionRoutingSelect, { key: 'ArrowDown' })
    fireEvent.click(screen.getByRole('option', { name: /仅专用视觉模型/ }))
    expect(visionRoutingSelect).toHaveTextContent('仅专用视觉模型')
    fireEvent.click(screen.getByRole('button', { name: '保存模型与 Provider' }))
    expect(await screen.findByText('模型与 Provider 已保存')).toBeInTheDocument()
    expect(await screen.findByText('Kemo API 已验证，已获取 1 个可用模型')).toBeInTheDocument()
    expect(modelDiscoveryCalls).toBe(2)
    expect(screen.queryByText('可选择')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '主模型选择模型' })).toHaveTextContent('选择模型')
    fireEvent.click(screen.getByRole('button', { name: '主模型选择模型' }))
    expect(screen.getByRole('listbox', { name: '主模型选项' })).toBeInTheDocument()
    fireEvent.change(screen.getByRole('textbox', { name: '主模型筛选' }), { target: { value: 'deepseek-v4' } })
    fireEvent.click(screen.getByRole('option', { name: /deepseek-deepseek-v4-flash/ }))
    expect(screen.getByLabelText('模型')).toHaveValue('deepseek-deepseek-v4-flash')
    fireEvent.click(screen.getByRole('button', { name: '默认子智能体模型选择模型' }))
    fireEvent.click(screen.getByRole('option', { name: /deepseek-deepseek-v4-flash/ }))
    expect(screen.getByLabelText('默认子智能体模型')).toHaveValue('deepseek-deepseek-v4-flash')
    fireEvent.click(screen.getByRole('button', { name: '图片识别选择模型' }))
    fireEvent.click(screen.getByRole('option', { name: /deepseek-deepseek-v4-flash/ }))
    expect(screen.getByLabelText('图片识别')).toHaveValue('deepseek-deepseek-v4-flash')
    await waitFor(() => expect(savedChanges).toMatchObject({
      provider: { reasoning_effort: 'high', input_modalities: ['text', 'image'] },
      agent_models: { default: 'agent-default', cheap: 'summary-model-next', reasoning: 'agent-reasoning' },
      multimodal_routing: { vision: 'dedicated' },
    }))

    fireEvent.click(screen.getByRole('button', { name: '用户切换' }))
    expect(screen.queryByRole('combobox', { name: '可切换用户' })).not.toBeInTheDocument()
    expect(await screen.findByRole('button', { name: '切换到用户 reviewer' })).toBeEnabled()

    fireEvent.click(screen.getByRole('button', { name: '记忆与上下文' }))
    expect(await screen.findByLabelText('Token 上限')).toHaveValue(1000000)
    expect(screen.getByLabelText('Token 压缩比例')).toHaveValue('0.3')
    expect(screen.getByLabelText('周记忆上限')).toHaveValue(100)

    fireEvent.click(screen.getByRole('button', { name: '权限边界' }))
    expect(await screen.findByRole('switch', { name: '使用共享知识库' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('switch', { name: '拓展数据实时注入' })).not.toBeChecked()
    expect(screen.getByRole('switch', { name: '感知数据实时注入' })).not.toBeChecked()
    expect(screen.getByLabelText('插件白名单输入')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '版本查看' }))
    expect(await screen.findByText('当前版本')).toBeInTheDocument()
    expect(screen.getAllByText('v0.2.0').length).toBeGreaterThanOrEqual(5)
    expect(screen.getByText('配套 Kemo 网关')).toBeInTheDocument()
    expect(screen.getByText('v0.8.2')).toBeInTheDocument()
    expect(screen.getByText('Schema 1')).toBeInTheDocument()
    expect(screen.getAllByText('核心引擎').length).toBeGreaterThan(0)
    expect(screen.getByText('只读')).toBeInTheDocument()
    expect(screen.getByText('配置 Schema 版本')).toBeInTheDocument()
    expect(screen.getByLabelText('配置结构 Schema 版本')).toHaveTextContent('1')
    expect(screen.getByLabelText('历史结构 Schema 版本')).toHaveTextContent('1')
    expect(screen.getByLabelText('记忆存储 Schema 版本')).toHaveTextContent('1')
    expect(await screen.findByText('发现新版本 v0.3.0')).toBeInTheDocument()
    expect(screen.getByText('python update.py --module all')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '复制更新命令' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '立即更新' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '运行限制' }))
    expect(await screen.findByLabelText('工具调用超时')).toHaveValue(240)
    expect(screen.getByLabelText('单轮最大工具调用数')).toHaveValue(80)
    expect(screen.getByLabelText('单个工具最大连续使用上限')).toHaveValue(8)
    expect(screen.getByLabelText('工具参数异常重试次数')).toHaveValue(2)
    expect(screen.getByRole('switch', { name: '自动接受任务计划' })).toHaveAttribute('aria-checked', 'false')
    fireEvent.click(screen.getByRole('button', { name: '重启智能体' }))
    const restartDialog = screen.getByRole('alertdialog', { name: '确认重启智能体' })
    expect(restartDialog).toBeInTheDocument()
    expect(restartDialog.parentElement?.parentElement).toBe(document.body)
    expect(screen.getByText('您确定要重启吗？')).toBeInTheDocument()
    expect(screen.getByText(/智能体在执行任务时重启可能会出现故障/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '确认重启' }))
    expect(await screen.findByText(/重启请求已提交/)).toBeInTheDocument()
  })

  it('chat 协议保存后不请求 Kemo 模型目录', async () => {
    let modelDiscoveryCalls = 0
    let chatDiscoveryCalls = 0
    server.use(http.get('/api/users/kesepain/config/full', () => HttpResponse.json({
      user: 'kesepain',
      config: { provider: { type: 'chat', model: 'test-model', base_url: 'http://127.0.0.1:8741', api_key: '***', stream: false, reasoning_effort: 'medium' } },
      redacted_paths: ['provider.api_key'],
    })), http.get('/api/users/kesepain/provider/chat-models', () => {
      chatDiscoveryCalls += 1
      return HttpResponse.json({
        user: 'kesepain', protocol: 'chat', api_valid: true, count: 1,
        data: [{ id: 'chat-visible-model', object: 'model', owned_by: 'provider' }],
      })
    }), http.get('/api/users/kesepain/provider/models', () => {
      modelDiscoveryCalls += 1
      return HttpResponse.json({ user: 'kesepain', protocol: 'kemo', api_valid: true, count: 0, data: [] })
    }))
    renderPage('settings')
    fireEvent.click(await screen.findByRole('button', { name: '模型与 Provider' }))
    expect(await screen.findByRole('combobox', { name: 'Provider 类型' })).toHaveTextContent('chat')
    fireEvent.click(await screen.findByRole('button', { name: '保存模型与 Provider' }))
    expect(await screen.findByText('模型与 Provider 已保存')).toBeInTheDocument()
    expect(modelDiscoveryCalls).toBe(0)
    expect(screen.queryByText(/Kemo API 已验证/)).not.toBeInTheDocument()
    expect(screen.queryByText('可选择')).not.toBeInTheDocument()
    expect(await screen.findByRole('button', { name: '主模型选择模型' })).toBeInTheDocument()
    expect(chatDiscoveryCalls).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button', { name: '主模型选择模型' }))
    expect(screen.getByText('Chat · Provider 模型 API')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('option', { name: /chat-visible-model/ }))
    expect(screen.getByLabelText('模型')).toHaveValue('chat-visible-model')
  })

  it('Kemo 模型目录为空时保留传统自由输入模式', async () => {
    server.use(http.get('/api/users/kesepain/provider/models', () => HttpResponse.json({
      user: 'kesepain', protocol: 'kemo', api_valid: true, count: 0, data: [],
    })))
    renderPage('settings')
    fireEvent.click(await screen.findByRole('button', { name: '模型与 Provider' }))
    expect(await screen.findByText('Kemo API 已验证，但当前密钥没有可用的 LLM 模型')).toBeInTheDocument()
    expect(screen.getByLabelText('模型')).not.toHaveAttribute('list')
    expect(screen.queryByText('可选择')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '主模型选择模型' })).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('模型'), { target: { value: 'manual-model' } })
    expect(screen.getByLabelText('模型')).toHaveValue('manual-model')
  })

  it('Kemo 配置页随所选模型切换动态思考档位并禁用非推理模型', async () => {
    server.use(
      http.get('/api/users/kesepain/config/full', () => HttpResponse.json({
        user: 'kesepain',
        config: {
          provider: { type: 'kemo', model: 'dynamic-model', base_url: 'http://127.0.0.1:8741', api_key: '***', stream: true, reasoning_effort: 'medium' },
        },
        redacted_paths: ['provider.api_key'],
      })),
      http.get('/api/users/kesepain/provider/models', () => HttpResponse.json({
        user: 'kesepain', protocol: 'kemo', api_valid: true, count: 2,
        data: [
          { id: 'dynamic-model', object: 'kemo.model', provider_id: 'test', provider_model: 'dynamic', task: 'llm', capabilities_available: true, capabilities_url: '/model/models/dynamic-model/capabilities' },
          { id: 'plain-model', object: 'kemo.model', provider_id: 'test', provider_model: 'plain', task: 'llm', capabilities_available: true, capabilities_url: '/model/models/plain-model/capabilities' },
        ],
      })),
      http.get('/api/users/kesepain/provider/model-capabilities', ({ request }) => {
        const model = new URL(request.url).searchParams.get('model') || ''
        const supported = model !== 'plain-model'
        return HttpResponse.json({
          user: 'kesepain', protocol: 'kemo', api_valid: true, model, stale: false, warning: '',
          capabilities: {
            model, task: 'llm', input_modalities: ['text'], output_modalities: ['text'], streaming: true,
            reasoning: { supported, efforts: supported ? ['none', 'minimal', 'ultra'] : [], summary: supported, persisted_state: false },
            tools: { function_calling: true, parallel_calls: false, multimodal_results: false },
            structured_output: true, metadata: {},
            extensions: supported ? { reasoning_policy: { mode: 'mapped', logical_efforts: ['minimal', 'ultra'], collapsed: true } } : {},
          },
        })
      }),
    )

    renderPage('settings')
    fireEvent.click(await screen.findByRole('button', { name: '模型与 Provider' }))
    const effortSelect = await screen.findByRole('combobox', { name: '思考强度' })
    await waitFor(() => expect(effortSelect).toHaveTextContent('极少'))
    expect(screen.getByText(/部分档位会映射到相同的上游强度/)).toBeInTheDocument()
    fireEvent.click(effortSelect)
    expect(screen.getByRole('option', { name: /极少.*几乎不思考/ })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /ultra.*Kemo 网关声明档位/ })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /none/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /中.*均衡/ })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('option', { name: /ultra.*Kemo 网关声明档位/ }))

    fireEvent.change(screen.getByLabelText('模型'), { target: { value: 'plain-model' } })
    await waitFor(() => expect(screen.getByRole('combobox', { name: '思考强度' })).toHaveTextContent('推理不可用'))
    expect(screen.getByRole('combobox', { name: '思考强度' })).toBeDisabled()
    expect(screen.getByText(/运行时不会向 Kemo 网关提交 reasoning 参数/)).toBeInTheDocument()
  })

  it('重启被运行态拦截后允许经二次确认强制重启', async () => {
    const restartBodies: Array<{ port: number; force?: boolean }> = []
    server.use(http.post('/api/system/restart', async ({ request }) => {
      const body = await request.json() as { port: number; force?: boolean }
      restartBodies.push(body)
      if (!body.force) {
        return HttpResponse.json({ error: { code: 'conflict', message: '存在正在运行的对话，请结束当前响应后再重启智能体', status: 409 } }, { status: 409 })
      }
      return HttpResponse.json({ ok: true, port: body.port, helper_pid: 4321 })
    }))

    renderPage('settings')
    fireEvent.click(await screen.findByRole('button', { name: '运行限制' }))
    fireEvent.click(await screen.findByRole('button', { name: '重启智能体' }))
    fireEvent.click(screen.getByRole('button', { name: '确认重启' }))

    expect(await screen.findByText('存在正在运行的对话，请结束当前响应后再重启智能体')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '强制重启' }))
    const forceDialog = screen.getByRole('alertdialog', { name: '确认强制重启智能体' })
    expect(within(forceDialog).getByText(/绕过运行状态检查/)).toBeInTheDocument()
    fireEvent.click(within(forceDialog).getByRole('button', { name: '确认强制重启' }))

    expect(await screen.findByText(/强制重启请求已提交/)).toBeInTheDocument()
    expect(restartBodies).toHaveLength(2)
    expect(restartBodies[0].force).toBeUndefined()
    expect(restartBodies[1]).toEqual({ port: restartBodies[0].port, force: true })
  })

  it('用户资料读取并原子保存人格 Markdown', async () => {
    renderPage('profile')
    expect(await screen.findByRole('heading', { name: '身份与人格' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '编辑用户人格' }))
    const editor = await screen.findByLabelText('用户人格 Markdown')
    expect(editor).toHaveValue('# 用户人格')
    fireEvent.change(editor, { target: { value: '# 更新后的用户人格' } })
    fireEvent.click(screen.getByRole('button', { name: '保存用户人格' }))
    expect(await screen.findByText('用户人格已原子写入。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '编辑用户人格' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '下载用户人格' })).toBeInTheDocument()
    const userActions = screen.getByRole('button', { name: '下载用户人格' }).parentElement
    expect(userActions).toHaveClass('profile-editor-actions')
    expect(within(userActions!).getAllByRole('button').map(button => button.getAttribute('aria-label') || button.textContent))
      .toEqual(['下载用户人格', '保存用户人格'])
    fireEvent.click(screen.getByRole('button', { name: '预览用户人格' }))
    expect(screen.getByLabelText('用户人格 Markdown 预览')).toHaveTextContent('更新后的用户人格')
    expect(screen.getByLabelText('全局人格 Markdown 预览')).toHaveTextContent('全局人格')
    expect(screen.getByRole('button', { name: '下载全局人格' })).toBeInTheDocument()
    expect(screen.queryByLabelText('全局人格 Markdown')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /保存全局人格/ })).not.toBeInTheDocument()
    const globalActions = screen.getByRole('button', { name: '下载全局人格' }).parentElement
    expect(globalActions).toHaveClass('profile-editor-actions')
    expect(within(globalActions!).getAllByRole('button')).toHaveLength(1)
    expect(screen.queryByText('全局安全边界')).not.toBeInTheDocument()
  })
})
