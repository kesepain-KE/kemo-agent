import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../test/server'
import {
  AVATAR_UPDATED_EVENT,
  commandPlan,
  editPlan,
  getExpandModulePanel,
  getLogoUrl,
  getRuntimeStatus,
  getSessions,
  getSenseModulePanel,
  getUserArtifactUrl,
  getUserAvatarUrl,
  getUserFileDownloadUrl,
  parseSseFrames,
  invokeExpandModulePanelAction,
  putExpandModulePanel,
  putSenseModulePanel,
  retryPlanStep,
  submitGuidance,
  streamChat,
  uploadUserAvatar,
} from './client'

describe('parseSseFrames', () => {
  it('历史会话请求可携带上海自然日筛选参数', async () => {
    let receivedUrl = ''
    server.use(http.get('/api/users/kesepain/sessions', ({ request }) => {
      receivedUrl = request.url
      return HttpResponse.json({ user: 'kesepain', source: 'all', date: '2026-09-25', sessions: [], has_more: false, next_cursor: '' })
    }))

    const result = await getSessions('kesepain', '', 50, '', 'all', '2026-09-25')

    expect(new URL(receivedUrl).searchParams.get('date')).toBe('2026-09-25')
    expect(result.date).toBe('2026-09-25')
  })

  it('模块面板接口使用独立端点并保持 snake_case 请求合同', async () => {
    const requests: Array<{ url: string; method: string; body?: unknown }> = []
    server.use(
      http.get('/api/users/kesepain/expand/global/demo/panel', ({ request }) => {
        requests.push({ url: request.url, method: request.method })
        return HttpResponse.json({ user: 'kesepain', scope: 'global', module: 'demo', panel: null, panel_error: '' })
      }),
      http.put('/api/users/kesepain/expand/global/demo/panel', async ({ request }) => {
        requests.push({ url: request.url, method: request.method, body: await request.json() })
        return HttpResponse.json({ user: 'kesepain', scope: 'global', module: 'demo', panel: null, panel_error: '', saved: true })
      }),
      http.post('/api/users/kesepain/expand/global/demo/panel/action', async ({ request }) => {
        requests.push({ url: request.url, method: request.method, body: await request.json() })
        return HttpResponse.json({ user: 'kesepain', scope: 'global', module: 'demo', panel: null, panel_error: '', command: 'probe' })
      }),
      http.get('/api/users/kesepain/sense/runtime/panel', ({ request }) => {
        requests.push({ url: request.url, method: request.method })
        return HttpResponse.json({ user: 'kesepain', module: 'runtime', panel: null, panel_error: '' })
      }),
      http.put('/api/users/kesepain/sense/runtime/panel', async ({ request }) => {
        requests.push({ url: request.url, method: request.method, body: await request.json() })
        return HttpResponse.json({ user: 'kesepain', module: 'runtime', panel: null, panel_error: '', saved: true })
      }),
    )

    await getExpandModulePanel('kesepain', 'global', 'demo')
    await putExpandModulePanel('kesepain', 'global', 'demo', { mode: 'fast' }, ['api_key'])
    await invokeExpandModulePanelAction('kesepain', 'global', 'demo', 'probe', { target: 'local' })
    await getSenseModulePanel('kesepain', 'runtime')
    await putSenseModulePanel('kesepain', 'runtime', { enabled: true }, ['token'])

    expect(requests.map(({ method }) => method)).toEqual(['GET', 'PUT', 'POST', 'GET', 'PUT'])
    expect(requests[1].body).toEqual({ values: { mode: 'fast' }, clear_secrets: ['api_key'] })
    expect(requests[2].body).toEqual({ command: 'probe', params: { target: 'local' } })
    expect(requests[4].body).toEqual({ values: { enabled: true }, clear_secrets: ['token'] })
  })

  it('subagent progress 在工具完成前透传且不终止 SSE', async () => {
    server.use(http.post('/api/chat', () => new HttpResponse(
      'event: subagent_progress\ndata: {"type":"subagent_progress","tool_call_id":"call-a","metadata":{"status":"tool_running","iteration":2,"tool_name":"file"}}\n\n'
      + 'event: tool_call_result\ndata: {"type":"tool_call_result","tool_call_id":"call-a","result":{"ok":true}}\n\n'
      + 'event: done\ndata: {"type":"done"}\n\n',
      { headers: { 'Content-Type': 'text/event-stream' } },
    )))
    const events: string[] = []
    await streamChat({ user: 'kesepain', sessionId: 's1', prompt: '执行', runId: 'run_progress', onEvent: (event) => {
      events.push(event.type)
      if (event.type === 'subagent_progress') expect(event).toMatchObject({ tool_call_id: 'call-a', metadata: { iteration: 2, tool_name: 'file' } })
    } })
    expect(events).toEqual(['subagent_progress', 'tool_call_result', 'done'])
  })

  it('保留跨块残片并解析完整事件', () => {
    const first = parseSseFrames('event: text_delta\ndata: {"type":"text_')
    expect(first.frames).toEqual([])
    const second = parseSseFrames(first.rest + 'delta","content":"你"}\n\n')
    expect(second.frames).toEqual([{ event: 'text_delta', data: '{"type":"text_delta","content":"你"}' }])
    expect(second.rest).toBe('')
  })

  it('兼容 CRLF 与多行 data', () => {
    const parsed = parseSseFrames('event: done\r\ndata: {"type":"done",\r\ndata: "metadata":{}}\r\n\r\n')
    expect(parsed.frames[0]).toEqual({ event: 'done', data: '{"type":"done",\n"metadata":{}}' })
  })

  it('提交运行中引导到指定 run_id', async () => {
    let body: Record<string, unknown> = {}
    server.use(http.post('/api/runs/:runId/guidance', async ({ params, request }) => {
      body = await request.json() as Record<string, unknown>
      return HttpResponse.json({ run_id: params.runId, status: 'accepted_current_run', queued: 1 })
    }))
    const result = await submitGuidance('kesepain', 'run_test_123', 'adjust', {
      sessionId: 'session-guidance',
      guidanceId: 'guidance_123',
      uploadedFiles: ['clip.mp4', 'voice.mp3'],
    })
    expect(result).toMatchObject({ run_id: 'run_test_123', status: 'accepted_current_run', queued: 1 })
    expect(body).toEqual({
      user: 'kesepain',
      source: 'web',
      session_id: 'session-guidance',
      guidance: 'adjust',
      guidance_id: 'guidance_123',
      uploaded_files: ['clip.mp4', 'voice.mp3'],
    })
  })

  it('计划执行请求携带 plan_id 且不需要伪造用户 prompt', async () => {
    let requestBody: Record<string, unknown> = {}
    server.use(http.post('/api/chat', async ({ request }) => {
      requestBody = await request.json() as Record<string, unknown>
      return new HttpResponse(
        'event: text_delta\ndata: {"type":"text_delta","content":"执行中"}\n\n'
        + 'event: done\ndata: {"type":"done"}\n\n',
        { headers: { 'Content-Type': 'text/event-stream' } },
      )
    }))
    const events: string[] = []

    await streamChat({
      user: 'kesepain',
      sessionId: 's1',
      clientId: 'web_client_test',
      prompt: '',
      planId: 'plan_12345678',
      runId: 'run_plan_123',
      onEvent: (event) => events.push(event.type),
    })

    expect(requestBody).toMatchObject({
      user: 'kesepain',
      session_id: 's1',
      prompt: '',
      plan_id: 'plan_12345678',
      run_id: 'run_plan_123',
      client_id: 'web_client_test',
    })
    expect(events).toEqual(['text_delta', 'done'])
  })

  it('不会把自动重试事件误判为流终态', async () => {
    server.use(http.post('/api/chat', () => new HttpResponse(
      'event: retrying\ndata: {"type":"retrying","content":"正在重试","metadata":{"next_attempt":2,"max_attempts":6}}\n\n'
      + 'event: done\ndata: {"type":"done","metadata":{"status":"completed"}}\n\n',
      { headers: { 'Content-Type': 'text/event-stream' } },
    )))
    const events: string[] = []

    await streamChat({
      user: 'kesepain',
      sessionId: 's1',
      prompt: '重试',
      runId: 'run_retry',
      onEvent: (event) => events.push(event.type),
    })

    expect(events).toEqual(['retrying', 'done'])
  })

  it('暂停计划使用无 revision 的状态指令接口', async () => {
    const result = await commandPlan('kesepain', 'plan_12345678', 'pause')
    expect(result).toMatchObject({ action: 'pause', updated: true, plan: { status: 'paused' } })
  })

  it('任务计划修正和重试请求携带对话空间身份', async () => {
    let editQuery = ''
    let retryQuery = ''
    server.use(
      http.patch('/api/users/kesepain/tasks/plans/plan_12345678/edit', ({ request }) => {
        editQuery = new URL(request.url).search
        return HttpResponse.json({ updated: true, plan: { plan_id: 'plan_12345678' } })
      }),
      http.post('/api/users/kesepain/tasks/plans/plan_12345678/steps/step_1/retry', ({ request }) => {
        retryQuery = new URL(request.url).search
        return HttpResponse.json({ updated: true, plan: { plan_id: 'plan_12345678' } })
      }),
    )

    await editPlan('kesepain', 'plan_12345678', { revision: 2 }, 'conversation-a')
    await retryPlanStep('kesepain', 'plan_12345678', 'step_1', 2, 'conversation-a')

    expect(new URLSearchParams(editQuery).get('session_id')).toBe('conversation-a')
    expect(new URLSearchParams(retryQuery).get('session_id')).toBe('conversation-a')
  })

  it('运行状态请求只携带当前栏目和摘要分区', async () => {
    let requestUrl = ''
    server.use(http.get('/api/users/kesepain/runtime/status', ({ request }) => {
      requestUrl = request.url
      return HttpResponse.json({ schema_version: 1 })
    }))

    await getRuntimeStatus('kesepain', 'session 1', ['summary', 'tokens'])

    const query = new URL(requestUrl).searchParams
    expect(query.get('session_id')).toBe('session 1')
    expect(query.get('sections')).toBe('summary,tokens')
  })

  it('为头像、Logo 和文件下载生成安全 URL', () => {
    expect(getLogoUrl()).toBe('/api/logo')
    expect(getUserAvatarUrl('a b', 7)).toBe('/api/users/a%20b/avatar?v=7')
    expect(getUserFileDownloadUrl('a b', 'download', 'dir/a b.txt')).toBe('/api/users/a%20b/files/download/download?path=dir%2Fa%20b.txt')
    expect(getUserArtifactUrl('a b', 'f'.repeat(64), 'nested/generated image.png', 2048)).toBe(`/api/users/a%20b/artifacts/${'f'.repeat(64)}?path=nested%2Fgenerated+image.png&size=2048`)
  })

  it('使用 multipart 上传头像并广播刷新事件', async () => {
    let updatedUser = ''
    window.addEventListener(AVATAR_UPDATED_EVENT, (event) => {
      updatedUser = (event as CustomEvent<{ user: string }>).detail.user
    }, { once: true })
    const result = await uploadUserAvatar('kesepain', new File(['png'], 'avatar.png', { type: 'image/png' }))
    expect(result).toMatchObject({ user: 'kesepain', format: 'image/png' })
    expect(updatedUser).toBe('kesepain')
  })
})
