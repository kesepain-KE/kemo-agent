import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../test/server'
import {
  AVATAR_UPDATED_EVENT,
  commandPlan,
  getLogoUrl,
  getUserAvatarUrl,
  getUserFileDownloadUrl,
  parseSseFrames,
  submitGuidance,
  streamChat,
  uploadUserAvatar,
} from './client'

describe('parseSseFrames', () => {
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
    const result = await submitGuidance('kesepain', 'run_test_123', 'adjust')
    expect(result).toMatchObject({ run_id: 'run_test_123', status: 'queued', queued: 1 })
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
    })
    expect(events).toEqual(['text_delta', 'done'])
  })

  it('暂停计划使用无 revision 的状态指令接口', async () => {
    const result = await commandPlan('kesepain', 'plan_12345678', 'pause')
    expect(result).toMatchObject({ action: 'pause', updated: true, plan: { status: 'paused' } })
  })

  it('为头像、Logo 和文件下载生成安全 URL', () => {
    expect(getLogoUrl()).toBe('/api/logo')
    expect(getUserAvatarUrl('a b', 7)).toBe('/api/users/a%20b/avatar?v=7')
    expect(getUserFileDownloadUrl('a b', 'download', 'dir/a b.txt')).toBe('/api/users/a%20b/files/download/download?path=dir%2Fa%20b.txt')
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
